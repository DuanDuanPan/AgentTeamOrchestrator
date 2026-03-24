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

from ato.models.db import get_connection, get_story
from ato.models.schemas import StateTransitionError, TransitionEvent
from ato.nudge import Nudge
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
    "start_create",   # queued → creating
    "create_done",    # creating → validating
    "validate_pass",  # validating → dev_ready
    "start_dev",      # dev_ready → developing
    "dev_done",       # developing → reviewing
    "review_pass",    # reviewing → qa_testing
    "qa_pass",        # qa_testing → uat
    "uat_pass",       # uat → merging
    "merge_done",     # merging → regression
    "regression_pass",  # regression → done
]
_HP_PHASES: list[str] = [
    "queued",
    "creating",
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

    def __init__(self, db_path: Path, nudge: Nudge | None = None) -> None:
        self._db_path = db_path
        self._nudge = nudge
        self._queue: asyncio.Queue[TransitionEvent | None] = asyncio.Queue()
        self._machines: dict[str, StoryLifecycle] = {}
        self._consumer_task: asyncio.Task[None] | None = None
        self._db: aiosqlite.Connection | None = None
        self._running = False

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
                await save_story_state(db, event.story_id, sm.current_state_value)
                await db.commit()

                latency_ms = (time.monotonic() - t_start) * 1000
                logger.info(
                    "transition_processing_end",
                    new_state=sm.current_state_value,
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
