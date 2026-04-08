"""test_approval — Approval 模型、DB 函数与通知机制测试（Story 4.1）。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
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
    status: Literal["pending", "approved", "rejected"] = "pending",
    recommended_action: str | None = "restart",
    risk_level: Literal["high", "medium", "low"] | None = None,
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

    async def test_commit_false_suppresses_nudge_and_bell(self, initialized_db_path: Path) -> None:
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

            with (
                patch("ato.approval_helpers.send_user_notification") as mock_bell,
                patch.object(nudge, "notify") as mock_notify,
            ):
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

    async def test_commit_true_sends_nudge_and_bell(self, initialized_db_path: Path) -> None:
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

            with (
                patch("ato.approval_helpers.send_user_notification") as mock_bell,
                patch.object(nudge, "notify") as mock_notify,
            ):
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

        assert "preflight_failure" in APPROVAL_TYPE_TO_NOTIFICATION
        assert APPROVAL_TYPE_TO_NOTIFICATION["preflight_failure"] == "normal"

    def test_all_types_mapped(self) -> None:
        """所有定义的 approval 类型都有通知级别映射。"""
        from typing import get_args

        from ato.models.schemas import ApprovalType

        all_types = get_args(ApprovalType)
        for t in all_types:
            assert t in APPROVAL_TYPE_TO_NOTIFICATION, f"Missing mapping for {t}"


class TestSendUserNotificationBell:
    def test_urgent_triggers_double_bell(self) -> None:
        """urgent 触发双次 bell + stderr 消息输出。"""
        import sys

        from ato.nudge import send_user_notification

        with patch.object(sys, "stderr") as mock_stderr:
            send_user_notification("urgent", "test")
            written = [c.args[0] for c in mock_stderr.write.call_args_list]
            assert "\a\a" in written, "urgent should emit double bell"
            assert any("⚠ 紧急" in w for w in written), "urgent should have prefix"

    def test_normal_triggers_single_bell(self) -> None:
        """normal 触发单次 bell + stderr 消息输出。"""
        import sys

        from ato.nudge import send_user_notification

        with patch.object(sys, "stderr") as mock_stderr:
            send_user_notification("normal", "test")
            written = [c.args[0] for c in mock_stderr.write.call_args_list]
            assert "\a" in written, "normal should emit single bell"
            assert any("test" in w for w in written)

    def test_silent_no_bell(self) -> None:
        """silent 无 bell 无 stderr 输出。"""
        import sys

        from ato.nudge import send_user_notification

        with patch.object(sys, "stderr") as mock_stderr:
            send_user_notification("silent", "test")
            mock_stderr.write.assert_not_called()


class TestApprovalNotificationContent:
    """Story 4.4: approval 通知正文自包含短 ID 与 CLI 快捷命令。"""

    async def test_create_approval_notification_contains_short_id_and_quick_command(
        self, initialized_db_path: Path
    ) -> None:
        """approval 通知正文自包含短 ID 与 CLI 快捷命令。"""
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
                    story_id="s-notif",
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
                    story_id="s-notif",
                    approval_type="merge_authorization",
                    nudge=nudge,
                )

                mock_bell.assert_called_once()
                call_args = mock_bell.call_args
                level = call_args[0][0]
                message = call_args[0][1]

                # 包含短 ID
                assert approval.approval_id[:8] in message
                # 包含快捷命令
                assert "ato approve" in message
                assert "--decision approve" in message
                # level 为 normal
                assert level == "normal"
        finally:
            await db.close()

    def test_recommended_action_aligns_with_valid_options(self) -> None:
        """推荐操作必须落在合法 decision 集内。"""
        from ato.models.schemas import APPROVAL_DEFAULT_VALID_OPTIONS, APPROVAL_RECOMMENDED_ACTIONS

        for atype, recommended in APPROVAL_RECOMMENDED_ACTIONS.items():
            valid = APPROVAL_DEFAULT_VALID_OPTIONS.get(atype, [])
            assert recommended in valid, (
                f"DRIFT: {atype} recommended='{recommended}' not in valid options {valid}"
            )

    def test_preflight_failure_summary_and_options(self) -> None:
        import json

        from ato.approval_helpers import (
            format_approval_summary,
            get_exception_context,
            get_options_for_approval,
        )

        payload: dict[str, object] = {
            "gate_type": "pre_review",
            "retry_event": "dev_done",
            "worktree_path": "/tmp/wt",
            "failure_reason": "UNCOMMITTED_CHANGES",
            "options": ["manual_commit_and_retry", "escalate"],
        }
        payload_json = json.dumps(payload)

        assert format_approval_summary("preflight_failure", payload_json) == (
            "Worktree 边界门控失败"
        )
        assert get_options_for_approval("preflight_failure", payload_json) == [
            "manual_commit_and_retry",
            "escalate",
        ]
        what, impact = get_exception_context("preflight_failure", payload)
        assert "Worktree 边界门控失败" in what
        assert "failure_reason: UNCOMMITTED_CHANGES" in impact
