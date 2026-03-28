"""Orchestrator 核心行为单元测试。"""

from __future__ import annotations

import asyncio
import json
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
        """有 running tasks 时进入崩溃恢复模式并返回 RecoveryResult。"""
        from ato.models.db import get_connection

        await _insert_test_task(initialized_db_path, status="running")

        settings = _make_settings()
        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)
        db = await get_connection(initialized_db_path)
        try:
            result = await orchestrator._detect_recovery_mode(db)
            assert result is not None
            assert result.recovery_mode == "crash"
        finally:
            await db.close()

    async def test_graceful_recovery_paused_tasks(self, initialized_db_path: Path) -> None:
        """有 paused tasks 时进入正常恢复模式并返回 RecoveryResult。"""
        from ato.models.db import get_connection

        await _insert_test_task(initialized_db_path, status="paused")

        settings = _make_settings()
        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)
        db = await get_connection(initialized_db_path)
        try:
            result = await orchestrator._detect_recovery_mode(db)
            assert result is not None
            assert result.recovery_mode == "normal"
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


async def _insert_test_approval(
    db_path: Path,
    *,
    approval_id: str = "aaaa1111-2222-3333-4444-555566667777",
    story_id: str = "test-story-1",
    approval_type: str = "session_timeout",
    status: str = "approved",
    decision: str = "restart",
) -> None:
    """插入一条已决策的 approval（先确保对应 story 存在）。"""
    from ato.models.db import get_connection, insert_approval, insert_story
    from ato.models.schemas import ApprovalRecord, StoryRecord

    now = datetime.now(tz=UTC)
    db = await get_connection(db_path)
    try:
        existing = await db.execute("SELECT 1 FROM stories WHERE story_id = ?", (story_id,))
        if await existing.fetchone() is None:
            await insert_story(
                db,
                StoryRecord(
                    story_id=story_id,
                    title="Test Story",
                    status="in_progress",
                    current_phase="developing",
                    created_at=now,
                    updated_at=now,
                ),
            )

        await insert_approval(
            db,
            ApprovalRecord(
                approval_id=approval_id,
                story_id=story_id,
                approval_type=approval_type,
                status=status,  # type: ignore[arg-type]
                decision=decision,
                decided_at=now,
                created_at=now,
                payload='{"task_id": "t1"}',
            ),
        )
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# Orchestrator 审批消费测试 (Story 4.1)
# ---------------------------------------------------------------------------


