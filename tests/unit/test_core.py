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

        orchestrator._poll_cycle = stop_immediately  # type: ignore[method-assign, unused-ignore]

        # 在 _shutdown 中 patch get_connection 使 mark_running_tasks_paused 失败
        original_shutdown = orchestrator._shutdown

        async def shutdown_with_db_failure() -> None:
            with patch(
                "ato.core.get_connection",
                side_effect=RuntimeError("DB crash"),
            ):
                await original_shutdown()

        orchestrator._shutdown = shutdown_with_db_failure  # type: ignore[method-assign, unused-ignore]

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

        orchestrator._startup = failing_startup  # type: ignore[method-assign, unused-ignore]

        with pytest.raises(RuntimeError, match="simulated signal handler failure"):
            await orchestrator.run()

        # PID 文件应被 _shutdown 清理
        assert not pid_path.exists()
        # TransitionQueue.stop() 应被调用
        assert orchestrator._tq is not None
        orchestrator._tq.stop.assert_awaited_once()  # type: ignore[attr-defined, unused-ignore]


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _make_settings() -> MagicMock:
    """创建一个 mock ATOSettings。"""
    settings = MagicMock()
    settings.polling_interval = 1.0
    settings.max_planning_concurrent = 3
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
            status=status,  # type: ignore[arg-type, unused-ignore]
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
                status=status,  # type: ignore[arg-type, unused-ignore]
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
            await db.execute(
                "UPDATE stories SET current_phase = 'reviewing' WHERE story_id = 'test-story-1'"
            )
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

    async def test_pending_fix_placeholder_uses_batch_restart(
        self, initialized_db_path: Path
    ) -> None:
        """pending fix placeholder 应由 restart 调度器接手，而不是落回初始分发。"""
        from ato.config import PhaseDefinition
        from ato.models.db import get_connection, update_task_status

        await _insert_test_task(
            initialized_db_path,
            status="failed",
            task_id="t-fix-placeholder",
            story_id="test-story-1",
        )

        db = await get_connection(initialized_db_path)
        try:
            await update_task_status(
                db,
                "t-fix-placeholder",
                "pending",
                expected_artifact="convergent_loop_fix_placeholder",
                context_briefing=json.dumps(
                    {
                        "fix_kind": "fix_dispatch",
                        "round_num": 1,
                        "stage": "standard",
                    }
                ),
            )
            await db.execute(
                "UPDATE tasks SET phase = 'fixing', role = 'developer' "
                "WHERE task_id = 't-fix-placeholder'"
            )
            await db.execute(
                "UPDATE stories SET current_phase = 'fixing' WHERE story_id = 'test-story-1'"
            )
            await db.commit()
        finally:
            await db.close()

        settings = _make_settings()
        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)

        mock_phase_defs = [
            PhaseDefinition(
                name="fixing",
                role="developer",
                cli_tool="claude",
                model="opus",
                sandbox=None,
                phase_type="structured_job",
                next_on_success="reviewing",
                next_on_failure=None,
                timeout_seconds=1800,
            ),
        ]
        with (
            patch("ato.config.build_phase_definitions", return_value=mock_phase_defs),
            patch.object(
                orchestrator,
                "_dispatch_batch_restart",
                new_callable=AsyncMock,
            ) as mock_batch,
            patch.object(
                orchestrator,
                "_dispatch_convergent_restart",
                new_callable=AsyncMock,
            ) as mock_convergent,
        ):
            await orchestrator._dispatch_pending_tasks()
            await asyncio.sleep(0.05)

        mock_batch.assert_called_once()
        mock_convergent.assert_not_called()
        dispatched_task = mock_batch.call_args[0][0]
        assert dispatched_task.task_id == "t-fix-placeholder"

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

    async def test_duplicate_restart_requests_same_story_phase_are_deduped(
        self, initialized_db_path: Path
    ) -> None:
        """同一 story/phase 的重复 restart_requested 只调度最新一条，其余封口。"""
        from ato.config import PhaseDefinition
        from ato.models.db import get_connection, get_tasks_by_story, update_task_status

        await _insert_test_task(
            initialized_db_path,
            status="failed",
            task_id="t1",
            story_id="test-story-1",
        )
        await _insert_test_task(
            initialized_db_path,
            status="failed",
            task_id="t2",
            story_id="test-story-1",
        )

        db = await get_connection(initialized_db_path)
        try:
            await update_task_status(db, "t1", "pending", expected_artifact="restart_requested")
            await update_task_status(db, "t2", "pending", expected_artifact="restart_requested")
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

        with (
            patch("ato.config.build_phase_definitions", return_value=mock_phase_defs),
            patch.object(
                orchestrator,
                "_dispatch_interactive_restart",
                new_callable=AsyncMock,
            ) as mock_dispatch,
        ):
            await orchestrator._dispatch_pending_tasks()
            await asyncio.gather(*orchestrator._background_tasks, return_exceptions=True)

        mock_dispatch.assert_awaited_once()
        assert mock_dispatch.await_args is not None
        dispatched_task = mock_dispatch.await_args.args[0]
        assert dispatched_task.task_id == "t2"

        db = await get_connection(initialized_db_path)
        try:
            tasks = {task.task_id: task for task in await get_tasks_by_story(db, "test-story-1")}
        finally:
            await db.close()

        assert tasks["t1"].status == "failed"
        assert tasks["t1"].expected_artifact == "restart_superseded"
        assert tasks["t1"].error_message == "superseded_by_duplicate_restart_request"

    async def test_pending_restart_blocked_when_same_story_phase_already_running(
        self, initialized_db_path: Path
    ) -> None:
        """已有 running task 时，同 story/phase 的 restart_requested 不应再次 dispatch。"""
        from ato.config import PhaseDefinition
        from ato.models.db import get_connection, get_tasks_by_story, update_task_status

        await _insert_test_task(
            initialized_db_path,
            status="running",
            task_id="t-running",
            story_id="test-story-1",
        )
        await _insert_test_task(
            initialized_db_path,
            status="failed",
            task_id="t-pending",
            story_id="test-story-1",
        )

        db = await get_connection(initialized_db_path)
        try:
            await update_task_status(
                db,
                "t-pending",
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

        with (
            patch("ato.config.build_phase_definitions", return_value=mock_phase_defs),
            patch.object(
                orchestrator,
                "_dispatch_interactive_restart",
                new_callable=AsyncMock,
            ) as mock_dispatch,
        ):
            await orchestrator._dispatch_pending_tasks()

        mock_dispatch.assert_not_called()

        db = await get_connection(initialized_db_path)
        try:
            tasks = {task.task_id: task for task in await get_tasks_by_story(db, "test-story-1")}
        finally:
            await db.close()

        assert tasks["t-pending"].status == "failed"
        assert tasks["t-pending"].expected_artifact == "restart_superseded"
        assert tasks["t-pending"].error_message == "superseded_by_running_story_phase"

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

    async def test_dispatch_interactive_restart_developing_includes_open_suggestions(
        self, initialized_db_path: Path
    ) -> None:
        """developing restart prompt 应携带 open suggestion findings 作为上下文。"""
        from ato.models.db import get_connection, insert_findings_batch
        from ato.models.schemas import FindingRecord, TaskRecord, compute_dedup_hash

        await _insert_test_task(
            initialized_db_path,
            status="pending",
            task_id="t-dev-suggest",
            story_id="test-story-1",
        )

        db = await get_connection(initialized_db_path)
        try:
            await db.execute(
                "UPDATE stories SET current_phase = ?, worktree_path = ? WHERE story_id = ?",
                ("developing", "/tmp/test-worktree", "test-story-1"),
            )
            now = datetime.now(tz=UTC)
            await insert_findings_batch(
                db,
                [
                    FindingRecord(
                        finding_id="f-suggest-1",
                        story_id="test-story-1",
                        round_num=1,
                        severity="suggestion",
                        description="sync story dependency notes with implementation plan",
                        status="open",
                        file_path="_bmad-output/implementation-artifacts/test-story-1.md",
                        rule_id="story_validation.remaining_risk",
                        dedup_hash=compute_dedup_hash(
                            "_bmad-output/implementation-artifacts/test-story-1.md",
                            "story_validation.remaining_risk",
                            "suggestion",
                            "sync story dependency notes with implementation plan",
                        ),
                        created_at=now,
                    ),
                ],
            )
            await db.commit()
        finally:
            await db.close()

        settings = _make_settings()
        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)
        task = TaskRecord(
            task_id="t-dev-suggest",
            story_id="test-story-1",
            phase="developing",
            role="developer",
            cli_tool="claude",
            status="pending",
        )

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

            await orchestrator._dispatch_interactive_restart(task, resume=False)

        prompt = mock_mgr.dispatch_interactive.call_args.kwargs["prompt"]
        assert "## Open Suggestions" in prompt
        assert "open_suggestion_findings" in prompt
        assert "sync story dependency notes with implementation plan" in prompt


