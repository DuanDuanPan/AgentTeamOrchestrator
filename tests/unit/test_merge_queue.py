"""test_merge_queue — MergeQueue 核心类与 DB CRUD 测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
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

    async def test_dequeue_order_uses_approved_at_then_id(
        self, initialized_db_path: Path
    ) -> None:
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

    async def test_dequeue_returns_none_when_empty(
        self, initialized_db_path: Path
    ) -> None:
        db = await get_connection(initialized_db_path)
        try:
            entry = await dequeue_next_merge(db)
            assert entry is None
        finally:
            await db.close()

    async def test_mark_regression_dispatched(
        self, initialized_db_path: Path
    ) -> None:
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

    async def test_merge_queue_state_singleton(
        self, initialized_db_path: Path
    ) -> None:
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

    async def test_set_current_merge_story(
        self, initialized_db_path: Path
    ) -> None:
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

    async def test_remove_from_merge_queue(
        self, initialized_db_path: Path
    ) -> None:
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

    def _make_queue(
        self, db_path: Path
    ) -> tuple[MagicMock, MagicMock, MagicMock]:
        """创建 MergeQueue 及其 mock 依赖。"""
        from ato.merge_queue import MergeQueue

        worktree_mgr = AsyncMock(spec=["rebase_onto_main", "merge_to_main",
                                       "cleanup", "get_path", "continue_rebase",
                                       "abort_rebase", "get_conflict_files",
                                       "project_root"])
        worktree_mgr.project_root = Path("/fake/repo")

        tq = AsyncMock()
        settings = MagicMock()
        settings.merge_rebase_timeout = 120
        settings.merge_conflict_resolution_max_attempts = 1
        settings.regression_test_command = "echo ok"
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

    async def test_enqueue_keeps_frozen_queue_frozen(
        self, initialized_db_path: Path
    ) -> None:
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

    async def test_process_next_frozen_returns_false(
        self, initialized_db_path: Path
    ) -> None:
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

    async def test_process_next_busy_returns_false(
        self, initialized_db_path: Path
    ) -> None:
        queue, _, _ = self._make_queue(initialized_db_path)

        # Set a current merge story
        db = await get_connection(initialized_db_path)
        try:
            await set_current_merge_story(db, "some-story")
        finally:
            await db.close()

        result = await queue.process_next()
        assert result is False

    async def test_process_next_empty_returns_false(
        self, initialized_db_path: Path
    ) -> None:
        queue, _, _ = self._make_queue(initialized_db_path)
        result = await queue.process_next()
        assert result is False

    async def test_process_next_dequeues_and_starts_worker(
        self, initialized_db_path: Path
    ) -> None:
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

    async def test_regression_fail_freezes_queue(
        self, initialized_db_path: Path
    ) -> None:
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

    async def test_regression_fail_creates_urgent_approval(
        self, initialized_db_path: Path
    ) -> None:
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

    async def test_unfreeze_restores_processing(
        self, initialized_db_path: Path
    ) -> None:
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

    async def test_concurrent_enqueue_serialized(
        self, initialized_db_path: Path
    ) -> None:
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

    async def test_rebase_conflict_escalates_on_failure(
        self, initialized_db_path: Path
    ) -> None:
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

    async def test_precommit_failure_creates_approval(
        self, initialized_db_path: Path
    ) -> None:
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

    def _make_queue(self, db_path: Path) -> tuple:
        from ato.merge_queue import MergeQueue

        worktree_mgr = AsyncMock()
        worktree_mgr.project_root = Path("/fake/repo")
        tq = AsyncMock()
        settings = MagicMock()
        settings.merge_rebase_timeout = 120
        settings.merge_conflict_resolution_max_attempts = 1
        settings.regression_test_command = "echo ok"
        settings.timeout.structured_job = 1800

        queue = MergeQueue(
            db_path=db_path,
            worktree_mgr=worktree_mgr,
            transition_queue=tq,
            settings=settings,
        )
        return queue

    async def test_clears_stale_lock_no_entry(
        self, initialized_db_path: Path
    ) -> None:
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

    async def test_clears_stale_lock_completed_entry(
        self, initialized_db_path: Path
    ) -> None:
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

    async def test_merging_entry_removed_and_lock_released(
        self, initialized_db_path: Path
    ) -> None:
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

    async def test_noop_when_no_lock(
        self, initialized_db_path: Path
    ) -> None:
        """current_merge_story_id 为 None 时无操作。"""
        queue = self._make_queue(initialized_db_path)
        await queue.recover_stale_lock()

        db = await get_connection(initialized_db_path)
        try:
            state = await get_merge_queue_state(db)
            assert state.current_merge_story_id is None
        finally:
            await db.close()


class TestRegressionTestExecution:
    """_run_regression_test 正确性验证。"""

    def _make_queue(
        self, db_path: Path
    ) -> tuple:
        from ato.merge_queue import MergeQueue

        worktree_mgr = AsyncMock()
        worktree_mgr.project_root = Path("/fake/repo")

        tq = AsyncMock()
        settings = MagicMock()
        settings.merge_rebase_timeout = 120
        settings.merge_conflict_resolution_max_attempts = 1
        settings.regression_test_command = "echo ok"
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
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))

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
        queue._settings.regression_test_command = (
            'pytest --cov="src dir" "tests/unit/test file.py"'
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

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))

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