class TestProcessApprovalDecisions:
    async def test_session_timeout_restart(self, initialized_db_path: Path) -> None:
        """超时审批消费 — restart 决策应重置 task 为 pending。"""
        # 插入关联 task（failed 状态，对应 payload 中的 task_id）
        await _insert_test_task(
            initialized_db_path, status="failed", task_id="t1", story_id="test-story-1"
        )
        await _insert_test_approval(
            initialized_db_path,
            approval_type="session_timeout",
            decision="restart",
        )

        settings = _make_settings()
        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)
        await orchestrator._process_approval_decisions()

        from ato.models.db import get_connection, get_decided_unconsumed_approvals

        db = await get_connection(initialized_db_path)
        try:
            # 验证 consumed
            remaining = await get_decided_unconsumed_approvals(db)
            assert len(remaining) == 0

            # 验证 task 被重置为 pending
            cursor = await db.execute("SELECT status FROM tasks WHERE task_id = ?", ("t1",))
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "pending"
        finally:
            await db.close()

    async def test_crash_recovery_resume(self, initialized_db_path: Path) -> None:
        """crash_recovery + resume 应标记 task 为 pending + resume_requested。"""
        await _insert_test_task(
            initialized_db_path, status="failed", task_id="t1", story_id="test-story-1"
        )
        await _insert_test_approval(
            initialized_db_path,
            approval_id="bbbb2222-0000-0000-0000-000000000000",
            approval_type="crash_recovery",
            decision="resume",
        )

        settings = _make_settings()
        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)
        await orchestrator._process_approval_decisions()

        from ato.models.db import get_connection, get_decided_unconsumed_approvals

        db = await get_connection(initialized_db_path)
        try:
            remaining = await get_decided_unconsumed_approvals(db)
            assert len(remaining) == 0

            # 验证 task 为 pending + resume 标记
            cursor = await db.execute(
                "SELECT status, expected_artifact FROM tasks WHERE task_id = ?", ("t1",)
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "pending"
            assert row[1] == "resume_requested"
        finally:
            await db.close()

    async def test_blocking_abnormal_consumed(self, initialized_db_path: Path) -> None:
        """blocking 审批消费。"""
        await _insert_test_approval(
            initialized_db_path,
            approval_id="cccc3333-0000-0000-0000-000000000000",
            approval_type="blocking_abnormal",
            decision="confirm_fix",
        )

        settings = _make_settings()
        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)
        # 需要 TQ 才能 submit transition
        orchestrator._tq = AsyncMock()
        await orchestrator._process_approval_decisions()

        from ato.models.db import get_connection, get_decided_unconsumed_approvals

        db = await get_connection(initialized_db_path)
        try:
            remaining = await get_decided_unconsumed_approvals(db)
            assert len(remaining) == 0
        finally:
            await db.close()

        # 验证 TQ 收到了 transition event
        orchestrator._tq.submit.assert_awaited_once()

    async def test_approval_non_blocking_other_stories(self, initialized_db_path: Path) -> None:
        """审批等待不阻塞其他 story。

        验证：pending approval 属于 story-A，同时 story-B 的 task 不受影响。
        """
        # 插入两个 story
        from ato.models.db import get_connection, insert_story
        from ato.models.schemas import StoryRecord

        now = datetime.now(tz=UTC)
        db = await get_connection(initialized_db_path)
        try:
            for sid in ("story-a", "story-b"):
                existing = await db.execute("SELECT 1 FROM stories WHERE story_id = ?", (sid,))
                if await existing.fetchone() is None:
                    await insert_story(
                        db,
                        StoryRecord(
                            story_id=sid,
                            title=f"Test {sid}",
                            status="in_progress",
                            current_phase="developing",
                            created_at=now,
                            updated_at=now,
                        ),
                    )
        finally:
            await db.close()

        # story-a 有 pending approval
        from ato.models.db import get_connection as gc2
        from ato.models.db import insert_approval
        from ato.models.schemas import ApprovalRecord

        db2 = await gc2(initialized_db_path)
        try:
            await insert_approval(
                db2,
                ApprovalRecord(
                    approval_id="dddd4444-0000-0000-0000-000000000001",
                    story_id="story-a",
                    approval_type="session_timeout",
                    status="pending",
                    created_at=now,
                ),
            )
        finally:
            await db2.close()

        # _process_approval_decisions 不会阻塞 — story-b 不受影响
        settings = _make_settings()
        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)
        await orchestrator._process_approval_decisions()  # 无异常 = 非阻塞

    async def test_needs_human_review_retry(self, initialized_db_path: Path) -> None:
        """needs_human_review + retry 应重调度 task。"""
        await _insert_test_task(
            initialized_db_path, status="failed", task_id="t1", story_id="test-story-1"
        )
        await _insert_test_approval(
            initialized_db_path,
            approval_id="ffff6666-0000-0000-0000-000000000000",
            approval_type="needs_human_review",
            decision="retry",
        )

        settings = _make_settings()
        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)
        await orchestrator._process_approval_decisions()

        from ato.models.db import get_connection, get_decided_unconsumed_approvals

        db = await get_connection(initialized_db_path)
        try:
            remaining = await get_decided_unconsumed_approvals(db)
            assert len(remaining) == 0

            cursor = await db.execute("SELECT status FROM tasks WHERE task_id = ?", ("t1",))
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "pending"
        finally:
            await db.close()

    async def test_needs_human_review_escalate(self, initialized_db_path: Path) -> None:
        """needs_human_review + escalate 应触发 escalate transition。"""
        await _insert_test_approval(
            initialized_db_path,
            approval_id="gggg7777-0000-0000-0000-000000000000",
            approval_type="needs_human_review",
            decision="escalate",
        )

        settings = _make_settings()
        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)
        orchestrator._tq = AsyncMock()
        await orchestrator._process_approval_decisions()

        from ato.models.db import get_connection, get_decided_unconsumed_approvals

        db = await get_connection(initialized_db_path)
        try:
            remaining = await get_decided_unconsumed_approvals(db)
            assert len(remaining) == 0
        finally:
            await db.close()

        orchestrator._tq.submit.assert_awaited_once()

    async def test_unrecognized_decision_not_consumed(self, initialized_db_path: Path) -> None:
        """无法识别的 decision 不会被消费（留待人工检查）。"""
        await _insert_test_approval(
            initialized_db_path,
            approval_id="eeee5555-0000-0000-0000-000000000000",
            approval_type="session_timeout",
            decision="foo_invalid",
        )

        settings = _make_settings()
        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)
        await orchestrator._process_approval_decisions()

        from ato.models.db import get_connection, get_decided_unconsumed_approvals

        db = await get_connection(initialized_db_path)
        try:
            remaining = await get_decided_unconsumed_approvals(db)
            # 无效 decision 不应被消费
            assert len(remaining) == 1
            assert remaining[0].decision == "foo_invalid"
        finally:
            await db.close()

    async def test_restart_then_poll_dispatches_interactive_task(
        self, initialized_db_path: Path
    ) -> None:
        """端到端：approval restart → task pending → dispatch_interactive 被调用。

        验证调度链完整性：approval 消费后 task 变为 pending(restart_requested)，
        _dispatch_pending_tasks() 识别为 interactive phase 并调用
        _dispatch_interactive_restart()。
        """
        from ato.config import PhaseDefinition

        # 插入 failed task (developing = interactive phase) + decided restart approval
        await _insert_test_task(
            initialized_db_path, status="failed", task_id="t1", story_id="test-story-1"
        )
        await _insert_test_approval(
            initialized_db_path,
            approval_type="session_timeout",
            decision="restart",
        )

        settings = _make_settings()
        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)

        # Step 1: 消费 approval → task 变为 pending + restart_requested
        await orchestrator._process_approval_decisions()

        from ato.models.db import get_connection

        db = await get_connection(initialized_db_path)
        try:
            cursor = await db.execute(
                "SELECT status, expected_artifact FROM tasks WHERE task_id = ?", ("t1",)
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "pending"
            assert row[1] == "restart_requested"
        finally:
            await db.close()

        # Step 2: dispatch pending → _dispatch_interactive_restart 被调用
        # mock build_phase_definitions 使 developing 被识别为 interactive
        mock_phase_defs = [
            PhaseDefinition(
                name="developing",
                role="developer",
                cli_tool="claude",
                model="opus",
                sandbox=None,
                phase_type="interactive_session",
                next_on_success="reviewing",
                next_on_failure=None,
                timeout_seconds=7200,
            ),
        ]
        with (
            patch("ato.config.build_phase_definitions", return_value=mock_phase_defs),
            patch.object(
                orchestrator,
                "_dispatch_interactive_restart",
                new_callable=AsyncMock,
            ) as mock_dispatch,
        ):
            await orchestrator._dispatch_pending_tasks()
            # 等待 background task
            await asyncio.sleep(0.05)

            mock_dispatch.assert_called_once()
            call_args = mock_dispatch.call_args
            dispatched_task = call_args[0][0]  # positional arg
            assert dispatched_task.task_id == "t1"
            assert call_args[1]["resume"] is False  # keyword arg

    async def test_restart_convergent_loop_task_uses_convergent_dispatch(
        self, initialized_db_path: Path
    ) -> None:
        """convergent_loop phase 的 retry 走 _dispatch_convergent_restart()，
        确保经过 BMAD parse/findings/convergence 管道，不会直接发 review_pass。
        """
        from ato.config import PhaseDefinition

        await _insert_test_task(
            initialized_db_path,
            status="failed",
            task_id="t1",
            story_id="test-story-1",
        )
        from ato.models.db import get_connection, update_task_status

        db = await get_connection(initialized_db_path)
        try:
            await update_task_status(
                db,
                "t1",
                "pending",
                expected_artifact="restart_requested",
            )
            await db.execute("UPDATE tasks SET phase = 'reviewing' WHERE task_id = 't1'")
            await db.commit()
        finally:
            await db.close()

        settings = _make_settings()
        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)

        mock_phase_defs = [
            PhaseDefinition(
                name="reviewing",
                role="reviewer",
                cli_tool="codex",
                model="opus",
                sandbox="read-only",
                phase_type="convergent_loop",
                next_on_success="fixing",
                next_on_failure=None,
                timeout_seconds=1800,
            ),
        ]
        with (
            patch("ato.config.build_phase_definitions", return_value=mock_phase_defs),
            patch.object(
                orchestrator,
                "_dispatch_convergent_restart",
                new_callable=AsyncMock,
            ) as mock_convergent,
            patch.object(
                orchestrator,
                "_dispatch_batch_restart",
                new_callable=AsyncMock,
            ) as mock_batch,
        ):
            await orchestrator._dispatch_pending_tasks()
            await asyncio.sleep(0.05)

            # convergent_loop phase 走 convergent 路径，NOT batch
            mock_convergent.assert_called_once()
            mock_batch.assert_not_called()
            dispatched_task = mock_convergent.call_args[0][0]
            assert dispatched_task.task_id == "t1"

    async def test_pending_restart_task_not_dispatched_twice_while_in_flight(
        self, initialized_db_path: Path
    ) -> None:
        """同一个 restart_requested task 在首次后台调度未结束前不能重复调度。"""
        from ato.config import PhaseDefinition
        from ato.models.db import get_connection, update_task_status

        await _insert_test_task(
            initialized_db_path,
            status="failed",
            task_id="t1",
            story_id="test-story-1",
        )

        db = await get_connection(initialized_db_path)
        try:
            await update_task_status(
                db,
                "t1",
                "pending",
                expected_artifact="restart_requested",
            )
        finally:
            await db.close()

        settings = _make_settings()
        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)

        mock_phase_defs = [
            PhaseDefinition(
                name="developing",
                role="developer",
                cli_tool="claude",
                model="opus",
                sandbox=None,
                phase_type="interactive_session",
                next_on_success="reviewing",
                next_on_failure=None,
                timeout_seconds=7200,
            ),
        ]

        release_dispatch = asyncio.Event()

        async def blocking_dispatch(task: object, *, resume: bool = False) -> None:
            assert resume is False
            assert getattr(task, "task_id", None) == "t1"
            await release_dispatch.wait()

        with (
            patch("ato.config.build_phase_definitions", return_value=mock_phase_defs),
            patch.object(
                orchestrator,
                "_dispatch_interactive_restart",
                new_callable=AsyncMock,
            ) as mock_dispatch,
        ):
            mock_dispatch.side_effect = blocking_dispatch

            await orchestrator._dispatch_pending_tasks()
            await asyncio.sleep(0.05)

            await orchestrator._dispatch_pending_tasks()
            await asyncio.sleep(0.05)

            assert mock_dispatch.await_count == 1

            release_dispatch.set()
            await asyncio.gather(*orchestrator._background_tasks, return_exceptions=True)

    async def test_interactive_restart_deletes_sidecar(self, initialized_db_path: Path) -> None:
        """restart 模式下 _dispatch_interactive_restart 删除 sidecar，
        防止 dispatch_interactive fallback 读取旧 session_id。
        """
        # 创建 sidecar 文件
        ato_dir = initialized_db_path.parent
        sessions_dir = ato_dir / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        sidecar = sessions_dir / "test-story-1.json"
        sidecar.write_text('{"session_id": "old-session-id", "pid": 99999}')
        assert sidecar.exists()

        await _insert_test_task(
            initialized_db_path,
            status="pending",
            task_id="t1",
            story_id="test-story-1",
        )

        settings = _make_settings()
        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)

        from ato.models.schemas import TaskRecord

        task = TaskRecord(
            task_id="t1",
            story_id="test-story-1",
            phase="developing",
            role="developer",
            cli_tool="claude",
            status="pending",
        )

        # mock SubprocessManager.dispatch_interactive 以避免真正启动终端
        with (
            patch("ato.subprocess_mgr.SubprocessManager") as mock_mgr_cls,
            patch("ato.adapters.claude_cli.ClaudeAdapter"),
            patch.object(
                orchestrator,
                "_get_base_commit",
                new_callable=AsyncMock,
                return_value="abc123",
            ),
        ):
            mock_mgr = AsyncMock()
            mock_mgr.dispatch_interactive = AsyncMock(return_value="new-task-id")
            mock_mgr_cls.return_value = mock_mgr

            # 设置 story 的 worktree_path
            from ato.models.db import get_connection

            db = await get_connection(initialized_db_path)
            try:
                await db.execute(
                    "UPDATE stories SET worktree_path = ? WHERE story_id = ?",
                    ("/tmp/test-worktree", "test-story-1"),
                )
                await db.commit()
            finally:
                await db.close()

            await orchestrator._dispatch_interactive_restart(task, resume=False)

            # sidecar 应已被删除
            assert not sidecar.exists()
            # dispatch_interactive 应被调用
            mock_mgr.dispatch_interactive.assert_awaited_once()

    async def test_needs_human_review_retry_no_task_id_not_consumed(
        self, initialized_db_path: Path
    ) -> None:
        """needs_human_review + retry，payload 无 task_id 时 approval 不被消费。

        真实 parse-failure 路径：record_parse_failure 未传 task_id 时，
        retry 无法定位目标 task，approval 应保留不消费。
        """
        from ato.models.db import get_connection, insert_approval, insert_story
        from ato.models.schemas import ApprovalRecord, StoryRecord

        now = datetime.now(tz=UTC)
        db = await get_connection(initialized_db_path)
        try:
            existing = await db.execute(
                "SELECT 1 FROM stories WHERE story_id = ?", ("test-story-1",)
            )
            if await existing.fetchone() is None:
                await insert_story(
                    db,
                    StoryRecord(
                        story_id="test-story-1",
                        title="Test Story",
                        status="in_progress",
                        current_phase="developing",
                        created_at=now,
                        updated_at=now,
                    ),
                )

            # 真实 parse-failure payload：无 task_id
            await insert_approval(
                db,
                ApprovalRecord(
                    approval_id="hhhh8888-0000-0000-0000-000000000000",
                    story_id="test-story-1",
                    approval_type="needs_human_review",
                    status="approved",
                    decision="retry",
                    decided_at=now,
                    created_at=now,
                    payload='{"reason": "bmad_parse_failed", "skill_type": "code_review", '
                    '"parser_mode": "failed", "error": "No structure found", '
                    '"raw_output_preview": "raw text", '
                    '"options": ["retry", "skip", "escalate"]}',
                ),
            )
        finally:
            await db.close()

        settings = _make_settings()
        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)
        await orchestrator._process_approval_decisions()

        from ato.models.db import get_connection as gc2
        from ato.models.db import get_decided_unconsumed_approvals

        db2 = await gc2(initialized_db_path)
        try:
            remaining = await get_decided_unconsumed_approvals(db2)
            # 无 task_id → reschedule 失败 → 不消费
            assert len(remaining) == 1
            assert remaining[0].approval_id == "hhhh8888-0000-0000-0000-000000000000"
        finally:
            await db2.close()

    async def test_needs_human_review_retry_with_task_id_consumed(
        self, initialized_db_path: Path
    ) -> None:
        """needs_human_review + retry，payload 含 task_id 时 approval 被消费且 task 重调度。"""
        # 插入 failed task
        await _insert_test_task(
            initialized_db_path, status="failed", task_id="t1", story_id="test-story-1"
        )

        from ato.models.db import get_connection, insert_approval
        from ato.models.schemas import ApprovalRecord

        now = datetime.now(tz=UTC)
        db = await get_connection(initialized_db_path)
        try:
            await insert_approval(
                db,
                ApprovalRecord(
                    approval_id="iiii9999-0000-0000-0000-000000000000",
                    story_id="test-story-1",
                    approval_type="needs_human_review",
                    status="approved",
                    decision="retry",
                    decided_at=now,
                    created_at=now,
                    payload='{"reason": "bmad_parse_failed", "task_id": "t1", '
                    '"options": ["retry", "skip", "escalate"]}',
                ),
            )
        finally:
            await db.close()

        settings = _make_settings()
        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)
        await orchestrator._process_approval_decisions()

        from ato.models.db import get_connection as gc3
        from ato.models.db import get_decided_unconsumed_approvals

        db3 = await gc3(initialized_db_path)
        try:
            # approval 应被消费
            remaining = await get_decided_unconsumed_approvals(db3)
            assert len(remaining) == 0

            # task 应被重置为 pending
            cursor = await db3.execute("SELECT status FROM tasks WHERE task_id = ?", ("t1",))
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "pending"
        finally:
            await db3.close()