# ---------------------------------------------------------------------------
# Story 9.3: spec_batch precommit_failure approval lifecycle
# ---------------------------------------------------------------------------


class TestSpecBatchApprovalLifecycle:
    """spec_batch precommit_failure 审批的消费语义。"""

    async def test_manual_fix_consumes_old_and_creates_new_approval(
        self, initialized_db_path: Path
    ) -> None:
        """manual_fix 消费旧 approval 并创建新的 pending approval，用户可对新 approval 决策。"""
        import json

        payload = json.dumps(
            {
                "scope": "spec_batch",
                "batch_id": "b1",
                "story_ids": ["s1"],
                "error_output": "pre-commit hook failed",
                "options": ["retry", "manual_fix", "skip"],
            }
        )
        await _insert_test_approval(
            initialized_db_path,
            approval_id="spec1111-0000-0000-0000-000000000000",
            approval_type="precommit_failure",
            decision="manual_fix",
            status="approved",
        )
        # 用 spec_batch payload 覆盖默认 payload
        from ato.models.db import get_connection

        db = await get_connection(initialized_db_path)
        try:
            await db.execute(
                "UPDATE approvals SET payload = ? WHERE approval_id = ?",
                (payload, "spec1111-0000-0000-0000-000000000000"),
            )
            await db.commit()
        finally:
            await db.close()

        settings = _make_settings()
        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)
        await orchestrator._process_approval_decisions()

        from ato.models.db import get_decided_unconsumed_approvals, get_pending_approvals

        db = await get_connection(initialized_db_path)
        try:
            # 旧 approval 已消费（不在 unconsumed 里）
            unconsumed = await get_decided_unconsumed_approvals(db)
            old_ids = [a.approval_id for a in unconsumed]
            assert "spec1111-0000-0000-0000-000000000000" not in old_ids

            # 新的 pending approval 已创建
            pending = await get_pending_approvals(db)
            spec_pending = [a for a in pending if a.approval_type == "precommit_failure"]
            assert len(spec_pending) == 1, "应创建新的 pending approval"
            new_payload = json.loads(spec_pending[0].payload or "{}")
            assert new_payload["scope"] == "spec_batch"
            assert new_payload["batch_id"] == "b1"
        finally:
            await db.close()

    async def test_skip_consumes_approval_and_marks_committed(
        self, initialized_db_path: Path
    ) -> None:
        """skip 决策消费 approval 并标记 batch spec_committed。"""
        import json

        from ato.models.db import get_connection, insert_batch
        from ato.models.schemas import BatchRecord

        now = datetime.now(tz=UTC)
        db = await get_connection(initialized_db_path)
        try:
            await insert_batch(
                db,
                BatchRecord(batch_id="b2", status="active", created_at=now),
            )
        finally:
            await db.close()

        payload = json.dumps(
            {
                "scope": "spec_batch",
                "batch_id": "b2",
                "story_ids": ["s1"],
                "error_output": "error",
                "options": ["retry", "manual_fix", "skip"],
            }
        )
        await _insert_test_approval(
            initialized_db_path,
            approval_id="spec2222-0000-0000-0000-000000000000",
            approval_type="precommit_failure",
            decision="skip",
            status="approved",
        )
        db = await get_connection(initialized_db_path)
        try:
            await db.execute(
                "UPDATE approvals SET payload = ? WHERE approval_id = ?",
                (payload, "spec2222-0000-0000-0000-000000000000"),
            )
            await db.commit()
        finally:
            await db.close()

        settings = _make_settings()
        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)
        await orchestrator._process_approval_decisions()

        # skip → consumed + spec_committed=True
        from ato.models.db import get_active_batch, get_decided_unconsumed_approvals

        db = await get_connection(initialized_db_path)
        try:
            remaining = await get_decided_unconsumed_approvals(db)
            assert len(remaining) == 0, "skip 应消费 approval"

            batch = await get_active_batch(db)
            assert batch is not None
            assert batch.spec_committed is True
        finally:
            await db.close()


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
            await db.execute(
                "UPDATE stories SET current_phase = 'reviewing' WHERE story_id = 'test-story-conv'"
            )
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
            await engine._mark_dispatch_failed(task)  # type: ignore[arg-type, unused-ignore]
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
# Convergent loop restart synthetic task metadata
# ---------------------------------------------------------------------------


