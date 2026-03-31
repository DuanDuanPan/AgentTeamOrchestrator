"""tests/unit/test_initial_dispatch.py — 初始 dispatch 检测与分派测试。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ato.core import Orchestrator
from ato.models.db import (
    get_connection,
    get_tasks_by_story,
    get_undispatched_stories,
    init_db,
    insert_approval,
    insert_story,
    insert_task,
)
from ato.models.schemas import ApprovalRecord, StoryRecord, TaskRecord

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _setup_db(tmp_path: Path) -> Path:
    db_path = tmp_path / ".ato" / "state.db"
    await init_db(db_path)
    return db_path


def _make_settings() -> MagicMock:
    settings = MagicMock()
    settings.polling_interval = 1.0
    return settings


def _make_story_record(
    story_id: str,
    *,
    current_phase: str = "creating",
    status: str = "planning",
) -> StoryRecord:
    now = datetime.now(tz=UTC)
    return StoryRecord(
        story_id=story_id,
        title=f"Test: {story_id}",
        status=status,  # type: ignore[arg-type]
        current_phase=current_phase,
        created_at=now,
        updated_at=now,
    )


async def _insert_story(
    db_path: Path,
    story_id: str,
    current_phase: str = "creating",
    status: str = "planning",
    *,
    batch_status: str = "active",
) -> None:
    db = await get_connection(db_path)
    try:
        now = datetime.now(tz=UTC).isoformat()
        batch_id = f"batch-{uuid.uuid4().hex[:8]}"

        await db.execute(
            "INSERT OR IGNORE INTO batches (batch_id, status, created_at) VALUES (?, ?, ?)",
            (batch_id, batch_status, now),
        )
        await insert_story(
            db,
            _make_story_record(
                story_id,
                current_phase=current_phase,
                status=status,
            ),
        )
        await db.execute(
            "INSERT INTO batch_stories (batch_id, story_id, sequence_no) VALUES (?, ?, ?)",
            (batch_id, story_id, 0),
        )
        await db.commit()
    finally:
        await db.close()


async def _insert_task_for_story(
    db_path: Path,
    story_id: str,
    status: str = "running",
    phase: str = "creating",
    *,
    expected_artifact: str | None = None,
    context_briefing: str | None = None,
) -> None:
    db = await get_connection(db_path)
    try:
        now = datetime.now(tz=UTC)
        task = TaskRecord(
            task_id=f"task-{uuid.uuid4().hex[:8]}",
            story_id=story_id,
            phase=phase,
            role="creator",
            cli_tool="claude",
            status=status,  # type: ignore[arg-type]
            pid=12345,
            expected_artifact=expected_artifact,
            context_briefing=context_briefing,
            started_at=now,
        )
        await insert_task(db, task)
        await db.commit()
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# Detection Tests
# ---------------------------------------------------------------------------


class TestGetUndispatchedStories:
    @pytest.mark.asyncio
    async def test_creating_story_without_task_detected(self, tmp_path: Path) -> None:
        """creating 阶段无 task 的 story 应被返回。"""
        db_path = await _setup_db(tmp_path)
        await _insert_story(db_path, "s-1", "creating", "planning")

        db = await get_connection(db_path)
        try:
            stories = await get_undispatched_stories(db)
        finally:
            await db.close()

        assert len(stories) == 1
        assert stories[0].story_id == "s-1"

    @pytest.mark.asyncio
    async def test_story_with_running_task_not_detected(self, tmp_path: Path) -> None:
        """有 running task 的 story 不应被返回。"""
        db_path = await _setup_db(tmp_path)
        await _insert_story(db_path, "s-1", "creating", "planning")
        await _insert_task_for_story(db_path, "s-1", status="running")

        db = await get_connection(db_path)
        try:
            stories = await get_undispatched_stories(db)
        finally:
            await db.close()

        assert len(stories) == 0

    @pytest.mark.asyncio
    async def test_story_with_pending_task_not_detected(self, tmp_path: Path) -> None:
        """有 pending task 的 story 不应被返回。"""
        db_path = await _setup_db(tmp_path)
        await _insert_story(db_path, "s-1", "creating", "planning")
        await _insert_task_for_story(db_path, "s-1", status="pending")

        db = await get_connection(db_path)
        try:
            stories = await get_undispatched_stories(db)
        finally:
            await db.close()

        assert len(stories) == 0

    @pytest.mark.asyncio
    async def test_story_with_fix_placeholder_not_detected(self, tmp_path: Path) -> None:
        """convergent_loop 的 pending fix placeholder 也应阻止初始分发。"""
        import json

        db_path = await _setup_db(tmp_path)
        await _insert_story(db_path, "s-1", "fixing", "in_progress")
        await _insert_task_for_story(
            db_path,
            "s-1",
            status="pending",
            phase="fixing",
            expected_artifact="convergent_loop_fix_placeholder",
            context_briefing=json.dumps(
                {
                    "fix_kind": "fix_dispatch",
                    "round_num": 1,
                    "stage": "standard",
                }
            ),
        )

        db = await get_connection(db_path)
        try:
            stories = await get_undispatched_stories(db)
        finally:
            await db.close()

        assert stories == []

    @pytest.mark.asyncio
    async def test_queued_story_not_detected(self, tmp_path: Path) -> None:
        """queued 阶段的 story 不应被返回。"""
        db_path = await _setup_db(tmp_path)
        await _insert_story(db_path, "s-1", "queued", "backlog")

        db = await get_connection(db_path)
        try:
            stories = await get_undispatched_stories(db)
        finally:
            await db.close()

        assert len(stories) == 0

    @pytest.mark.asyncio
    async def test_done_story_not_detected(self, tmp_path: Path) -> None:
        """done 阶段的 story 不应被返回。"""
        db_path = await _setup_db(tmp_path)
        await _insert_story(db_path, "s-1", "done", "done")

        db = await get_connection(db_path)
        try:
            stories = await get_undispatched_stories(db)
        finally:
            await db.close()

        assert len(stories) == 0

    @pytest.mark.asyncio
    async def test_inactive_batch_not_detected(self, tmp_path: Path) -> None:
        """非 active batch 中的 story 不应被返回。"""
        db_path = await _setup_db(tmp_path)
        await _insert_story(db_path, "s-1", "creating", "planning", batch_status="completed")

        db = await get_connection(db_path)
        try:
            stories = await get_undispatched_stories(db)
        finally:
            await db.close()

        assert len(stories) == 0

    @pytest.mark.asyncio
    async def test_completed_task_still_detected(self, tmp_path: Path) -> None:
        """只有 completed task（非 running/pending/paused）的 story 应被返回。"""
        db_path = await _setup_db(tmp_path)
        await _insert_story(db_path, "s-1", "creating", "planning")
        await _insert_task_for_story(db_path, "s-1", status="completed")

        db = await get_connection(db_path)
        try:
            stories = await get_undispatched_stories(db)
        finally:
            await db.close()

        assert len(stories) == 1

    @pytest.mark.asyncio
    async def test_story_with_pending_crash_recovery_approval_not_detected(
        self, tmp_path: Path
    ) -> None:
        """等待 crash_recovery 决策的 story 不应被初始调度再次补发。"""
        import json

        db_path = await _setup_db(tmp_path)
        await _insert_story(db_path, "s-1", "designing", "in_progress")

        db = await get_connection(db_path)
        try:
            now = datetime.now(tz=UTC)
            await insert_approval(
                db,
                ApprovalRecord(
                    approval_id="apr-crash-1",
                    story_id="s-1",
                    approval_type="crash_recovery",
                    status="pending",
                    created_at=now,
                    payload=json.dumps(
                        {
                            "task_id": "t-1",
                            "phase": "designing",
                            "options": ["restart", "resume", "abandon"],
                        }
                    ),
                ),
            )
        finally:
            await db.close()

        db2 = await get_connection(db_path)
        try:
            stories = await get_undispatched_stories(db2)
        finally:
            await db2.close()

        assert stories == []


# ---------------------------------------------------------------------------
# Delegation Tests
# ---------------------------------------------------------------------------


class TestInitialDispatchDelegation:
    @pytest.mark.asyncio
    async def test_validating_initial_dispatch_reuses_convergent_pipeline(
        self,
        tmp_path: Path,
    ) -> None:
        """validating 首次调度应委托给 convergent restart 路径。"""
        db_path = await _setup_db(tmp_path)
        story = _make_story_record("s-val", current_phase="validating", status="review")

        db = await get_connection(db_path)
        try:
            await insert_story(db, story)
        finally:
            await db.close()

        orchestrator = Orchestrator(settings=_make_settings(), db_path=db_path)

        with (
            patch(
                "ato.recovery.RecoveryEngine._resolve_phase_config_static",
                return_value={
                    "role": "validator",
                    "cli_tool": "codex",
                    "phase_type": "convergent_loop",
                },
            ),
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
            await orchestrator._dispatch_initial_phase(story)

        mock_convergent.assert_called_once()
        mock_batch.assert_not_called()

        dispatched_task = mock_convergent.call_args.args[0]
        assert dispatched_task.story_id == "s-val"
        assert dispatched_task.phase == "validating"
        assert dispatched_task.role == "validator"
        assert dispatched_task.cli_tool == "codex"
        assert dispatched_task.expected_artifact == "initial_dispatch_requested"

        db2 = await get_connection(db_path)
        try:
            tasks = await get_tasks_by_story(db2, "s-val")
        finally:
            await db2.close()

        assert len(tasks) == 1
        assert tasks[0].task_id == dispatched_task.task_id
        assert tasks[0].status == "pending"

    @pytest.mark.asyncio
    async def test_creating_initial_dispatch_reuses_structured_job_pipeline(
        self,
        tmp_path: Path,
    ) -> None:
        """creating 首次调度应委托给 structured-job restart 路径。"""
        db_path = await _setup_db(tmp_path)
        story = _make_story_record("s-create", current_phase="creating", status="in_progress")

        db = await get_connection(db_path)
        try:
            await insert_story(db, story)
        finally:
            await db.close()

        orchestrator = Orchestrator(settings=_make_settings(), db_path=db_path)

        with (
            patch(
                "ato.recovery.RecoveryEngine._resolve_phase_config_static",
                return_value={
                    "role": "creator",
                    "cli_tool": "claude",
                    "phase_type": "structured_job",
                },
            ),
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
            await orchestrator._dispatch_initial_phase(story)

        mock_batch.assert_called_once()
        mock_convergent.assert_not_called()

        dispatched_task = mock_batch.call_args.args[0]
        assert dispatched_task.story_id == "s-create"
        assert dispatched_task.phase == "creating"
        assert dispatched_task.role == "creator"
        assert dispatched_task.cli_tool == "claude"
        assert dispatched_task.expected_artifact == "initial_dispatch_requested"

        db2 = await get_connection(db_path)
        try:
            tasks = await get_tasks_by_story(db2, "s-create")
        finally:
            await db2.close()

        assert len(tasks) == 1
        assert tasks[0].task_id == dispatched_task.task_id
        assert tasks[0].status == "pending"

    @pytest.mark.asyncio
    async def test_initial_dispatch_skips_story_blocked_by_crash_recovery(
        self,
        tmp_path: Path,
    ) -> None:
        """pending crash_recovery approval 存在时不创建新的初始 task。"""
        import json

        db_path = await _setup_db(tmp_path)
        story = _make_story_record("s-blocked", current_phase="creating", status="in_progress")

        db = await get_connection(db_path)
        try:
            await insert_story(db, story)
            now = datetime.now(tz=UTC)
            await insert_approval(
                db,
                ApprovalRecord(
                    approval_id="apr-crash-2",
                    story_id="s-blocked",
                    approval_type="crash_recovery",
                    status="pending",
                    created_at=now,
                    payload=json.dumps(
                        {
                            "task_id": "old-task",
                            "phase": "creating",
                            "options": ["restart", "resume", "abandon"],
                        }
                    ),
                ),
            )
        finally:
            await db.close()

        orchestrator = Orchestrator(settings=_make_settings(), db_path=db_path)

        with (
            patch(
                "ato.recovery.RecoveryEngine._resolve_phase_config_static",
                return_value={
                    "role": "creator",
                    "cli_tool": "claude",
                    "phase_type": "structured_job",
                },
            ),
            patch.object(
                orchestrator,
                "_dispatch_batch_restart",
                new_callable=AsyncMock,
            ) as mock_batch,
        ):
            await orchestrator._dispatch_initial_phase(story)

        mock_batch.assert_not_called()

        db2 = await get_connection(db_path)
        try:
            tasks = await get_tasks_by_story(db2, "s-blocked")
        finally:
            await db2.close()

        assert tasks == []

    @pytest.mark.asyncio
    async def test_dev_ready_initial_dispatch_uses_transition_queue_reconcile(
        self,
        tmp_path: Path,
    ) -> None:
        """dev_ready 是自动 gate，不应创建 LLM task。"""
        db_path = await _setup_db(tmp_path)
        story = _make_story_record("s-dev-ready", current_phase="dev_ready", status="ready")

        db = await get_connection(db_path)
        try:
            await insert_story(db, story)
        finally:
            await db.close()

        orchestrator = Orchestrator(settings=_make_settings(), db_path=db_path)
        orchestrator._tq = AsyncMock()

        with (
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
            await orchestrator._dispatch_initial_phase(story)

        orchestrator._tq.submit.assert_awaited_once()
        event = orchestrator._tq.submit.call_args.args[0]
        assert event.story_id == "s-dev-ready"
        assert event.event_name == "start_dev"
        mock_batch.assert_not_called()
        mock_convergent.assert_not_called()

        db2 = await get_connection(db_path)
        try:
            tasks = await get_tasks_by_story(db2, "s-dev-ready")
        finally:
            await db2.close()

        assert tasks == []

    @pytest.mark.asyncio
    async def test_worktree_phase_initial_dispatch_defers_until_worktree_exists(
        self,
        tmp_path: Path,
    ) -> None:
        """workspace: worktree 且尚无 worktree_path 时不应提前创建初始 task。"""
        db_path = await _setup_db(tmp_path)
        story = _make_story_record("s-dev", current_phase="developing", status="in_progress")

        db = await get_connection(db_path)
        try:
            await insert_story(db, story)
        finally:
            await db.close()

        orchestrator = Orchestrator(settings=_make_settings(), db_path=db_path)

        with (
            patch(
                "ato.recovery.RecoveryEngine._resolve_phase_config_static",
                return_value={
                    "role": "developer",
                    "cli_tool": "codex",
                    "phase_type": "interactive_session",
                    "workspace": "worktree",
                },
            ),
            patch.object(
                orchestrator,
                "_dispatch_interactive_restart",
                new_callable=AsyncMock,
            ) as mock_interactive,
        ):
            await orchestrator._dispatch_initial_phase(story)

        mock_interactive.assert_not_called()

        db2 = await get_connection(db_path)
        try:
            tasks = await get_tasks_by_story(db2, "s-dev")
        finally:
            await db2.close()

        assert tasks == []