# ---------------------------------------------------------------------------
# Convergent restart 异常路径：不得重复创建 approval
# ---------------------------------------------------------------------------


class TestConvergentRestartSingleApproval:
    """_dispatch_convergent_restart 遇到内部 recovery 异常时只应生成一条 approval。

    回归测试：RecoveryEngine._dispatch_convergent_loop() except 分支已调用
    _mark_dispatch_failed，外层不应再次调用。
    """

    async def test_inner_exception_creates_single_approval(self, initialized_db_path: Path) -> None:
        from ato.config import PhaseDefinition
        from ato.models.db import get_connection, get_pending_approvals, update_task_status

        # 插入一条 reviewing phase 的 pending task
        await _insert_test_task(
            initialized_db_path,
            status="failed",
            task_id="t-conv-1",
            story_id="test-story-conv",
        )
        db = await get_connection(initialized_db_path)
        try:
            await update_task_status(
                db,
                "t-conv-1",
                "pending",
                expected_artifact="restart_requested",
            )
            await db.execute("UPDATE tasks SET phase = 'reviewing' WHERE task_id = 't-conv-1'")
            await db.commit()
        finally:
            await db.close()

        settings = _make_settings()
        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)

        mock_phase_defs = [
            PhaseDefinition(
                name="reviewing",
                role="reviewer",
                cli_tool="codex",
                model="opus",
                sandbox="read-only",
                phase_type="convergent_loop",
                next_on_success="fixing",
                next_on_failure=None,
                timeout_seconds=1800,
            ),
        ]

        # RecoveryEngine._dispatch_convergent_loop 内部抛异常
        # 它的 except 分支会调用 _mark_dispatch_failed → 生成 approval #1
        # _dispatch_convergent_restart 收到 False 后不应再次 _mark_dispatch_failed
        async def mock_dispatch_convergent_loop(task: object) -> bool:
            """模拟内部异常已被处理并升级。"""
            from ato.recovery import RecoveryEngine

            # 直接调用 recovery engine 的 _mark_dispatch_failed
            engine = RecoveryEngine(
                db_path=initialized_db_path,
                subprocess_mgr=None,
                transition_queue=MagicMock(),
                nudge=MagicMock(),
            )
            await engine._mark_dispatch_failed(task)  # type: ignore[arg-type]
            return False

        with (
            patch("ato.config.build_phase_definitions", return_value=mock_phase_defs),
            patch(
                "ato.recovery.RecoveryEngine._dispatch_convergent_loop",
                side_effect=mock_dispatch_convergent_loop,
            ),
        ):
            await orchestrator._dispatch_pending_tasks()
            # 等后台 task 完成
            await asyncio.sleep(0.1)
            for bg in orchestrator._background_tasks:
                if not bg.done():
                    await asyncio.wait_for(bg, timeout=2.0)

        # 验证：只有一条 crash_recovery pending approval
        db2 = await get_connection(initialized_db_path)
        try:
            approvals = await get_pending_approvals(db2)
            crash_approvals = [a for a in approvals if a.approval_type == "crash_recovery"]
            assert len(crash_approvals) == 1, (
                f"Expected exactly 1 crash_recovery approval, got {len(crash_approvals)}"
            )
        finally:
            await db2.close()


