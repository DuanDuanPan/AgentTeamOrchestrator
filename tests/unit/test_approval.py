"""test_approval — Approval 模型、DB 函数与通知机制测试（Story 4.1）。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from ato.models.db import (
    get_approval_by_id,
    get_connection,
    get_decided_unconsumed_approvals,
    get_pending_approvals,
    insert_approval,
    mark_approval_consumed,
    update_approval_decision,
)
from ato.models.schemas import (
    APPROVAL_TYPE_TO_NOTIFICATION,
    ApprovalRecord,
)

_NOW = datetime.now(tz=UTC)


def _make_approval(
    *,
    approval_id: str = "aaaa1111-2222-3333-4444-555566667777",
    story_id: str = "test-story-1",
    approval_type: str = "session_timeout",
    status: str = "pending",
    recommended_action: str | None = "restart",
    risk_level: str | None = None,
) -> ApprovalRecord:
    return ApprovalRecord(
        approval_id=approval_id,
        story_id=story_id,
        approval_type=approval_type,
        status=status,
        payload='{"task_id": "t1", "options": ["restart", "resume"]}',
        created_at=_NOW,
        recommended_action=recommended_action,
        risk_level=risk_level,
    )


class TestCreateApprovalInsertAndNudge:
    async def test_create_approval_inserts_and_nudges(self, initialized_db_path: Path) -> None:
        """创建 approval 写入 DB + 触发 nudge。"""
        from ato.approval_helpers import create_approval
        from ato.nudge import Nudge

        nudge = Nudge()
        db = await get_connection(initialized_db_path)
        try:
            from ato.models.db import insert_story
            from ato.models.schemas import StoryRecord

            await insert_story(
                db,
                StoryRecord(
                    story_id="s1",
                    title="Test",
                    status="in_progress",
                    current_phase="developing",
                    created_at=_NOW,
                    updated_at=_NOW,
                ),
            )

            with patch("ato.approval_helpers.send_user_notification") as mock_bell:
                approval = await create_approval(
                    db,
                    story_id="s1",
                    approval_type="session_timeout",
                    payload_dict={"task_id": "t1"},
                    nudge=nudge,
                )

                assert approval.approval_id is not None
                assert approval.approval_type == "session_timeout"
                assert approval.recommended_action == "restart"  # 自动推导

                # 验证 DB 已写入
                pending = await get_pending_approvals(db)
                assert len(pending) == 1
                assert pending[0].approval_id == approval.approval_id

                # 验证 bell 通知被调用
                mock_bell.assert_called_once()
        finally:
            await db.close()


class TestCreateApprovalCommitFalseSuppressesNudge:
    """commit=False 时 nudge 和 bell 必须被抑制，避免 poll loop 空转。"""

    async def test_commit_false_suppresses_nudge_and_bell(
        self, initialized_db_path: Path
    ) -> None:
        from ato.approval_helpers import create_approval
        from ato.nudge import Nudge

        nudge = Nudge()

        db = await get_connection(initialized_db_path)
        try:
            from ato.models.db import insert_story
            from ato.models.schemas import StoryRecord

            await insert_story(
                db,
                StoryRecord(
                    story_id="s-cf",
                    title="Test",
                    status="in_progress",
                    current_phase="developing",
                    created_at=_NOW,
                    updated_at=_NOW,
                ),
            )

            with patch("ato.approval_helpers.send_user_notification") as mock_bell:
                with patch.object(nudge, "notify") as mock_notify:
                    approval = await create_approval(
                        db,
                        story_id="s-cf",
                        approval_type="crash_recovery",
                        payload_dict={"task_id": "t1"},
                        nudge=nudge,
                        commit=False,
                    )

                    # DB 已写入（在当前连接可见）
                    pending = await get_pending_approvals(db)
                    assert any(a.approval_id == approval.approval_id for a in pending)

                    # nudge 和 bell 均未触发
                    mock_notify.assert_not_called()
                    mock_bell.assert_not_called()
        finally:
            await db.close()

    async def test_commit_true_sends_nudge_and_bell(
        self, initialized_db_path: Path
    ) -> None:
        """对照：commit=True（默认）时 nudge 和 bell 正常触发。"""
        from ato.approval_helpers import create_approval
        from ato.nudge import Nudge

        nudge = Nudge()

        db = await get_connection(initialized_db_path)
        try:
            from ato.models.db import insert_story
            from ato.models.schemas import StoryRecord

            await insert_story(
                db,
                StoryRecord(
                    story_id="s-ct",
                    title="Test",
                    status="in_progress",
                    current_phase="developing",
                    created_at=_NOW,
                    updated_at=_NOW,
                ),
            )

            with patch("ato.approval_helpers.send_user_notification") as mock_bell:
                with patch.object(nudge, "notify") as mock_notify:
                    await create_approval(
                        db,
                        story_id="s-ct",
                        approval_type="session_timeout",
                        payload_dict={"task_id": "t1"},
                        nudge=nudge,
                        commit=True,
                    )

                    mock_notify.assert_called_once()
                    mock_bell.assert_called_once()
        finally:
            await db.close()


class TestUpdateApprovalDecision:
    async def test_update_approval_decision(self, initialized_db_path: Path) -> None:
        """更新决策后 status / decision / decision_reason / decided_at 正确。"""
        from ato.models.db import insert_story
        from ato.models.schemas import StoryRecord

        db = await get_connection(initialized_db_path)
        try:
            await insert_story(
                db,
                StoryRecord(
                    story_id="s1",
                    title="Test",
                    status="in_progress",
                    current_phase="developing",
                    created_at=_NOW,
                    updated_at=_NOW,
                ),
            )
            approval = _make_approval(story_id="s1")
            await insert_approval(db, approval)

            now = datetime.now(tz=UTC)
            await update_approval_decision(
                db,
                approval.approval_id,
                status="approved",
                decision="restart",
                decision_reason="超时后重启",
                decided_at=now,
            )
            await db.commit()

            updated = await get_approval_by_id(db, approval.approval_id[:8])
            assert updated.status == "approved"
            assert updated.decision == "restart"
            assert updated.decision_reason == "超时后重启"
            assert updated.decided_at is not None
        finally:
            await db.close()


class TestGetPendingApprovalsFiltersDecided:
    async def test_get_pending_approvals_filters_decided(self, initialized_db_path: Path) -> None:
        """仅返回 pending 状态。"""
        from ato.models.db import insert_story
        from ato.models.schemas import StoryRecord

        db = await get_connection(initialized_db_path)
        try:
            await insert_story(
                db,
                StoryRecord(
                    story_id="s1",
                    title="Test",
                    status="in_progress",
                    current_phase="developing",
                    created_at=_NOW,
                    updated_at=_NOW,
                ),
            )
            pending_a = _make_approval(
                approval_id="aaaa0001-0000-0000-0000-000000000000",
                story_id="s1",
            )
            decided_a = _make_approval(
                approval_id="bbbb0002-0000-0000-0000-000000000000",
                story_id="s1",
                status="approved",
            )
            await insert_approval(db, pending_a)
            await insert_approval(db, decided_a)

            results = await get_pending_approvals(db)
            assert len(results) == 1
            assert results[0].approval_id == pending_a.approval_id
        finally:
            await db.close()


class TestGetApprovalByIdPrefixMatch:
    async def test_prefix_match(self, initialized_db_path: Path) -> None:
        """前缀匹配查询。"""
        from ato.models.db import insert_story
        from ato.models.schemas import StoryRecord

        db = await get_connection(initialized_db_path)
        try:
            await insert_story(
                db,
                StoryRecord(
                    story_id="s1",
                    title="Test",
                    status="in_progress",
                    current_phase="developing",
                    created_at=_NOW,
                    updated_at=_NOW,
                ),
            )
            approval = _make_approval(story_id="s1")
            await insert_approval(db, approval)

            result = await get_approval_by_id(db, "aaaa1111")
            assert result.approval_id == approval.approval_id
        finally:
            await db.close()

    async def test_prefix_too_short(self, initialized_db_path: Path) -> None:
        """前缀过短时报错。"""
        db = await get_connection(initialized_db_path)
        try:
            with pytest.raises(ValueError, match="至少需要 4 个字符"):
                await get_approval_by_id(db, "aa")
        finally:
            await db.close()


class TestGetDecidedUnconsumedAndMarkConsumed:
    async def test_decided_unconsumed_and_mark_consumed(self, initialized_db_path: Path) -> None:
        """DB 幂等消费。"""
        from ato.models.db import insert_story
        from ato.models.schemas import StoryRecord

        db = await get_connection(initialized_db_path)
        try:
            await insert_story(
                db,
                StoryRecord(
                    story_id="s1",
                    title="Test",
                    status="in_progress",
                    current_phase="developing",
                    created_at=_NOW,
                    updated_at=_NOW,
                ),
            )
            # 插入 approved 但未消费的 approval
            approval = _make_approval(
                story_id="s1",
                status="approved",
            )
            await insert_approval(db, approval)

            # 查询
            unconsumed = await get_decided_unconsumed_approvals(db)
            assert len(unconsumed) == 1
            assert unconsumed[0].consumed_at is None

            # 标记消费
            now = datetime.now(tz=UTC)
            await mark_approval_consumed(db, approval.approval_id, now)

            # 再次查询应为空
            unconsumed2 = await get_decided_unconsumed_approvals(db)
            assert len(unconsumed2) == 0
        finally:
            await db.close()


class TestNotificationLevelMapping:
    def test_all_approval_types_have_mapping(self) -> None:
        """已存在 approval 类型正确映射到通知级别。"""
        assert "regression_failure" in APPROVAL_TYPE_TO_NOTIFICATION
        assert APPROVAL_TYPE_TO_NOTIFICATION["regression_failure"] == "urgent"

        assert "crash_recovery" in APPROVAL_TYPE_TO_NOTIFICATION
        assert APPROVAL_TYPE_TO_NOTIFICATION["crash_recovery"] == "normal"

        assert "session_timeout" in APPROVAL_TYPE_TO_NOTIFICATION
        assert APPROVAL_TYPE_TO_NOTIFICATION["session_timeout"] == "normal"

    def test_all_types_mapped(self) -> None:
        """所有定义的 approval 类型都有通知级别映射。"""
        from typing import get_args

        from ato.models.schemas import ApprovalType

        all_types = get_args(ApprovalType)
        for t in all_types:
            assert t in APPROVAL_TYPE_TO_NOTIFICATION, f"Missing mapping for {t}"


class TestSendUserNotificationBell:
    def test_urgent_triggers_bell(self) -> None:
        """urgent 触发 bell（写入 stderr）。"""
        import sys

        from ato.nudge import send_user_notification

        with patch.object(sys, "stderr") as mock_stderr:
            send_user_notification("urgent", "test")
            mock_stderr.write.assert_called_once_with("\a")
            mock_stderr.flush.assert_called_once()

    def test_normal_triggers_bell(self) -> None:
        """normal 触发 bell。"""
        import sys

        from ato.nudge import send_user_notification

        with patch.object(sys, "stderr") as mock_stderr:
            send_user_notification("normal", "test")
            mock_stderr.write.assert_called_once_with("\a")

    def test_silent_no_bell(self) -> None:
        """silent 无动作（不写 stderr）。"""
        import sys

        from ato.nudge import send_user_notification

        with patch.object(sys, "stderr") as mock_stderr:
            send_user_notification("silent", "test")
            mock_stderr.write.assert_not_called()
