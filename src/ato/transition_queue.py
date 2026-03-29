"""transition_queue — 串行状态转换队列。

所有状态转换通过单个 consumer 串行化处理，保证：
1. FIFO 顺序——事件按提交顺序逐一执行
2. 原子性——每个事件 send() → persist → commit 不可拆分
3. 错误隔离——单事件失败 rollback + 驱逐缓存，不影响后续事件
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import aiosqlite
import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars

from ato.config import PhaseDefinition, evaluate_skip_condition
from ato.models.db import get_connection, get_story
from ato.models.schemas import StateTransitionError, TransitionEvent
from ato.nudge import Nudge, send_user_notification
from ato.state_machine import (
    StoryLifecycle,
    save_story_state,
)

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Replay 辅助：phase → 到达该 phase 所需的事件序列
# ---------------------------------------------------------------------------

# happy-path phase → 从 queued 出发的 success 事件序列
_HAPPY_PATH_EVENTS: dict[str, list[str]] = {}
_HP_EVENTS: list[str] = [
    "start_create",  # queued → planning
    "plan_done",  # planning → creating
    "create_done",  # creating → designing
    "design_done",  # designing → validating
    "validate_pass",  # validating → dev_ready
    "start_dev",  # dev_ready → developing
    "dev_done",  # developing → reviewing
    "review_pass",  # reviewing → qa_testing
    "qa_pass",  # qa_testing → uat
    "uat_pass",  # uat → merging
    "merge_done",  # merging → regression
    "regression_pass",  # regression → done
]
_HP_PHASES: list[str] = [
    "queued",
    "planning",
    "creating",
    "designing",
    "validating",
    "dev_ready",
    "developing",
    "reviewing",
    "qa_testing",
    "uat",
    "merging",
    "regression",
    "done",
]
for _i, _phase in enumerate(_HP_PHASES):
    _HAPPY_PATH_EVENTS[_phase] = _HP_EVENTS[:_i]

# phase → 该 phase 的 success 事件（用于条件跳过时自动提交）
_PHASE_SUCCESS_EVENT: dict[str, str] = {}
for _i, _phase in enumerate(_HP_PHASES[:-1]):  # exclude "done"
    _PHASE_SUCCESS_EVENT[_phase] = _HP_EVENTS[_i]

# 非 happy-path phases 的特殊 replay 路径
_SPECIAL_REPLAY: dict[str, list[str]] = {
    # fixing 可以从 reviewing（review_fail）或 qa_testing（qa_fail）到达
    # 使用最短路径：queued → ... → reviewing → review_fail → fixing
    "fixing": _HAPPY_PATH_EVENTS["reviewing"] + ["review_fail"],
    # blocked 从任意非 final 状态 escalate 到达；最短：queued → escalate
    "blocked": ["escalate"],
}


async def _replay_to_phase(sm: StoryLifecycle, target_phase: str) -> None:
    """从 queued 状态 replay 事件序列到达目标 phase。

    Args:
        sm: 已激活初始状态（queued）的状态机。
        target_phase: 目标阶段名。

    Raises:
        StateTransitionError: 目标 phase 无法到达。
    """
    if target_phase == "queued":
        return  # 已在初始状态

    events = _HAPPY_PATH_EVENTS.get(target_phase) or _SPECIAL_REPLAY.get(target_phase)
    if events is None:
        msg = f"Cannot replay to unknown phase '{target_phase}'"
        raise StateTransitionError(msg)

    for event_name in events:
        await sm.send(event_name)


# ---------------------------------------------------------------------------
# TransitionQueue
# ---------------------------------------------------------------------------


class TransitionQueue:
    """串行状态转换队列。

    所有状态转换通过 ``submit()`` 放入 ``asyncio.Queue``，由单个
    ``_consumer`` 后台任务按 FIFO 顺序逐一执行。

    用法::

        tq = TransitionQueue(db_path)
        await tq.start()
        await tq.submit(event)
        ...
        await tq.stop()
    """

    def __init__(
        self,
        db_path: Path,
        nudge: Nudge | None = None,
        phase_defs: list[PhaseDefinition] | None = None,
    ) -> None:
        self._db_path = db_path
        self._nudge = nudge
        self._queue: asyncio.Queue[TransitionEvent | None] = asyncio.Queue()
        self._machines: dict[str, StoryLifecycle] = {}
        self._consumer_task: asyncio.Task[None] | None = None
        self._db: aiosqlite.Connection | None = None
        self._running = False
        # phase name → PhaseDefinition lookup for skip_when evaluation
        self._phase_defs: dict[str, PhaseDefinition] = (
            {pd.name: pd for pd in phase_defs} if phase_defs else {}
        )

    async def start(self) -> None:
        """启动 consumer 后台任务并打开长连接。

        重复调用不会创建第二个 consumer。重新 start 前会排空残留哨兵。
        """
        if self._consumer_task is not None and not self._consumer_task.done():
            logger.warning("transition_queue_already_running")
            return

        # 排空上一轮 stop() 可能残留的哨兵（或任何残留项）
        self._queue = asyncio.Queue()
        db = await get_connection(self._db_path)
        self._db = db
        self._consumer_task = asyncio.create_task(self._consumer(db))
        self._running = True
        logger.info("transition_queue_started", db_path=str(self._db_path))

    async def stop(self) -> None:
        """优雅停止 consumer：发送哨兵，等待完成，关闭连接。

        幂等：对未启动或已停止的队列调用 stop() 是安全的。
        """
        if not self._running:
            return
        self._running = False

        if self._consumer_task is not None and not self._consumer_task.done():
            await self._queue.put(None)  # 哨兵
            await self._consumer_task
        self._consumer_task = None

        if self._db is not None:
            db = self._db
            self._db = None
            await db.close()
        self._machines.clear()
        logger.info("transition_queue_stopped")

    async def submit(self, event: TransitionEvent) -> None:
        """提交状态转换事件到队列。

        Args:
            event: 要处理的状态转换事件。

        Raises:
            StateTransitionError: 队列已停止，拒绝新事件。
        """
        if not self._running:
            msg = "TransitionQueue is not running, call start() first"
            raise StateTransitionError(msg)

        await self._queue.put(event)
        logger.info(
            "transition_submitted",
            story_id=event.story_id,
            event_name=event.event_name,
            source=event.source,
            queue_depth=self._queue.qsize(),
        )
        if self._nudge is not None:
            self._nudge.notify()

    async def _consumer(self, db: aiosqlite.Connection) -> None:
        """串行处理队列中的事件。"""

        while True:
            event = await self._queue.get()
            if event is None:
                self._queue.task_done()
                break  # 哨兵 → 退出

            t_start = time.monotonic()
            bind_contextvars(
                story_id=event.story_id,
                event_name=event.event_name,
                source=event.source,
            )
            try:
                queue_depth = self._queue.qsize()
                logger.info("transition_processing_start", queue_depth=queue_depth)

                sm = await self._get_or_create_machine(event.story_id, db)
                await sm.send(event.event_name)
                new_state = sm.current_state_value
                await save_story_state(db, event.story_id, new_state)
                await db.commit()

                # Post-commit hooks
                await self._on_phase_skip_check(db, event.story_id, new_state, event.source)
                if new_state == "dev_ready":
                    await self._on_enter_dev_ready(db, event.story_id)
                elif new_state == "developing":
                    await self._on_enter_developing(db, event.story_id)
                elif new_state == "done":
                    await self._on_story_done(db, event.story_id)

                latency_ms = (time.monotonic() - t_start) * 1000
                logger.info(
                    "transition_processing_end",
                    new_state=new_state,
                    latency_ms=round(latency_ms, 1),
                )
            except Exception:
                logger.exception("transition_failed")
                # send() 后可能内存状态已变但 DB 未 commit——驱逐缓存
                self._machines.pop(event.story_id, None)
                try:
                    await db.rollback()
                except Exception:
                    logger.exception("rollback_failed")
            finally:
                self._queue.task_done()
                clear_contextvars()

    async def _on_phase_skip_check(
        self,
        db: aiosqlite.Connection,
        story_id: str,
        new_phase: str,
        source: str,
    ) -> None:
        """Post-commit hook：检查新 phase 是否配置了 skip_when 条件跳过。

        若 skip_when 求值为 True，自动将对应的 success event 放入队列，
        使 story 合法地经过该 phase 然后立即转入下一个 phase。
        """
        if not self._phase_defs:
            return

        phase_def = self._phase_defs.get(new_phase)
        if phase_def is None or phase_def.skip_when is None:
            return

        story = await get_story(db, story_id)
        if story is None:
            return

        should_skip = evaluate_skip_condition(phase_def.skip_when, story)
        if not should_skip:
            return

        success_event = _PHASE_SUCCESS_EVENT.get(new_phase)
        if success_event is None:
            logger.warning(
                "phase_skip_no_success_event",
                story_id=story_id,
                phase=new_phase,
            )
            return

        from datetime import UTC, datetime

        skip_event = TransitionEvent(
            story_id=story_id,
            event_name=success_event,
            source=source,
            submitted_at=datetime.now(tz=UTC),
        )
        await self._queue.put(skip_event)
        logger.info(
            "phase_skipped",
            story_id=story_id,
            phase=new_phase,
            skip_expression=phase_def.skip_when,
            skip_reason=f"skip_when evaluated to True: {phase_def.skip_when}",
            auto_event=success_event,
        )

    async def _on_enter_dev_ready(self, db: aiosqlite.Connection, story_id: str) -> None:
        """Story 进入 dev_ready 时的 post-commit hook：检查 batch spec commit。

        当 active batch 内所有 story 均到达 dev_ready 且 spec 尚未提交时，
        执行单次本地 commit 将规格文件提交到 main。
        """
        from ato.models.db import (
            get_active_batch,
            get_batch_stories,
            insert_approval,
            mark_batch_spec_committed,
        )

        batch = await get_active_batch(db)
        if batch is None:
            return

        # 已提交则跳过
        if batch.spec_committed:
            return

        # 检查 batch 内所有 story 是否都到达 dev_ready
        batch_stories = await get_batch_stories(db, batch.batch_id)
        all_dev_ready = all(s.current_phase == "dev_ready" for _, s in batch_stories)
        if not all_dev_ready:
            return

        story_ids = [s.story_id for _, s in batch_stories]

        try:
            from ato.core import derive_project_root
            from ato.worktree_mgr import WorktreeManager

            project_root = derive_project_root(self._db_path)
            mgr = WorktreeManager(project_root=project_root, db_path=self._db_path)
            success, message = await mgr.batch_spec_commit(batch.batch_id, story_ids)

            if success:
                await mark_batch_spec_committed(db, batch.batch_id)
                logger.info(
                    "batch_spec_commit_success",
                    batch_id=batch.batch_id,
                    story_ids=story_ids,
                    commit_hash=message,
                )
            else:
                # 创建 precommit_failure approval
                import json
                import uuid
                from datetime import UTC
                from datetime import datetime as dt_cls

                from ato.models.schemas import ApprovalRecord

                payload = json.dumps({
                    "scope": "spec_batch",
                    "batch_id": batch.batch_id,
                    "story_ids": story_ids,
                    "error_output": message,
                    "options": ["retry", "manual_fix", "skip"],
                })
                approval = ApprovalRecord(
                    approval_id=str(uuid.uuid4()),
                    story_id=story_id,
                    approval_type="precommit_failure",
                    status="pending",
                    payload=payload,
                    created_at=dt_cls.now(tz=UTC),
                    recommended_action="retry",
                    risk_level="medium",
                )
                await insert_approval(db, approval)
                send_user_notification(
                    "normal",
                    f"Batch spec commit 失败：{message}",
                )
                logger.warning(
                    "batch_spec_commit_failed",
                    batch_id=batch.batch_id,
                    error=message,
                )
        except Exception as exc:
            logger.exception(
                "batch_spec_commit_error",
                story_id=story_id,
            )
            # 异常也需创建 approval，否则 batch 卡死且无恢复路径
            try:
                import json
                import uuid
                from datetime import UTC
                from datetime import datetime as dt_cls

                from ato.models.schemas import ApprovalRecord

                payload = json.dumps({
                    "scope": "spec_batch",
                    "batch_id": batch.batch_id,
                    "story_ids": story_ids,
                    "error_output": str(exc),
                    "options": ["retry", "manual_fix", "skip"],
                })
                approval = ApprovalRecord(
                    approval_id=str(uuid.uuid4()),
                    story_id=story_id,
                    approval_type="precommit_failure",
                    status="pending",
                    payload=payload,
                    created_at=dt_cls.now(tz=UTC),
                    recommended_action="retry",
                    risk_level="medium",
                )
                await insert_approval(db, approval)
                send_user_notification(
                    "normal",
                    f"Batch spec commit 异常：{exc}",
                )
            except Exception:
                logger.exception("batch_spec_commit_approval_creation_failed")

    async def _on_enter_developing(self, db: aiosqlite.Connection, story_id: str) -> None:
        """Story 首次进入 developing 时的 post-commit hook：创建 worktree。

        幂等：WorktreeManager.create() 已有幂等逻辑——worktree 已存在则跳过。
        """
        story = await get_story(db, story_id)
        if story is None:
            return

        # 已有 worktree 则跳过
        if story.worktree_path is not None:
            logger.info(
                "worktree_already_exists",
                story_id=story_id,
                worktree_path=story.worktree_path,
            )
            return

        try:
            from ato.core import derive_project_root
            from ato.worktree_mgr import WorktreeManager

            project_root = derive_project_root(self._db_path)
            mgr = WorktreeManager(project_root=project_root, db_path=self._db_path)
            worktree_path = await mgr.create(story_id, base_ref="HEAD")
            logger.info(
                "worktree_created_on_developing",
                story_id=story_id,
                worktree_path=str(worktree_path),
            )
        except Exception:
            logger.exception(
                "worktree_creation_failed_on_developing",
                story_id=story_id,
            )

    async def _on_story_done(self, db: aiosqlite.Connection, story_id: str) -> None:
        """Story 完成后的 post-commit hook：里程碑通知 + batch 完成检测。"""
        from ato.models.db import complete_batch, get_active_batch, get_batch_progress

        send_user_notification("milestone", f"Story {story_id} 已完成！")

        # 检查 active batch 是否全部交付
        batch = await get_active_batch(db)
        if batch is None:
            return
        progress = await get_batch_progress(db, batch.batch_id)
        if progress.done == progress.total and progress.total > 0:
            completed = await complete_batch(db, batch.batch_id)
            if completed:
                send_user_notification("milestone", "Batch 全部交付完成！")

    async def _get_or_create_machine(
        self,
        story_id: str,
        db: aiosqlite.Connection,
    ) -> StoryLifecycle:
        """获取缓存的状态机，或从 SQLite 恢复。"""
        if story_id in self._machines:
            return self._machines[story_id]

        story = await get_story(db, story_id)
        if story is None:
            msg = f"Story '{story_id}' not found in database"
            raise StateTransitionError(msg)

        sm = await StoryLifecycle.create()
        try:
            await _replay_to_phase(sm, story.current_phase)
        except Exception:
            # 恢复失败——不缓存半初始化的实例
            logger.exception(
                "machine_replay_failed",
                story_id=story_id,
                target_phase=story.current_phase,
            )
            raise

        self._machines[story_id] = sm
        logger.info(
            "machine_restored",
            story_id=story_id,
            phase=story.current_phase,
        )
        return sm