# ---------------------------------------------------------------------------
# Story 4.2 — Merge Queue 集成测试
# ---------------------------------------------------------------------------


class TestMergeAuthorizationCreation:
    """merging 阶段 merge_authorization approval 创建测试。"""

    async def test_merging_phase_creates_merge_authorization_once(
        self, initialized_db_path: Path
    ) -> None:
        """进入 merging 时创建 approval，且幂等不重复创建。"""
        from ato.models.db import (
            get_connection,
            get_pending_approvals,
            insert_story,
        )
        from ato.models.schemas import StoryRecord

        now = datetime.now(tz=UTC)
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(
                db,
                StoryRecord(
                    story_id="s1",
                    title="Test story",
                    status="in_progress",
                    current_phase="merging",
                    created_at=now,
                    updated_at=now,
                ),
            )
        finally:
            await db.close()

        settings = _make_settings()
        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)
        orchestrator._merge_queue = MagicMock()
        orchestrator._nudge = MagicMock()

        # First call should create the approval
        await orchestrator._create_merge_authorizations()

        db = await get_connection(initialized_db_path)
        try:
            pending = await get_pending_approvals(db)
            merge_auths = [a for a in pending if a.approval_type == "merge_authorization"]
            assert len(merge_auths) == 1
        finally:
            await db.close()

        # Second call should NOT create a duplicate
        await orchestrator._create_merge_authorizations()

        db = await get_connection(initialized_db_path)
        try:
            pending = await get_pending_approvals(db)
            merge_auths = [a for a in pending if a.approval_type == "merge_authorization"]
            assert len(merge_auths) == 1  # still just 1
        finally:
            await db.close()

    async def test_merge_authorization_handles_naive_started_at(
        self, initialized_db_path: Path
    ) -> None:
        """naive started_at 不应导致 elapsed_seconds 计算崩溃。"""
        from ato.models.db import (
            get_connection,
            get_pending_approvals,
            insert_story,
        )
        from ato.models.schemas import StoryRecord

        now = datetime.now(tz=UTC)
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(
                db,
                StoryRecord(
                    story_id="s-naive",
                    title="Naive Task Story",
                    status="in_progress",
                    current_phase="merging",
                    created_at=now,
                    updated_at=now,
                ),
            )
            await db.execute(
                "INSERT INTO tasks "
                "(task_id, story_id, phase, role, cli_tool, status, started_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "t-naive",
                    "s-naive",
                    "developing",
                    "developer",
                    "claude",
                    "completed",
                    "2020-01-01T00:00:00",
                ),
            )
            await db.commit()
        finally:
            await db.close()

        settings = _make_settings()
        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)
        orchestrator._merge_queue = MagicMock()
        orchestrator._nudge = MagicMock()

        await orchestrator._create_merge_authorizations()

        db = await get_connection(initialized_db_path)
        try:
            pending = await get_pending_approvals(db)
            merge_auths = [a for a in pending if a.approval_type == "merge_authorization"]
            assert len(merge_auths) == 1
            payload = json.loads(merge_auths[0].payload or "{}")
            assert int(payload["elapsed_seconds"]) > 0
        finally:
            await db.close()

    async def test_frozen_queue_only_authorizes_recovery_story(
        self, initialized_db_path: Path
    ) -> None:
        """冻结期间只允许触发冻结的 recovery story 重新拿 merge_authorization。"""
        from ato.models.db import (
            get_connection,
            get_pending_approvals,
            insert_story,
            set_merge_queue_frozen,
        )
        from ato.models.schemas import StoryRecord

        now = datetime.now(tz=UTC)
        db = await get_connection(initialized_db_path)
        try:
            for story_id in ("s1", "s2"):
                await insert_story(
                    db,
                    StoryRecord(
                        story_id=story_id,
                        title=f"Test story {story_id}",
                        status="in_progress",
                        current_phase="merging",
                        created_at=now,
                        updated_at=now,
                    ),
                )
            await set_merge_queue_frozen(
                db,
                frozen=True,
                reason="regression failed for s1",
            )
        finally:
            await db.close()

        settings = _make_settings()
        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)
        orchestrator._merge_queue = MagicMock()
        orchestrator._nudge = MagicMock()

        with patch("ato.core.logger") as mock_logger:
            await orchestrator._create_merge_authorizations()

        db = await get_connection(initialized_db_path)
        try:
            pending = await get_pending_approvals(db)
            merge_auths = [a for a in pending if a.approval_type == "merge_authorization"]
            assert len(merge_auths) == 1
            assert merge_auths[0].story_id == "s1"
        finally:
            await db.close()

        mock_logger.info.assert_any_call(
            "merge_authorization_skipped_frozen",
            story_id="s2",
            recovery_story_id="s1",
        )


