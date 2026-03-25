"""Orchestrator 核心行为单元测试。"""

from __future__ import annotations

import asyncio
import os
import signal
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ato.core import (
    Orchestrator,
    is_orchestrator_running,
    read_pid_file,
    remove_pid_file,
    write_pid_file,
)

# ---------------------------------------------------------------------------
# PID 文件管理测试
# ---------------------------------------------------------------------------


class TestWritePidFile:
    def test_writes_current_pid(self, tmp_path: Path) -> None:
        pid_path = tmp_path / ".ato" / "orchestrator.pid"
        write_pid_file(pid_path)
        assert pid_path.exists()
        assert int(pid_path.read_text().strip()) == os.getpid()

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        pid_path = tmp_path / "a" / "b" / "c" / "orchestrator.pid"
        write_pid_file(pid_path)
        assert pid_path.exists()


class TestReadPidFile:
    def test_returns_pid(self, tmp_path: Path) -> None:
        pid_path = tmp_path / "orchestrator.pid"
        pid_path.write_text("12345")
        assert read_pid_file(pid_path) == 12345

    def test_returns_none_when_missing(self, tmp_path: Path) -> None:
        pid_path = tmp_path / "orchestrator.pid"
        assert read_pid_file(pid_path) is None

    def test_returns_none_when_invalid(self, tmp_path: Path) -> None:
        pid_path = tmp_path / "orchestrator.pid"
        pid_path.write_text("not-a-number")
        assert read_pid_file(pid_path) is None


class TestIsOrchestratorRunning:
    def test_returns_false_no_pid_file(self, tmp_path: Path) -> None:
        pid_path = tmp_path / "orchestrator.pid"
        assert is_orchestrator_running(pid_path) is False

    def test_returns_true_when_process_alive(self, tmp_path: Path) -> None:
        pid_path = tmp_path / "orchestrator.pid"
        pid_path.write_text(str(os.getpid()))  # 当前进程存活
        assert is_orchestrator_running(pid_path) is True

    def test_returns_false_stale_pid(self, tmp_path: Path) -> None:
        pid_path = tmp_path / "orchestrator.pid"
        # 使用一个不太可能存在的 PID
        pid_path.write_text("9999999")
        with patch("ato.core.os.kill", side_effect=ProcessLookupError):
            assert is_orchestrator_running(pid_path) is False

    def test_returns_true_permission_error(self, tmp_path: Path) -> None:
        pid_path = tmp_path / "orchestrator.pid"
        pid_path.write_text("12345")
        with patch("ato.core.os.kill", side_effect=PermissionError):
            assert is_orchestrator_running(pid_path) is True


class TestRemovePidFile:
    def test_removes_existing_file(self, tmp_path: Path) -> None:
        pid_path = tmp_path / "orchestrator.pid"
        pid_path.write_text("12345")
        remove_pid_file(pid_path)
        assert not pid_path.exists()

    def test_idempotent_on_missing_file(self, tmp_path: Path) -> None:
        pid_path = tmp_path / "orchestrator.pid"
        remove_pid_file(pid_path)  # 不应抛出异常


# ---------------------------------------------------------------------------
# Orchestrator 恢复检测测试
# ---------------------------------------------------------------------------


