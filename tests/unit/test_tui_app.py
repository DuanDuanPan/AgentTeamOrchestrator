"""ATOApp unit tests for lightweight data-loading behavior."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ato.models.db import get_connection, insert_findings_batch, insert_story
from ato.models.schemas import ApprovalRecord, FindingRecord, StoryRecord, compute_dedup_hash
from ato.tui.app import ATOApp

_NOW = datetime.now(tz=UTC)


def _make_story(story_id: str) -> StoryRecord:
    return StoryRecord(
        story_id=story_id,
        title=f"Story {story_id}",
        status="in_progress",
        current_phase="reviewing",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _make_finding(story_id: str, *, round_num: int) -> FindingRecord:
    description = "needs escalation"
    file_path = "src/review.py"
    rule_id = "R001"
    return FindingRecord(
        finding_id="f1",
        story_id=story_id,
        round_num=round_num,
        severity="blocking",
        description=description,
        status="open",
        file_path=file_path,
        rule_id=rule_id,
        dedup_hash=compute_dedup_hash(file_path, rule_id, "blocking", description),
        created_at=_NOW,
    )


@pytest.mark.asyncio
async def test_story_stage_falls_back_to_pending_escalation_approval(
    initialized_db_path: Path,
) -> None:
    """No active escalated task should still render escalated stage from approval metadata."""
    story_id = "s-escalated-approval"
    db = await get_connection(initialized_db_path)
    try:
        await insert_story(db, _make_story(story_id))
        await insert_findings_batch(
            db,
            [_make_finding(story_id, round_num=4)],
        )
        approval = ApprovalRecord(
            approval_id="appr-escalated-1",
            story_id=story_id,
            approval_type="convergent_loop_escalation",
            status="pending",
            payload=json.dumps({"stage": "escalated"}),
            created_at=_NOW,
        )
        await db.execute(
            "INSERT INTO approvals "
            "(approval_id, story_id, approval_type, status, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                approval.approval_id,
                approval.story_id,
                approval.approval_type,
                approval.status,
                approval.payload,
                approval.created_at.isoformat(),
            ),
        )
        await db.commit()
    finally:
        await db.close()

    app = ATOApp(db_path=initialized_db_path)
    app._update_dashboard = lambda **_: None  # type: ignore[method-assign]
    await app._load_data()

    assert app._story_cl_rounds[story_id] == 4
    assert app._story_cl_stages[story_id] == "escalated"


@pytest.mark.asyncio
async def test_story_stage_detected_from_active_task_context(
    initialized_db_path: Path,
) -> None:
    """Active task 的 context_briefing.stage 应驱动 escalated 显示。"""
    story_id = "s-escalated-task"
    db = await get_connection(initialized_db_path)
    try:
        await insert_story(db, _make_story(story_id))
        await insert_findings_batch(
            db,
            [_make_finding(story_id, round_num=5)],
        )
        await db.execute(
            "INSERT INTO tasks "
            "(task_id, story_id, phase, role, cli_tool, status, context_briefing, started_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "task-escalated-1",
                story_id,
                "reviewing",
                "reviewer",
                "codex",
                "pending",
                json.dumps({"stage": "escalated"}),
                _NOW.isoformat(),
            ),
        )
        await db.commit()
    finally:
        await db.close()

    app = ATOApp(db_path=initialized_db_path)
    app._update_dashboard = lambda **_: None  # type: ignore[method-assign]
    await app._load_data()

    assert app._story_cl_rounds[story_id] == 5
    assert app._story_cl_stages[story_id] == "escalated"