class TestMergeAuthorizationConsumption:
    """merge_authorization 消费测试。"""

    async def test_approve_enqueues(self, initialized_db_path: Path) -> None:
        """approve 决策调用 merge_queue.enqueue()。"""
        from ato.models.schemas import ApprovalRecord

        now = datetime.now(tz=UTC)
        settings = _make_settings()
        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)
        orchestrator._merge_queue = AsyncMock()
        orchestrator._tq = AsyncMock()

        approval = ApprovalRecord(
            approval_id="appr-1",
            story_id="s1",
            approval_type="merge_authorization",
            status="approved",
            decision="approve",
            decided_at=now,
            created_at=now,
        )

        result = await orchestrator._handle_approval_decision(approval)
        assert result is True
        orchestrator._merge_queue.enqueue.assert_awaited_once_with("s1", "appr-1", now)

    async def test_reject_escalates(self, initialized_db_path: Path) -> None:
        """reject 决策提交 escalate transition。"""
        from ato.models.schemas import ApprovalRecord

        now = datetime.now(tz=UTC)
        settings = _make_settings()
        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)
        orchestrator._merge_queue = AsyncMock()
        orchestrator._tq = AsyncMock()

        approval = ApprovalRecord(
            approval_id="appr-1",
            story_id="s1",
            approval_type="merge_authorization",
            status="rejected",
            decision="reject",
            decided_at=now,
            created_at=now,
        )

        result = await orchestrator._handle_approval_decision(approval)
        assert result is True
        orchestrator._tq.submit.assert_awaited_once()
        event = orchestrator._tq.submit.call_args[0][0]
        assert event.event_name == "escalate"

    async def test_regression_failure_fix_forward(self, initialized_db_path: Path) -> None:
        """fix_forward 提交 regression_fail transition，清理旧 row 且 queue 保持冻结。"""
        from ato.models.db import (
            enqueue_merge,
            get_connection,
            get_merge_queue_entry,
            insert_story,
            set_merge_queue_frozen,
        )
        from ato.models.schemas import ApprovalRecord, StoryRecord

        now = datetime.now(tz=UTC)
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(
                db,
                StoryRecord(
                    story_id="s1",
                    title="Test story",
                    status="in_progress",
                    current_phase="regression",
                    created_at=now,
                    updated_at=now,
                ),
            )
            await enqueue_merge(db, "s1", "merge-appr-1", now, now)
            await db.execute(
                "UPDATE merge_queue SET status = 'regression_pending' WHERE story_id = ?",
                ("s1",),
            )
            await db.commit()
            await set_merge_queue_frozen(
                db,
                frozen=True,
                reason="crash during regression for s1",
            )
        finally:
            await db.close()

        settings = _make_settings()
        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)
        orchestrator._merge_queue = AsyncMock()
        orchestrator._tq = AsyncMock()

        approval = ApprovalRecord(
            approval_id="appr-1",
            story_id="s1",
            approval_type="regression_failure",
            status="approved",
            decision="fix_forward",
            decided_at=now,
            created_at=now,
        )

        result = await orchestrator._handle_approval_decision(approval)
        assert result is True
        orchestrator._tq.submit.assert_awaited_once()
        event = orchestrator._tq.submit.call_args[0][0]
        assert event.event_name == "regression_fail"

        db = await get_connection(initialized_db_path)
        try:
            entry = await get_merge_queue_entry(db, "s1")
            cursor = await db.execute(
                "SELECT frozen FROM merge_queue_state WHERE id = 1",
            )
            frozen_row = await cursor.fetchone()
            assert entry is None
            assert frozen_row is not None
            assert frozen_row[0] == 1
        finally:
            await db.close()

        orchestrator._merge_queue.unfreeze.assert_not_awaited()

    async def test_regression_failure_revert_failure_keeps_queue_frozen(
        self, initialized_db_path: Path
    ) -> None:
        """revert 失败时不得解冻 queue，也不得清理 worktree。"""
        from ato.models.db import (
            enqueue_merge,
            get_connection,
            insert_story,
            set_merge_queue_frozen,
        )
        from ato.models.schemas import ApprovalRecord, StoryRecord

        now = datetime.now(tz=UTC)
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(
                db,
                StoryRecord(
                    story_id="s1",
                    title="Test story",
                    status="in_progress",
                    current_phase="regression",
                    created_at=now,
                    updated_at=now,
                ),
            )
            await enqueue_merge(db, "s1", "merge-appr-1", now, now)
            await db.execute(
                "UPDATE merge_queue SET status = 'failed', pre_merge_head = ? WHERE story_id = ?",
                ("abc123", "s1"),
            )
            await db.commit()
            await set_merge_queue_frozen(
                db,
                frozen=True,
                reason="regression failed for s1",
            )
        finally:
            await db.close()

        settings = _make_settings()
        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)
        orchestrator._merge_queue = AsyncMock()
        orchestrator._worktree_mgr = AsyncMock()
        orchestrator._worktree_mgr.revert_merge_range = AsyncMock(
            return_value=(False, "revert conflict"),
        )

        approval = ApprovalRecord(
            approval_id="appr-1",
            story_id="s1",
            approval_type="regression_failure",
            status="approved",
            decision="revert",
            decided_at=now,
            created_at=now,
        )

        result = await orchestrator._handle_approval_decision(approval)

        assert result is False
        orchestrator._merge_queue.unfreeze.assert_not_awaited()
        orchestrator._worktree_mgr.cleanup.assert_not_awaited()

        db = await get_connection(initialized_db_path)
        try:
            cursor = await db.execute(
                "SELECT frozen FROM merge_queue_state WHERE id = 1",
            )
            frozen_row = await cursor.fetchone()
            assert frozen_row is not None
            assert frozen_row[0] == 1
        finally:
            await db.close()


# ---------------------------------------------------------------------------
# Story 4.5: regression_failure 决策分支端到端覆盖
# ---------------------------------------------------------------------------


class TestRegressionFailureDecisions:
    """regression_failure approval 三种决策的端到端验证。"""

    async def _setup_regression_scenario(self, initialized_db_path: Path) -> None:
        """创建 story + merge queue entry + 冻结 queue。"""
        from ato.models.db import (
            enqueue_merge,
            get_connection,
            insert_story,
            set_merge_queue_frozen,
            set_pre_merge_head,
        )
        from ato.models.schemas import StoryRecord

        now = datetime.now(tz=UTC)
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(
                db,
                StoryRecord(
                    story_id="s1",
                    title="Test story",
                    status="in_progress",
                    current_phase="regression",
                    created_at=now,
                    updated_at=now,
                ),
            )
            await enqueue_merge(db, "s1", "merge-appr-1", now, now)
            # 模拟已 merge 且 regression 失败
            await db.execute(
                "UPDATE merge_queue SET status = 'failed' WHERE story_id = ?",
                ("s1",),
            )
            await set_pre_merge_head(db, "s1", "abc123def")
            await db.commit()
            await set_merge_queue_frozen(
                db,
                frozen=True,
                reason="regression failed for s1",
            )
        finally:
            await db.close()

    async def test_revert_success_unfreezes_and_cleans_worktree(
        self, initialized_db_path: Path
    ) -> None:
        """revert 成功 → unfreeze + cleanup worktree（AC4 revert）。"""
        from ato.models.schemas import ApprovalRecord

        await self._setup_regression_scenario(initialized_db_path)

        now = datetime.now(tz=UTC)
        settings = _make_settings()
        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)
        orchestrator._merge_queue = AsyncMock()
        orchestrator._worktree_mgr = AsyncMock()
        orchestrator._worktree_mgr.revert_merge_range = AsyncMock(return_value=(True, ""))

        approval = ApprovalRecord(
            approval_id="appr-1",
            story_id="s1",
            approval_type="regression_failure",
            status="approved",
            decision="revert",
            decided_at=now,
            created_at=now,
        )

        result = await orchestrator._handle_approval_decision(approval)

        assert result is True
        # revert 成功后才 unfreeze
        orchestrator._merge_queue.unfreeze.assert_awaited_once()
        assert "revert completed" in orchestrator._merge_queue.unfreeze.call_args[0][0]
        # cleanup worktree
        orchestrator._worktree_mgr.cleanup.assert_awaited_once_with("s1")

    async def test_fix_forward_submits_regression_fail_and_keeps_frozen(
        self, initialized_db_path: Path
    ) -> None:
        """fix_forward → regression_fail event + queue 保持冻结 + worktree 保留（AC4）。"""
        from ato.models.db import get_connection, get_merge_queue_entry
        from ato.models.schemas import ApprovalRecord

        await self._setup_regression_scenario(initialized_db_path)

        now = datetime.now(tz=UTC)
        settings = _make_settings()
        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)
        orchestrator._merge_queue = AsyncMock()
        orchestrator._tq = AsyncMock()
        orchestrator._worktree_mgr = AsyncMock()

        approval = ApprovalRecord(
            approval_id="appr-1",
            story_id="s1",
            approval_type="regression_failure",
            status="approved",
            decision="fix_forward",
            decided_at=now,
            created_at=now,
        )

        result = await orchestrator._handle_approval_decision(approval)
        assert result is True

        # regression_fail event submitted
        orchestrator._tq.submit.assert_awaited_once()
        event = orchestrator._tq.submit.call_args[0][0]
        assert event.event_name == "regression_fail"

        # queue 保持冻结
        orchestrator._merge_queue.unfreeze.assert_not_awaited()

        # worktree 保留（cleanup 不被调用）
        orchestrator._worktree_mgr.cleanup.assert_not_awaited()

        # merge_queue entry 被移除
        db = await get_connection(initialized_db_path)
        try:
            entry = await get_merge_queue_entry(db, "s1")
            assert entry is None, "merge_queue entry should be removed"
            cursor = await db.execute(
                "SELECT frozen FROM merge_queue_state WHERE id = 1",
            )
            frozen_row = await cursor.fetchone()
            assert frozen_row is not None
            assert frozen_row[0] == 1, "Queue must stay frozen"
        finally:
            await db.close()

    async def test_pause_keeps_queue_frozen_without_fake_unblock(
        self, initialized_db_path: Path
    ) -> None:
        """pause → queue 保持冻结，不伪造 unblock 路径（AC4）。"""
        from ato.models.db import get_connection
        from ato.models.schemas import ApprovalRecord

        await self._setup_regression_scenario(initialized_db_path)

        now = datetime.now(tz=UTC)
        settings = _make_settings()
        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)
        orchestrator._merge_queue = AsyncMock()
        orchestrator._tq = AsyncMock()
        orchestrator._worktree_mgr = AsyncMock()

        approval = ApprovalRecord(
            approval_id="appr-1",
            story_id="s1",
            approval_type="regression_failure",
            status="approved",
            decision="pause",
            decided_at=now,
            created_at=now,
        )

        result = await orchestrator._handle_approval_decision(approval)
        assert result is True

        # 不 unfreeze
        orchestrator._merge_queue.unfreeze.assert_not_awaited()
        # 不 submit 任何 transition
        orchestrator._tq.submit.assert_not_awaited()
        # 不 cleanup worktree
        orchestrator._worktree_mgr.cleanup.assert_not_awaited()

        # queue 仍然冻结
        db = await get_connection(initialized_db_path)
        try:
            cursor = await db.execute(
                "SELECT frozen FROM merge_queue_state WHERE id = 1",
            )
            frozen_row = await cursor.fetchone()
            assert frozen_row is not None
            assert frozen_row[0] == 1, "Queue must stay frozen on pause"
        finally:
            await db.close()