class TestRecoveryDetection:
    async def test_fresh_start_no_tasks(self, initialized_db_path: Path) -> None:
        """无 running/paused tasks 时输出全新启动日志。"""
        from ato.models.db import get_connection

        settings = _make_settings()
        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)
        db = await get_connection(initialized_db_path)
        try:
            with patch("ato.core.logger") as mock_logger:
                await orchestrator._detect_recovery_mode(db)
                mock_logger.info.assert_any_call("fresh_start", message="无待恢复任务")
        finally:
            await db.close()

    async def test_crash_recovery_running_tasks(self, initialized_db_path: Path) -> None:
        """有 running tasks 时输出崩溃恢复日志。"""
        from ato.models.db import get_connection

        await _insert_test_task(initialized_db_path, status="running")

        settings = _make_settings()
        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)
        db = await get_connection(initialized_db_path)
        try:
            with patch("ato.core.logger") as mock_logger:
                await orchestrator._detect_recovery_mode(db)
                mock_logger.warning.assert_any_call("crash_recovery_detected", running_tasks=1)
        finally:
            await db.close()

    async def test_graceful_recovery_paused_tasks(self, initialized_db_path: Path) -> None:
        """有 paused tasks 时输出正常恢复日志。"""
        from ato.models.db import get_connection

        await _insert_test_task(initialized_db_path, status="paused")

        settings = _make_settings()
        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)
        db = await get_connection(initialized_db_path)
        try:
            with patch("ato.core.logger") as mock_logger:
                await orchestrator._detect_recovery_mode(db)
                mock_logger.info.assert_any_call("graceful_recovery_detected", paused_tasks=1)
        finally:
            await db.close()


# ---------------------------------------------------------------------------
# Orchestrator SIGUSR1 handler 测试
# ---------------------------------------------------------------------------


class TestSignalHandlers:
    async def test_sigusr1_registers_nudge_notify(self, initialized_db_path: Path) -> None:
        """启动时注册 SIGUSR1 handler 指向 nudge.notify。"""
        settings = _make_settings()
        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)

        with (
            patch.object(orchestrator, "_detect_recovery_mode", new_callable=AsyncMock),
            patch("ato.core.TransitionQueue") as mock_tq_cls,
        ):
            mock_tq = AsyncMock()
            mock_tq_cls.return_value = mock_tq

            loop = asyncio.get_running_loop()
            registered_handlers: dict[int, object] = {}

            def capture_handler(sig: int, callback: object, *args: object) -> None:
                registered_handlers[sig] = callback

            with patch.object(loop, "add_signal_handler", side_effect=capture_handler):
                await orchestrator._startup()

            assert signal.SIGUSR1 in registered_handlers
            assert registered_handlers[signal.SIGUSR1] == orchestrator._nudge.notify
            assert signal.SIGTERM in registered_handlers
            assert registered_handlers[signal.SIGTERM] == orchestrator._request_shutdown

            # 清理
            await orchestrator._shutdown()


# ---------------------------------------------------------------------------
# Orchestrator shutdown 测试
# ---------------------------------------------------------------------------


class TestShutdownPausesTasks:
    async def test_running_tasks_marked_paused(self, initialized_db_path: Path) -> None:
        """shutdown 时 running tasks 被标记为 paused。"""
        from ato.models.db import get_connection

        await _insert_test_task(initialized_db_path, status="running", task_id="task-run-1")
        await _insert_test_task(initialized_db_path, status="running", task_id="task-run-2")
        await _insert_test_task(initialized_db_path, status="completed", task_id="task-done-1")

        settings = _make_settings()
        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)
        # 手动设置 _tq 以避免完整启动
        orchestrator._tq = AsyncMock()

        # 写 PID 文件以便 shutdown 能删除
        write_pid_file(orchestrator._pid_path)

        await orchestrator._shutdown()

        # 验证 running → paused
        db = await get_connection(initialized_db_path)
        try:
            from ato.models.db import count_tasks_by_status

            running_count = await count_tasks_by_status(db, "running")
            paused_count = await count_tasks_by_status(db, "paused")
            completed_count = await count_tasks_by_status(db, "completed")

            assert running_count == 0
            assert paused_count == 2
            assert completed_count == 1
        finally:
            await db.close()

        # PID 文件应已删除
        assert not orchestrator._pid_path.exists()


