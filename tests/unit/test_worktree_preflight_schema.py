"""Worktree boundary preflight schema and DB helper tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from ato.models.db import get_connection, insert_story, save_worktree_preflight_result
from ato.models.schemas import StoryRecord, WorktreeFinalizeResult, WorktreePreflightResult


def _story(story_id: str = "story-preflight") -> StoryRecord:
    now = datetime.now(tz=UTC)
    return StoryRecord(
        story_id=story_id,
        title="Preflight Story",
        status="in_progress",
        current_phase="developing",
        created_at=now,
        updated_at=now,
    )


def test_worktree_preflight_result_validates_strict_model() -> None:
    now = datetime.now(tz=UTC)
    result = WorktreePreflightResult.model_validate(
        {
            "story_id": "s1",
            "gate_type": "pre_review",
            "passed": False,
            "base_ref": "main",
            "base_sha": "base",
            "head_sha": "head",
            "porcelain_output": "?? file.txt\n",
            "diffstat": "",
            "changed_files": ["src/a.py"],
            "failure_reason": "UNCOMMITTED_CHANGES",
            "error_output": None,
            "checked_at": now,
        }
    )

    assert result.gate_type == "pre_review"
    assert result.failure_reason == "UNCOMMITTED_CHANGES"
    assert result.changed_files == ["src/a.py"]


def test_worktree_finalize_result_defaults_files_changed() -> None:
    result = WorktreeFinalizeResult.model_validate(
        {"story_id": "s1", "committed": False, "error": "no commit"}
    )

    assert result.files_changed == []


async def test_save_worktree_preflight_result_serializes_changed_files(
    initialized_db_path: Path,
) -> None:
    now = datetime.now(tz=UTC)
    db = await get_connection(initialized_db_path)
    try:
        await insert_story(db, _story())
        result = WorktreePreflightResult.model_validate(
            {
                "story_id": "story-preflight",
                "gate_type": "pre_merge",
                "passed": True,
                "base_ref": "origin/main",
                "base_sha": "base",
                "head_sha": "head",
                "porcelain_output": "",
                "diffstat": " src/a.py | 1 +",
                "changed_files": ["src/a.py", "src/b.py"],
                "checked_at": now,
            }
        )

        row_id = await save_worktree_preflight_result(db, result, commit=True)
        assert row_id > 0

        cursor = await db.execute(
            "SELECT changed_files, passed FROM worktree_preflight_results WHERE id = ?",
            (row_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert json.loads(row["changed_files"]) == ["src/a.py", "src/b.py"]
        assert row["passed"] == 1
    finally:
        await db.close()