class TestRebaseConflictDecisions:
    """rebase_conflict approval 决策路由验证（AC5）。"""

    async def _setup_rebase_scenario(self, initialized_db_path: Path) -> None:
        from ato.models.db import enqueue_merge, get_connection, insert_story
        from ato.models.schemas import StoryRecord

        now = datetime.now(tz=UTC)
        db = await get_connection(initialized_db_path)
        try:
            await insert_story(
                db,
                StoryRecord(
                    story_id="s1",
                    title="Test story",
                    status="in_progress",
                    current_phase="merging",
                    created_at=now,
                    updated_at=now,
                ),
            )
            await enqueue_merge(db, "s1", "a1", now, now)
        finally:
            await db.close()

    async def test_rebase_manual_resolve_removes_entry_and_releases_lock(
        self, initialized_db_path: Path
    ) -> None:
        """manual_resolve: 移除 merge queue entry + 释放锁，保留 worktree。"""
        from ato.models.schemas import ApprovalRecord

        await self._setup_rebase_scenario(initialized_db_path)

        now = datetime.now(tz=UTC)
        settings = _make_settings()
        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)
        orchestrator._merge_queue = AsyncMock()
        orchestrator._worktree_mgr = AsyncMock()

        approval = ApprovalRecord(
            approval_id="appr-1",
            story_id="s1",
            approval_type="rebase_conflict",
            status="approved",
            decision="manual_resolve",
            decided_at=now,
            created_at=now,
        )

        result = await orchestrator._handle_approval_decision(approval)
        assert result is True
        # worktree 不清理
        orchestrator._worktree_mgr.cleanup.assert_not_awaited()

    async def test_rebase_abandon_escalates_story(self, initialized_db_path: Path) -> None:
        """abandon: 移除 entry + escalate story。"""
        from ato.models.schemas import ApprovalRecord

        await self._setup_rebase_scenario(initialized_db_path)

        now = datetime.now(tz=UTC)
        settings = _make_settings()
        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)
        orchestrator._merge_queue = AsyncMock()
        orchestrator._tq = AsyncMock()

        approval = ApprovalRecord(
            approval_id="appr-1",
            story_id="s1",
            approval_type="rebase_conflict",
            status="approved",
            decision="abandon",
            decided_at=now,
            created_at=now,
        )

        result = await orchestrator._handle_approval_decision(approval)
        assert result is True
        orchestrator._tq.submit.assert_awaited_once()
        event = orchestrator._tq.submit.call_args[0][0]
        assert event.event_name == "escalate"


# ---------------------------------------------------------------------------
# Story 8.1: _dispatch_batch_restart 透传 phase-derived model/sandbox
# ---------------------------------------------------------------------------


class TestBatchRestartPhaseOptions:
    """验证 _dispatch_batch_restart 将 phase config 的 model/sandbox 传到 adapter。"""

    async def test_batch_restart_passes_phase_model_and_sandbox(
        self, initialized_db_path: Path
    ) -> None:
        """phase config 有 model/sandbox 时，dispatch options 应包含这两个字段。"""
        from ato.config import ATOSettings
        from ato.models.db import get_connection, update_story_worktree_path
        from ato.models.schemas import AdapterResult, TaskRecord

        settings = ATOSettings(
            roles={
                "creator": {"cli": "claude", "model": "opus", "sandbox": None},
            },
            phases=[
                {
                    "name": "creating",
                    "role": "creator",
                    "type": "structured_job",
                    "next_on_success": "done",
                },
            ],
        )

        await _insert_test_task(initialized_db_path, status="pending", task_id="t1", story_id="s1")
        db = await get_connection(initialized_db_path)
        try:
            await db.execute(
                "UPDATE tasks SET phase = 'creating', role = 'creator', "
                "cli_tool = 'claude', expected_artifact = 'restart_requested' "
                "WHERE task_id = 't1'"
            )
            await update_story_worktree_path(db, "s1", "/tmp/wt")
            await db.commit()
        finally:
            await db.close()

        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)
        orchestrator._tq = AsyncMock()

        mock_adapter = AsyncMock()
        mock_adapter.execute = AsyncMock(
            return_value=AdapterResult(
                status="success", exit_code=0, duration_ms=10, text_result="ok"
            )
        )

        task = TaskRecord(
            task_id="t1",
            story_id="s1",
            phase="creating",
            role="creator",
            cli_tool="claude",
            status="pending",
            started_at=datetime.now(tz=UTC),
        )

        with patch("ato.recovery._create_adapter", return_value=mock_adapter):
            await orchestrator._dispatch_batch_restart(task)

        mock_adapter.execute.assert_called_once()
        call_args = mock_adapter.execute.call_args
        options = call_args[0][1]  # 第二个位置参数是 options
        assert options["cwd"] == "/tmp/wt"
        assert options["model"] == "opus"
        # creator 角色 sandbox=None → 不应出现
        assert "sandbox" not in options

    async def test_batch_restart_no_model_no_sandbox_when_omitted(
        self, initialized_db_path: Path
    ) -> None:
        """phase config 无 model/sandbox 时，dispatch options 不包含这两个字段。"""
        from ato.config import ATOSettings
        from ato.models.db import get_connection, update_story_worktree_path
        from ato.models.schemas import AdapterResult, TaskRecord

        settings = ATOSettings(
            roles={
                "creator": {"cli": "claude"},  # 无 model 无 sandbox
            },
            phases=[
                {
                    "name": "creating",
                    "role": "creator",
                    "type": "structured_job",
                    "next_on_success": "done",
                },
            ],
        )

        await _insert_test_task(initialized_db_path, status="pending", task_id="t2", story_id="s2")
        db = await get_connection(initialized_db_path)
        try:
            await db.execute(
                "UPDATE tasks SET phase = 'creating', role = 'creator', "
                "cli_tool = 'claude', expected_artifact = 'restart_requested' "
                "WHERE task_id = 't2'"
            )
            await update_story_worktree_path(db, "s2", "/tmp/wt2")
            await db.commit()
        finally:
            await db.close()

        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)
        orchestrator._tq = AsyncMock()

        mock_adapter = AsyncMock()
        mock_adapter.execute = AsyncMock(
            return_value=AdapterResult(
                status="success", exit_code=0, duration_ms=10, text_result="ok"
            )
        )

        task = TaskRecord(
            task_id="t2",
            story_id="s2",
            phase="creating",
            role="creator",
            cli_tool="claude",
            status="pending",
            started_at=datetime.now(tz=UTC),
        )

        with patch("ato.recovery._create_adapter", return_value=mock_adapter):
            await orchestrator._dispatch_batch_restart(task)

        mock_adapter.execute.assert_called_once()
        call_args = mock_adapter.execute.call_args
        options = call_args[0][1]
        assert options["cwd"] == "/tmp/wt2"
        assert "model" not in options
        assert "sandbox" not in options


# ---------------------------------------------------------------------------
# Pre-worktree structured_job 串行控制测试 (Story 9.1 AC#5)
# ---------------------------------------------------------------------------