class TestShutdownDirty:
    async def test_mark_paused_failure_raises_and_still_cleans_pid(
        self, initialized_db_path: Path
    ) -> None:
        """mark_running_tasks_paused 失败时：re-raise 异常 + 仍清理 PID。"""
        settings = _make_settings()
        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)
        orchestrator._tq = AsyncMock()
        write_pid_file(orchestrator._pid_path)

        # 让 get_connection 在 shutdown 时失败
        with (
            patch("ato.core.get_connection", side_effect=RuntimeError("DB unavailable")),
            pytest.raises(RuntimeError, match="DB unavailable"),
        ):
            await orchestrator._shutdown()

        # PID 仍被清理（资源不能泄漏）
        assert not orchestrator._pid_path.exists()
        # TQ 仍被停止
        assert orchestrator._tq is not None
        orchestrator._tq.stop.assert_awaited_once()

    async def test_dirty_shutdown_propagates_through_run(self, initialized_db_path: Path) -> None:
        """mark_running_tasks_paused 失败导致 run() 也抛异常（非零退出信号）。"""
        settings = _make_settings()
        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)

        # 正常启动但让 shutdown 时 DB 不可用
        async def stop_immediately() -> None:
            orchestrator._request_shutdown()

        orchestrator._poll_cycle = stop_immediately  # type: ignore[method-assign]

        # 在 _shutdown 中 patch get_connection 使 mark_running_tasks_paused 失败
        original_shutdown = orchestrator._shutdown

        async def shutdown_with_db_failure() -> None:
            with patch(
                "ato.core.get_connection",
                side_effect=RuntimeError("DB crash"),
            ):
                await original_shutdown()

        orchestrator._shutdown = shutdown_with_db_failure  # type: ignore[method-assign]

        with pytest.raises(RuntimeError, match="DB crash"):
            await orchestrator.run()


class TestStartupFailureCleanup:
    async def test_startup_exception_cleans_up_pid_and_tq(self, initialized_db_path: Path) -> None:
        """_startup() 中途异常时 run() 仍清理 PID 文件和 TransitionQueue。"""
        settings = _make_settings()
        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)
        pid_path = orchestrator._pid_path

        # 让 _startup 在信号注册阶段抛异常
        async def failing_startup() -> None:
            # 执行真实 startup 直到写 PID + 启动 TQ
            write_pid_file(pid_path)
            orchestrator._tq = AsyncMock()
            # 模拟信号注册失败
            raise RuntimeError("simulated signal handler failure")

        orchestrator._startup = failing_startup  # type: ignore[method-assign]

        with pytest.raises(RuntimeError, match="simulated signal handler failure"):
            await orchestrator.run()

        # PID 文件应被 _shutdown 清理
        assert not pid_path.exists()
        # TransitionQueue.stop() 应被调用
        assert orchestrator._tq is not None
        orchestrator._tq.stop.assert_awaited_once()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _make_settings() -> MagicMock:
    """创建一个 mock ATOSettings。"""
    settings = MagicMock()
    settings.polling_interval = 1.0
    return settings


async def _insert_test_task(
    db_path: Path,
    *,
    status: str = "pending",
    task_id: str = "test-task-1",
    story_id: str = "test-story-1",
) -> None:
    """插入一条测试 task（先确保对应 story 存在）。"""
    from ato.models.db import get_connection, insert_story, insert_task
    from ato.models.schemas import StoryRecord, TaskRecord

    now = datetime.now(tz=UTC)
    db = await get_connection(db_path)
    try:
        # 确保 story 存在（幂等）
        existing = await db.execute("SELECT 1 FROM stories WHERE story_id = ?", (story_id,))
        if await existing.fetchone() is None:
            story = StoryRecord(
                story_id=story_id,
                title="Test Story",
                status="in_progress",
                current_phase="developing",
                created_at=now,
                updated_at=now,
            )
            await insert_story(db, story)

        task = TaskRecord(
            task_id=task_id,
            story_id=story_id,
            phase="developing",
            role="developer",
            cli_tool="claude",
            status=status,  # type: ignore[arg-type]
            started_at=now,
        )
        await insert_task(db, task)
    finally:
        await db.close()
