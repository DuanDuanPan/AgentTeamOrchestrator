"""test_merge_queue — MergeQueue 核心类与 DB CRUD 测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ato.config import ATOSettings, PhaseTestPolicyConfig, TestLayerConfig
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
from ato.models.schemas import CLIAdapterError, StoryRecord, WorktreePreflightResult

_NOW = datetime.now(tz=UTC)
_EARLIER = _NOW - timedelta(minutes=5)


def _preflight_result(
    story_id: str,
    *,
    passed: bool = True,
    failure_reason: str | None = None,
) -> WorktreePreflightResult:
    return WorktreePreflightResult.model_validate(
        {
            "story_id": story_id,
            "gate_type": "pre_merge",
            "passed": passed,
            "base_ref": "origin/main",
            "base_sha": "base",
            "head_sha": "head",
            "porcelain_output": "?? dirty.py\n" if failure_reason else "",
            "diffstat": " dirty.py | 1 +\n" if passed else "",
            "changed_files": ["dirty.py"] if passed else [],
            "failure_reason": failure_reason,
            "checked_at": datetime.now(tz=UTC),
        }
    )


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

    async def test_set_current_merge_story_self_heals_missing_singleton(
        self, initialized_db_path: Path
    ) -> None:
        db = await get_connection(initialized_db_path)
        try:
            await db.execute("DELETE FROM merge_queue_state")
            await db.commit()

            await set_current_merge_story(db, "s1")
            state = await get_merge_queue_state(db)
            assert state.current_merge_story_id == "s1"
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
                "preflight_check",
                "project_root",
            ]
        )
        worktree_mgr.project_root = Path("/fake/repo")
        worktree_mgr.preflight_check = AsyncMock(
            side_effect=lambda story_id, _gate_type: _preflight_result(story_id)
        )

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

    def test_conflict_resolution_prompt_forbids_dangerous_git_commands(self) -> None:
        from ato.merge_queue import _build_conflict_resolution_prompt

        prompt = _build_conflict_resolution_prompt(["a.py"], "CONFLICT", 0)

        assert "Do NOT create new commits" in prompt
        for command in (
            "git reset",
            "git checkout",
            "git switch",
            "git stash",
            "git clean",
            "git merge",
        ):
            assert command in prompt

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

    async def test_rebase_conflict_escalates_when_disabled(self, initialized_db_path: Path) -> None:
        """max_attempts=0 时直接 escalate 给操作者。"""
        queue, worktree_mgr, _ = self._make_queue(initialized_db_path)
        queue._settings.merge_conflict_resolution_max_attempts = 0
        await _insert_test_story(initialized_db_path, "s1")

        worktree_mgr.get_conflict_files = AsyncMock(return_value=["file1.py", "file2.py"])
        worktree_mgr.get_path = AsyncMock(return_value=Path("/fake/worktree"))
        worktree_mgr.abort_rebase = AsyncMock()

        result = await queue._handle_rebase_conflict("s1", "CONFLICT in file1.py")
        assert result is False
        worktree_mgr.abort_rebase.assert_awaited_once()

        from ato.models.db import get_pending_approvals

        db = await get_connection(initialized_db_path)
        try:
            pending = await get_pending_approvals(db)
            conflict_approvals = [a for a in pending if a.approval_type == "rebase_conflict"]
            assert len(conflict_approvals) == 1
        finally:
            await db.close()

    async def test_rebase_conflict_escalates_when_no_worktree(
        self, initialized_db_path: Path
    ) -> None:
        """worktree 不存在时直接 escalate。"""
        queue, worktree_mgr, _ = self._make_queue(initialized_db_path)
        await _insert_test_story(initialized_db_path, "s1")

        worktree_mgr.get_conflict_files = AsyncMock(return_value=["file1.py"])
        worktree_mgr.get_path = AsyncMock(return_value=None)
        worktree_mgr.abort_rebase = AsyncMock()

        result = await queue._handle_rebase_conflict("s1", "CONFLICT in file1.py")
        assert result is False

    async def test_rebase_conflict_agent_resolves_successfully(
        self,
        initialized_db_path: Path,
    ) -> None:
        """agent 成功解决冲突后 rebase --continue 成功。"""
        queue, worktree_mgr, _ = self._make_queue(initialized_db_path)
        queue._settings.merge_conflict_resolution_max_attempts = 1
        await _insert_test_story(initialized_db_path, "s1")

        worktree_mgr.get_path = AsyncMock(return_value=Path("/fake/worktree"))
        # agent 解决后无剩余冲突文件
        worktree_mgr.get_conflict_files = AsyncMock(
            side_effect=[["file1.py"], []],
        )
        worktree_mgr.continue_rebase = AsyncMock(return_value=(True, ""))
        worktree_mgr.abort_rebase = AsyncMock()

        with (
            patch("ato.subprocess_mgr.SubprocessManager") as mock_mgr_cls,
            patch("ato.adapters.claude_cli.ClaudeAdapter"),
        ):
            mock_mgr_inst = AsyncMock()
            mock_mgr_cls.return_value = mock_mgr_inst
            mock_mgr_inst.dispatch_with_retry = AsyncMock(return_value=MagicMock())

            result = await queue._handle_rebase_conflict("s1", "CONFLICT in file1.py")

        assert result is True
        worktree_mgr.continue_rebase.assert_awaited_once()
        worktree_mgr.abort_rebase.assert_not_awaited()

    async def test_rebase_conflict_agent_fails_then_escalates(
        self,
        initialized_db_path: Path,
    ) -> None:
        """agent 失败后 escalate 给操作者。"""
        queue, worktree_mgr, _ = self._make_queue(initialized_db_path)
        queue._settings.merge_conflict_resolution_max_attempts = 1
        await _insert_test_story(initialized_db_path, "s1")

        worktree_mgr.get_path = AsyncMock(return_value=Path("/fake/worktree"))
        worktree_mgr.get_conflict_files = AsyncMock(return_value=["file1.py"])
        worktree_mgr.abort_rebase = AsyncMock()

        with (
            patch("ato.subprocess_mgr.SubprocessManager") as mock_mgr_cls,
            patch("ato.adapters.claude_cli.ClaudeAdapter"),
        ):
            mock_mgr_inst = AsyncMock()
            mock_mgr_cls.return_value = mock_mgr_inst
            mock_mgr_inst.dispatch_with_retry = AsyncMock(
                side_effect=CLIAdapterError("agent crashed", exit_code=1),
            )

            result = await queue._handle_rebase_conflict("s1", "CONFLICT in file1.py")

        assert result is False
        worktree_mgr.abort_rebase.assert_awaited_once()

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
        worktree_mgr.preflight_check = AsyncMock(
            side_effect=lambda story_id, _gate_type: _preflight_result(story_id)
        )
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
        worktree_mgr.preflight_check = AsyncMock(
            side_effect=lambda story_id, _gate_type: _preflight_result(story_id)
        )

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

            payload = json.loads(reg_approvals[0].payload)  # type: ignore[arg-type]
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
    """Regression 相关测试：completion / unfreeze / merge 流程。"""

    def _make_queue(self, db_path: Path) -> Any:
        from ato.merge_queue import MergeQueue

        worktree_mgr = AsyncMock()
        worktree_mgr.project_root = Path("/fake/repo")
        worktree_mgr.preflight_check = AsyncMock(
            side_effect=lambda story_id, _gate_type: _preflight_result(story_id)
        )

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

    async def test_dispatch_regression_resume_recreates_tracking(
        self, initialized_db_path: Path
    ) -> None:
        """regression-origin resume 应直接重建 regression_pending 跟踪。"""
        queue, _worktree_mgr, _tq = self._make_queue(initialized_db_path)
        await _insert_test_story(initialized_db_path, "s1")

        db = await get_connection(initialized_db_path)
        try:
            await db.execute(
                "UPDATE stories SET current_phase = 'regression' WHERE story_id = ?",
                ("s1",),
            )
            await db.execute(
                """
                INSERT INTO approvals (
                    approval_id, story_id, approval_type, status, decision,
                    decided_at, created_at
                ) VALUES (?, ?, 'regression_failure', 'approved', 'fix_forward', ?, ?)
                """,
                ("appr-reg-1", "s1", _NOW.isoformat(), _NOW.isoformat()),
            )
            await db.commit()
        finally:
            await db.close()

        with patch.object(
            queue,
            "_dispatch_regression_test",
            AsyncMock(return_value="task-reg-resume-1"),
        ):
            dispatched = await queue.dispatch_regression_resume("s1")

        assert dispatched is True

        db = await get_connection(initialized_db_path)
        try:
            entry = await get_merge_queue_entry(db, "s1")
            state = await get_merge_queue_state(db)
            assert entry is not None
            assert entry.status == "regression_pending"
            assert entry.regression_task_id == "task-reg-resume-1"
            assert entry.approval_id == "appr-reg-1"
            assert state.current_merge_story_id == "s1"
        finally:
            await db.close()

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

    async def test_pre_merge_preflight_failure_blocks_before_rebase_and_releases_lock(
        self,
        initialized_db_path: Path,
    ) -> None:
        """pre_merge gate 失败时不得 rebase/merge，且释放 merge queue 锁。"""
        from ato.models.db import get_pending_approvals

        queue, worktree_mgr, _ = self._make_queue(initialized_db_path)
        await _insert_test_story(initialized_db_path, "s-pre-merge-fail")

        db = await get_connection(initialized_db_path)
        try:
            await enqueue_merge(db, "s-pre-merge-fail", "a1", _NOW, _NOW)
            await dequeue_next_merge(db)
            await set_current_merge_story(db, "s-pre-merge-fail")
        finally:
            await db.close()

        worktree_mgr.preflight_check = AsyncMock(
            return_value=_preflight_result(
                "s-pre-merge-fail",
                passed=False,
                failure_reason="EMPTY_DIFF",
            )
        )
        worktree_mgr.get_path = AsyncMock(return_value=None)
        worktree_mgr.rebase_onto_main = AsyncMock(return_value=(True, ""))
        worktree_mgr.merge_to_main = AsyncMock(return_value=(True, ""))

        await queue._execute_merge("s-pre-merge-fail")

        db = await get_connection(initialized_db_path)
        try:
            entry = await get_merge_queue_entry(db, "s-pre-merge-fail")
            state = await get_merge_queue_state(db)
            pending = await get_pending_approvals(db)
            preflight_approvals = [
                approval for approval in pending if approval.approval_type == "preflight_failure"
            ]
            assert entry is not None
            assert entry.status == "failed"
            assert state.current_merge_story_id is None
            assert len(preflight_approvals) == 1
        finally:
            await db.close()

        worktree_mgr.rebase_onto_main.assert_not_awaited()
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
        worktree_mgr.preflight_check = AsyncMock(
            side_effect=lambda story_id, _gate_type: _preflight_result(story_id)
        )

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

    async def test_complete_regression_pass_full_flow(self, initialized_db_path: Path) -> None:
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


class TestCommittedMergeQueueTransitions:
    """真实 TransitionQueue 下，merge queue 返回前必须完成状态落库。"""

    async def test_regression_pass_waits_for_transition_commit(
        self, initialized_db_path: Path
    ) -> None:
        from ato.merge_queue import MergeQueue
        from ato.models.db import get_story, insert_task
        from ato.models.schemas import TaskRecord
        from ato.transition_queue import TransitionQueue

        worktree_mgr = AsyncMock()
        worktree_mgr.project_root = Path("/fake/repo")
        worktree_mgr.cleanup = AsyncMock()

        settings = MagicMock()
        settings.merge_rebase_timeout = 120
        settings.merge_conflict_resolution_max_attempts = 1
        settings.regression_test_command = "echo ok"
        settings.get_regression_commands = MagicMock(return_value=["echo ok"])
        settings.timeout.structured_job = 1800

        tq = TransitionQueue(initialized_db_path)
        await tq.start()
        try:
            queue = MergeQueue(
                db_path=initialized_db_path,
                worktree_mgr=worktree_mgr,
                transition_queue=tq,
                settings=settings,
            )
            await _insert_test_story(initialized_db_path, "s-merge-commit")

            task_id = "task-reg-commit"
            now = datetime.now(tz=UTC)
            db = await get_connection(initialized_db_path)
            try:
                await enqueue_merge(db, "s-merge-commit", "a-commit", _NOW, _NOW)
                await dequeue_next_merge(db)
                await mark_regression_dispatched(db, "s-merge-commit", task_id)
                await db.execute(
                    "UPDATE stories SET current_phase = 'regression' WHERE story_id = ?",
                    ("s-merge-commit",),
                )
                await insert_task(
                    db,
                    TaskRecord(
                        task_id=task_id,
                        story_id="s-merge-commit",
                        phase="regression",
                        role="qa",
                        cli_tool="codex",
                        status="completed",
                        started_at=now,
                        completed_at=now,
                        exit_code=0,
                    ),
                )
                await set_current_merge_story(db, "s-merge-commit")
            finally:
                await db.close()

            await queue.check_regression_completion()

            db = await get_connection(initialized_db_path)
            try:
                story = await get_story(db, "s-merge-commit")
            finally:
                await db.close()
            assert story is not None
            assert story.current_phase == "done"
            worktree_mgr.cleanup.assert_awaited_once_with("s-merge-commit")
        finally:
            await tq.stop()


class TestRebaseConflictDoesNotFreezeQueue:
    """AC5: rebase 冲突不触发 merge queue 冻结。"""

    def _make_queue(self, db_path: Path) -> tuple[Any, Any, Any]:
        from ato.merge_queue import MergeQueue

        worktree_mgr = AsyncMock()
        worktree_mgr.project_root = Path("/fake/repo")
        worktree_mgr.get_conflict_files = AsyncMock(return_value=["a.py"])
        worktree_mgr.abort_rebase = AsyncMock()
        worktree_mgr.preflight_check = AsyncMock(
            side_effect=lambda story_id, _gate_type: _preflight_result(story_id)
        )

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
        worktree_mgr.preflight_check = AsyncMock(
            side_effect=lambda story_id, _gate_type: _preflight_result(story_id)
        )

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
            payload = json.loads(reg[0].payload)  # type: ignore[arg-type]
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
            payload = json.loads(reg[0].payload)  # type: ignore[arg-type]
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
            payload = json.loads(reg[0].payload)  # type: ignore[arg-type]
            assert "test_output_summary" in payload
            assert "FAILED" in payload["test_output_summary"]
        finally:
            await db.close()


# ---------------------------------------------------------------------------
# LLM-Assisted Regression Runner (Codex) 测试
# ---------------------------------------------------------------------------


class TestCodexRegressionRunner:
    """Codex-based regression runner 正确性验证。"""

    def _make_queue(self, db_path: Path) -> Any:
        from ato.merge_queue import MergeQueue

        worktree_mgr = AsyncMock()
        worktree_mgr.project_root = Path("/fake/repo")
        worktree_mgr.preflight_check = AsyncMock(
            side_effect=lambda story_id, _gate_type: _preflight_result(story_id)
        )

        tq = AsyncMock()
        settings = MagicMock()
        settings.merge_rebase_timeout = 120
        settings.merge_conflict_resolution_max_attempts = 1
        settings.regression_test_commands = [
            "uv run pytest tests/unit/",
            "uv run pytest tests/integration/",
        ]
        settings.get_regression_commands = MagicMock(
            return_value=[
                "uv run pytest tests/unit/",
                "uv run pytest tests/integration/",
            ]
        )
        settings.timeout.structured_job = 1800

        queue = MergeQueue(
            db_path=db_path,
            worktree_mgr=worktree_mgr,
            transition_queue=tq,
            settings=settings,
        )
        return queue

    async def _setup_task(self, db_path: Path, story_id: str = "s1") -> str:
        """辅助：创建 story 和 running 状态的 regression task。"""
        from ato.models.db import get_connection, insert_task
        from ato.models.schemas import TaskRecord

        await _insert_test_story(db_path, story_id)
        task_id = f"task-codex-{story_id}"
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

    def _make_adapter_result(
        self,
        *,
        structured_output: dict[str, Any] | None = None,
        exit_code: int = 0,
        text_result: str = "All tests passed.",
    ) -> Any:
        """构造 mock AdapterResult。"""
        from ato.models.schemas import AdapterResult

        if (
            structured_output is not None
            and "command_audit" not in structured_output
            and isinstance(structured_output.get("commands_attempted"), list)
        ):
            structured_output = dict(structured_output)
            structured_output["command_audit"] = [
                {
                    "command": command,
                    "source": "project_defined",
                    "trigger_reason": "legacy_baseline",
                    "exit_code": 0,
                }
                for command in structured_output["commands_attempted"]
            ]

        return AdapterResult(
            status="success" if exit_code == 0 else "failure",
            exit_code=exit_code,
            text_result=text_result,
            structured_output=structured_output,
            cost_usd=0.01,
            input_tokens=100,
            output_tokens=50,
        )

    async def test_build_regression_prompt_with_explicit_plural_commands(
        self,
    ) -> None:
        """regression_test_commands 显式配置 → prompt 包含基线命令。"""
        from ato.merge_queue import _build_regression_prompt

        settings = ATOSettings(
            roles={"qa": {"cli": "codex"}},  # type: ignore[dict-item]
            phases=[
                {  # type: ignore[list-item]
                    "name": "regression",
                    "role": "qa",
                    "type": "structured_job",
                    "next_on_success": "done",
                    "next_on_failure": "done",
                },
            ],
            regression_test_commands=["uv run pytest tests/unit/"],
        )

        prompt = _build_regression_prompt(Path("/repo"), settings)
        assert "uv run pytest tests/unit/" in prompt
        assert "baseline regression commands" in prompt
        assert "command_audit" in prompt
        assert "git-clean relative to the starting snapshot" in prompt
        assert "legacy_baseline" in prompt

    async def test_build_regression_prompt_with_explicit_singular_command(
        self,
    ) -> None:
        """singular 被用户改为非默认值 → 作为 baseline 命令。"""
        from ato.merge_queue import _build_regression_prompt

        settings = ATOSettings(
            roles={"qa": {"cli": "codex"}},  # type: ignore[dict-item]
            phases=[
                {  # type: ignore[list-item]
                    "name": "regression",
                    "role": "qa",
                    "type": "structured_job",
                    "next_on_success": "done",
                    "next_on_failure": "done",
                },
            ],
            regression_test_command="make test",
        )

        prompt = _build_regression_prompt(Path("/repo"), settings)
        assert "make test" in prompt
        assert "baseline regression commands" in prompt
        assert "git-clean relative to the starting snapshot" in prompt

    async def test_build_regression_prompt_defaults_use_autonomous_discovery(
        self,
    ) -> None:
        """两者均为默认/未配置 → autonomous discovery（AC7 可达路径）。"""
        from ato.merge_queue import _build_regression_prompt

        settings = ATOSettings(
            roles={"qa": {"cli": "codex"}},  # type: ignore[dict-item]
            phases=[
                {  # type: ignore[list-item]
                    "name": "regression",
                    "role": "qa",
                    "type": "structured_job",
                    "next_on_success": "done",
                    "next_on_failure": "done",
                },
            ],
        )

        prompt = _build_regression_prompt(Path("/repo"), settings)
        assert "bounded discovery only" in prompt
        assert "git-clean relative to the starting snapshot" in prompt
        assert "command_audit.source" in prompt
        assert "policy-domain commands" in prompt

    async def test_build_regression_prompt_with_explicit_phase_policy(self) -> None:
        from ato.merge_queue import _build_regression_prompt

        settings = ATOSettings(
            roles={"qa": {"cli": "codex"}},  # type: ignore[dict-item]
            phases=[
                {  # type: ignore[list-item]
                    "name": "regression",
                    "role": "qa",
                    "type": "structured_job",
                    "next_on_success": "done",
                    "next_on_failure": "done",
                },
            ],
            test_catalog={
                "unit": TestLayerConfig(commands=["uv run pytest tests/unit/"]),
                "integration": TestLayerConfig(commands=["uv run pytest tests/integration/"]),
            },
            phase_test_policy={
                "regression": PhaseTestPolicyConfig(
                    required_layers=["unit"],
                    optional_layers=["integration"],
                    allow_discovery=True,
                    max_additional_commands=2,
                    allowed_when="after_required_failure",
                )
            },
        )

        prompt = _build_regression_prompt(Path("/repo"), settings)
        assert "Required layers: unit" in prompt
        assert "Optional layers: integration" in prompt
        assert "uv run pytest tests/integration/" in prompt
        assert "trigger_reason" in prompt
        assert "Do NOT include auxiliary inspection commands" in prompt

    def test_validate_regression_command_audit_ignores_auxiliary_inspection(self) -> None:
        from ato.config import resolve_effective_test_policy
        from ato.merge_queue import _validate_regression_command_audit
        from ato.models.schemas import RegressionCommandAuditEntry

        settings = ATOSettings(
            roles={"qa": {"cli": "codex"}},  # type: ignore[dict-item]
            phases=[
                {  # type: ignore[list-item]
                    "name": "regression",
                    "role": "qa",
                    "type": "structured_job",
                    "next_on_success": "done",
                    "next_on_failure": "done",
                },
            ],
            test_catalog={
                "unit": TestLayerConfig(commands=["uv run pytest tests/unit/"]),
                "integration": TestLayerConfig(commands=["uv run pytest tests/integration/"]),
            },
            phase_test_policy={
                "regression": PhaseTestPolicyConfig(
                    required_layers=["unit"],
                    optional_layers=["integration"],
                    allow_discovery=False,
                    max_additional_commands=1,
                    allowed_when="after_required_commands",
                )
            },
        )
        policy = resolve_effective_test_policy(settings, "regression")
        assert policy is not None

        entries = [
            RegressionCommandAuditEntry(
                command="git status --short",
                source="llm_diagnostic",
                trigger_reason="diagnostic",
                exit_code=0,
            ),
            RegressionCommandAuditEntry(
                command="sed -n '1,220p' package.json",
                source="llm_diagnostic",
                trigger_reason="diagnostic",
                exit_code=0,
            ),
            RegressionCommandAuditEntry(
                command="uv run pytest tests/unit/",
                source="project_defined",
                trigger_reason="required_layer",
                exit_code=0,
            ),
            RegressionCommandAuditEntry(
                command="rg --files -g 'package.json' .",
                source="llm_diagnostic",
                trigger_reason="diagnostic",
                exit_code=0,
            ),
            RegressionCommandAuditEntry(
                command="uv run pytest tests/integration/",
                source="project_defined",
                trigger_reason="optional_layer",
                exit_code=0,
            ),
        ]

        _validate_regression_command_audit(
            commands_attempted=[entry.command for entry in entries],
            command_audit=entries,
            test_policy=policy,
            skipped_command_reason=None,
        )

    def test_validate_regression_command_audit_rejects_closed_failure_gate(self) -> None:
        from ato.config import resolve_effective_test_policy
        from ato.merge_queue import _validate_regression_command_audit
        from ato.models.schemas import RegressionCommandAuditEntry

        settings = ATOSettings(
            roles={"qa": {"cli": "codex"}},  # type: ignore[dict-item]
            phases=[
                {  # type: ignore[list-item]
                    "name": "regression",
                    "role": "qa",
                    "type": "structured_job",
                    "next_on_success": "done",
                    "next_on_failure": "done",
                },
            ],
            test_catalog={
                "unit": TestLayerConfig(commands=["uv run pytest tests/unit/"]),
                "integration": TestLayerConfig(commands=["uv run pytest tests/integration/"]),
            },
            phase_test_policy={
                "regression": PhaseTestPolicyConfig(
                    required_layers=["unit"],
                    optional_layers=["integration"],
                    allow_discovery=True,
                    max_additional_commands=1,
                    allowed_when="after_required_failure",
                )
            },
        )
        policy = resolve_effective_test_policy(settings, "regression")
        assert policy is not None

        entries = [
            RegressionCommandAuditEntry(
                command="uv run pytest tests/unit/",
                source="project_defined",
                trigger_reason="required_layer",
                exit_code=0,
            ),
            RegressionCommandAuditEntry(
                command="uv run pytest tests/integration/",
                source="project_defined",
                trigger_reason="optional_layer",
                exit_code=0,
            ),
        ]

        with pytest.raises(ValueError, match="after_required_failure"):
            _validate_regression_command_audit(
                commands_attempted=[entry.command for entry in entries],
                command_audit=entries,
                test_policy=policy,
                skipped_command_reason=None,
            )

    def test_validate_regression_command_audit_rejects_discovery_when_disabled(self) -> None:
        from ato.config import resolve_effective_test_policy
        from ato.merge_queue import _validate_regression_command_audit
        from ato.models.schemas import RegressionCommandAuditEntry

        settings = ATOSettings(
            roles={"qa": {"cli": "codex"}},  # type: ignore[dict-item]
            phases=[
                {  # type: ignore[list-item]
                    "name": "regression",
                    "role": "qa",
                    "type": "structured_job",
                    "next_on_success": "done",
                    "next_on_failure": "done",
                },
            ],
            test_catalog={
                "unit": TestLayerConfig(commands=["uv run pytest tests/unit/"]),
            },
            phase_test_policy={
                "regression": PhaseTestPolicyConfig(
                    required_layers=["unit"],
                    optional_layers=[],
                    allow_discovery=False,
                    max_additional_commands=1,
                    allowed_when="after_required_commands",
                )
            },
        )
        policy = resolve_effective_test_policy(settings, "regression")
        assert policy is not None

        entries = [
            RegressionCommandAuditEntry(
                command="uv run pytest tests/unit/",
                source="project_defined",
                trigger_reason="required_layer",
                exit_code=0,
            ),
            RegressionCommandAuditEntry(
                command="pytest tests/e2e/",
                source="llm_discovered",
                trigger_reason="discovery_fallback",
                exit_code=1,
            ),
        ]

        with pytest.raises(ValueError, match="allow_discovery=false"):
            _validate_regression_command_audit(
                commands_attempted=[entry.command for entry in entries],
                command_audit=entries,
                test_policy=policy,
                skipped_command_reason=None,
            )

    def test_validate_regression_command_audit_rejects_optional_priority_violation(self) -> None:
        from ato.config import resolve_effective_test_policy
        from ato.merge_queue import _validate_regression_command_audit
        from ato.models.schemas import RegressionCommandAuditEntry

        settings = ATOSettings(
            roles={"qa": {"cli": "codex"}},  # type: ignore[dict-item]
            phases=[
                {  # type: ignore[list-item]
                    "name": "regression",
                    "role": "qa",
                    "type": "structured_job",
                    "next_on_success": "done",
                    "next_on_failure": "done",
                },
            ],
            test_catalog={
                "unit": TestLayerConfig(commands=["uv run pytest tests/unit/"]),
                "integration": TestLayerConfig(commands=["uv run pytest tests/integration/"]),
            },
            phase_test_policy={
                "regression": PhaseTestPolicyConfig(
                    required_layers=["unit"],
                    optional_layers=["integration"],
                    allow_discovery=True,
                    max_additional_commands=2,
                    allowed_when="after_required_commands",
                )
            },
        )
        policy = resolve_effective_test_policy(settings, "regression")
        assert policy is not None

        entries = [
            RegressionCommandAuditEntry(
                command="uv run pytest tests/unit/",
                source="project_defined",
                trigger_reason="required_layer",
                exit_code=0,
            ),
            RegressionCommandAuditEntry(
                command="uv run pytest tests/smoke/",
                source="llm_discovered",
                trigger_reason="discovery_fallback",
                exit_code=0,
            ),
        ]

        with pytest.raises(ValueError, match="optional commands 必须先于 discovered"):
            _validate_regression_command_audit(
                commands_attempted=[entry.command for entry in entries],
                command_audit=entries,
                test_policy=policy,
                skipped_command_reason=None,
            )

    async def test_dispatch_regression_preserves_single_task_contract(
        self, initialized_db_path: Path
    ) -> None:
        """AC1: _dispatch_regression_test 立即返回 task_id，task 表存在 running record。"""
        queue = self._make_queue(initialized_db_path)
        await _insert_test_story(initialized_db_path, "s1")

        # Mock 后台 runner 使其不真正执行
        with patch.object(queue, "_run_regression_via_codex", new_callable=AsyncMock):
            task_id = await queue._dispatch_regression_test("s1")

        assert task_id is not None

        db = await get_connection(initialized_db_path)
        try:
            cursor = await db.execute(
                "SELECT status, phase, expected_artifact FROM tasks WHERE task_id = ?",
                (task_id,),
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "running"
            assert row[1] == "regression"
            assert row[2] == "regression_test"
        finally:
            await db.close()

    async def test_run_regression_via_codex_pass_normalizes_to_exit_code_zero(
        self, initialized_db_path: Path
    ) -> None:
        """AC3: structured pass + clean workspace → exit_code=0。"""
        from ato.core import reset_main_path_gate

        reset_main_path_gate()

        queue = self._make_queue(initialized_db_path)
        task_id = await self._setup_task(initialized_db_path)

        result = self._make_adapter_result(
            structured_output={
                "regression_status": "pass",
                "summary": "All 42 tests passed",
                "commands_attempted": [
                    "uv run pytest tests/unit/",
                    "uv run pytest tests/integration/",
                ],
                "skipped_command_reason": None,
                "discovery_notes": "pytest detected",
            },
        )

        mock_update = AsyncMock()
        with (
            patch(
                "ato.subprocess_mgr.SubprocessManager",
                return_value=MagicMock(
                    dispatch_with_retry=AsyncMock(return_value=result),
                ),
            ),
            patch("ato.adapters.codex_cli.CodexAdapter"),
            patch(
                "ato.merge_queue._snapshot_workspace_changes",
                new_callable=AsyncMock,
                side_effect=[set(), set()],  # pre and post: no changes
            ),
            patch(
                "ato.recovery.RecoveryEngine._resolve_phase_config_static",
                return_value={"workspace": "main"},
            ),
            patch(
                "ato.models.db.update_task_status",
                mock_update,
            ),
        ):
            await queue._run_regression_via_codex("s1", task_id)

        # pass + clean workspace → runner 不应调用 update_task_status
        # （保留 SubprocessManager 写的状态）
        mock_update.assert_not_called()

    async def test_run_regression_via_codex_fail_normalizes_to_completed_exit_code_one(
        self, initialized_db_path: Path
    ) -> None:
        """AC4: structured fail → status=completed, exit_code=1, error_message=summary。"""
        from ato.core import reset_main_path_gate

        reset_main_path_gate()

        queue = self._make_queue(initialized_db_path)
        task_id = await self._setup_task(initialized_db_path)

        result = self._make_adapter_result(
            structured_output={
                "regression_status": "fail",
                "summary": "3 tests failed in test_auth.py",
                "commands_attempted": [
                    "uv run pytest tests/unit/",
                    "uv run pytest tests/integration/",
                ],
                "skipped_command_reason": None,
                "discovery_notes": "pytest detected",
            },
        )

        with (
            patch(
                "ato.subprocess_mgr.SubprocessManager",
                return_value=MagicMock(
                    dispatch_with_retry=AsyncMock(return_value=result),
                ),
            ),
            patch("ato.adapters.codex_cli.CodexAdapter"),
            patch(
                "ato.merge_queue._snapshot_workspace_changes",
                new_callable=AsyncMock,
                return_value=set(),
            ),
            patch(
                "ato.recovery.RecoveryEngine._resolve_phase_config_static",
                return_value={"workspace": "main"},
            ),
        ):
            await queue._run_regression_via_codex("s1", task_id)

        db = await get_connection(initialized_db_path)
        try:
            cursor = await db.execute(
                "SELECT status, exit_code, error_message FROM tasks WHERE task_id = ?",
                (task_id,),
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "completed"
            assert row[1] == 1
            assert "3 tests failed" in row[2]
        finally:
            await db.close()

    async def test_run_regression_via_codex_invalid_command_audit_fails_closed(
        self, initialized_db_path: Path
    ) -> None:
        """违反 additional-command gate 的 command_audit → fail-closed。"""
        from ato.core import reset_main_path_gate

        reset_main_path_gate()

        queue = self._make_queue(initialized_db_path)
        queue._settings = ATOSettings(
            roles={"qa": {"cli": "codex"}},  # type: ignore[dict-item]
            phases=[
                {  # type: ignore[list-item]
                    "name": "regression",
                    "role": "qa",
                    "type": "structured_job",
                    "next_on_success": "done",
                    "next_on_failure": "done",
                },
            ],
            test_catalog={
                "unit": TestLayerConfig(commands=["uv run pytest tests/unit/"]),
                "integration": TestLayerConfig(commands=["uv run pytest tests/integration/"]),
            },
            phase_test_policy={
                "regression": PhaseTestPolicyConfig(
                    required_layers=["unit"],
                    optional_layers=["integration"],
                    allow_discovery=True,
                    max_additional_commands=1,
                    allowed_when="after_required_failure",
                )
            },
        )
        task_id = await self._setup_task(initialized_db_path)

        result = self._make_adapter_result(
            structured_output={
                "regression_status": "pass",
                "summary": "All tests passed",
                "commands_attempted": [
                    "uv run pytest tests/unit/",
                    "uv run pytest tests/integration/",
                ],
                "command_audit": [
                    {
                        "command": "uv run pytest tests/unit/",
                        "source": "project_defined",
                        "trigger_reason": "required_layer",
                        "exit_code": 0,
                    },
                    {
                        "command": "uv run pytest tests/integration/",
                        "source": "project_defined",
                        "trigger_reason": "optional_layer",
                        "exit_code": 0,
                    },
                ],
                "skipped_command_reason": None,
                "discovery_notes": "policy-driven run",
            },
        )

        with (
            patch(
                "ato.subprocess_mgr.SubprocessManager",
                return_value=MagicMock(
                    dispatch_with_retry=AsyncMock(return_value=result),
                ),
            ),
            patch("ato.adapters.codex_cli.CodexAdapter"),
            patch(
                "ato.merge_queue._snapshot_workspace_changes",
                new_callable=AsyncMock,
                return_value=set(),
            ),
        ):
            await queue._run_regression_via_codex("s1", task_id)

        db = await get_connection(initialized_db_path)
        try:
            cursor = await db.execute(
                "SELECT status, exit_code, error_message FROM tasks WHERE task_id = ?",
                (task_id,),
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "completed"
            assert row[1] == 1
            assert "audit validation failed" in row[2].lower()
        finally:
            await db.close()

    async def test_run_regression_via_codex_invalid_structured_output_fails_closed(
        self, initialized_db_path: Path
    ) -> None:
        """无效 structured_output → 标记失败。"""
        from ato.core import reset_main_path_gate

        reset_main_path_gate()

        queue = self._make_queue(initialized_db_path)
        task_id = await self._setup_task(initialized_db_path)

        # structured_output 缺少 regression_status 字段
        result = self._make_adapter_result(
            structured_output={"unexpected": "format"},
        )

        with (
            patch(
                "ato.subprocess_mgr.SubprocessManager",
                return_value=MagicMock(
                    dispatch_with_retry=AsyncMock(return_value=result),
                ),
            ),
            patch("ato.adapters.codex_cli.CodexAdapter"),
            patch(
                "ato.merge_queue._snapshot_workspace_changes",
                new_callable=AsyncMock,
                return_value=set(),
            ),
            patch(
                "ato.recovery.RecoveryEngine._resolve_phase_config_static",
                return_value={"workspace": "main"},
            ),
        ):
            await queue._run_regression_via_codex("s1", task_id)

        db = await get_connection(initialized_db_path)
        try:
            cursor = await db.execute(
                "SELECT status, exit_code, error_message FROM tasks WHERE task_id = ?",
                (task_id,),
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "completed"
            assert row[1] == 1
            assert "invalid" in row[2].lower() or "validation" in row[2].lower()
        finally:
            await db.close()

    async def test_run_regression_via_codex_partial_schema_fails_closed(
        self, initialized_db_path: Path
    ) -> None:
        """半残 payload（有 regression_status 但缺其他 required 字段）→ fail-closed。"""
        from ato.core import reset_main_path_gate

        reset_main_path_gate()

        queue = self._make_queue(initialized_db_path)
        task_id = await self._setup_task(initialized_db_path)

        # 只有 regression_status，缺 summary/commands_attempted 等
        result = self._make_adapter_result(
            structured_output={"regression_status": "pass"},
        )

        with (
            patch(
                "ato.subprocess_mgr.SubprocessManager",
                return_value=MagicMock(
                    dispatch_with_retry=AsyncMock(return_value=result),
                ),
            ),
            patch("ato.adapters.codex_cli.CodexAdapter"),
            patch(
                "ato.merge_queue._snapshot_workspace_changes",
                new_callable=AsyncMock,
                return_value=set(),
            ),
            patch(
                "ato.recovery.RecoveryEngine._resolve_phase_config_static",
                return_value={"workspace": "main"},
            ),
        ):
            await queue._run_regression_via_codex("s1", task_id)

        db = await get_connection(initialized_db_path)
        try:
            cursor = await db.execute(
                "SELECT status, exit_code, error_message FROM tasks WHERE task_id = ?",
                (task_id,),
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "completed"
            assert row[1] == 1
            # Pydantic ValidationError 会列出缺失字段
            assert "summary" in row[2].lower()
        finally:
            await db.close()

    async def test_run_regression_via_codex_unknown_status_fails_closed(
        self, initialized_db_path: Path
    ) -> None:
        """非�� regression_status 枚举值 → fail-closed。"""
        from ato.core import reset_main_path_gate

        reset_main_path_gate()

        queue = self._make_queue(initialized_db_path)
        task_id = await self._setup_task(initialized_db_path)

        result = self._make_adapter_result(
            structured_output={
                "regression_status": "unknown",
                "summary": "Something happened",
                "commands_attempted": [],
                "skipped_command_reason": None,
                "discovery_notes": "",
            },
        )

        with (
            patch(
                "ato.subprocess_mgr.SubprocessManager",
                return_value=MagicMock(
                    dispatch_with_retry=AsyncMock(return_value=result),
                ),
            ),
            patch("ato.adapters.codex_cli.CodexAdapter"),
            patch(
                "ato.merge_queue._snapshot_workspace_changes",
                new_callable=AsyncMock,
                return_value=set(),
            ),
            patch(
                "ato.recovery.RecoveryEngine._resolve_phase_config_static",
                return_value={"workspace": "main"},
            ),
        ):
            await queue._run_regression_via_codex("s1", task_id)

        db = await get_connection(initialized_db_path)
        try:
            cursor = await db.execute(
                "SELECT status, exit_code, error_message FROM tasks WHERE task_id = ?",
                (task_id,),
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "completed"
            assert row[1] == 1
            # Pydantic Literal 校验拒绝 "unknown"
            assert "invalid" in row[2].lower() or "regression_status" in row[2]
        finally:
            await db.close()

    async def test_run_regression_via_codex_type_error_in_payload_fails_closed(
        self, initialized_db_path: Path
    ) -> None:
        """字段类型错误（如 commands_attempted 为 str）→ fail-closed。"""
        from ato.core import reset_main_path_gate

        reset_main_path_gate()

        queue = self._make_queue(initialized_db_path)
        task_id = await self._setup_task(initialized_db_path)

        result = self._make_adapter_result(
            structured_output={
                "regression_status": "pass",
                "summary": "ok",
                "commands_attempted": "not a list",  # 类型错误
                "skipped_command_reason": None,
                "discovery_notes": "detected",
            },
        )

        with (
            patch(
                "ato.subprocess_mgr.SubprocessManager",
                return_value=MagicMock(
                    dispatch_with_retry=AsyncMock(return_value=result),
                ),
            ),
            patch("ato.adapters.codex_cli.CodexAdapter"),
            patch(
                "ato.merge_queue._snapshot_workspace_changes",
                new_callable=AsyncMock,
                return_value=set(),
            ),
            patch(
                "ato.recovery.RecoveryEngine._resolve_phase_config_static",
                return_value={"workspace": "main"},
            ),
        ):
            await queue._run_regression_via_codex("s1", task_id)

        db = await get_connection(initialized_db_path)
        try:
            cursor = await db.execute(
                "SELECT status, exit_code, error_message FROM tasks WHERE task_id = ?",
                (task_id,),
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "completed"
            assert row[1] == 1
            assert "commands_attempted" in row[2]
        finally:
            await db.close()

    async def test_run_regression_via_codex_snapshot_failure_fails_closed(
        self, initialized_db_path: Path
    ) -> None:
        """git status 失败 → fail-closed，不放行 regression pass。"""
        from ato.core import reset_main_path_gate
        from ato.merge_queue import _WorkspaceSnapshotError

        reset_main_path_gate()

        queue = self._make_queue(initialized_db_path)
        task_id = await self._setup_task(initialized_db_path)

        with (
            patch(
                "ato.subprocess_mgr.SubprocessManager",
                return_value=MagicMock(
                    dispatch_with_retry=AsyncMock(),
                ),
            ),
            patch("ato.adapters.codex_cli.CodexAdapter"),
            patch(
                "ato.merge_queue._snapshot_workspace_changes",
                new_callable=AsyncMock,
                side_effect=_WorkspaceSnapshotError("git status failed (exit=128)"),
            ),
            patch(
                "ato.recovery.RecoveryEngine._resolve_phase_config_static",
                return_value={"workspace": "main"},
            ),
        ):
            await queue._run_regression_via_codex("s1", task_id)

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
            assert "snapshot failed" in row[2].lower()
        finally:
            await db.close()

    async def test_run_regression_via_codex_workspace_dirty_fails_closed(
        self, initialized_db_path: Path
    ) -> None:
        """AC8: workspace 新增脏文件 → 即使 regression_status=pass 也判失败。"""
        from ato.core import reset_main_path_gate

        reset_main_path_gate()

        queue = self._make_queue(initialized_db_path)
        task_id = await self._setup_task(initialized_db_path)

        result = self._make_adapter_result(
            structured_output={
                "regression_status": "pass",
                "summary": "All tests passed",
                "commands_attempted": [
                    "uv run pytest tests/unit/",
                    "uv run pytest tests/integration/",
                ],
                "skipped_command_reason": None,
                "discovery_notes": "pytest",
            },
        )

        with (
            patch(
                "ato.subprocess_mgr.SubprocessManager",
                return_value=MagicMock(
                    dispatch_with_retry=AsyncMock(return_value=result),
                ),
            ),
            patch("ato.adapters.codex_cli.CodexAdapter"),
            patch(
                "ato.merge_queue._snapshot_workspace_changes",
                new_callable=AsyncMock,
                # pre: empty, post: new file appeared
                side_effect=[set(), {"src/new_file.py"}],
            ),
            patch(
                "ato.recovery.RecoveryEngine._resolve_phase_config_static",
                return_value={"workspace": "main"},
            ),
        ):
            await queue._run_regression_via_codex("s1", task_id)

        db = await get_connection(initialized_db_path)
        try:
            cursor = await db.execute(
                "SELECT status, exit_code, error_message FROM tasks WHERE task_id = ?",
                (task_id,),
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "completed"
            assert row[1] == 1
            assert "modified main workspace" in row[2].lower()
            assert "src/new_file.py" in row[2]
        finally:
            await db.close()

    async def test_run_regression_via_codex_cli_error_leaves_subprocess_mgr_terminal_state(
        self, initialized_db_path: Path
    ) -> None:
        """AC5: CLIAdapterError → SubprocessManager 已写终态，runner 只记日志不覆写。"""
        from ato.core import reset_main_path_gate
        from ato.models.schemas import CLIAdapterError, ErrorCategory

        reset_main_path_gate()

        queue = self._make_queue(initialized_db_path)
        task_id = await self._setup_task(initialized_db_path)

        with (
            patch(
                "ato.subprocess_mgr.SubprocessManager",
                return_value=MagicMock(
                    dispatch_with_retry=AsyncMock(
                        side_effect=CLIAdapterError(
                            "Codex CLI timed out",
                            category=ErrorCategory.TIMEOUT,
                            retryable=True,
                        ),
                    ),
                ),
            ),
            patch("ato.adapters.codex_cli.CodexAdapter"),
            patch(
                "ato.merge_queue._snapshot_workspace_changes",
                new_callable=AsyncMock,
                return_value=set(),
            ),
            patch(
                "ato.recovery.RecoveryEngine._resolve_phase_config_static",
                return_value={"workspace": "main"},
            ),
        ):
            # Should NOT raise
            await queue._run_regression_via_codex("s1", task_id)

        # Task 仍为初始 running 状态（SubprocessManager mock 未写回，
        # 但 runner 也不应覆写——验证 runner 不会意外标记为其他状态）
        db = await get_connection(initialized_db_path)
        try:
            cursor = await db.execute(
                "SELECT status FROM tasks WHERE task_id = ?",
                (task_id,),
            )
            row = await cursor.fetchone()
            assert row is not None
            # 在真实场景中 SubprocessManager 会写 failed；
            # 此处验证 runner 不会额外覆写
            assert row[0] == "running"  # mock 未修改
        finally:
            await db.close()