class TestPreWorktreeSerialControl:
    """验证 pre-worktree phases（planning/creating/designing）共享 main-path limiter (max=1)。"""

    async def test_limiter_is_shared_singleton(self) -> None:
        """get_main_path_limiter 返回同一个 Semaphore 实例。"""
        from ato.core import get_main_path_limiter, reset_main_path_limiter

        reset_main_path_limiter()
        lim1 = get_main_path_limiter()
        lim2 = get_main_path_limiter()
        assert lim1 is lim2
        reset_main_path_limiter()

    async def test_pre_worktree_phases_include_designing(self) -> None:
        """PRE_WORKTREE_PHASES 包含 planning、creating、designing。"""
        from ato.core import PRE_WORKTREE_PHASES

        assert "planning" in PRE_WORKTREE_PHASES
        assert "creating" in PRE_WORKTREE_PHASES
        assert "designing" in PRE_WORKTREE_PHASES
        # 非 pre-worktree phases 不应在集合中
        assert "developing" not in PRE_WORKTREE_PHASES
        assert "reviewing" not in PRE_WORKTREE_PHASES

    async def test_only_one_pre_worktree_job_at_a_time(self) -> None:
        """同一时刻最多只有 1 个 pre-worktree structured_job 在执行。"""
        from ato.core import get_main_path_limiter, reset_main_path_limiter

        reset_main_path_limiter()
        limiter = get_main_path_limiter()

        max_concurrent = 0
        active = 0

        async def simulate_dispatch(phase: str) -> str:
            nonlocal max_concurrent, active
            async with limiter:
                active += 1
                if active > max_concurrent:
                    max_concurrent = active
                await asyncio.sleep(0.01)  # simulate work
                active -= 1
                return phase

        # 同时启动 3 个 pre-worktree dispatch
        results = await asyncio.gather(
            simulate_dispatch("planning"),
            simulate_dispatch("creating"),
            simulate_dispatch("designing"),
        )

        assert set(results) == {"planning", "creating", "designing"}
        assert max_concurrent == 1, f"Expected max 1 concurrent, got {max_concurrent}"
        reset_main_path_limiter()


# ---------------------------------------------------------------------------
# Design gate 测试 (Story 9.1 AC#6)
# ---------------------------------------------------------------------------


