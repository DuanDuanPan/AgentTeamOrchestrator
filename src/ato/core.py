"""core — 主事件循环、启动与恢复。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import uuid
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import structlog
from structlog.contextvars import bind_contextvars

from ato.config import ATOSettings
from ato.models.db import (
    count_tasks_by_status,
    get_connection,
    get_tasks_by_status,
    insert_approval,
    mark_running_tasks_paused,
)
from ato.models.schemas import ApprovalRecord, TransitionEvent
from ato.nudge import Nudge
from ato.transition_queue import TransitionQueue

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


# ---------------------------------------------------------------------------
# PID 文件管理
# ---------------------------------------------------------------------------


def write_pid_file(pid_path: Path) -> None:
    """写入当前进程 PID 到文件。"""
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(os.getpid()))


def read_pid_file(pid_path: Path) -> int | None:
    """读取 PID 文件，文件不存在或内容无效返回 None。"""
    if not pid_path.exists():
        return None
    try:
        return int(pid_path.read_text().strip())
    except (ValueError, OSError):
        return None


def is_orchestrator_running(pid_path: Path) -> bool:
    """检测 Orchestrator 是否正在运行。

    读取 PID 文件 + ``os.kill(pid, 0)`` 检测进程存活。
    Stale PID（进程不存在）视为未运行。
    """
    pid = read_pid_file(pid_path)
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # 进程存在但无权发信号——视为存活
        return True


def remove_pid_file(pid_path: Path) -> None:
    """删除 PID 文件（幂等）。"""
    with contextlib.suppress(FileNotFoundError):
        pid_path.unlink()


# ---------------------------------------------------------------------------
# Interactive Session 检测辅助
# ---------------------------------------------------------------------------


async def _check_interactive_timeouts(
    db: aiosqlite.Connection,
    *,
    interactive_phases: set[str],
    timeout_seconds: int,
) -> None:
    """检测 interactive session 超时并创建 approval 请求。

    对 running 状态的 task，若 phase 属于 interactive_phases 且已超时，
    创建 session_timeout 类型的 approval 供操作者决策。
    对已有 pending session_timeout approval 的 task 不重复创建。
    """
    from ato.models.db import get_pending_approvals

    tasks = await get_tasks_by_status(db, "running")
    now = datetime.now(tz=UTC)

    # 收集已有 pending session_timeout 的 story_id 集合，避免重复
    pending_approvals = await get_pending_approvals(db)
    stories_with_timeout = {
        a.story_id for a in pending_approvals if a.approval_type == "session_timeout"
    }

    for task in tasks:
        if task.phase not in interactive_phases:
            continue
        if task.started_at is None:
            continue
        elapsed = (now - task.started_at).total_seconds()
        if elapsed <= timeout_seconds:
            continue
        # 已有 pending timeout approval 则跳过
        if task.story_id in stories_with_timeout:
            continue

        approval = ApprovalRecord(
            approval_id=str(uuid.uuid4()),
            story_id=task.story_id,
            approval_type="session_timeout",
            status="pending",
            payload=json.dumps(
                {
                    "task_id": task.task_id,
                    "elapsed_seconds": elapsed,
                    "options": ["restart", "resume", "abandon"],
                    "recommended_action": "restart",
                }
            ),
            created_at=now,
        )
        await insert_approval(db, approval)
        stories_with_timeout.add(task.story_id)
        logger.warning(
            "interactive_session_timeout",
            story_id=task.story_id,
            task_id=task.task_id,
            elapsed_seconds=elapsed,
        )


async def _detect_completed_interactive_tasks(
    db: aiosqlite.Connection,
    *,
    interactive_phases: set[str],
    phase_event_map: dict[str, str],
) -> list[tuple[str, TransitionEvent]]:
    """检测已由 `ato submit` 标记完成的 interactive task。

    仅处理 story.current_phase 仍停留在 interactive phase 的 completed task，
    防止重复派发。**不在此函数内标记已消费**——调用方在 TQ.submit() 成功后
    逐个标记 ``expected_artifact='transition_submitted'``，确保原子性。

    Returns:
        (task_id, TransitionEvent) 对列表。
    """
    from ato.models.db import get_story

    tasks = await get_tasks_by_status(db, "completed")
    now = datetime.now(tz=UTC)
    results: list[tuple[str, TransitionEvent]] = []
    for task in tasks:
        if task.phase not in interactive_phases:
            continue
        # 已经被消费过的 task 不再处理
        if task.expected_artifact == "transition_submitted":
            continue
        # 校验 story.current_phase 仍在该 interactive phase
        story = await get_story(db, task.story_id)
        if story is None or story.current_phase != task.phase:
            continue
        event_name = phase_event_map.get(task.phase)
        if event_name is None:
            logger.warning(
                "no_event_mapping_for_phase",
                phase=task.phase,
                story_id=task.story_id,
            )
            continue
        results.append(
            (
                task.task_id,
                TransitionEvent(
                    story_id=task.story_id,
                    event_name=event_name,
                    source="cli",
                    submitted_at=now,
                ),
            )
        )
    return results


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class Orchestrator:
    """编排器主类——asyncio 事件循环 + 轮询/nudge 混合模式。"""

    def __init__(self, *, settings: ATOSettings, db_path: Path) -> None:
        self._settings = settings
        self._db_path = db_path
        self._nudge = Nudge()
        self._tq: TransitionQueue | None = None
        self._running = True
        self._pid_path = db_path.parent / "orchestrator.pid"

    async def run(self) -> None:
        """主入口——启动 → 轮询 → 停止。

        _startup() 在 try/finally 内执行，确保即使启动阶段抛异常
        也能正确清理已分配的资源（PID 文件、TransitionQueue）。
        """
        try:
            await self._startup()
            while self._running:
                await self._poll_cycle()
                if self._running:
                    await self._nudge.wait(timeout=self._settings.polling_interval)
        finally:
            await self._shutdown()

    async def _startup(self) -> None:
        """启动序列：注册信号 → 写 PID → 初始化组件 → 恢复检测。

        信号 handler 最先注册，确保写 PID 后任何 SIGTERM 都走优雅停止路径，
        消除 "PID 已可见但 handler 未就绪" 的竞态窗口。
        handler 只设 flag + nudge，不依赖 TQ 或 DB，可安全最先注册。
        """
        bind_contextvars(component="orchestrator")

        # 注册信号 handler（必须最先完成——消除 PID 写入后的竞态窗口）
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, self._request_shutdown)
        loop.add_signal_handler(signal.SIGUSR1, self._nudge.notify)

        # 写 PID 文件（此时 SIGTERM 已有 handler，不会被默认行为杀死）
        write_pid_file(self._pid_path)
        logger.info("pid_file_written", pid=os.getpid(), path=str(self._pid_path))

        # 初始化 TransitionQueue
        self._tq = TransitionQueue(self._db_path, nudge=self._nudge)
        await self._tq.start()

        # 恢复检测
        db = await get_connection(self._db_path)
        try:
            await self._detect_recovery_mode(db)
        finally:
            await db.close()

        logger.info("orchestrator_started", polling_interval=self._settings.polling_interval)

    async def _shutdown(self) -> None:
        """优雅停止：标记 running tasks → 停止 TransitionQueue → 删除 PID。

        对部分初始化（_startup 中途失败）安全——每个阶段独立 try/except。
        所有资源无条件清理；如果关键操作（task paused）失败则最终 re-raise，
        让调用方知道这不是一次干净的停止。
        """
        shutdown_error: Exception | None = None

        # 1. 标记所有 running tasks 为 paused（DB 可能尚未就绪）
        try:
            db = await get_connection(self._db_path)
            try:
                count = await mark_running_tasks_paused(db)
                await db.commit()
                if count > 0:
                    logger.info("shutdown_tasks_paused", count=count)
            finally:
                await db.close()
        except Exception as exc:
            logger.error("shutdown_mark_paused_failed", exc_info=True)
            shutdown_error = exc

        # 2. 停止 TransitionQueue
        if self._tq is not None:
            try:
                await self._tq.stop()
            except Exception:
                logger.warning("shutdown_tq_stop_failed", exc_info=True)

        # 3. 删除 PID 文件
        remove_pid_file(self._pid_path)

        if shutdown_error is not None:
            logger.error(
                "orchestrator_stopped_dirty",
                reason="mark_running_tasks_paused failed, tasks may remain running",
            )
            raise shutdown_error
        logger.info("orchestrator_stopped")

    async def _poll_cycle(self) -> None:
        """单次轮询：检测新事件、检查 approval 状态、调度就绪任务。

        Interactive session 检测：
        1. 超时的 interactive task → 创建 approval 请求
        2. 已完成的 interactive task → 生成 success TransitionEvent
        """
        logger.debug("poll_cycle")

        # 构建 interactive phase 集合
        from ato.config import build_phase_definitions

        phase_defs = build_phase_definitions(self._settings)
        interactive_phases = {
            pd.name for pd in phase_defs if pd.phase_type == "interactive_session"
        }

        if interactive_phases:
            db = await get_connection(self._db_path)
            try:
                # 检测超时
                await _check_interactive_timeouts(
                    db,
                    interactive_phases=interactive_phases,
                    timeout_seconds=self._settings.timeout.interactive_session,
                )

                # 检测已完成的 interactive task
                # 显式映射 phase → success event（必须与 state_machine.py 一致）
                # 不能用 f"{name}_pass"，因为某些 phase 的 event 名不规则
                # 例如 developing → dev_done（非 developing_pass）
                phase_success_event: dict[str, str] = {
                    "uat": "uat_pass",
                    "developing": "dev_done",
                }
                phase_event_map: dict[str, str] = {}
                for pd in phase_defs:
                    if pd.phase_type == "interactive_session":
                        mapped_event = phase_success_event.get(pd.name)
                        if mapped_event is not None:
                            phase_event_map[pd.name] = mapped_event
                        else:
                            logger.error(
                                "unmapped_interactive_phase",
                                phase=pd.name,
                                hint="Add mapping to _PHASE_SUCCESS_EVENT before enabling",
                            )

                task_events = await _detect_completed_interactive_tasks(
                    db,
                    interactive_phases=interactive_phases,
                    phase_event_map=phase_event_map,
                )

                # 提交 transition events，成功后才标记已消费
                if task_events and self._tq is not None:
                    from ato.models.db import update_task_status

                    for task_id, event in task_events:
                        await self._tq.submit(event)
                        # submit 成功后才标记——崩溃时下次轮询会重试
                        await update_task_status(
                            db,
                            task_id,
                            "completed",
                            expected_artifact="transition_submitted",
                        )
            finally:
                await db.close()

    async def _detect_recovery_mode(self, db: object) -> None:
        """启动时扫描 tasks 表，检测恢复模式并输出日志。

        仅做检测和 structlog 输出，不执行实际恢复（Epic 5 范畴）。
        """
        import aiosqlite

        if not isinstance(db, aiosqlite.Connection):
            return

        running = await count_tasks_by_status(db, "running")
        paused = await count_tasks_by_status(db, "paused")

        if running > 0:
            logger.warning("crash_recovery_detected", running_tasks=running)
        elif paused > 0:
            logger.info("graceful_recovery_detected", paused_tasks=paused)
        else:
            logger.info("fresh_start", message="无待恢复任务")

    def _request_shutdown(self) -> None:
        """SIGTERM handler：标记停止并唤醒轮询循环。"""
        self._running = False
        self._nudge.notify()
