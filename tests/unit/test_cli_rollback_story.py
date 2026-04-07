"""test_cli_rollback_story — ato rollback-story CLI tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from ato.cli import app
from ato.models.db import (
    get_connection,
    get_findings_by_story,
    get_pending_approvals,
    get_story,
    get_tasks_by_story,
    init_db,
    insert_approval,
    insert_finding,
    insert_story,
    insert_task,
)
from ato.models.schemas import ApprovalRecord, FindingRecord, StoryRecord, TaskRecord

runner = CliRunner()
_NOW = datetime.now(tz=UTC)


def _init_story_with_invalid_rows(db_path: Path) -> None:
    async def _inner() -> None:
        db = await get_connection(db_path)
        try:
            await insert_story(
                db,
                StoryRecord(
                    story_id="story-rb",
                    title="Rollback Story",
                    status="in_progress",
                    current_phase="developing",
                    created_at=_NOW,
                    updated_at=_NOW,
                ),
            )
            await insert_task(
                db,
                TaskRecord(
                    task_id="task-rb",
                    story_id="story-rb",
                    phase="developing",
                    role="developer",
                    cli_tool="codex",
                    status="pending",
                    pid=123,
                ),
            )
            await insert_finding(
                db,
                FindingRecord(
                    finding_id="finding-rb",
                    story_id="story-rb",
                    round_num=1,
                    severity="blocking",
                    description="Needs fix",
                    status="open",
                    file_path="src/demo.py",
                    rule_id="VAL001",
                    dedup_hash="dedup-rb",
                    created_at=_NOW,
                ),
            )
            await insert_approval(
                db,
                ApprovalRecord(
                    approval_id="approval-rb",
                    story_id="story-rb",
                    approval_type="crash_recovery",
                    status="pending",
                    created_at=_NOW,
                ),
            )
            await db.execute("UPDATE tasks SET status = 'cancelled' WHERE task_id = 'task-rb'")
            await db.execute(
                "UPDATE findings SET status = 'resolved' WHERE finding_id = 'finding-rb'"
            )
            await db.commit()
        finally:
            await db.close()

    asyncio.run(_inner())


class TestRollbackStoryCli:
    def test_rollback_story_repairs_invalid_rows(self, tmp_path: Path) -> None:
        db_path = tmp_path / ".ato" / "state.db"
        asyncio.run(init_db(db_path))
        _init_story_with_invalid_rows(db_path)

        result = runner.invoke(
            app,
            [
                "rollback-story",
                "story-rb",
                "--phase",
                "creating",
                "--db-path",
                str(db_path),
                "--yes",
            ],
        )

        assert result.exit_code == 0
        assert "已回退 story-rb: developing -> creating" in result.stdout

        async def _verify() -> None:
            db = await get_connection(db_path)
            try:
                story = await get_story(db, "story-rb")
                tasks = await get_tasks_by_story(db, "story-rb")
                findings = await get_findings_by_story(db, "story-rb")
                approvals = await get_pending_approvals(db)
            finally:
                await db.close()

            assert story is not None
            assert story.current_phase == "creating"
            assert story.status == "planning"
            assert tasks[0].status == "failed"
            assert findings == []
            assert approvals == []

        asyncio.run(_verify())

    def test_cleanup_worktree_rejected_for_non_preworktree_phase(self, tmp_path: Path) -> None:
        db_path = tmp_path / ".ato" / "state.db"
        asyncio.run(init_db(db_path))
        _init_story_with_invalid_rows(db_path)

        result = runner.invoke(
            app,
            [
                "rollback-story",
                "story-rb",
                "--phase",
                "developing",
                "--cleanup-worktree",
                "--db-path",
                str(db_path),
                "--yes",
            ],
        )

        assert result.exit_code == 1
        output = result.stdout + (result.stderr or "")
        assert "pre-worktree phase" in output