class TestDesignGate:
    """check_design_gate 通过/失败路径 + 持久化证据链强制校验。

    Gate 通过条件（AC#3, AC#4）:
    - story spec 存在
    - prototype.pen 存在且 JSON 合法 + 含必需顶层字段
    - prototype.save-report.json 存在且 json_parse_verified=true + reopen_verified=true
    """

    _VALID_PEN = '{"version": "1.0.0", "children": [], "variables": {}}'
    _VALID_SAVE_REPORT = (
        '{"story_id": "s1", "saved_at": "2026-03-28T00:00:00+00:00",'
        ' "pen_file": "prototype.pen", "snapshot_file": "prototype.snapshot.json",'
        ' "children_count": 0, "json_parse_verified": true,'
        ' "reopen_verified": true, "exported_png_count": 0}'
    )

    @staticmethod
    def _setup_project(tmp_path: Path, story_id: str) -> tuple[Path, Path, Path]:
        """构建 project_root 布局，返回 (project_root, artifacts_dir, ux_dir)。"""
        from ato.design_artifacts import ARTIFACTS_REL

        project_root = tmp_path / "proj"
        artifacts_dir = project_root / ARTIFACTS_REL
        artifacts_dir.mkdir(parents=True)
        ux_dir = artifacts_dir / f"{story_id}-ux"
        return project_root, artifacts_dir, ux_dir

    def _setup_full_prerequisites(
        self, tmp_path: Path, story_id: str = "s1"
    ) -> tuple[Path, Path, Path]:
        """构建完整通过条件：story spec + valid .pen + valid snapshot + valid save-report。"""
        root, arts, ux = self._setup_project(tmp_path, story_id)
        (arts / f"{story_id}.md").touch()
        ux.mkdir()
        (ux / "prototype.pen").write_text(self._VALID_PEN)
        (ux / "prototype.snapshot.json").write_text('{"version":"1.0.0","children":[]}')
        (ux / "prototype.save-report.json").write_text(self._VALID_SAVE_REPORT)
        return root, arts, ux

    # --- Happy path ---

    async def test_gate_pass_minimal(self, tmp_path: Path) -> None:
        """最小通过场景：story spec + valid .pen + valid save-report。"""
        from ato.core import check_design_gate

        root, _arts, _ux = self._setup_full_prerequisites(tmp_path)
        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is True
        assert result.pen_integrity_ok is True
        assert result.save_report_valid is True

    async def test_gate_pass_with_extra_artifacts(self, tmp_path: Path) -> None:
        """完整通过场景 + 额外工件（ux-spec, snapshot, exports）。"""
        from ato.core import check_design_gate

        root, _arts, ux = self._setup_full_prerequisites(tmp_path)
        (ux / "ux-spec.md").touch()
        (ux / "prototype.snapshot.json").touch()
        exports = ux / "exports"
        exports.mkdir()
        (exports / "screen.png").touch()

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is True
        # .pen + save-report + ux-spec + snapshot + 1 png = 5
        assert result.artifact_count == 5

    # --- Story spec 缺失 ---

    async def test_gate_fail_no_story_spec(self, tmp_path: Path) -> None:
        """story spec 不存在时 gate 失败。"""
        from ato.core import check_design_gate

        root, _arts, ux = self._setup_project(tmp_path, "s1")
        ux.mkdir()
        (ux / "prototype.pen").write_text(self._VALID_PEN)
        (ux / "prototype.save-report.json").write_text(self._VALID_SAVE_REPORT)

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is False
        assert result.story_spec_exists is False
        assert "Story spec missing" in result.reason

    # --- .pen 缺失或无效 ---

    async def test_gate_fail_pen_missing(self, tmp_path: Path) -> None:
        """prototype.pen 不存在时 gate 失败 (AC#4)。"""
        from ato.core import check_design_gate

        root, arts, ux = self._setup_project(tmp_path, "s1")
        (arts / "s1.md").touch()
        ux.mkdir()
        (ux / "prototype.save-report.json").write_text(self._VALID_SAVE_REPORT)

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is False
        assert result.pen_integrity_ok is False
        assert "not found" in result.reason.lower()

    async def test_gate_fail_pen_invalid_json(self, tmp_path: Path) -> None:
        """prototype.pen 存在但 JSON 无效时 gate 失败 (AC#4)。"""
        from ato.core import check_design_gate

        root, arts, ux = self._setup_project(tmp_path, "s1")
        (arts / "s1.md").touch()
        ux.mkdir()
        (ux / "prototype.pen").write_text("not valid json {{{")
        (ux / "prototype.save-report.json").write_text(self._VALID_SAVE_REPORT)

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is False
        assert result.pen_integrity_ok is False
        assert "integrity" in result.reason.lower()

    async def test_gate_fail_pen_missing_required_keys(self, tmp_path: Path) -> None:
        """prototype.pen 缺少必需顶层字段时 gate 失败 (AC#4)。"""
        import json

        from ato.core import check_design_gate

        root, arts, ux = self._setup_project(tmp_path, "s1")
        (arts / "s1.md").touch()
        ux.mkdir()
        (ux / "prototype.pen").write_text(json.dumps({"version": "1.0.0"}))
        (ux / "prototype.snapshot.json").write_text('{"children":[]}')
        (ux / "prototype.save-report.json").write_text(self._VALID_SAVE_REPORT)

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is False
        assert result.pen_integrity_ok is False

    # --- snapshot 缺失或无效 ---

    async def test_gate_fail_snapshot_missing(self, tmp_path: Path) -> None:
        """prototype.snapshot.json 不存在时 gate 失败 (AC#3)。"""
        from ato.core import check_design_gate

        root, arts, ux = self._setup_project(tmp_path, "s1")
        (arts / "s1.md").touch()
        ux.mkdir()
        (ux / "prototype.pen").write_text(self._VALID_PEN)
        (ux / "prototype.save-report.json").write_text(self._VALID_SAVE_REPORT)

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is False
        assert result.snapshot_valid is False
        assert "snapshot" in result.reason.lower()

    async def test_gate_fail_snapshot_invalid_json(self, tmp_path: Path) -> None:
        """prototype.snapshot.json 存在但 JSON 无效时 gate 失败 (AC#3)。"""
        from ato.core import check_design_gate

        root, arts, ux = self._setup_project(tmp_path, "s1")
        (arts / "s1.md").touch()
        ux.mkdir()
        (ux / "prototype.pen").write_text(self._VALID_PEN)
        (ux / "prototype.snapshot.json").write_text("not json {{{")
        (ux / "prototype.save-report.json").write_text(self._VALID_SAVE_REPORT)

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is False
        assert result.snapshot_valid is False

    async def test_gate_fail_snapshot_wrong_shape(self, tmp_path: Path) -> None:
        """prototype.snapshot.json 为合法 JSON 但非结构化快照时 gate 失败 (AC#3)。"""
        from ato.core import check_design_gate

        root, arts, ux = self._setup_project(tmp_path, "s1")
        (arts / "s1.md").touch()
        ux.mkdir()
        (ux / "prototype.pen").write_text(self._VALID_PEN)
        (ux / "prototype.snapshot.json").write_text('{"foo":1}')
        (ux / "prototype.save-report.json").write_text(self._VALID_SAVE_REPORT)

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is False
        assert result.snapshot_valid is False
        assert "snapshot" in result.reason.lower()

    # --- save-report 缺失或无效 ---

    async def test_gate_fail_save_report_missing(self, tmp_path: Path) -> None:
        """prototype.save-report.json 不存在时 gate 失败 (AC#3)。"""
        from ato.core import check_design_gate

        root, arts, ux = self._setup_project(tmp_path, "s1")
        (arts / "s1.md").touch()
        ux.mkdir()
        (ux / "prototype.pen").write_text(self._VALID_PEN)
        (ux / "prototype.snapshot.json").write_text('{"children":[]}')

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is False
        assert result.save_report_valid is False
        assert "not found" in result.reason.lower()

    async def test_gate_fail_save_report_json_parse_false(self, tmp_path: Path) -> None:
        """save-report 中 json_parse_verified=false 时 gate 失败 (AC#4)。"""
        import json

        from ato.core import check_design_gate

        root, arts, ux = self._setup_project(tmp_path, "s1")
        (arts / "s1.md").touch()
        ux.mkdir()
        (ux / "prototype.pen").write_text(self._VALID_PEN)
        (ux / "prototype.snapshot.json").write_text('{"children":[]}')
        (ux / "prototype.save-report.json").write_text(
            json.dumps(
                {
                    "story_id": "s1",
                    "saved_at": "2026-03-28T00:00:00+00:00",
                    "pen_file": "prototype.pen",
                    "snapshot_file": "prototype.snapshot.json",
                    "children_count": 0,
                    "json_parse_verified": False,
                    "reopen_verified": True,
                    "exported_png_count": 0,
                }
            )
        )

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is False
        assert result.save_report_valid is False
        assert "save-report" in result.reason.lower()

    async def test_gate_fail_save_report_reopen_false(self, tmp_path: Path) -> None:
        """save-report 中 reopen_verified=false 时 gate 失败 (AC#4)。"""
        import json

        from ato.core import check_design_gate

        root, arts, ux = self._setup_project(tmp_path, "s1")
        (arts / "s1.md").touch()
        ux.mkdir()
        (ux / "prototype.pen").write_text(self._VALID_PEN)
        (ux / "prototype.snapshot.json").write_text('{"children":[]}')
        (ux / "prototype.save-report.json").write_text(
            json.dumps(
                {
                    "story_id": "s1",
                    "saved_at": "2026-03-28T00:00:00+00:00",
                    "pen_file": "prototype.pen",
                    "snapshot_file": "prototype.snapshot.json",
                    "children_count": 0,
                    "json_parse_verified": True,
                    "reopen_verified": False,
                    "exported_png_count": 0,
                }
            )
        )

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is False
        assert result.save_report_valid is False

    # --- 只有部分工件不足以通过 gate ---

    async def test_gate_fail_only_ux_spec(self, tmp_path: Path) -> None:
        """只有 ux-spec.md 无 .pen/save-report 时 gate 失败。"""
        from ato.core import check_design_gate

        root, arts, ux = self._setup_project(tmp_path, "s1")
        (arts / "s1.md").touch()
        ux.mkdir()
        (ux / "ux-spec.md").touch()

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is False
        assert result.pen_integrity_ok is False

    async def test_gate_fail_only_snapshot(self, tmp_path: Path) -> None:
        """只有 snapshot.json 无 .pen/save-report 时 gate 失败。"""
        from ato.core import check_design_gate

        root, arts, ux = self._setup_project(tmp_path, "s1")
        (arts / "s1.md").touch()
        ux.mkdir()
        (ux / "prototype.snapshot.json").touch()

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is False
        assert result.pen_integrity_ok is False

    async def test_gate_fail_only_exports(self, tmp_path: Path) -> None:
        """只有 exports/*.png 无 .pen/save-report 时 gate 失败。"""
        from ato.core import check_design_gate

        root, arts, ux = self._setup_project(tmp_path, "s1")
        (arts / "s1.md").touch()
        exports = ux / "exports"
        exports.mkdir(parents=True)
        (exports / "screen.png").touch()

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is False

    # --- 无 UX 目录 / 无工件 ---

    async def test_gate_fail_no_ux_dir(self, tmp_path: Path) -> None:
        """UX 目录不存在时 gate 失败。"""
        from ato.core import check_design_gate

        root, arts, _ux = self._setup_project(tmp_path, "s1")
        (arts / "s1.md").touch()

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is False
        assert result.story_spec_exists is True
        assert result.artifact_count == 0

    async def test_gate_fail_empty_ux_dir(self, tmp_path: Path) -> None:
        """UX 目录存在但没有已知工件时 gate 失败。"""
        from ato.core import check_design_gate

        root, arts, ux = self._setup_project(tmp_path, "s1")
        (arts / "s1.md").touch()
        ux.mkdir()
        (ux / "README.txt").touch()

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is False
        assert result.artifact_count == 0

    async def test_gate_fail_unknown_json(self, tmp_path: Path) -> None:
        """非核心工件的 .json（如 debug.json）不被计为有效工件。"""
        from ato.core import check_design_gate

        root, arts, ux = self._setup_project(tmp_path, "s1")
        (arts / "s1.md").touch()
        ux.mkdir()
        (ux / "debug.json").touch()

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is False
        assert result.artifact_count == 0

    # --- artifact_count 计数验证 ---

    async def test_artifact_count_includes_all_known_names(self, tmp_path: Path) -> None:
        """artifact_count 正确计数所有已知核心工件类型。"""
        from ato.core import check_design_gate

        root, _arts, ux = self._setup_full_prerequisites(tmp_path)
        (ux / "ux-spec.md").touch()
        (ux / "prototype.snapshot.json").touch()

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        # .pen + save-report + ux-spec + snapshot = 4
        assert result.artifact_count == 4

    # --- 日志 ---

    async def test_gate_logs_event(
        self,
        tmp_path: Path,
        capfd: pytest.CaptureFixture[str],
    ) -> None:
        """gate 检查应记录 structlog 事件。"""
        from ato.core import check_design_gate

        root, _arts, _ux = self._setup_project(tmp_path, "s1")

        await check_design_gate(story_id="s1", task_id="t1", project_root=root)

        captured = capfd.readouterr()
        output = captured.out + captured.err
        assert "design_gate_check" in output
