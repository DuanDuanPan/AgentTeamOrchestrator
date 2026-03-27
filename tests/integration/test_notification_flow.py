"""test_notification_flow — 端到端通知触发测试（Story 4.4）。"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from ato.models.db import (
    get_active_batch,
    get_connection,
    insert_batch,
    insert_batch_story_links,
    insert_story,
)
from ato.models.schemas import (
    BatchRecord,
    BatchStoryLink,
    StoryRecord,
    TransitionEvent,
)
from ato.transition_queue import TransitionQueue

_NOW = datetime.now(tz=UTC)


async def _seed_story(db_path: Path, story_id: str, phase: str = "queued") -> None:
    """插入 story 到 SQLite。"""
    status_map = {
        "queued": "backlog",
        "creating": "planning",
        "validating": "planning",
        "dev_ready": "ready",
        "developing": "in_progress",
        "reviewing": "review",
        "fixing": "review",
        "qa_testing": "in_progress",
        "uat": "uat",
        "merging": "in_progress",
        "regression": "in_progress",
        "done": "done",
    }
    record = StoryRecord(
        story_id=story_id,
        title=f"Test {story_id}",
        status=status_map.get(phase, "in_progress"),  # type: ignore[arg-type]
        current_phase=phase,
        created_at=_NOW,
        updated_at=_NOW,
    )
    db = await get_connection(db_path)
    try:
        await insert_story(db, record)
        await db.commit()
    finally:
        await db.close()


class TestMilestoneNotificationOnStoryDone:
    """story 完成通知仅在状态 commit 后触发。"""

    async def test_milestone_notification_on_story_done_post_commit(
        self, initialized_db_path: Path
    ) -> None:
        await _seed_story(initialized_db_path, "story-done-test", phase="regression")

        tq = TransitionQueue(initialized_db_path)
        await tq.start()

        try:
            with patch("ato.transition_queue.send_user_notification") as mock_notify:
                event = TransitionEvent(
                    story_id="story-done-test",
                    event_name="regression_pass",
                    source="cli",
                    submitted_at=_NOW,
                )
                await tq.submit(event)
                # 带超时等待，避免无限挂起
                await asyncio.wait_for(tq._queue.join(), timeout=10.0)

                # 验证里程碑通知被发送
                milestone_calls = [
                    c for c in mock_notify.call_args_list if c.args[0] == "milestone"
                ]
                assert len(milestone_calls) >= 1, "Should send milestone notification on story done"
                assert "story-done-test" in milestone_calls[0].args[1]

        finally:
            await tq.stop()

        # 验证通知发生时 story 状态已经 commit 到 DB（done）
        db = await get_connection(initialized_db_path)
        try:
            from ato.models.db import get_story

            story = await get_story(db, "story-done-test")
            assert story is not None
            assert story.current_phase == "done", (
                "Story phase should be 'done' after regression_pass commit"
            )
        finally:
            await db.close()


class TestBatchCompletionNotification:
    """batch 完成时持久化为 completed 且仅通知一次。"""

    async def test_batch_completion_marks_completed_and_notifies_once(
        self, initialized_db_path: Path
    ) -> None:
        story_id = "batch-done-story"
        await _seed_story(initialized_db_path, story_id, phase="regression")

        batch_id = str(uuid.uuid4())
        db = await get_connection(initialized_db_path)
        try:
            await insert_batch(
                db,
                BatchRecord(
                    batch_id=batch_id,
                    status="active",
                    created_at=_NOW,
                    completed_at=None,
                ),
            )
            await insert_batch_story_links(
                db,
                [BatchStoryLink(batch_id=batch_id, story_id=story_id, sequence_no=1)],
            )
        finally:
            await db.close()

        tq = TransitionQueue(initialized_db_path)
        await tq.start()

        try:
            with patch("ato.transition_queue.send_user_notification") as mock_notify:
                event = TransitionEvent(
                    story_id=story_id,
                    event_name="regression_pass",
                    source="cli",
                    submitted_at=_NOW,
                )
                await tq.submit(event)
                await asyncio.wait_for(tq._queue.join(), timeout=10.0)

                milestone_calls = [
                    c for c in mock_notify.call_args_list if c.args[0] == "milestone"
                ]
                assert len(milestone_calls) == 2, (
                    f"Expected 2 milestone calls (story + batch), got {len(milestone_calls)}"
                )
                assert "Batch 全部交付完成" in milestone_calls[1].args[1]
        finally:
            await tq.stop()

        # 验证 batch 状态已持久化为 completed 且 completed_at 已落库
        db = await get_connection(initialized_db_path)
        try:
            batch = await get_active_batch(db)
            assert batch is None, "Active batch should be None after completion"

            # 直接查询 batch 记录，验证 completed_at 字段已写入
            cursor = await db.execute(
                "SELECT status, completed_at FROM batches WHERE batch_id = ?",
                (batch_id,),
            )
            row = await cursor.fetchone()
            assert row is not None, "Batch record should exist"
            assert row[0] == "completed", f"Batch status should be 'completed', got '{row[0]}'"
            assert row[1] is not None, "completed_at should be set after batch completion"
        finally:
            await db.close()


class TestUrgentNotificationOnRegressionFailure:
    """regression 失败时触发 urgent bell。"""

    async def test_urgent_notification_on_regression_failure(
        self, initialized_db_path: Path
    ) -> None:
        from ato.approval_helpers import create_approval

        db = await get_connection(initialized_db_path)
        try:
            await insert_story(
                db,
                StoryRecord(
                    story_id="s-reg-fail",
                    title="Reg Fail Test",
                    status="in_progress",
                    current_phase="regression",
                    created_at=_NOW,
                    updated_at=_NOW,
                ),
            )

            with patch("ato.approval_helpers.send_user_notification") as mock_notify:
                await create_approval(
                    db,
                    story_id="s-reg-fail",
                    approval_type="regression_failure",
                    payload_dict={"blocked_stories": ["s2"]},
                    risk_level="high",
                )

                mock_notify.assert_called_once()
                assert mock_notify.call_args.args[0] == "urgent"
        finally:
            await db.close()


class TestNormalNotificationOnApprovalCreation:
    """常规 approval 创建时触发 normal bell。"""

    async def test_normal_notification_on_approval_creation(
        self, initialized_db_path: Path
    ) -> None:
        from ato.approval_helpers import create_approval

        db = await get_connection(initialized_db_path)
        try:
            await insert_story(
                db,
                StoryRecord(
                    story_id="s-normal",
                    title="Normal Test",
                    status="in_progress",
                    current_phase="developing",
                    created_at=_NOW,
                    updated_at=_NOW,
                ),
            )

            with patch("ato.approval_helpers.send_user_notification") as mock_notify:
                await create_approval(
                    db,
                    story_id="s-normal",
                    approval_type="merge_authorization",
                )

                mock_notify.assert_called_once()
                assert mock_notify.call_args.args[0] == "normal"
        finally:
            await db.close()
