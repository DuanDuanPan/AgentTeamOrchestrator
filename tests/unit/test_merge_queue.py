"""test_merge_queue — MergeQueue 核心类与 DB CRUD 测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from ato.models.db import (
    complete_merge,
    dequeue_next_merge,
    enqueue_merge,
    get_connection,
    get_merge_queue_entry,
    get_merge_queue_state,
    get_pending_merges,
    insert_story,
    mark_regression_dispatched,
    remove_from_merge_queue,
    set_current_merge_story,
    set_merge_queue_frozen,
)
from ato.models.schemas import StoryRecord

_NOW = datetime.now(tz=UTC)
_EARLIER = _NOW - timedelta(minutes=5)


# ---------------------------------------------------------------------------
# Helper: mock subprocess 兼容 drain 架构
# ---------------------------------------------------------------------------


def _make_mock_proc(
    returncode: int = 0,
    stdout_data: bytes = b"",
    stderr_data: bytes = b"",
) -> MagicMock:
    """创建兼容 drain 架构的 mock subprocess。

    新的 _run_regression_test 使用 proc.wait() + StreamReader drain，
    而不是 proc.communicate()。此 helper 正确模拟两种 pipe 读取路径。
    """
    import asyncio as _aio

    mock_proc = MagicMock()
    mock_proc.returncode = returncode

    # proc.wait() 返回 future（立即完成）
    wait_future: _aio.Future[int] = _aio.get_event_loop().create_future()
    wait_future.set_result(returncode)
    mock_proc.wait = MagicMock(return_value=wait_future)

    # stdout/stderr 作为 StreamReader（drain tasks 读取）
    mock_proc.stdout = _aio.StreamReader()
    mock_proc.stdout.feed_data(stdout_data)
    mock_proc.stdout.feed_eof()

    mock_proc.stderr = _aio.StreamReader()
    mock_proc.stderr.feed_data(stderr_data)
    mock_proc.stderr.feed_eof()

    return mock_proc


# ---------------------------------------------------------------------------
# Helper: 插入 story 以满足外键约束
# ---------------------------------------------------------------------------


async def _insert_test_story(db_path: Path, story_id: str) -> None:
    db = await get_connection(db_path)
    try:
        story = StoryRecord(
            story_id=story_id,
            title=f"Test story {story_id}",
            status="in_progress",
            current_phase="merging",
            created_at=_NOW,
            updated_at=_NOW,
        )
        await insert_story(db, story)
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# DB CRUD 测试
# ---------------------------------------------------------------------------


class TestMergeQueueCRUD:
    """merge_queue 表 CRUD 操作测试。"""

    async def test_enqueue_adds_to_queue(self, initialized_db_path: Path) -> None:
        await _insert_test_story(initialized_db_path, "s1")
        db = await get_connection(initialized_db_path)
        try:
            await enqueue_merge(db, "s1", "appr-1", _NOW, _NOW)
            entry = await get_merge_queue_entry(db, "s1")
            assert entry is not None
            assert entry.story_id == "s1"
            assert entry.status == "waiting"
            assert entry.approval_id == "appr-1"
        finally:
            await db.close()

    async def test_dequeue_order_uses_approved_at_then_id(self, initialized_db_path: Path) -> None:
        await _insert_test_story(initialized_db_path, "s1")
        await _insert_test_story(initialized_db_path, "s2")
        await _insert_test_story(initialized_db_path, "s3")

        db = await get_connection(initialized_db_path)
        try:
            # s2 approved earlier, s1 and s3 at same time
            await enqueue_merge(db, "s1", "a1", _NOW, _NOW)
            await enqueue_merge(db, "s2", "a2", _EARLIER, _NOW)
            await enqueue_merge(db, "s3", "a3", _NOW, _NOW)

            # First dequeue should be s2 (earlier approved_at)
            entry = await dequeue_next_merge(db)
            assert entry is not None
            assert entry.story_id == "s2"
            assert entry.status == "merging"

            # Second dequeue should be s1 (same approved_at, lower id)
            entry2 = await dequeue_next_merge(db)
            assert entry2 is not None
            assert entry2.story_id == "s1"
        finally:
            await db.close()

    async def test_dequeue_returns_none_when_empty(self, initialized_db_path: Path) -> None:
        db = await get_connection(initialized_db_path)
        try:
            entry = await dequeue_next_merge(db)
            assert entry is None
        finally:
            await db.close()

    async def test_mark_regression_dispatched(self, initialized_db_path: Path) -> None:
        await _insert_test_story(initialized_db_path, "s1")
        db = await get_connection(initialized_db_path)
        try:
            await enqueue_merge(db, "s1", "a1", _NOW, _NOW)
            _ = await dequeue_next_merge(db)
            await mark_regression_dispatched(db, "s1", "task-123")

            entry = await get_merge_queue_entry(db, "s1")
            assert entry is not None
            assert entry.status == "regression_pending"
            assert entry.regression_task_id == "task-123"
        finally:
            await db.close()

    async def test_complete_merge_success(self, initialized_db_path: Path) -> None:
        await _insert_test_story(initialized_db_path, "s1")
        db = await get_connection(initialized_db_path)
        try:
            await enqueue_merge(db, "s1", "a1", _NOW, _NOW)
            _ = await dequeue_next_merge(db)
            await complete_merge(db, "s1", success=True)

            entry = await get_merge_queue_entry(db, "s1")
            assert entry is not None
            assert entry.status == "merged"
        finally:
            await db.close()

    async def test_complete_merge_failure(self, initialized_db_path: Path) -> None:
        await _insert_test_story(initialized_db_path, "s1")
        db = await get_connection(initialized_db_path)
        try:
            await enqueue_merge(db, "s1", "a1", _NOW, _NOW)
            _ = await dequeue_next_merge(db)
            await complete_merge(db, "s1", success=False)

            entry = await get_merge_queue_entry(db, "s1")
            assert entry is not None
            assert entry.status == "failed"
        finally:
            await db.close()

    async def test_merge_queue_state_singleton(self, initialized_db_path: Path) -> None:
        db = await get_connection(initialized_db_path)
        try:
            state = await get_merge_queue_state(db)
            assert state.frozen is False
            assert state.current_merge_story_id is None

            await set_merge_queue_frozen(db, frozen=True, reason="test freeze")
            state = await get_merge_queue_state(db)
            assert state.frozen is True
            assert state.frozen_reason == "test freeze"
            assert state.frozen_at is not None

            await set_merge_queue_frozen(db, frozen=False, reason=None)
            state = await get_merge_queue_state(db)
            assert state.frozen is False
        finally:
            await db.close()

    async def test_set_current_merge_story(self, initialized_db_path: Path) -> None:
        db = await get_connection(initialized_db_path)
        try:
            await set_current_merge_story(db, "s1")
            state = await get_merge_queue_state(db)
            assert state.current_merge_story_id == "s1"

            await set_current_merge_story(db, None)
            state = await get_merge_queue_state(db)
            assert state.current_merge_story_id is None
        finally:
            await db.close()

    async def test_get_pending_merges(self, initialized_db_path: Path) -> None:
        await _insert_test_story(initialized_db_path, "s1")
        await _insert_test_story(initialized_db_path, "s2")
        db = await get_connection(initialized_db_path)
        try:
            await enqueue_merge(db, "s1", "a1", _NOW, _NOW)
            await enqueue_merge(db, "s2", "a2", _EARLIER, _NOW)

            pending = await get_pending_merges(db)
            assert len(pending) == 2
            assert pending[0].story_id == "s2"  # earlier approved_at first
        finally:
            await db.close()

    async def test_remove_from_merge_queue(self, initialized_db_path: Path) -> None:
        await _insert_test_story(initialized_db_path, "s1")
        db = await get_connection(initialized_db_path)
        try:
            await enqueue_merge(db, "s1", "a1", _NOW, _NOW)
            await remove_from_merge_queue(db, "s1")

            entry = await get_merge_queue_entry(db, "s1")
            assert entry is None
        finally:
            await db.close()


# ---------------------------------------------------------------------------
# MergeQueue 类测试
# ---------------------------------------------------------------------------


class TestMergeQueueClass:
    """MergeQueue 核心逻辑测试。"""

    def _make_queue(self, db_path: Path) -> tuple[Any, Any, Any]:
        """创建 MergeQueue 及其 mock 依赖。"""
        from ato.merge_queue import MergeQueue

        worktree_mgr = AsyncMock(
            spec=[
                "rebase_onto_main",
                "merge_to_main",
                "cleanup",
                "get_path",
                "continue_rebase",
                "abort_rebase",
                "get_conflict_files",
                "project_root",
            ]
        )
        worktree_mgr.project_root = Path("/fake/repo")

        tq = AsyncMock()
        settings = MagicMock()
        settings.merge_rebase_timeout = 120
        settings.merge_conflict_resolution_max_attempts = 1
        settings.regression_test_command = "echo ok"
        settings.get_regression_commands = MagicMock(return_value=["echo ok"])
        settings.timeout.structured_job = 1800

        queue = MergeQueue(
            db_path=db_path,
            worktree_mgr=worktree_mgr,
            transition_queue=tq,
            settings=settings,
        )
        return queue, worktree_mgr, tq

    async def test_enqueue_writes_to_db(self, initialized_db_path: Path) -> None:
        queue, _, _ = self._make_queue(initialized_db_path)
        await _insert_test_story(initialized_db_path, "s1")

        await queue.enqueue("s1", "appr-1", _NOW)

        db = await get_connection(initialized_db_path)
        try:
            entry = await get_merge_queue_entry(db, "s1")
            assert entry is not None
            assert entry.status == "waiting"
        finally:
            await db.close()

    async def test_enqueue_keeps_frozen_queue_frozen(self, initialized_db_path: Path) -> None:
        queue, _, _ = self._make_queue(initialized_db_path)
        await _insert_test_story(initialized_db_path, "s1")

        db = await get_connection(initialized_db_path)
        try:
            await set_merge_queue_frozen(
                db,
                frozen=True,
                reason="regression failed for s1",
            )
        finally:
            await db.close()

        await queue.enqueue("s1", "appr-1", _NOW)

        db = await get_connection(initialized_db_path)
        try:
            state = await get_merge_queue_state(db)
            assert state.frozen is True
            assert state.frozen_reason == "regression failed for s1"
        finally:
            await db.close()

    async def test_process_next_frozen_returns_false(self, initialized_db_path: Path) -> None:
        queue, _, _ = self._make_queue(initialized_db_path)

        # Freeze the queue
        db = await get_connection(initialized_db_path)
        try:
            await set_merge_queue_frozen(db, frozen=True, reason="test")
        finally:
            await db.close()

        result = await queue.process_next()
        assert result is False

    async def test_process_next_frozen_recovery_story_still_runs(
        self, initialized_db_path: Path
    ) -> None:
        queue, _, _ = self._make_queue(initialized_db_path)
        await _insert_test_story(initialized_db_path, "s1")
        await _insert_test_story(initialized_db_path, "s2")

        db = await get_connection(initialized_db_path)
        try:
            await enqueue_merge(db, "s2", "a2", _EARLIER, _NOW)
            await enqueue_merge(db, "s1", "a1", _NOW, _NOW)
            await set_merge_queue_frozen(
                db,
                frozen=True,
                reason="regression failed for s1",
            )
        finally:
            await db.close()

        with patch.object(queue, "_run_merge_worker", new_callable=AsyncMock):
            result = await queue.process_next()
            assert result is True

        db = await get_connection(initialized_db_path)
        try:
            state = await get_merge_queue_state(db)
            entry_s1 = await get_merge_queue_entry(db, "s1")
            entry_s2 = await get_merge_queue_entry(db, "s2")
            assert state.frozen is True
            assert state.current_merge_story_id == "s1"
            assert entry_s1 is not None
            assert entry_s2 is not None
            assert entry_s1.status == "merging"
            assert entry_s2.status == "waiting"
        finally:
            await db.close()

    async def test_process_next_busy_returns_false(self, initialized_db_path: Path) -> None:
        queue, _, _ = self._make_queue(initialized_db_path)

        # Set a current merge story
        db = await get_connection(initialized_db_path)
        try:
            await set_current_merge_story(db, "some-story")
        finally:
            await db.close()

        result = await queue.process_next()
        assert result is False

    async def test_process_next_empty_returns_false(self, initialized_db_path: Path) -> None:
        queue, _, _ = self._make_queue(initialized_db_path)
        result = await queue.process_next()
        assert result is False

    async def test_process_next_dequeues_and_starts_worker(self, initialized_db_path: Path) -> None:
        queue, _, _ = self._make_queue(initialized_db_path)
        await _insert_test_story(initialized_db_path, "s1")

        db = await get_connection(initialized_db_path)
        try:
            await enqueue_merge(db, "s1", "a1", _NOW, _NOW)
        finally:
            await db.close()

        # Mock the execute to avoid real git operations
        with patch.object(queue, "_run_merge_worker", new_callable=AsyncMock):
            result = await queue.process_next()
            assert result is True

            # Verify current_merge_story_id was set
            db = await get_connection(initialized_db_path)
            try:
                state = await get_merge_queue_state(db)
                assert state.current_merge_story_id == "s1"
            finally:
                await db.close()

    async def test_regression_fail_freezes_queue(self, initialized_db_path: Path) -> None:
        queue, _, _ = self._make_queue(initialized_db_path)
        await _insert_test_story(initialized_db_path, "s1")

        db = await get_connection(initialized_db_path)
        try:
            await enqueue_merge(db, "s1", "a1", _NOW, _NOW)
            _ = await dequeue_next_merge(db)
        finally:
            await db.close()

        await queue._handle_regression_failure("s1")

        db = await get_connection(initialized_db_path)
        try:
            state = await get_merge_queue_state(db)
            assert state.frozen is True
            assert "regression failed" in (state.frozen_reason or "")

            entry = await get_merge_queue_entry(db, "s1")
            assert entry is not None
            assert entry.status == "failed"
        finally:
            await db.close()

    async def test_regression_fail_creates_urgent_approval(self, initialized_db_path: Path) -> None:
        queue, _, _ = self._make_queue(initialized_db_path)
        await _insert_test_story(initialized_db_path, "s1")

        db = await get_connection(initialized_db_path)
        try:
            await enqueue_merge(db, "s1", "a1", _NOW, _NOW)
            _ = await dequeue_next_merge(db)
        finally:
            await db.close()

        await queue._handle_regression_failure("s1")

        from ato.models.db import get_pending_approvals

        db = await get_connection(initialized_db_path)
        try:
            pending = await get_pending_approvals(db)
            reg_approvals = [a for a in pending if a.approval_type == "regression_failure"]
            assert len(reg_approvals) == 1
            assert reg_approvals[0].risk_level == "high"
        finally:
            await db.close()

    async def test_unfreeze_restores_processing(self, initialized_db_path: Path) -> None:
        queue, _, _ = self._make_queue(initialized_db_path)

        # Freeze first
        db = await get_connection(initialized_db_path)
        try:
            await set_merge_queue_frozen(db, frozen=True, reason="test")
        finally:
            await db.close()

        # Unfreeze
        await queue.unfreeze("test completed")

        db = await get_connection(initialized_db_path)
        try:
            state = await get_merge_queue_state(db)
            assert state.frozen is False
        finally:
            await db.close()

    async def test_concurrent_enqueue_serialized(self, initialized_db_path: Path) -> None:
        queue, _, _ = self._make_queue(initialized_db_path)
        await _insert_test_story(initialized_db_path, "s1")
        await _insert_test_story(initialized_db_path, "s2")
        await _insert_test_story(initialized_db_path, "s3")

        # Enqueue multiple stories
        t1 = _NOW - timedelta(minutes=3)
        t2 = _NOW - timedelta(minutes=2)
        t3 = _NOW - timedelta(minutes=1)

        await queue.enqueue("s1", "a1", t1)
        await queue.enqueue("s2", "a2", t2)
        await queue.enqueue("s3", "a3", t3)

        # Verify ordering
        db = await get_connection(initialized_db_path)
        try:
            pending = await get_pending_merges(db)
            assert len(pending) == 3
            assert [e.story_id for e in pending] == ["s1", "s2", "s3"]
        finally:
            await db.close()

    async def test_rebase_conflict_escalates_on_failure(self, initialized_db_path: Path) -> None:
        queue, worktree_mgr, _ = self._make_queue(initialized_db_path)
        await _insert_test_story(initialized_db_path, "s1")

        worktree_mgr.get_conflict_files = AsyncMock(return_value=["file1.py", "file2.py"])
        worktree_mgr.continue_rebase = AsyncMock(return_value=(False, "still conflicting"))
        worktree_mgr.abort_rebase = AsyncMock()

        result = await queue._handle_rebase_conflict("s1", "CONFLICT in file1.py")
        assert result is False
        worktree_mgr.continue_rebase.assert_not_awaited()

        # Should have created a rebase_conflict approval
        from ato.models.db import get_pending_approvals

        db = await get_connection(initialized_db_path)
        try:
            pending = await get_pending_approvals(db)
            conflict_approvals = [a for a in pending if a.approval_type == "rebase_conflict"]
            assert len(conflict_approvals) == 1
        finally:
            await db.close()

    async def test_precommit_failure_creates_approval(self, initialized_db_path: Path) -> None:
        queue, _, _ = self._make_queue(initialized_db_path)
        await _insert_test_story(initialized_db_path, "s1")

        result = await queue._handle_precommit_failure("s1", "ruff check failed")
        assert result is False

        from ato.models.db import get_pending_approvals

        db = await get_connection(initialized_db_path)
        try:
            pending = await get_pending_approvals(db)
            pc_approvals = [a for a in pending if a.approval_type == "precommit_failure"]
            assert len(pc_approvals) == 1
        finally:
            await db.close()


# ---------------------------------------------------------------------------
# Regression 修复验证测试
# ---------------------------------------------------------------------------


class TestStaleLockRecovery:
    """recover_stale_lock() 验证。"""

    def _make_queue(self, db_path: Path) -> Any:
        from ato.merge_queue import MergeQueue

        worktree_mgr = AsyncMock()
        worktree_mgr.project_root = Path("/fake/repo")
        tq = AsyncMock()
        settings = MagicMock()
        settings.merge_rebase_timeout = 120
        settings.merge_conflict_resolution_max_attempts = 1
        settings.regression_test_command = "echo ok"
        settings.get_regression_commands = MagicMock(return_value=["echo ok"])
        settings.timeout.structured_job = 1800

        queue = MergeQueue(
            db_path=db_path,
            worktree_mgr=worktree_mgr,
            transition_queue=tq,
            settings=settings,
        )
        return queue

    async def test_clears_stale_lock_no_entry(self, initialized_db_path: Path) -> None:
        """current_merge_story_id 指向不存在的 entry → 清空。"""
        queue = self._make_queue(initialized_db_path)

        db = await get_connection(initialized_db_path)
        try:
            await set_current_merge_story(db, "ghost-story")
        finally:
            await db.close()

        await queue.recover_stale_lock()

        db = await get_connection(initialized_db_path)
        try:
            state = await get_merge_queue_state(db)
            assert state.current_merge_story_id is None
        finally:
            await db.close()

    async def test_clears_stale_lock_completed_entry(self, initialized_db_path: Path) -> None:
        """current_merge_story_id 指向已 merged 的 entry → 清空。"""
        queue = self._make_queue(initialized_db_path)
        await _insert_test_story(initialized_db_path, "s1")

        db = await get_connection(initialized_db_path)
        try:
            await enqueue_merge(db, "s1", "a1", _NOW, _NOW)
            await dequeue_next_merge(db)
            await complete_merge(db, "s1", success=True)
            await set_current_merge_story(db, "s1")
        finally:
            await db.close()

        await queue.recover_stale_lock()

        db = await get_connection(initialized_db_path)
        try:
            state = await get_merge_queue_state(db)
            assert state.current_merge_story_id is None
        finally:
            await db.close()

    async def test_merging_entry_removed_and_lock_released(self, initialized_db_path: Path) -> None:
        """crash 后 entry 在 merging → 移除 entry + 释放锁，使 story 可重建 approval。"""
        queue = self._make_queue(initialized_db_path)
        await _insert_test_story(initialized_db_path, "s1")

        db = await get_connection(initialized_db_path)
        try:
            await enqueue_merge(db, "s1", "a1", _NOW, _NOW)
            await dequeue_next_merge(db)  # status → merging
            await set_current_merge_story(db, "s1")
        finally:
            await db.close()

        await queue.recover_stale_lock()

        db = await get_connection(initialized_db_path)
        try:
            state = await get_merge_queue_state(db)
            assert state.current_merge_story_id is None
            # entry 被移除，_create_merge_authorizations 可以重建 approval
            entry = await get_merge_queue_entry(db, "s1")
            assert entry is None
        finally:
            await db.close()

    async def test_regression_pending_freezes_and_creates_approval(
        self, initialized_db_path: Path
    ) -> None:
        """crash 后 entry 在 regression_pending → 冻结 queue + 创建 approval。"""
        from ato.models.db import get_pending_approvals, mark_regression_dispatched

        queue = self._make_queue(initialized_db_path)
        await _insert_test_story(initialized_db_path, "s1")

        db = await get_connection(initialized_db_path)
        try:
            await enqueue_merge(db, "s1", "a1", _NOW, _NOW)
            await dequeue_next_merge(db)  # → merging
            await mark_regression_dispatched(db, "s1", "task-reg-1")  # → regression_pending
            await set_current_merge_story(db, "s1")
        finally:
            await db.close()

        await queue.recover_stale_lock()

        db = await get_connection(initialized_db_path)
        try:
            state = await get_merge_queue_state(db)
            # 锁释放但 queue 冻结
            assert state.current_merge_story_id is None
            assert state.frozen is True
            # 应创建 regression_failure approval
            pending = await get_pending_approvals(db)
            reg_approvals = [a for a in pending if a.approval_type == "regression_failure"]
            assert len(reg_approvals) == 1
        finally:
            await db.close()

    async def test_regression_pending_completed_success_recovers_as_pass(
        self, initialized_db_path: Path
    ) -> None:
        """已成功完成的 regression task 不应被误判成 failure approval。"""
        from ato.models.db import get_pending_approvals, insert_task
        from ato.models.schemas import TaskRecord

        queue = self._make_queue(initialized_db_path)
        await _insert_test_story(initialized_db_path, "s1")

        task_id = "task-reg-1"
        now = datetime.now(tz=UTC)
        db = await get_connection(initialized_db_path)
        try:
            await enqueue_merge(db, "s1", "a1", _NOW, _NOW)
            await dequeue_next_merge(db)
            await mark_regression_dispatched(db, "s1", task_id)
            await insert_task(
                db,
                TaskRecord(
                    task_id=task_id,
                    story_id="s1",
                    phase="regression",
                    role="qa",
                    cli_tool="codex",
                    status="completed",
                    started_at=now,
                    completed_at=now,
                    exit_code=0,
                ),
            )
            await set_current_merge_story(db, "s1")
        finally:
            await db.close()

        await queue.recover_stale_lock()

        db = await get_connection(initialized_db_path)
        try:
            state = await get_merge_queue_state(db)
            entry = await get_merge_queue_entry(db, "s1")
            pending = await get_pending_approvals(db)
            reg_approvals = [a for a in pending if a.approval_type == "regression_failure"]
            assert state.current_merge_story_id is None
            assert state.frozen is False
            assert entry is not None
            assert entry.status == "merged"
            assert reg_approvals == []
        finally:
            await db.close()

        queue._tq.submit.assert_awaited_once()
        event = queue._tq.submit.call_args[0][0]
        assert event.event_name == "regression_pass"
        queue._worktree_mgr.cleanup.assert_awaited_once_with("s1")

    async def test_noop_when_no_lock(self, initialized_db_path: Path) -> None:
        """current_merge_story_id 为 None 时无操作。"""
        queue = self._make_queue(initialized_db_path)
        await queue.recover_stale_lock()

        db = await get_connection(initialized_db_path)
        try:
            state = await get_merge_queue_state(db)
            assert state.current_merge_story_id is None
        finally:
            await db.close()


class TestCrashRecoveryScenarios:
    """Story 4.5 / Task 5: 崩溃恢复场景验证。"""

    def _make_queue(self, db_path: Path) -> Any:
        from ato.merge_queue import MergeQueue

        worktree_mgr = AsyncMock()
        worktree_mgr.project_root = Path("/fake/repo")
        worktree_mgr.cleanup = AsyncMock()

        tq = AsyncMock()
        settings = MagicMock()
        settings.merge_rebase_timeout = 120
        settings.merge_conflict_resolution_max_attempts = 1
        settings.regression_test_command = "echo ok"
        settings.get_regression_commands = MagicMock(return_value=["echo ok"])
        settings.timeout.structured_job = 1800

        queue = MergeQueue(
            db_path=db_path,
            worktree_mgr=worktree_mgr,
            transition_queue=tq,
            settings=settings,
        )
        return queue

    async def test_crash_during_regression_task_failed_recovers_with_freeze(
        self, initialized_db_path: Path
    ) -> None:
        """crash 后 task completed + exit_code != 0 → 冻结 + regression_failure approval。"""
        from ato.models.db import get_pending_approvals, insert_task
        from ato.models.schemas import TaskRecord

        queue = self._make_queue(initialized_db_path)
        await _insert_test_story(initialized_db_path, "s1")

        task_id = "task-reg-crash-fail"
        now = datetime.now(tz=UTC)
        db = await get_connection(initialized_db_path)
        try:
            await enqueue_merge(db, "s1", "a1", _NOW, _NOW)
            await dequeue_next_merge(db)
            await mark_regression_dispatched(db, "s1", task_id)
            await insert_task(
                db,
                TaskRecord(
                    task_id=task_id,
                    story_id="s1",
                    phase="regression",
                    role="qa",
                    cli_tool="codex",
                    status="completed",
                    started_at=now,
                    completed_at=now,
                    exit_code=1,
                    error_message="FAILED test_integration.py",
                ),
            )
            await set_current_merge_story(db, "s1")
        finally:
            await db.close()

        await queue.recover_stale_lock()

        db = await get_connection(initialized_db_path)
        try:
            state = await get_merge_queue_state(db)
            assert state.current_merge_story_id is None, "Lock should be released"
            assert state.frozen is True, "Queue should be frozen"
            assert "regression failed" in (state.frozen_reason or "")

            pending = await get_pending_approvals(db)
            reg_approvals = [a for a in pending if a.approval_type == "regression_failure"]
            assert len(reg_approvals) == 1
        finally:
            await db.close()

    async def test_crash_during_regression_unknown_result_freezes_and_escalates(
        self, initialized_db_path: Path
    ) -> None:
        """crash 后 task 仍在 running 且结果未知 → 冻结 + approval（安全语义）。"""
        from ato.models.db import get_pending_approvals, insert_task
        from ato.models.schemas import TaskRecord

        queue = self._make_queue(initialized_db_path)
        await _insert_test_story(initialized_db_path, "s1")

        task_id = "task-reg-unknown"
        now = datetime.now(tz=UTC)
        db = await get_connection(initialized_db_path)
        try:
            await enqueue_merge(db, "s1", "a1", _NOW, _NOW)
            await dequeue_next_merge(db)
            await mark_regression_dispatched(db, "s1", task_id)
            # Task 仍在 running — 结果未知
            await insert_task(
                db,
                TaskRecord(
                    task_id=task_id,
                    story_id="s1",
                    phase="regression",
                    role="qa",
                    cli_tool="codex",
                    status="running",
                    started_at=now,
                ),
            )
            await set_current_merge_story(db, "s1")
        finally:
            await db.close()

        await queue.recover_stale_lock()

        db = await get_connection(initialized_db_path)
        try:
            state = await get_merge_queue_state(db)
            assert state.current_merge_story_id is None
            assert state.frozen is True
            assert "regression failed for s1" in (state.frozen_reason or ""), (
                "Crash recovery uses _handle_regression_failure, same frozen_reason format"
            )

            # entry 必须被标记为 failed（quarantine），防止 check_regression_completion
            # 在 lingering task 写回结果后绕过人工决策 gate
            entry = await get_merge_queue_entry(db, "s1")
            assert entry is not None
            assert entry.status == "failed", (
                "Entry must be quarantined (failed) to prevent "
                "check_regression_completion from auto-converging"
            )

            pending = await get_pending_approvals(db)
            reg_approvals = [a for a in pending if a.approval_type == "regression_failure"]
            assert len(reg_approvals) == 1

            # payload 合同必须与正常失败路径一致（AC3）
            import json

            payload = json.loads(reg_approvals[0].payload)
            assert payload["story_id"] == "s1", (
                "crash recovery path must include story_id in payload"
            )
            assert set(payload["options"]) == {"revert", "fix_forward", "pause"}
        finally:
            await db.close()

    async def test_crash_during_merge_removes_entry_allows_rebuild(
        self, initialized_db_path: Path
    ) -> None:
        """crash 后 entry 在 merging → 移除 entry + 释放锁（poll cycle 重建 approval）。"""
        queue = self._make_queue(initialized_db_path)
        await _insert_test_story(initialized_db_path, "s1")

        db = await get_connection(initialized_db_path)
        try:
            await enqueue_merge(db, "s1", "a1", _NOW, _NOW)
            await dequeue_next_merge(db)  # → merging
            await set_current_merge_story(db, "s1")
        finally:
            await db.close()

        await queue.recover_stale_lock()

        db = await get_connection(initialized_db_path)
        try:
            state = await get_merge_queue_state(db)
            entry = await get_merge_queue_entry(db, "s1")
            # 锁释放
            assert state.current_merge_story_id is None
            # Entry 被移除（不是标记 failed）
            assert entry is None
            # Queue 不冻结（仅 regression 失败才冻结）
            assert state.frozen is False
        finally:
            await db.close()


class TestRegressionTestExecution:
    """_run_regression_test 正确性验证。"""

    def _make_queue(self, db_path: Path) -> Any:
        from ato.merge_queue import MergeQueue

        worktree_mgr = AsyncMock()
        worktree_mgr.project_root = Path("/fake/repo")

        tq = AsyncMock()
        settings = MagicMock()
        settings.merge_rebase_timeout = 120
        settings.merge_conflict_resolution_max_attempts = 1
        settings.regression_test_command = "echo ok"
        settings.get_regression_commands = MagicMock(return_value=["echo ok"])
        settings.timeout.structured_job = 1800

        queue = MergeQueue(
            db_path=db_path,
            worktree_mgr=worktree_mgr,
            transition_queue=tq,
            settings=settings,
        )
        return queue, worktree_mgr, tq

    async def test_run_regression_test_completed_at_is_datetime(
        self, initialized_db_path: Path
    ) -> None:
        """completed_at 必须是 datetime，不能是 ISO 字符串 — 验证 Fix #1。"""
        from ato.models.db import get_connection, insert_task
        from ato.models.schemas import TaskRecord

        queue, _, _ = self._make_queue(initialized_db_path)
        await _insert_test_story(initialized_db_path, "s1")

        # 手动创建 task record 以便调用 _run_regression_test
        task_id = "test-regression-task-1"
        now = datetime.now(tz=UTC)
        task = TaskRecord(
            task_id=task_id,
            story_id="s1",
            phase="regression",
            role="qa",
            cli_tool="codex",
            status="running",
            expected_artifact="regression_test",
            started_at=now,
        )
        db = await get_connection(initialized_db_path)
        try:
            await insert_task(db, task)
        finally:
            await db.close()

        # Mock subprocess to succeed quickly
        mock_proc = _make_mock_proc(returncode=0, stdout_data=b"ok")

        with patch("ato.merge_queue.asyncio.create_subprocess_exec", return_value=mock_proc):
            await queue._run_regression_test("s1", task_id)

        # Verify task was updated without TypeError
        from ato.models.db import get_tasks_by_story

        db = await get_connection(initialized_db_path)
        try:
            tasks = await get_tasks_by_story(db, "s1")
            regression_task = next(t for t in tasks if t.task_id == task_id)
            assert regression_task.status == "completed"
            assert regression_task.exit_code == 0
            assert regression_task.completed_at is not None
        finally:
            await db.close()

    async def test_run_regression_test_uses_shell_aware_split(
        self, initialized_db_path: Path
    ) -> None:
        """带引号和空格的 regression_test_command 必须被正确解析。"""
        from ato.models.db import get_connection, insert_task
        from ato.models.schemas import TaskRecord

        queue, _, _ = self._make_queue(initialized_db_path)
        queue._settings.regression_test_command = 'pytest --cov="src dir" "tests/unit/test file.py"'
        queue._settings.get_regression_commands = MagicMock(
            return_value=['pytest --cov="src dir" "tests/unit/test file.py"']
        )
        await _insert_test_story(initialized_db_path, "s1")

        task_id = "test-regression-task-quoted"
        now = datetime.now(tz=UTC)
        task = TaskRecord(
            task_id=task_id,
            story_id="s1",
            phase="regression",
            role="qa",
            cli_tool="codex",
            status="running",
            expected_artifact="regression_test",
            started_at=now,
        )
        db = await get_connection(initialized_db_path)
        try:
            await insert_task(db, task)
        finally:
            await db.close()

        mock_proc = _make_mock_proc(returncode=0, stdout_data=b"ok")

        with patch(
            "ato.merge_queue.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ) as mock_exec:
            await queue._run_regression_test("s1", task_id)

        assert mock_exec.await_args is not None
        assert mock_exec.await_args.args == (
            "pytest",
            "--cov=src dir",
            "tests/unit/test file.py",
        )

    async def test_check_regression_completion_unfreezes_recovery_story(
        self, initialized_db_path: Path
    ) -> None:
        """fix_forward recovery story 二次回归通过后应自动解冻 queue。"""
        from ato.models.db import insert_task
        from ato.models.schemas import TaskRecord

        queue, worktree_mgr, tq = self._make_queue(initialized_db_path)
        await _insert_test_story(initialized_db_path, "s1")

        task_id = "task-reg-1"
        now = datetime.now(tz=UTC)
        db = await get_connection(initialized_db_path)
        try:
            await enqueue_merge(db, "s1", "a1", _NOW, _NOW)
            await dequeue_next_merge(db)
            await mark_regression_dispatched(db, "s1", task_id)
            await insert_task(
                db,
                TaskRecord(
                    task_id=task_id,
                    story_id="s1",
                    phase="regression",
                    role="qa",
                    cli_tool="codex",
                    status="completed",
                    started_at=now,
                    completed_at=now,
                    exit_code=0,
                ),
            )
            await set_current_merge_story(db, "s1")
            await set_merge_queue_frozen(
                db,
                frozen=True,
                reason="regression failed for s1",
            )
        finally:
            await db.close()

        await queue.check_regression_completion()

        db = await get_connection(initialized_db_path)
        try:
            state = await get_merge_queue_state(db)
            entry = await get_merge_queue_entry(db, "s1")
            assert state.frozen is False
            assert state.current_merge_story_id is None
            assert entry is not None
            assert entry.status == "merged"
        finally:
            await db.close()

        tq.submit.assert_awaited_once()
        event = tq.submit.call_args[0][0]
        assert event.event_name == "regression_pass"
        worktree_mgr.cleanup.assert_awaited_once_with("s1")

    async def test_check_regression_completion_processes_only_one_entry_per_poll(
        self, initialized_db_path: Path
    ) -> None:
        """异常情况下有多个 regression_pending 时，每轮只收敛一个 entry。"""
        from ato.models.db import insert_task
        from ato.models.schemas import TaskRecord

        queue, worktree_mgr, tq = self._make_queue(initialized_db_path)
        await _insert_test_story(initialized_db_path, "s1")
        await _insert_test_story(initialized_db_path, "s2")

        now = datetime.now(tz=UTC)
        db = await get_connection(initialized_db_path)
        try:
            for story_id, task_id, approved_at in (
                ("s1", "task-reg-1", _EARLIER),
                ("s2", "task-reg-2", _NOW),
            ):
                await enqueue_merge(db, story_id, f"{story_id}-approval", approved_at, _NOW)
                await dequeue_next_merge(db)
                await mark_regression_dispatched(db, story_id, task_id)
                await insert_task(
                    db,
                    TaskRecord(
                        task_id=task_id,
                        story_id=story_id,
                        phase="regression",
                        role="qa",
                        cli_tool="codex",
                        status="completed",
                        started_at=now,
                        completed_at=now,
                        exit_code=0,
                    ),
                )
            await set_current_merge_story(db, "s1")
        finally:
            await db.close()

        await queue.check_regression_completion()

        db = await get_connection(initialized_db_path)
        try:
            entry_s1 = await get_merge_queue_entry(db, "s1")
            entry_s2 = await get_merge_queue_entry(db, "s2")
            state = await get_merge_queue_state(db)
            assert entry_s1 is not None
            assert entry_s2 is not None
            assert entry_s1.status == "merged"
            assert entry_s2.status == "regression_pending"
            assert state.current_merge_story_id is None
        finally:
            await db.close()

        assert tq.submit.await_count == 1
        worktree_mgr.cleanup.assert_awaited_once_with("s1")

    async def test_missing_pre_merge_head_aborts_merge_and_marks_failed(
        self, initialized_db_path: Path
    ) -> None:
        """缺少 pre_merge_head 时不得继续 merge 到 main。"""
        queue, worktree_mgr, _ = self._make_queue(initialized_db_path)
        await _insert_test_story(initialized_db_path, "s1")

        db = await get_connection(initialized_db_path)
        try:
            await enqueue_merge(db, "s1", "a1", _NOW, _NOW)
            await dequeue_next_merge(db)
            await set_current_merge_story(db, "s1")
        finally:
            await db.close()

        worktree_mgr.rebase_onto_main = AsyncMock(return_value=(True, ""))
        worktree_mgr.get_main_head = AsyncMock(return_value=None)
        worktree_mgr.merge_to_main = AsyncMock(return_value=(True, ""))

        await queue._run_merge_worker("s1")

        db = await get_connection(initialized_db_path)
        try:
            entry = await get_merge_queue_entry(db, "s1")
            state = await get_merge_queue_state(db)
            assert entry is not None
            assert entry.status == "failed"
            assert state.current_merge_story_id is None
        finally:
            await db.close()

        worktree_mgr.merge_to_main.assert_not_awaited()

    async def test_mark_merge_failed_still_clears_lock_when_status_update_fails(
        self, initialized_db_path: Path
    ) -> None:
        """cleanup 路径中 complete_merge 失败也必须尝试清锁。"""
        queue, _, _ = self._make_queue(initialized_db_path)
        fake_db = AsyncMock()

        with (
            patch(
                "ato.models.db.get_connection",
                new_callable=AsyncMock,
                return_value=fake_db,
            ),
            patch(
                "ato.models.db.complete_merge",
                new_callable=AsyncMock,
                side_effect=RuntimeError("db locked"),
            ),
            patch(
                "ato.models.db.set_current_merge_story",
                new_callable=AsyncMock,
            ) as mock_clear_lock,
        ):
            await queue._mark_merge_failed_and_release_lock("s1", context="test")

        mock_clear_lock.assert_awaited_once_with(fake_db, None)
        fake_db.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# Story 4.5: 端到端集成验证追加测试
# ---------------------------------------------------------------------------


class TestHappyPathMergeRegressionPassToDone:
    """merge → regression pass → merged → done → cleanup 端到端流程（AC1, AC2）。"""

    def _make_queue(self, db_path: Path) -> tuple[Any, Any, Any]:
        from ato.merge_queue import MergeQueue

        worktree_mgr = AsyncMock()
        worktree_mgr.project_root = Path("/fake/repo")
        worktree_mgr.cleanup = AsyncMock()

        tq = AsyncMock()
        settings = MagicMock()
        settings.merge_rebase_timeout = 120
        settings.merge_conflict_resolution_max_attempts = 1
        settings.regression_test_command = "echo ok"
        settings.get_regression_commands = MagicMock(return_value=["echo ok"])
        settings.timeout.structured_job = 1800

        queue = MergeQueue(
            db_path=db_path,
            worktree_mgr=worktree_mgr,
            transition_queue=tq,
            settings=settings,
        )
        return queue, worktree_mgr, tq

    async def test_complete_regression_pass_full_flow(
        self, initialized_db_path: Path
    ) -> None:
        """_complete_regression_pass: transition + merge merged + lock released + cleanup。"""
        from ato.models.db import insert_task
        from ato.models.schemas import TaskRecord

        queue, worktree_mgr, tq = self._make_queue(initialized_db_path)
        await _insert_test_story(initialized_db_path, "s1")

        task_id = "task-reg-happy"
        now = datetime.now(tz=UTC)
        db = await get_connection(initialized_db_path)
        try:
            await enqueue_merge(db, "s1", "a1", _NOW, _NOW)
            await dequeue_next_merge(db)
            await mark_regression_dispatched(db, "s1", task_id)
            await insert_task(
                db,
                TaskRecord(
                    task_id=task_id,
                    story_id="s1",
                    phase="regression",
                    role="qa",
                    cli_tool="codex",
                    status="completed",
                    started_at=now,
                    completed_at=now,
                    exit_code=0,
                ),
            )
            await set_current_merge_story(db, "s1")
        finally:
            await db.close()

        # 执行 check_regression_completion（模拟 poll cycle）
        await queue.check_regression_completion()

        # 验证完整闭环
        db = await get_connection(initialized_db_path)
        try:
            entry = await get_merge_queue_entry(db, "s1")
            state = await get_merge_queue_state(db)
            assert entry is not None
            assert entry.status == "merged", "Entry should be marked merged"
            assert state.current_merge_story_id is None, "Lock should be released"
            assert state.frozen is False, "Queue should not be frozen"
        finally:
            await db.close()

        # 验证 regression_pass event 提交到 TQ
        tq.submit.assert_awaited_once()
        event = tq.submit.call_args[0][0]
        assert event.event_name == "regression_pass"
        assert event.story_id == "s1"

        # 验证 worktree cleanup
        worktree_mgr.cleanup.assert_awaited_once_with("s1")


class TestRebaseConflictDoesNotFreezeQueue:
    """AC5: rebase 冲突不触发 merge queue 冻结。"""

    def _make_queue(self, db_path: Path) -> tuple[Any, Any, Any]:
        from ato.merge_queue import MergeQueue

        worktree_mgr = AsyncMock()
        worktree_mgr.project_root = Path("/fake/repo")
        worktree_mgr.get_conflict_files = AsyncMock(return_value=["a.py"])
        worktree_mgr.abort_rebase = AsyncMock()

        tq = AsyncMock()
        settings = MagicMock()
        settings.merge_rebase_timeout = 120
        settings.merge_conflict_resolution_max_attempts = 0
        settings.regression_test_command = "echo ok"
        settings.get_regression_commands = MagicMock(return_value=["echo ok"])
        settings.timeout.structured_job = 1800

        queue = MergeQueue(
            db_path=db_path,
            worktree_mgr=worktree_mgr,
            transition_queue=tq,
            settings=settings,
        )
        return queue, worktree_mgr, tq

    async def test_rebase_conflict_creates_approval_without_freezing(
        self, initialized_db_path: Path
    ) -> None:
        """rebase 冲突创建 approval 但不冻结 queue（AC5）。"""
        queue, _, _ = self._make_queue(initialized_db_path)
        await _insert_test_story(initialized_db_path, "s1")

        result = await queue._handle_rebase_conflict("s1", "CONFLICT in a.py")
        assert result is False  # 需要人工介入

        from ato.models.db import get_pending_approvals

        db = await get_connection(initialized_db_path)
        try:
            state = await get_merge_queue_state(db)
            assert state.frozen is False, "Queue must NOT freeze on rebase conflict"

            pending = await get_pending_approvals(db)
            conflict_approvals = [a for a in pending if a.approval_type == "rebase_conflict"]
            assert len(conflict_approvals) == 1
        finally:
            await db.close()


class TestRegressionFailurePayloadContent:
    """AC3: regression failure approval payload 内容验证。"""

    def _make_queue(self, db_path: Path) -> Any:
        from ato.merge_queue import MergeQueue

        worktree_mgr = AsyncMock()
        worktree_mgr.project_root = Path("/fake/repo")

        tq = AsyncMock()
        settings = MagicMock()
        settings.merge_rebase_timeout = 120
        settings.merge_conflict_resolution_max_attempts = 1
        settings.regression_test_command = "echo ok"
        settings.get_regression_commands = MagicMock(return_value=["echo ok"])
        settings.timeout.structured_job = 1800

        queue = MergeQueue(
            db_path=db_path,
            worktree_mgr=worktree_mgr,
            transition_queue=tq,
            settings=settings,
        )
        return queue

    async def test_regression_failure_payload_includes_story_id_and_options(
        self, initialized_db_path: Path
    ) -> None:
        """approval payload 包含 story_id 和三个选项。"""
        import json

        queue = self._make_queue(initialized_db_path)
        await _insert_test_story(initialized_db_path, "s1")

        db = await get_connection(initialized_db_path)
        try:
            await enqueue_merge(db, "s1", "a1", _NOW, _NOW)
            _ = await dequeue_next_merge(db)
        finally:
            await db.close()

        await queue._handle_regression_failure("s1")

        from ato.models.db import get_pending_approvals

        db = await get_connection(initialized_db_path)
        try:
            pending = await get_pending_approvals(db)
            reg = [a for a in pending if a.approval_type == "regression_failure"]
            assert len(reg) == 1
            payload = json.loads(reg[0].payload)
            assert payload["story_id"] == "s1"
            assert set(payload["options"]) == {"revert", "fix_forward", "pause"}
        finally:
            await db.close()

    async def test_regression_failure_payload_includes_test_output_summary(
        self, initialized_db_path: Path
    ) -> None:
        """有 test_output_summary 时 payload 包含摘要。"""
        import json

        queue = self._make_queue(initialized_db_path)
        await _insert_test_story(initialized_db_path, "s1")

        db = await get_connection(initialized_db_path)
        try:
            await enqueue_merge(db, "s1", "a1", _NOW, _NOW)
            _ = await dequeue_next_merge(db)
        finally:
            await db.close()

        await queue._handle_regression_failure(
            "s1",
            test_output_summary="FAILED tests/test_foo.py::test_bar - AssertionError",
        )

        from ato.models.db import get_pending_approvals

        db = await get_connection(initialized_db_path)
        try:
            pending = await get_pending_approvals(db)
            reg = [a for a in pending if a.approval_type == "regression_failure"]
            assert len(reg) == 1
            payload = json.loads(reg[0].payload)
            assert "test_output_summary" in payload
            assert "FAILED" in payload["test_output_summary"]
        finally:
            await db.close()

    async def test_check_regression_completion_passes_error_message_to_payload(
        self, initialized_db_path: Path
    ) -> None:
        """check_regression_completion 将 task.error_message 传入 approval payload。"""
        import json

        from ato.models.db import insert_task
        from ato.models.schemas import TaskRecord

        queue = self._make_queue(initialized_db_path)
        await _insert_test_story(initialized_db_path, "s1")

        task_id = "task-reg-fail"
        now = datetime.now(tz=UTC)
        db = await get_connection(initialized_db_path)
        try:
            await enqueue_merge(db, "s1", "a1", _NOW, _NOW)
            await dequeue_next_merge(db)
            await mark_regression_dispatched(db, "s1", task_id)
            await insert_task(
                db,
                TaskRecord(
                    task_id=task_id,
                    story_id="s1",
                    phase="regression",
                    role="qa",
                    cli_tool="codex",
                    status="completed",
                    started_at=now,
                    completed_at=now,
                    exit_code=1,
                    error_message="FAILED test_foo.py::test_bar",
                ),
            )
            await set_current_merge_story(db, "s1")
        finally:
            await db.close()

        await queue.check_regression_completion()

        from ato.models.db import get_pending_approvals

        db = await get_connection(initialized_db_path)
        try:
            pending = await get_pending_approvals(db)
            reg = [a for a in pending if a.approval_type == "regression_failure"]
            assert len(reg) == 1
            payload = json.loads(reg[0].payload)
            assert "test_output_summary" in payload
            assert "FAILED" in payload["test_output_summary"]
        finally:
            await db.close()

    async def test_run_regression_test_stores_stderr_on_failure(
        self, initialized_db_path: Path
    ) -> None:
        """regression test 失败时 stderr 存入 task.error_message。"""
        from ato.models.db import insert_task
        from ato.models.schemas import TaskRecord

        queue = self._make_queue(initialized_db_path)
        await _insert_test_story(initialized_db_path, "s1")

        task_id = "task-reg-stderr"
        now = datetime.now(tz=UTC)
        db = await get_connection(initialized_db_path)
        try:
            await insert_task(
                db,
                TaskRecord(
                    task_id=task_id,
                    story_id="s1",
                    phase="regression",
                    role="qa",
                    cli_tool="codex",
                    status="running",
                    expected_artifact="regression_test",
                    started_at=now,
                ),
            )
        finally:
            await db.close()

        mock_proc = _make_mock_proc(
            returncode=1,
            stderr_data=b"FAILED test_bar.py::test_x - AssertionError",
        )

        with patch("ato.merge_queue.asyncio.create_subprocess_exec", return_value=mock_proc):
            await queue._run_regression_test("s1", task_id)

        db = await get_connection(initialized_db_path)
        try:
            cursor = await db.execute(
                "SELECT error_message FROM tasks WHERE task_id = ?",
                (task_id,),
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] is not None
            assert "FAILED" in row[0]
        finally:
            await db.close()

    async def test_run_regression_test_captures_stdout_on_failure(
        self, initialized_db_path: Path
    ) -> None:
        """失败详情在 stdout 时（如 pytest）也应被捕获到 error_message。"""
        from ato.models.db import insert_task
        from ato.models.schemas import TaskRecord

        queue = self._make_queue(initialized_db_path)
        await _insert_test_story(initialized_db_path, "s1")

        task_id = "task-reg-stdout"
        now = datetime.now(tz=UTC)
        db = await get_connection(initialized_db_path)
        try:
            await insert_task(
                db,
                TaskRecord(
                    task_id=task_id,
                    story_id="s1",
                    phase="regression",
                    role="qa",
                    cli_tool="codex",
                    status="running",
                    expected_artifact="regression_test",
                    started_at=now,
                ),
            )
        finally:
            await db.close()

        # pytest 把失败详情输出到 stdout，stderr 为空
        mock_proc = _make_mock_proc(
            returncode=1,
            stdout_data=b"FAILED tests/test_foo.py::test_bar - assert 1 == 2",
        )

        with patch("ato.merge_queue.asyncio.create_subprocess_exec", return_value=mock_proc):
            await queue._run_regression_test("s1", task_id)

        db = await get_connection(initialized_db_path)
        try:
            cursor = await db.execute(
                "SELECT error_message FROM tasks WHERE task_id = ?",
                (task_id,),
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] is not None
            assert "FAILED" in row[0], (
                "stdout failure details must be captured when stderr is empty"
            )
            assert "assert 1 == 2" in row[0]
        finally:
            await db.close()

    async def test_run_regression_test_combines_stdout_and_stderr(
        self, initialized_db_path: Path
    ) -> None:
        """stdout 和 stderr 都有内容时合并到 error_message。"""
        from ato.models.db import insert_task
        from ato.models.schemas import TaskRecord

        queue = self._make_queue(initialized_db_path)
        await _insert_test_story(initialized_db_path, "s1")

        task_id = "task-reg-combined"
        now = datetime.now(tz=UTC)
        db = await get_connection(initialized_db_path)
        try:
            await insert_task(
                db,
                TaskRecord(
                    task_id=task_id,
                    story_id="s1",
                    phase="regression",
                    role="qa",
                    cli_tool="codex",
                    status="running",
                    expected_artifact="regression_test",
                    started_at=now,
                ),
            )
        finally:
            await db.close()

        mock_proc = _make_mock_proc(
            returncode=1,
            stdout_data=b"FAILED test_bar",
            stderr_data=b"ERROR: coverage below threshold",
        )

        with patch("ato.merge_queue.asyncio.create_subprocess_exec", return_value=mock_proc):
            await queue._run_regression_test("s1", task_id)

        db = await get_connection(initialized_db_path)
        try:
            cursor = await db.execute(
                "SELECT error_message FROM tasks WHERE task_id = ?",
                (task_id,),
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] is not None
            # 两个来源都应该出现
            assert "coverage below threshold" in row[0]
            assert "FAILED test_bar" in row[0]
        finally:
            await db.close()


# ---------------------------------------------------------------------------
# Story 8.4: 多命令 regression runner 测试
# ---------------------------------------------------------------------------


class TestMultiCommandRegression:
    """AC3-AC5: 多命令 regression 顺序执行、失败中止、独立超时。"""

    def _make_queue(self, db_path: Path) -> tuple[Any, Any, Any]:
        from ato.merge_queue import MergeQueue

        worktree_mgr = AsyncMock()
        worktree_mgr.project_root = Path("/fake/repo")

        tq = AsyncMock()
        settings = MagicMock()
        settings.merge_rebase_timeout = 120
        settings.merge_conflict_resolution_max_attempts = 1
        settings.regression_test_command = "echo ok"
        settings.get_regression_commands = MagicMock(return_value=["echo ok"])
        settings.timeout.structured_job = 1800
        # 使用 get_regression_commands 返回多命令
        settings.get_regression_commands = MagicMock(return_value=[
            "uv run pytest tests/unit/",
            "uv run pytest tests/integration/",
            "uv run pytest tests/smoke/",
        ])

        queue = MergeQueue(
            db_path=db_path,
            worktree_mgr=worktree_mgr,
            transition_queue=tq,
            settings=settings,
        )
        return queue, worktree_mgr, tq

    async def _setup_task(self, db_path: Path, story_id: str = "s1") -> str:
        """辅助：创建 story 和 running 状态的 regression task。"""
        from ato.models.db import get_connection, insert_task
        from ato.models.schemas import TaskRecord

        await _insert_test_story(db_path, story_id)
        task_id = f"task-multi-{story_id}"
        now = datetime.now(tz=UTC)
        task = TaskRecord(
            task_id=task_id,
            story_id=story_id,
            phase="regression",
            role="qa",
            cli_tool="codex",
            status="running",
            expected_artifact="regression_test",
            started_at=now,
        )
        db = await get_connection(db_path)
        try:
            await insert_task(db, task)
        finally:
            await db.close()
        return task_id

    async def test_all_commands_succeed(self, initialized_db_path: Path) -> None:
        """AC3/AC5: 所有命令成功 → task completed, exit_code=0。"""
        queue, _, _ = self._make_queue(initialized_db_path)
        task_id = await self._setup_task(initialized_db_path)

        call_count = 0

        async def fake_exec(*args: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            return _make_mock_proc(returncode=0, stdout_data=b"ok")

        with patch("ato.merge_queue.asyncio.create_subprocess_exec", side_effect=fake_exec):
            await queue._run_regression_test("s1", task_id)

        assert call_count == 3, "All 3 commands should be executed"

        from ato.models.db import get_tasks_by_story

        db = await get_connection(initialized_db_path)
        try:
            tasks = await get_tasks_by_story(db, "s1")
            reg_task = next(t for t in tasks if t.task_id == task_id)
            assert reg_task.status == "completed"
            assert reg_task.exit_code == 0
            assert reg_task.error_message is None
        finally:
            await db.close()

    async def test_second_command_fails_short_circuits(self, initialized_db_path: Path) -> None:
        """AC4: 第 2 条命令失败 → 第 3 条不执行，error_message 包含序号和命令。"""
        queue, _, _ = self._make_queue(initialized_db_path)
        task_id = await self._setup_task(initialized_db_path)

        call_count = 0

        async def fake_exec(*args: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                return _make_mock_proc(
                    returncode=1, stdout_data=b"FAILED test_x", stderr_data=b"err output",
                )
            return _make_mock_proc(returncode=0, stdout_data=b"ok")

        with patch("ato.merge_queue.asyncio.create_subprocess_exec", side_effect=fake_exec):
            await queue._run_regression_test("s1", task_id)

        assert call_count == 2, "Third command should NOT be executed"

        db = await get_connection(initialized_db_path)
        try:
            cursor = await db.execute(
                "SELECT status, exit_code, error_message FROM tasks WHERE task_id = ?",
                (task_id,),
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "completed"  # status
            assert row[1] != 0  # exit_code
            error_msg = row[2]
            assert error_msg is not None
            # AC4: error_message 包含失败命令的 1-based 序号和命令文本
            assert "2" in error_msg
            assert "uv run pytest tests/integration/" in error_msg
        finally:
            await db.close()

    async def test_each_command_uses_independent_shlex_split(
        self, initialized_db_path: Path
    ) -> None:
        """AC5: 每条命令独立使用 shlex.split() 解析。"""
        queue, _, _ = self._make_queue(initialized_db_path)
        queue._settings.get_regression_commands = MagicMock(
            return_value=['pytest --cov="src dir"', 'pytest "tests/unit/test file.py"']
        )
        task_id = await self._setup_task(initialized_db_path)

        exec_calls: list[tuple[Any, ...]] = []

        async def fake_exec(*args: Any, **kwargs: Any) -> Any:
            exec_calls.append(args)
            return _make_mock_proc(returncode=0, stdout_data=b"ok")

        with patch("ato.merge_queue.asyncio.create_subprocess_exec", side_effect=fake_exec):
            await queue._run_regression_test("s1", task_id)

        assert len(exec_calls) == 2
        # 第一条命令
        assert exec_calls[0] == ("pytest", "--cov=src dir")
        # 第二条命令
        assert exec_calls[1] == ("pytest", "tests/unit/test file.py")

    async def test_singular_fallback_still_works(self, initialized_db_path: Path) -> None:
        """AC2: 仅 singular 配置时行为不变。"""
        queue, _, _ = self._make_queue(initialized_db_path)
        queue._settings.get_regression_commands = MagicMock(return_value=["echo ok"])
        task_id = await self._setup_task(initialized_db_path)

        call_count = 0

        async def fake_exec(*args: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            return _make_mock_proc(returncode=0, stdout_data=b"ok")

        with patch("ato.merge_queue.asyncio.create_subprocess_exec", side_effect=fake_exec):
            await queue._run_regression_test("s1", task_id)

        assert call_count == 1

        from ato.models.db import get_tasks_by_story

        db = await get_connection(initialized_db_path)
        try:
            tasks = await get_tasks_by_story(db, "s1")
            reg_task = next(t for t in tasks if t.task_id == task_id)
            assert reg_task.status == "completed"
            assert reg_task.exit_code == 0
        finally:
            await db.close()

    async def test_error_message_within_1000_chars(self, initialized_db_path: Path) -> None:
        """AC4: 失败摘要遵循 <=1000 字符截断合同。"""
        queue, _, _ = self._make_queue(initialized_db_path)
        task_id = await self._setup_task(initialized_db_path)

        async def fake_exec(*args: Any, **kwargs: Any) -> Any:
            # 生成超长输出
            return _make_mock_proc(
                returncode=1, stdout_data=b"X" * 2000, stderr_data=b"Y" * 2000,
            )

        with patch("ato.merge_queue.asyncio.create_subprocess_exec", side_effect=fake_exec):
            await queue._run_regression_test("s1", task_id)

        db = await get_connection(initialized_db_path)
        try:
            cursor = await db.execute(
                "SELECT error_message FROM tasks WHERE task_id = ?",
                (task_id,),
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] is not None
            assert len(row[0]) <= 1000
        finally:
            await db.close()

    async def test_timeout_captures_partial_output_via_drain(
        self, initialized_db_path: Path
    ) -> None:
        """AC4/AC5: 超时 → error_message 包含 drain 缓冲区中的部分输出。

        架构：_drain tasks 在超时前持续累积 pipe 数据到外部 bytearray，
        超时后取消 task 但 bytearray 已有数据可用。不依赖超时后读 pipe。
        """
        import asyncio as _aio

        queue, _, _ = self._make_queue(initialized_db_path)
        task_id = await self._setup_task(initialized_db_path)

        exec_count = 0

        async def fake_exec(*args: Any, **kwargs: Any) -> Any:
            nonlocal exec_count
            exec_count += 1

            mock_proc = MagicMock()
            mock_proc.returncode = None

            # 模拟 stdout/stderr pipe：先输出部分数据，然后挂起
            # 这模拟了真实场景：子进程已输出部分内容但未退出
            async def make_reader(data: bytes) -> _aio.StreamReader:
                reader = _aio.StreamReader()
                reader.feed_data(data)
                # 不 feed_eof → read 会在读完 data 后等待
                return reader

            mock_proc.stdout = _aio.StreamReader()
            mock_proc.stdout.feed_data(b"partial test output")
            # 不 feed_eof，模拟进程仍在运行

            mock_proc.stderr = _aio.StreamReader()
            mock_proc.stderr.feed_data(b"partial error log")

            # proc.wait() 永不返回（模拟超时）
            wait_future: _aio.Future[int] = _aio.get_event_loop().create_future()
            mock_proc.wait = MagicMock(return_value=wait_future)

            # kill 后标记退出 + 关闭 pipe
            def do_kill() -> None:
                mock_proc.returncode = -9
                mock_proc.stdout.feed_eof()
                mock_proc.stderr.feed_eof()
                if not wait_future.done():
                    wait_future.set_result(-9)

            mock_proc.kill = MagicMock(side_effect=do_kill)
            mock_proc.terminate = MagicMock(side_effect=do_kill)

            return mock_proc

        with (
            patch(
                "ato.merge_queue.asyncio.create_subprocess_exec",
                side_effect=fake_exec,
            ),
            patch("ato.adapters.base.cleanup_process", new_callable=AsyncMock),
        ):
            # 设置极短超时触发 timeout
            queue._settings.timeout.structured_job = 0.1
            await queue._run_regression_test("s1", task_id)

        assert exec_count == 1, "Timed out on first → second should NOT run"

        db = await get_connection(initialized_db_path)
        try:
            cursor = await db.execute(
                "SELECT status, exit_code, error_message FROM tasks WHERE task_id = ?",
                (task_id,),
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "failed"
            assert row[1] == -1
            error_msg = row[2]
            # AC4: 序号 + 命令文本
            assert "1" in error_msg
            assert "timed out" in error_msg.lower()
            # AC4: 超时前已累积的 stdout/stderr 摘要
            assert "partial test output" in error_msg, (
                f"Drain buffer should capture partial stdout. Got: {error_msg}"
            )
            assert "partial error log" in error_msg, (
                f"Drain buffer should capture partial stderr. Got: {error_msg}"
            )
        finally:
            await db.close()