class TestConvergentLoopRestartMetadata:
    async def test_escalated_restart_uses_resolved_fix_profile_metadata(
        self,
        initialized_db_path: Path,
    ) -> None:
        """restart_phase2 synthetic task 应写入真实的 escalated fix 元数据。"""
        from ato.config import ATOSettings
        from ato.models.db import get_connection, get_tasks_by_story, insert_story
        from ato.models.schemas import ApprovalRecord, StoryRecord

        now = datetime.now(tz=UTC)
        settings = ATOSettings(
            roles={
                "reviewer": {"cli": "codex"},  # type: ignore[dict-item]
                "developer": {"cli": "claude"},  # type: ignore[dict-item]
                "reviewer_escalated": {"cli": "claude"},  # type: ignore[dict-item]
                "fixer_escalation": {"cli": "claude"},  # type: ignore[dict-item]
            },
            phases=[
                {  # type: ignore[list-item]
                    "name": "reviewing",
                    "role": "reviewer",
                    "type": "convergent_loop",
                    "next_on_success": "done",
                },
            ],
        )

        db = await get_connection(initialized_db_path)
        try:
            await insert_story(
                db,
                StoryRecord(
                    story_id="s-cl-restart",
                    title="Restart Story",
                    status="in_progress",
                    current_phase="reviewing",
                    created_at=now,
                    updated_at=now,
                ),
            )
        finally:
            await db.close()

        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)
        approval = ApprovalRecord(
            approval_id="appr-cl-restart",
            story_id="s-cl-restart",
            approval_type="convergent_loop_escalation",
            status="approved",
            decision="restart_phase2",
            decided_at=now,
            created_at=now,
        )

        result = await orchestrator._handle_convergent_loop_restart(
            approval,
            restart_target="escalated_fix",
        )

        assert result is True

        db = await get_connection(initialized_db_path)
        try:
            tasks = await get_tasks_by_story(db, "s-cl-restart")
        finally:
            await db.close()

        assert len(tasks) == 1
        task = tasks[0]
        assert task.phase == "reviewing"
        assert task.role == "fixer_escalation"
        assert task.cli_tool == "claude"
        assert task.expected_artifact == "restart_requested"
        assert task.context_briefing is not None
        context = json.loads(task.context_briefing)
        assert context["restart_target"] == "escalated_fix"
        assert context["stage"] == "escalated"


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
                "creator": {"cli": "claude", "model": "opus", "sandbox": None},  # type: ignore[dict-item, unused-ignore]
            },
            phases=[
                {  # type: ignore[list-item, unused-ignore]
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
        # creating phase defaults to workspace: main → cwd = project_root
        from ato.core import derive_project_root

        assert options["cwd"] == str(derive_project_root(initialized_db_path))
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
                "creator": {"cli": "claude"},  # type: ignore[dict-item]  # 无 model 无 sandbox
            },
            phases=[
                {  # type: ignore[list-item, unused-ignore]
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
        # creating phase defaults to workspace: main → cwd = project_root
        from ato.core import derive_project_root

        assert options["cwd"] == str(derive_project_root(initialized_db_path))
        assert "model" not in options
        assert "sandbox" not in options

    async def test_batch_restart_passes_orchestrator_progress_callback(
        self, initialized_db_path: Path
    ) -> None:
        """core 层应向 dispatch_with_retry 传入 on_progress，以暴露后台流式事件。"""
        from ato.config import ATOSettings
        from ato.models.schemas import AdapterResult, ProgressEvent, TaskRecord

        settings = ATOSettings(
            roles={"creator": {"cli": "claude"}},  # type: ignore[dict-item, unused-ignore]
            phases=[
                {  # type: ignore[list-item, unused-ignore]
                    "name": "creating",
                    "role": "creator",
                    "type": "structured_job",
                    "next_on_success": "done",
                },
            ],
        )

        await _insert_test_task(
            initialized_db_path, status="pending", task_id="t-progress", story_id="s1"
        )

        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)
        orchestrator._tq = AsyncMock()

        result = AdapterResult(status="success", exit_code=0, duration_ms=10, text_result="ok")
        dispatch_mock = AsyncMock(return_value=result)
        task = TaskRecord(
            task_id="t-progress",
            story_id="s1",
            phase="creating",
            role="creator",
            cli_tool="claude",
            status="pending",
            started_at=datetime.now(tz=UTC),
        )

        with (
            patch("ato.recovery._create_adapter", return_value=object()),
            patch("ato.subprocess_mgr.SubprocessManager.dispatch_with_retry", dispatch_mock),
            patch("ato.core.logger") as mock_logger,
        ):
            await orchestrator._dispatch_batch_restart(task)

            assert dispatch_mock.await_args is not None
            on_progress = dispatch_mock.await_args.kwargs["on_progress"]
            assert callable(on_progress)

            await on_progress(
                ProgressEvent(
                    event_type="tool_use",
                    summary="调用工具: Read",
                    cli_tool="claude",
                    timestamp=datetime.now(tz=UTC),
                    raw={"type": "assistant"},
                )
            )

        assert any(
            call.args and call.args[0] == "agent_progress"
            for call in mock_logger.info.call_args_list
        )


# ---------------------------------------------------------------------------
# Pre-worktree structured_job 串行控制测试 (Story 9.1 AC#5)
# ---------------------------------------------------------------------------


class TestPreWorktreeSerialControl:
    """验证 main-path gate 共享-独占门控行为。"""

    async def test_gate_is_shared_singleton(self) -> None:
        """get_main_path_gate 返回同一个 MainPathGate 实例。"""
        from ato.core import get_main_path_gate, reset_main_path_gate

        reset_main_path_gate()
        g1 = get_main_path_gate()
        g2 = get_main_path_gate()
        assert g1 is g2
        reset_main_path_gate()

    async def test_workspace_main_phases_use_gate(self) -> None:
        """workspace: main 阶段通过 phase_cfg 驱动 gate（不再依赖硬编码集合）。"""
        from ato.config import ATOSettings
        from ato.recovery import RecoveryEngine

        settings = ATOSettings(
            roles={"dev": {"cli": "claude"}},  # type: ignore[dict-item, unused-ignore]
            phases=[
                {  # type: ignore[list-item, unused-ignore]
                    "name": "planning",
                    "role": "dev",
                    "type": "structured_job",
                    "next_on_success": "developing",
                    "workspace": "main",
                },
                {  # type: ignore[list-item, unused-ignore]
                    "name": "developing",
                    "role": "dev",
                    "type": "structured_job",
                    "next_on_success": "done",
                    "workspace": "worktree",
                },
            ],
        )
        planning_cfg = RecoveryEngine._resolve_phase_config_static(settings, "planning")
        developing_cfg = RecoveryEngine._resolve_phase_config_static(settings, "developing")
        assert planning_cfg["workspace"] == "main"  # should use gate
        assert developing_cfg["workspace"] == "worktree"  # should NOT use gate

    async def test_exclusive_serializes_jobs(self) -> None:
        """独占模式下同一时刻最多只有 1 个 job 在执行。"""
        from ato.core import get_main_path_gate, reset_main_path_gate

        reset_main_path_gate()
        gate = get_main_path_gate()

        max_concurrent = 0
        active = 0

        async def simulate_dispatch(phase: str) -> str:
            nonlocal max_concurrent, active
            async with gate.exclusive():
                active += 1
                if active > max_concurrent:
                    max_concurrent = active
                await asyncio.sleep(0.01)
                active -= 1
                return phase

        results = await asyncio.gather(
            simulate_dispatch("merging"),
            simulate_dispatch("regression"),
            simulate_dispatch("batch_commit"),
        )

        assert set(results) == {"merging", "regression", "batch_commit"}
        assert max_concurrent == 1, f"Expected max 1 concurrent, got {max_concurrent}"
        reset_main_path_gate()


class TestMainPathGateConcurrency:
    """MainPathGate 共享-独占门控并发语义测试。"""

    async def test_shared_mode_allows_concurrent(self) -> None:
        """多个 shared holder 可以同时持有。"""
        from ato.core import get_main_path_gate, reset_main_path_gate

        reset_main_path_gate(max_shared=3)
        gate = get_main_path_gate()

        max_concurrent = 0
        active = 0

        async def shared_task() -> None:
            nonlocal max_concurrent, active
            async with gate.shared():
                active += 1
                if active > max_concurrent:
                    max_concurrent = active
                await asyncio.sleep(0.02)
                active -= 1

        await asyncio.gather(shared_task(), shared_task(), shared_task())
        assert max_concurrent == 3, f"Expected 3 concurrent shared, got {max_concurrent}"
        reset_main_path_gate()

    async def test_shared_mode_respects_max_cap(self) -> None:
        """5 个并发 shared 请求在 max_shared=3 下最多 3 个同时运行。"""
        from ato.core import get_main_path_gate, reset_main_path_gate

        reset_main_path_gate(max_shared=3)
        gate = get_main_path_gate()

        max_concurrent = 0
        active = 0

        async def shared_task() -> None:
            nonlocal max_concurrent, active
            async with gate.shared():
                active += 1
                if active > max_concurrent:
                    max_concurrent = active
                await asyncio.sleep(0.02)
                active -= 1

        await asyncio.gather(*(shared_task() for _ in range(5)))
        assert max_concurrent == 3, f"Expected max 3 concurrent shared, got {max_concurrent}"
        reset_main_path_gate()

    async def test_exclusive_blocked_by_shared(self) -> None:
        """独占获取需等待所有共享持有者释放。"""
        from ato.core import get_main_path_gate, reset_main_path_gate

        reset_main_path_gate(max_shared=3)
        gate = get_main_path_gate()

        exclusive_entered = False
        await gate.acquire_shared()
        try:

            async def try_exclusive() -> None:
                nonlocal exclusive_entered
                await gate.acquire_exclusive()
                exclusive_entered = True
                await gate.release_exclusive()

            task = asyncio.create_task(try_exclusive())
            await asyncio.sleep(0.02)
            assert not exclusive_entered, "Exclusive should be blocked by shared"
        finally:
            await gate.release_shared()

        await asyncio.wait_for(task, timeout=1.0)
        assert exclusive_entered
        reset_main_path_gate()

    async def test_shared_blocked_by_exclusive(self) -> None:
        """共享获取被独占持有者阻塞。"""
        from ato.core import get_main_path_gate, reset_main_path_gate

        reset_main_path_gate()
        gate = get_main_path_gate()

        shared_entered = False
        await gate.acquire_exclusive()
        try:

            async def try_shared() -> None:
                nonlocal shared_entered
                await gate.acquire_shared()
                shared_entered = True
                await gate.release_shared()

            task = asyncio.create_task(try_shared())
            await asyncio.sleep(0.02)
            assert not shared_entered, "Shared should be blocked by exclusive"
        finally:
            await gate.release_exclusive()

        await asyncio.wait_for(task, timeout=1.0)
        assert shared_entered
        reset_main_path_gate()

    async def test_shared_blocked_by_waiting_exclusive(self) -> None:
        """写优先：一旦有独占等待者，新共享请求被阻塞。"""
        from ato.core import get_main_path_gate, reset_main_path_gate

        reset_main_path_gate(max_shared=3)
        gate = get_main_path_gate()

        # 先获取一个 shared
        await gate.acquire_shared()
        exclusive_acquired = False
        new_shared_acquired = False

        async def exclusive_waiter() -> None:
            nonlocal exclusive_acquired
            await gate.acquire_exclusive()
            exclusive_acquired = True
            await gate.release_exclusive()

        async def new_shared_waiter() -> None:
            nonlocal new_shared_acquired
            await gate.acquire_shared()
            new_shared_acquired = True
            await gate.release_shared()

        # 启动独占等待者
        exc_task = asyncio.create_task(exclusive_waiter())
        await asyncio.sleep(0.02)
        assert not exclusive_acquired

        # 启动新的共享请求（应被独占等待者阻塞）
        shared_task = asyncio.create_task(new_shared_waiter())
        await asyncio.sleep(0.02)
        assert not new_shared_acquired, "New shared should be blocked by waiting exclusive"

        # 释放初始 shared → 独占获取 → 释放 → 新 shared 获取
        await gate.release_shared()
        await asyncio.wait_for(exc_task, timeout=1.0)
        await asyncio.wait_for(shared_task, timeout=1.0)
        assert exclusive_acquired
        assert new_shared_acquired
        reset_main_path_gate()

    async def test_exclusive_mutual_exclusion(self) -> None:
        """独占持有者之间互斥。"""
        from ato.core import get_main_path_gate, reset_main_path_gate

        reset_main_path_gate()
        gate = get_main_path_gate()

        max_concurrent = 0
        active = 0

        async def exclusive_task() -> None:
            nonlocal max_concurrent, active
            async with gate.exclusive():
                active += 1
                if active > max_concurrent:
                    max_concurrent = active
                await asyncio.sleep(0.01)
                active -= 1

        await asyncio.gather(exclusive_task(), exclusive_task(), exclusive_task())
        assert max_concurrent == 1
        reset_main_path_gate()

    async def test_gate_context_managers(self) -> None:
        """shared() 和 exclusive() context manager 正常工作。"""
        from ato.core import get_main_path_gate, reset_main_path_gate

        reset_main_path_gate(max_shared=2)
        gate = get_main_path_gate()

        async with gate.shared():
            assert gate._shared_holders == 1
        assert gate._shared_holders == 0

        async with gate.exclusive():
            assert gate._exclusive_held is True
        assert gate._exclusive_held is False
        reset_main_path_gate()

    async def test_configure_rejects_busy_gate(self) -> None:
        """gate 忙时不允许 reconfigure。"""
        import pytest

        from ato.core import get_main_path_gate, reset_main_path_gate

        reset_main_path_gate()
        gate = get_main_path_gate()

        await gate.acquire_shared()
        with pytest.raises(RuntimeError, match="cannot reconfigure"):
            gate.configure(5)
        await gate.release_shared()
        reset_main_path_gate()

    async def test_configure_idle_gate(self) -> None:
        """空闲 gate 可以成功 reconfigure。"""
        from ato.core import configure_main_path_gate, get_main_path_gate, reset_main_path_gate

        reset_main_path_gate()
        gate = get_main_path_gate()
        configure_main_path_gate(5)
        assert gate._max_shared == 5
        reset_main_path_gate()

    async def test_max_shared_validation(self) -> None:
        """max_shared < 1 应被拒绝。"""
        import pytest

        from ato.core import MainPathGate

        with pytest.raises(ValueError, match="max_shared must be >= 1"):
            MainPathGate(max_shared=0)

    async def test_release_without_holder_raises(self) -> None:
        """无持有者时 release 应抛出错误。"""
        import pytest

        from ato.core import MainPathGate

        gate = MainPathGate()
        with pytest.raises(RuntimeError, match="release_shared without holder"):
            await gate.release_shared()
        with pytest.raises(RuntimeError, match="release_exclusive without holder"):
            await gate.release_exclusive()


# ---------------------------------------------------------------------------
# Design gate V2 测试 (Story 9.1c)
# ---------------------------------------------------------------------------


class TestDesignGate:
    """check_design_gate V2 严格校验通过/失败矩阵 (Story 9.1c AC#1-#5)。

    Gate V2 通过条件（全部满足）:
    - story spec 存在
    - ux-spec.md 存在
    - prototype.pen 存在且 JSON 合法 + 含 version/children 顶层字段
    - prototype.snapshot.json 存在且为合法结构化快照
    - prototype.save-report.json 存在且 json_parse_verified=true + reopen_verified=true
    - exports/ 下至少 1 个 .png
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
        """构建完整 V2 通过条件（全部核心工件）。"""
        from ato.design_artifacts import write_prototype_manifest

        root, arts, ux = self._setup_project(tmp_path, story_id)
        (arts / f"{story_id}.md").touch()
        ux.mkdir()
        (ux / "ux-spec.md").touch()
        (ux / "prototype.pen").write_text(self._VALID_PEN)
        (ux / "prototype.snapshot.json").write_text(
            '[{"id":"frame-1","type":"FRAME","name":"Screen 1","children":[]}]'
        )
        (ux / "prototype.save-report.json").write_text(self._VALID_SAVE_REPORT)
        exports = ux / "exports"
        exports.mkdir()
        (exports / "screen.png").touch()
        write_prototype_manifest(story_id, root)
        return root, arts, ux

    # --- Happy path ---

    async def test_gate_pass_all_artifacts(self, tmp_path: Path) -> None:
        """全部核心工件齐全时 gate 通过 (AC#5 matrix: pass)。"""
        from ato.core import check_design_gate

        root, _arts, _ux = self._setup_full_prerequisites(tmp_path)
        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is True
        assert result.pen_integrity_ok is True
        assert result.save_report_valid is True
        assert result.ux_spec_exists is True
        assert result.snapshot_valid is True
        assert result.exports_png_count >= 1
        assert result.failure_codes == ()
        assert result.missing_files == ()

    async def test_gate_pass_multiple_pngs(self, tmp_path: Path) -> None:
        """多个 PNG 导出时仍通过且 exports_png_count 准确。"""
        from ato.core import check_design_gate

        root, _arts, ux = self._setup_full_prerequisites(tmp_path)
        exports = ux / "exports"
        (exports / "screen2.png").touch()
        (exports / "screen3.png").touch()

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is True
        assert result.exports_png_count == 3

    async def test_gate_pass_pen_without_variables(self, tmp_path: Path) -> None:
        """AC#2: .pen 只含 version + children（无 variables）时仍通过。"""
        from ato.core import check_design_gate

        root, _arts, ux = self._setup_full_prerequisites(tmp_path)
        # 覆盖 .pen 为只含 version + children 的最小合法文件
        (ux / "prototype.pen").write_text('{"version": "1.0.0", "children": []}')

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is True
        assert result.pen_integrity_ok is True

    # --- AC#5 matrix: 缺 prototype.pen 失败 ---

    async def test_gate_fail_pen_missing(self, tmp_path: Path) -> None:
        """prototype.pen 不存在时 gate 失败。"""
        from ato.core import check_design_gate

        root, _arts, ux = self._setup_full_prerequisites(tmp_path)
        (ux / "prototype.pen").unlink()

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is False
        assert result.pen_integrity_ok is False
        assert "PEN_MISSING" in result.failure_codes

    # --- AC#5 matrix: prototype.pen 非 JSON 失败 ---

    async def test_gate_fail_pen_invalid_json(self, tmp_path: Path) -> None:
        """prototype.pen 存在但 JSON 无效时 gate 失败。"""
        from ato.core import check_design_gate

        root, _arts, ux = self._setup_full_prerequisites(tmp_path)
        (ux / "prototype.pen").write_text("not valid json {{{")

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is False
        assert result.pen_integrity_ok is False
        assert "PEN_INVALID_JSON" in result.failure_codes

    async def test_gate_fail_pen_non_dict_root(self, tmp_path: Path) -> None:
        """prototype.pen 为合法 JSON 但根非 dict 时 gate 失败（不崩溃）。"""
        from ato.core import check_design_gate

        root, _arts, ux = self._setup_full_prerequisites(tmp_path)
        (ux / "prototype.pen").write_text("[1, 2, 3]")

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is False
        assert result.pen_integrity_ok is False
        assert "PEN_MISSING_KEYS" in result.failure_codes

    async def test_gate_fail_pen_missing_required_keys(self, tmp_path: Path) -> None:
        """prototype.pen 缺少必需顶层字段时 gate 失败。"""
        import json

        from ato.core import check_design_gate

        root, _arts, ux = self._setup_full_prerequisites(tmp_path)
        (ux / "prototype.pen").write_text(json.dumps({"version": "1.0.0"}))

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is False
        assert result.pen_integrity_ok is False
        assert "PEN_MISSING_KEYS" in result.failure_codes

    # --- AC#5 matrix: 缺 prototype.save-report.json 失败 ---

    async def test_gate_fail_save_report_missing(self, tmp_path: Path) -> None:
        """prototype.save-report.json 不存在时 gate 失败。"""
        from ato.core import check_design_gate

        root, _arts, ux = self._setup_full_prerequisites(tmp_path)
        (ux / "prototype.save-report.json").unlink()

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is False
        assert result.save_report_valid is False
        assert "SAVE_REPORT_MISSING" in result.failure_codes

    # --- AC#5 matrix: save-report.reopen_verified=false 失败 ---

    async def test_gate_fail_save_report_reopen_false(self, tmp_path: Path) -> None:
        """save-report 中 reopen_verified=false 时 gate 失败。"""
        import json

        from ato.core import check_design_gate

        root, _arts, ux = self._setup_full_prerequisites(tmp_path)
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
        assert "SAVE_REPORT_VERIFICATION_FAILED" in result.failure_codes
        assert result.save_report_summary is not None
        assert result.save_report_summary["reopen_verified"] is False

    async def test_gate_fail_save_report_json_parse_false(self, tmp_path: Path) -> None:
        """save-report 中 json_parse_verified=false 时 gate 失败。"""
        import json

        from ato.core import check_design_gate

        root, _arts, ux = self._setup_full_prerequisites(tmp_path)
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
        assert "SAVE_REPORT_VERIFICATION_FAILED" in result.failure_codes
        assert result.save_report_summary is not None
        assert result.save_report_summary["json_parse_verified"] is False

    async def test_gate_fail_save_report_invalid_json(self, tmp_path: Path) -> None:
        """save-report 为坏 JSON 时给 SAVE_REPORT_INVALID_JSON 且 summary 含 parse_error。"""
        from ato.core import check_design_gate

        root, _arts, ux = self._setup_full_prerequisites(tmp_path)
        (ux / "prototype.save-report.json").write_text("not json {{{")

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is False
        assert result.save_report_valid is False
        assert "SAVE_REPORT_INVALID_JSON" in result.failure_codes
        assert "SAVE_REPORT_VERIFICATION_FAILED" not in result.failure_codes
        assert result.save_report_summary is not None
        assert "parse_error" in result.save_report_summary

    async def test_gate_fail_save_report_non_dict_root(self, tmp_path: Path) -> None:
        """save-report 为合法 JSON 但根非 dict 时给 SAVE_REPORT_INVALID_JSON（不崩溃）。"""
        from ato.core import check_design_gate

        root, _arts, ux = self._setup_full_prerequisites(tmp_path)
        (ux / "prototype.save-report.json").write_text("[1, 2, 3]")

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is False
        assert result.save_report_valid is False
        assert "SAVE_REPORT_INVALID_JSON" in result.failure_codes
        assert result.save_report_summary is not None
        assert "parse_error" in result.save_report_summary
        assert "list" in str(result.save_report_summary["parse_error"])

    async def test_gate_fail_save_report_missing_keys(self, tmp_path: Path) -> None:
        """save-report 缺少必需键时给 SAVE_REPORT_MISSING_KEYS 且 summary 保留已有字段。"""
        import json

        from ato.core import check_design_gate

        root, _arts, ux = self._setup_full_prerequisites(tmp_path)
        (ux / "prototype.save-report.json").write_text(
            json.dumps({"story_id": "s1", "saved_at": "2026-03-28"})
        )

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is False
        assert result.save_report_valid is False
        assert "SAVE_REPORT_MISSING_KEYS" in result.failure_codes
        assert "SAVE_REPORT_VERIFICATION_FAILED" not in result.failure_codes
        assert result.save_report_summary is not None

    # --- AC#5 matrix: 缺 PNG 失败 ---

    async def test_gate_fail_exports_png_missing(self, tmp_path: Path) -> None:
        """exports/ 下无 .png 时 gate 失败。"""
        import shutil

        from ato.core import check_design_gate

        root, _arts, ux = self._setup_full_prerequisites(tmp_path)
        shutil.rmtree(ux / "exports")

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is False
        assert result.exports_png_count == 0
        assert "EXPORTS_PNG_MISSING" in result.failure_codes

    async def test_gate_fail_exports_dir_exists_but_empty(self, tmp_path: Path) -> None:
        """exports/ 存在但无 .png 文件时 gate 失败。"""
        from ato.core import check_design_gate

        root, _arts, ux = self._setup_full_prerequisites(tmp_path)
        (ux / "exports" / "screen.png").unlink()

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is False
        assert "EXPORTS_PNG_MISSING" in result.failure_codes

    async def test_gate_fail_exports_non_png(self, tmp_path: Path) -> None:
        """exports/ 只有非 .png 文件时 gate 失败。"""
        from ato.core import check_design_gate

        root, _arts, ux = self._setup_full_prerequisites(tmp_path)
        (ux / "exports" / "screen.png").unlink()
        (ux / "exports" / "readme.txt").touch()

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is False
        assert "EXPORTS_PNG_MISSING" in result.failure_codes

    # --- AC#1: 缺 ux-spec.md 失败 ---

    async def test_gate_fail_ux_spec_missing(self, tmp_path: Path) -> None:
        """ux-spec.md 不存在时 gate 失败。"""
        from ato.core import check_design_gate

        root, _arts, ux = self._setup_full_prerequisites(tmp_path)
        (ux / "ux-spec.md").unlink()

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is False
        assert result.ux_spec_exists is False
        assert "UX_SPEC_MISSING" in result.failure_codes

    # --- snapshot 缺失或无效 ---

    async def test_gate_fail_snapshot_missing(self, tmp_path: Path) -> None:
        """prototype.snapshot.json 不存在时 gate 失败。"""
        from ato.core import check_design_gate

        root, _arts, ux = self._setup_full_prerequisites(tmp_path)
        (ux / "prototype.snapshot.json").unlink()

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is False
        assert result.snapshot_valid is False
        assert "SNAPSHOT_MISSING" in result.failure_codes

    async def test_gate_fail_snapshot_invalid_json(self, tmp_path: Path) -> None:
        """prototype.snapshot.json 存在但 JSON 无效时 gate 失败。"""
        from ato.core import check_design_gate

        root, _arts, ux = self._setup_full_prerequisites(tmp_path)
        (ux / "prototype.snapshot.json").write_text("not json {{{")

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is False
        assert result.snapshot_valid is False
        assert "SNAPSHOT_INVALID" in result.failure_codes

    async def test_gate_fail_snapshot_wrong_shape(self, tmp_path: Path) -> None:
        """prototype.snapshot.json 为合法 JSON 但非结构化快照时 gate 失败。"""
        from ato.core import check_design_gate

        root, _arts, ux = self._setup_full_prerequisites(tmp_path)
        (ux / "prototype.snapshot.json").write_text('{"foo":1}')

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is False
        assert result.snapshot_valid is False
        assert "SNAPSHOT_INVALID" in result.failure_codes

    # --- Story spec 缺失 ---

    async def test_gate_fail_no_story_spec(self, tmp_path: Path) -> None:
        """story spec 不存在时 gate 失败。"""
        from ato.core import check_design_gate

        root, arts, _ux = self._setup_full_prerequisites(tmp_path)
        (arts / "s1.md").unlink()

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is False
        assert result.story_spec_exists is False
        assert "STORY_SPEC_MISSING" in result.failure_codes

    # --- 多个工件同时缺失：failure_codes 应收集全部 ---

    async def test_gate_fail_multiple_missing(self, tmp_path: Path) -> None:
        """多个核心工件缺失时 failure_codes 收集所有失败码。"""
        from ato.core import check_design_gate

        root, arts, ux = self._setup_project(tmp_path, "s1")
        (arts / "s1.md").touch()
        ux.mkdir()
        # 什么核心工件都没有

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is False
        assert "UX_SPEC_MISSING" in result.failure_codes
        assert "PEN_MISSING" in result.failure_codes
        assert "SNAPSHOT_MISSING" in result.failure_codes
        assert "SAVE_REPORT_MISSING" in result.failure_codes
        assert "EXPORTS_PNG_MISSING" in result.failure_codes
        assert len(result.missing_files) >= 4

    # --- missing_files 包含具体路径 ---

    async def test_missing_files_contains_paths(self, tmp_path: Path) -> None:
        """missing_files 列出缺失文件的具体路径。"""
        from ato.core import check_design_gate

        root, _arts, ux = self._setup_project(tmp_path, "s1")
        ux.mkdir()
        # 无 story spec → missing_files 中应包含 story spec 路径

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is False
        assert any("s1.md" in p for p in result.missing_files)

    # --- save_report_summary 结构 (AC#3) ---

    async def test_save_report_summary_present_on_failure(self, tmp_path: Path) -> None:
        """save-report 存在但校验失败时，payload 包含关键状态摘要。"""
        import json

        from ato.core import check_design_gate

        root, _arts, ux = self._setup_full_prerequisites(tmp_path)
        (ux / "prototype.save-report.json").write_text(
            json.dumps(
                {
                    "story_id": "s1",
                    "saved_at": "2026-03-28T00:00:00+00:00",
                    "pen_file": "prototype.pen",
                    "snapshot_file": "prototype.snapshot.json",
                    "children_count": 5,
                    "json_parse_verified": True,
                    "reopen_verified": False,
                    "exported_png_count": 2,
                }
            )
        )

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.save_report_summary is not None
        assert result.save_report_summary["json_parse_verified"] is True
        assert result.save_report_summary["reopen_verified"] is False
        assert result.save_report_summary["children_count"] == 5

    async def test_save_report_summary_none_when_missing(self, tmp_path: Path) -> None:
        """save-report 不存在时 save_report_summary 为 None。"""
        from ato.core import check_design_gate

        root, _arts, ux = self._setup_full_prerequisites(tmp_path)
        (ux / "prototype.save-report.json").unlink()

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.save_report_summary is None

    # --- build_design_gate_payload 共享 helper (AC#3, AC#4) ---

    async def test_build_payload_structure(self, tmp_path: Path) -> None:
        """build_design_gate_payload 返回结构化可操作 payload。"""
        from ato.core import build_design_gate_payload, check_design_gate

        root, _arts, ux = self._setup_full_prerequisites(tmp_path)
        (ux / "prototype.pen").unlink()

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        payload = build_design_gate_payload("t1", result)

        assert payload["task_id"] == "t1"
        assert "artifact_dir" in payload
        assert "failure_codes" in payload
        assert "missing_files" in payload
        assert "reason" in payload
        assert "PEN_MISSING" in payload["failure_codes"]  # type: ignore[operator, unused-ignore]

    async def test_build_payload_includes_save_report_summary(self, tmp_path: Path) -> None:
        """save-report 存在时 payload 包含摘要。"""
        import json

        from ato.core import build_design_gate_payload, check_design_gate

        root, _arts, ux = self._setup_full_prerequisites(tmp_path)
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
        payload = build_design_gate_payload("t1", result)
        assert "save_report_summary" in payload

    # --- 无 UX 目录 ---

    async def test_gate_fail_no_ux_dir(self, tmp_path: Path) -> None:
        """UX 目录不存在时 gate 失败，所有核心工件都在 failure_codes 中。"""
        from ato.core import check_design_gate

        root, arts, _ux = self._setup_project(tmp_path, "s1")
        (arts / "s1.md").touch()

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is False
        assert result.story_spec_exists is True
        assert result.artifact_count == 0
        assert len(result.failure_codes) >= 4

    # --- artifact_count 信息性统计 ---

    async def test_artifact_count_includes_all_known_names(self, tmp_path: Path) -> None:
        """artifact_count 正确计数所有已知核心工件类型（信息性，不影响 pass）。"""
        from ato.core import check_design_gate

        root, _arts, _ux = self._setup_full_prerequisites(tmp_path)

        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        # ux-spec + .pen + snapshot + save-report + manifest + 1 png = 6
        assert result.artifact_count == 6

    # --- 日志 ---

    async def test_gate_logs_event(
        self,
        tmp_path: Path,
        capfd: pytest.CaptureFixture[str],
    ) -> None:
        """gate 检查应记录 structlog 事件，包含 failure_codes 字段。"""
        from ato.core import check_design_gate

        root, _arts, _ux = self._setup_project(tmp_path, "s1")

        await check_design_gate(story_id="s1", task_id="t1", project_root=root)

        captured = capfd.readouterr()
        output = captured.out + captured.err
        assert "design_gate_check" in output

    # --- Story 9.1d: manifest gate 校验 ---

    async def test_gate_fail_manifest_missing(self, tmp_path: Path) -> None:
        """prototype.manifest.yaml 缺失时 gate 失败 (AC#4)。"""
        from ato.core import check_design_gate

        root, _arts, ux = self._setup_full_prerequisites(tmp_path)
        # 删除 manifest
        (ux / "prototype.manifest.yaml").unlink()
        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is False
        assert "MANIFEST_MISSING" in result.failure_codes
        assert result.manifest_valid is False

    async def test_gate_fail_manifest_invalid(self, tmp_path: Path) -> None:
        """prototype.manifest.yaml 解析失败时 gate 失败 (AC#4)。"""
        from ato.core import check_design_gate

        root, _arts, ux = self._setup_full_prerequisites(tmp_path)
        (ux / "prototype.manifest.yaml").write_text("{{invalid yaml")
        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is False
        assert "MANIFEST_INVALID" in result.failure_codes

    async def test_gate_fail_manifest_story_id_mismatch(self, tmp_path: Path) -> None:
        """manifest story_id 不匹配时 gate 失败 (AC#4)。"""
        import yaml

        from ato.core import check_design_gate

        root, _arts, ux = self._setup_full_prerequisites(tmp_path)
        manifest = ux / "prototype.manifest.yaml"
        data = yaml.safe_load(manifest.read_text())
        data["story_id"] = "wrong-story"
        manifest.write_text(yaml.safe_dump(data))
        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is False
        assert "MANIFEST_STORY_ID_MISMATCH" in result.failure_codes

    async def test_gate_fail_manifest_paths_missing(self, tmp_path: Path) -> None:
        """manifest 中引用的路径不存在时 gate 失败 (AC#4)。"""
        import yaml

        from ato.core import check_design_gate

        root, _arts, ux = self._setup_full_prerequisites(tmp_path)
        manifest = ux / "prototype.manifest.yaml"
        data = yaml.safe_load(manifest.read_text())
        data["reference_exports"] = ["exports/nonexistent.png"]
        manifest.write_text(yaml.safe_dump(data))
        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is False
        assert "MANIFEST_PATHS_MISSING" in result.failure_codes

    async def test_gate_pass_manifest_valid(self, tmp_path: Path) -> None:
        """完整 manifest 通过校验时 manifest_valid=True (AC#4)。"""
        from ato.core import check_design_gate

        root, _arts, _ux = self._setup_full_prerequisites(tmp_path)
        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is True
        assert result.manifest_valid is True

    async def test_gate_fail_manifest_absolute_story_file(self, tmp_path: Path) -> None:
        """story_file 为绝对路径时 gate 失败 (AC#2 相对路径合同)。"""
        import yaml

        from ato.core import check_design_gate

        root, _arts, ux = self._setup_full_prerequisites(tmp_path)
        manifest = ux / "prototype.manifest.yaml"
        data = yaml.safe_load(manifest.read_text())
        data["story_file"] = str(root / data["story_file"])  # 改为绝对路径
        manifest.write_text(yaml.safe_dump(data))
        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is False
        assert "MANIFEST_PATHS_MISSING" in result.failure_codes

    async def test_gate_fail_manifest_non_png_export(self, tmp_path: Path) -> None:
        """reference_exports 含非 .png 文件时 gate 失败 (AC#4 PNG 合同)。"""
        import yaml

        from ato.core import check_design_gate

        root, _arts, ux = self._setup_full_prerequisites(tmp_path)
        exports_dir = ux / "exports"
        (exports_dir / "readme.txt").write_bytes(b"text")
        manifest = ux / "prototype.manifest.yaml"
        data = yaml.safe_load(manifest.read_text())
        data["reference_exports"] = ["exports/readme.txt"]
        manifest.write_text(yaml.safe_dump(data))
        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is False
        assert "MANIFEST_PATHS_MISSING" in result.failure_codes

    async def test_gate_fail_manifest_path_traversal(self, tmp_path: Path) -> None:
        """story_file 含 .. 路径越界时 gate 失败。"""
        import yaml

        from ato.core import check_design_gate

        root, _arts, ux = self._setup_full_prerequisites(tmp_path)
        manifest = ux / "prototype.manifest.yaml"
        data = yaml.safe_load(manifest.read_text())
        data["story_file"] = "../../etc/passwd"
        manifest.write_text(yaml.safe_dump(data))
        result = await check_design_gate(story_id="s1", task_id="t1", project_root=root)
        assert result.passed is False
        assert "MANIFEST_PATHS_MISSING" in result.failure_codes


class TestDevelopingPromptUxContext:
    """Story 9.1d: developing prompt 包含 manifest / PNG / .pen 引用 (AC#3, #5)。"""

    def test_developing_prompt_includes_ux_context(self, tmp_path: Path) -> None:
        """_build_interactive_prompt 在 manifest 存在时附加 UX 上下文。"""
        from ato.core import _build_interactive_prompt
        from ato.design_artifacts import write_prototype_manifest
        from ato.models.schemas import TaskRecord

        root = tmp_path / "proj"
        arts = root / "_bmad-output/implementation-artifacts"
        ux = arts / "s1-ux"
        exports = ux / "exports"
        exports.mkdir(parents=True)
        (arts / "s1.md").touch()
        (ux / "ux-spec.md").touch()
        (ux / "prototype.pen").write_text('{"version":"1.0.0","children":[]}')
        (ux / "prototype.snapshot.json").write_text('{"children":[]}')
        (ux / "prototype.save-report.json").write_text("{}")
        (exports / "a.png").write_bytes(b"PNG")
        write_prototype_manifest("s1", root)

        task = TaskRecord(
            task_id="t1",
            story_id="s1",
            phase="developing",
            role="developer",
            cli_tool="claude",
            status="running",
        )
        prompt = _build_interactive_prompt(task, "/worktree", project_root=root)
        assert "UX Design Context" in prompt
        assert "prototype.manifest.yaml" in prompt
        assert "prototype.pen" in prompt

    def test_developing_prompt_no_manifest_passthrough(self, tmp_path: Path) -> None:
        """无 manifest 时 prompt 不含 UX 上下文（兼容无 UI story）。"""
        from ato.core import _build_interactive_prompt
        from ato.models.schemas import TaskRecord

        root = tmp_path / "proj"
        root.mkdir()
        task = TaskRecord(
            task_id="t1",
            story_id="s1",
            phase="developing",
            role="developer",
            cli_tool="claude",
            status="running",
        )
        prompt = _build_interactive_prompt(task, "/worktree", project_root=root)
        assert "UX Design Context" not in prompt


# ---------------------------------------------------------------------------
# UAT interactive prompt 专用模板
# ---------------------------------------------------------------------------


class TestUatInteractivePrompt:
    """UAT 阶段 interactive prompt 包含启动指引和 story spec 路径。"""

    def test_uat_prompt_contains_story_file(self) -> None:
        """UAT prompt 应包含 story 规格文件路径。"""
        from ato.core import _build_interactive_prompt
        from ato.models.schemas import TaskRecord

        task = TaskRecord(
            task_id="t1",
            story_id="story-uat-1",
            phase="uat",
            role="qa",
            cli_tool="claude",
            status="running",
        )
        prompt = _build_interactive_prompt(task, "/worktree")
        assert "_bmad-output/implementation-artifacts/story-uat-1.md" in prompt

    def test_uat_prompt_contains_startup_guidance(self) -> None:
        """UAT prompt 应包含应用启动指引。"""
        from ato.core import _build_interactive_prompt
        from ato.models.schemas import TaskRecord

        task = TaskRecord(
            task_id="t1",
            story_id="story-uat-1",
            phase="uat",
            role="qa",
            cli_tool="claude",
            status="running",
        )
        prompt = _build_interactive_prompt(task, "/worktree")
        assert "启动应用" in prompt
        assert "package.json" in prompt
        assert "pyproject.toml" in prompt
        assert "docker-compose.yml" in prompt

    def test_uat_prompt_contains_result_command(self) -> None:
        """UAT prompt 应提示用户如何提交 UAT 结果。"""
        from ato.core import _build_interactive_prompt
        from ato.models.schemas import TaskRecord

        task = TaskRecord(
            task_id="t1",
            story_id="story-uat-1",
            phase="uat",
            role="qa",
            cli_tool="claude",
            status="running",
        )
        prompt = _build_interactive_prompt(task, "/worktree")
        assert "ato uat story-uat-1 --result pass/fail" in prompt

    def test_uat_prompt_not_generic_fallback(self) -> None:
        """UAT 阶段不应使用通用 fallback prompt。"""
        from ato.core import _build_interactive_prompt
        from ato.models.schemas import TaskRecord

        task = TaskRecord(
            task_id="t1",
            story_id="story-uat-1",
            phase="uat",
            role="qa",
            cli_tool="claude",
            status="running",
        )
        prompt = _build_interactive_prompt(task, "/worktree")
        assert "Interactive session restart" not in prompt

    def test_uat_prompt_appends_previous_context(self) -> None:
        """UAT prompt 应附加之前的上下文信息。"""
        from ato.core import _build_interactive_prompt
        from ato.models.schemas import TaskRecord

        task = TaskRecord(
            task_id="t1",
            story_id="story-uat-1",
            phase="uat",
            role="qa",
            cli_tool="claude",
            status="running",
        )
        prompt = _build_interactive_prompt(task, "/worktree", story_ctx="\n\nPrevious: 首次 UAT")
        assert "Previous: 首次 UAT" in prompt


# ---------------------------------------------------------------------------
# Story 9.1e: _dispatch_batch_restart creating 路径使用 findings helper
# ---------------------------------------------------------------------------


class TestBatchRestartCreatingFindings:
    """验证 _dispatch_batch_restart creating 路径经过 _build_creating_prompt_with_findings。"""

    async def test_batch_restart_creating_includes_findings(
        self, initialized_db_path: Path
    ) -> None:
        """AC4: core._dispatch_batch_restart creating 路径追加 validation findings。"""
        from ato.config import ATOSettings
        from ato.models.db import (
            get_connection,
            insert_findings_batch,
            update_story_worktree_path,
        )
        from ato.models.schemas import (
            AdapterResult,
            FindingRecord,
            TaskRecord,
            compute_dedup_hash,
        )

        settings = ATOSettings(
            roles={"creator": {"cli": "claude"}},  # type: ignore[dict-item, unused-ignore]
            phases=[
                {  # type: ignore[list-item, unused-ignore]
                    "name": "creating",
                    "role": "creator",
                    "type": "structured_job",
                    "next_on_success": "done",
                },
            ],
        )

        await _insert_test_task(
            initialized_db_path, status="pending", task_id="t-cr", story_id="s-cr"
        )
        db = await get_connection(initialized_db_path)
        try:
            await db.execute(
                "UPDATE tasks SET phase = 'creating', role = 'creator', "
                "cli_tool = 'claude', expected_artifact = 'restart_requested' "
                "WHERE task_id = 't-cr'"
            )
            await update_story_worktree_path(db, "s-cr", "/tmp/wt-cr")
            now = datetime.now(tz=UTC)
            await insert_findings_batch(
                db,
                [
                    FindingRecord(
                        finding_id="f-core-1",
                        story_id="s-cr",
                        round_num=1,
                        severity="blocking",
                        description="missing acceptance criteria",
                        status="open",
                        file_path="story.md",
                        rule_id="SV001",
                        dedup_hash=compute_dedup_hash(
                            "story.md",
                            "SV001",
                            "blocking",
                            "missing acceptance criteria",
                        ),
                        created_at=now,
                    ),
                ],
            )
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
            task_id="t-cr",
            story_id="s-cr",
            phase="creating",
            role="creator",
            cli_tool="claude",
            status="pending",
            started_at=datetime.now(tz=UTC),
        )

        with patch("ato.recovery._create_adapter", return_value=mock_adapter):
            await orchestrator._dispatch_batch_restart(task)

        mock_adapter.execute.assert_called_once()
        prompt = mock_adapter.execute.call_args[0][0]
        assert "## Validation Feedback" in prompt
        assert "missing acceptance criteria" in prompt
        assert "/bmad-create-story" in prompt

    async def test_batch_restart_creating_preserves_context_briefing(
        self, initialized_db_path: Path
    ) -> None:
        """core._dispatch_batch_restart creating 模板分支保留 context_briefing。"""
        from ato.config import ATOSettings
        from ato.models.db import get_connection, update_story_worktree_path
        from ato.models.schemas import AdapterResult, TaskRecord

        settings = ATOSettings(
            roles={"creator": {"cli": "claude"}},  # type: ignore[dict-item, unused-ignore]
            phases=[
                {  # type: ignore[list-item, unused-ignore]
                    "name": "creating",
                    "role": "creator",
                    "type": "structured_job",
                    "next_on_success": "done",
                },
            ],
        )

        await _insert_test_task(
            initialized_db_path, status="pending", task_id="t-cb", story_id="s-cb"
        )
        db = await get_connection(initialized_db_path)
        try:
            await db.execute(
                "UPDATE tasks SET phase = 'creating', role = 'creator', "
                "cli_tool = 'claude', expected_artifact = 'restart_requested' "
                "WHERE task_id = 't-cb'"
            )
            await update_story_worktree_path(db, "s-cb", "/tmp/wt-cb")
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
            task_id="t-cb",
            story_id="s-cb",
            phase="creating",
            role="creator",
            cli_tool="claude",
            status="pending",
            started_at=datetime.now(tz=UTC),
            context_briefing="human approved retry: fix scope",
        )

        with patch("ato.recovery._create_adapter", return_value=mock_adapter):
            await orchestrator._dispatch_batch_restart(task)

        mock_adapter.execute.assert_called_once()
        prompt = mock_adapter.execute.call_args[0][0]
        assert "/bmad-create-story" in prompt
        assert "human approved retry: fix scope" in prompt


# ---------------------------------------------------------------------------
# Story 9.2: _dispatch_batch_restart workspace 分支
# ---------------------------------------------------------------------------


class TestBatchRestartWorkspaceBranches:
    """直接测试 _dispatch_batch_restart 的 workspace-aware 逻辑。

    确保 restart 路径和 recovery 路径的 workspace 行为一致——
    防止两条路径实现漂移。
    """

    async def test_batch_restart_dev_ready_reconciles_without_adapter(
        self, initialized_db_path: Path
    ) -> None:
        """dev_ready restart 走自动 gate，不应再启动 adapter。"""
        from ato.config import ATOSettings
        from ato.models.db import get_connection, get_tasks_by_story, update_story_worktree_path
        from ato.models.schemas import AdapterResult, TaskRecord

        settings = ATOSettings(
            roles={"dev": {"cli": "claude"}},  # type: ignore[dict-item, unused-ignore]
            phases=[
                {  # type: ignore[list-item, unused-ignore]
                    "name": "dev_ready",
                    "role": "dev",
                    "type": "structured_job",
                    "next_on_success": "done",
                    "workspace": "main",
                },
            ],
        )

        await _insert_test_task(
            initialized_db_path, status="pending", task_id="t-ws1", story_id="s-ws1"
        )
        db = await get_connection(initialized_db_path)
        try:
            await db.execute(
                "UPDATE tasks SET phase = 'dev_ready', role = 'dev', "
                "cli_tool = 'claude', expected_artifact = 'restart_requested' "
                "WHERE task_id = 't-ws1'"
            )
            # story 有 worktree，但 dev_ready 是 main → cwd 应该是 project_root
            await update_story_worktree_path(db, "s-ws1", "/tmp/wt-should-ignore")
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
            task_id="t-ws1",
            story_id="s-ws1",
            phase="dev_ready",
            role="dev",
            cli_tool="claude",
            status="pending",
            started_at=datetime.now(tz=UTC),
        )

        with patch("ato.recovery._create_adapter", return_value=mock_adapter):
            await orchestrator._dispatch_batch_restart(task)

        mock_adapter.execute.assert_not_called()
        orchestrator._tq.submit.assert_awaited_once()
        event = orchestrator._tq.submit.call_args.args[0]
        assert event.story_id == "s-ws1"
        assert event.event_name == "start_dev"

        db2 = await get_connection(initialized_db_path)
        try:
            tasks = await get_tasks_by_story(db2, "s-ws1")
        finally:
            await db2.close()

        assert tasks[0].status == "completed"
        assert tasks[0].expected_artifact == "dev_ready_gate_reconciled"

    async def test_batch_restart_worktree_workspace_missing_worktree_dispatch_failed(
        self, initialized_db_path: Path
    ) -> None:
        """workspace: worktree 且无 worktree_path 时尝试创建失败 → dispatch_failed。"""
        from ato.config import ATOSettings
        from ato.models.schemas import AdapterResult, TaskRecord

        settings = ATOSettings(
            roles={
                "fixer": {"cli": "claude"},  # type: ignore[dict-item, unused-ignore]
                "reviewer": {"cli": "codex"},  # type: ignore[dict-item, unused-ignore]
            },
            phases=[
                {  # type: ignore[list-item, unused-ignore]
                    "name": "fixing",
                    "role": "fixer",
                    "type": "structured_job",
                    "next_on_success": "done",
                    "workspace": "worktree",
                },
            ],
        )

        await _insert_test_task(
            initialized_db_path, status="pending", task_id="t-ws2", story_id="s-ws2"
        )

        orchestrator = Orchestrator(settings=settings, db_path=initialized_db_path)
        orchestrator._tq = AsyncMock()

        mock_adapter = AsyncMock()
        mock_adapter.execute = AsyncMock(
            return_value=AdapterResult(
                status="success", exit_code=0, duration_ms=10, text_result="ok"
            )
        )

        task = TaskRecord(
            task_id="t-ws2",
            story_id="s-ws2",
            phase="fixing",
            role="fixer",
            cli_tool="claude",
            status="pending",
            started_at=datetime.now(tz=UTC),
        )

        # WorktreeManager.create 会失败（非 git 仓库）→ dispatch_failed
        with patch("ato.recovery._create_adapter", return_value=mock_adapter):
            await orchestrator._dispatch_batch_restart(task)

        # 不应调用 adapter（worktree 创建失败 → 不在 main 上执行 fixing）
        mock_adapter.execute.assert_not_called()

    async def test_batch_restart_worktree_workspace_with_worktree_uses_it(
        self, initialized_db_path: Path, tmp_path: Path
    ) -> None:
        """workspace: worktree 且 worktree_path 存在时使用 worktree_path 作为 cwd。"""
        from ato.config import ATOSettings
        from ato.models.db import get_connection, update_story_worktree_path
        from ato.models.schemas import AdapterResult, TaskRecord

        # 使用真实存在的目录（Bug 3 fix 会验证目录存在性）
        wt_dir = tmp_path / "wt-fix"
        wt_dir.mkdir()

        settings = ATOSettings(
            roles={
                "fixer": {"cli": "claude"},  # type: ignore[dict-item, unused-ignore]
                "reviewer": {"cli": "codex"},  # type: ignore[dict-item, unused-ignore]
            },
            phases=[
                {  # type: ignore[list-item, unused-ignore]
                    "name": "fixing",
                    "role": "fixer",
                    "type": "structured_job",
                    "next_on_success": "done",
                    "workspace": "worktree",
                },
            ],
        )

        await _insert_test_task(
            initialized_db_path, status="pending", task_id="t-ws3", story_id="s-ws3"
        )
        db = await get_connection(initialized_db_path)
        try:
            await db.execute(
                "UPDATE tasks SET phase = 'fixing', role = 'fixer', "
                "cli_tool = 'claude', expected_artifact = 'restart_requested' "
                "WHERE task_id = 't-ws3'"
            )
            await db.execute(
                "UPDATE stories SET current_phase = 'fixing' WHERE story_id = 's-ws3'"
            )
            await update_story_worktree_path(db, "s-ws3", str(wt_dir))
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
            task_id="t-ws3",
            story_id="s-ws3",
            phase="fixing",
            role="fixer",
            cli_tool="claude",
            status="pending",
            started_at=datetime.now(tz=UTC),
        )

        with patch("ato.recovery._create_adapter", return_value=mock_adapter):
            await orchestrator._dispatch_batch_restart(task)

        mock_adapter.execute.assert_called_once()
        options = mock_adapter.execute.call_args[0][1]
        assert options["cwd"] == str(wt_dir)

    async def test_batch_restart_fixing_context_continues_convergent_loop(
        self, initialized_db_path: Path, tmp_path: Path
    ) -> None:
        """fixing restart 成功后应提交 fix_done，并继续触发 convergent-loop 后续 rereview。"""
        from ato.config import ATOSettings
        from ato.models.db import get_connection, update_story_worktree_path
        from ato.models.schemas import AdapterResult, TaskRecord

        settings = ATOSettings(
            roles={
                "fixer": {"cli": "claude"},  # type: ignore[dict-item, unused-ignore]
                "reviewer": {"cli": "codex"},  # type: ignore[dict-item, unused-ignore]
            },
            phases=[
                {  # type: ignore[list-item, unused-ignore]
                    "name": "fixing",
                    "role": "fixer",
                    "type": "structured_job",
                    "next_on_success": "reviewing",
                    "workspace": "worktree",
                },
                {  # type: ignore[list-item, unused-ignore]
                    "name": "reviewing",
                    "role": "reviewer",
                    "type": "convergent_loop",
                    "next_on_success": "qa_testing",
                },
            ],
        )

        await _insert_test_task(
            initialized_db_path,
            status="pending",
            task_id="t-fix-followup",
            story_id="s-fix-followup",
        )
        db = await get_connection(initialized_db_path)
        try:
            await db.execute(
                "UPDATE tasks SET phase = 'fixing', role = 'fixer', "
                "cli_tool = 'claude', expected_artifact = 'convergent_loop_fix_placeholder', "
                "context_briefing = ? "
                "WHERE task_id = 't-fix-followup'",
                (
                    json.dumps(
                        {
                            "fix_kind": "fix_dispatch",
                            "round_num": 2,
                            "stage": "escalated",
                        }
                    ),
                ),
            )
            await db.execute(
                "UPDATE stories SET current_phase = 'fixing' "
                "WHERE story_id = 's-fix-followup'"
            )
            wt_dir = tmp_path / "wt-followup"
            wt_dir.mkdir()
            await update_story_worktree_path(db, "s-fix-followup", str(wt_dir))
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
            task_id="t-fix-followup",
            story_id="s-fix-followup",
            phase="fixing",
            role="fixer",
            cli_tool="claude",
            status="pending",
            context_briefing=json.dumps(
                {
                    "fix_kind": "fix_dispatch",
                    "round_num": 2,
                    "stage": "escalated",
                }
            ),
            started_at=datetime.now(tz=UTC),
        )

        with (
            patch("ato.recovery._create_adapter", return_value=mock_adapter),
            patch(
                "ato.recovery.RecoveryEngine.continue_after_fix_success",
                new=AsyncMock(),
            ) as mock_continue,
        ):
            await orchestrator._dispatch_batch_restart(task)

        orchestrator._tq.submit.assert_called_once()
        event = orchestrator._tq.submit.call_args[0][0]
        assert event.event_name == "fix_done"
        mock_continue.assert_awaited_once()
        assert mock_continue.await_args is not None
        assert mock_continue.await_args.kwargs["worktree_path"] == str(wt_dir)
