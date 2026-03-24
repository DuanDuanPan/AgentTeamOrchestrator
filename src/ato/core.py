"""core — 主事件循环、启动与恢复。"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from pathlib import Path

import structlog
from structlog.contextvars import bind_contextvars

from ato.config import ATOSettings
from ato.models.db import (
    count_tasks_by_status,
    get_connection,
    mark_running_tasks_paused,
)
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

        MVP 阶段：仅记录日志。Agent 调度由 Epic 2B/3 接入。
        """
        logger.debug("poll_cycle")

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
