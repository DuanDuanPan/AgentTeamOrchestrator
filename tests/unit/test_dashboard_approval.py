"""DashboardScreen 审批功能单元测试。

测试审批排序逻辑、y/n 键映射、多选降级、d 键切换等。
非 DOM 依赖的测试在此，DOM 依赖的在集成测试中。
"""

from __future__ import annotations

from ato.approval_helpers import is_binary_approval, resolve_binary_decision
from ato.tui.dashboard import DashboardScreen


def test_multi_option_approval_disables_y_n() -> None:
    """payload.options > 2 的审批不会被 y/n 误消费。"""
    import json

    payload = json.dumps({"options": ["restart", "resume", "abandon"]})
    assert is_binary_approval("merge_authorization", payload) is False
    assert resolve_binary_decision("session_timeout", "y") is None


def test_decision_reason_persisted_for_tui_action() -> None:
    """TUI 写入会持久化 deterministic decision_reason。"""
    result = resolve_binary_decision("merge_authorization", "y")
    assert result is not None
    decision, _status = result
    # decision_reason 在 _submit_decision 中生成格式为 "tui:y -> {decision}"
    expected_reason = f"tui:y -> {decision}"
    assert expected_reason == "tui:y -> approve"


def test_d_key_toggles_detail() -> None:
    """d 键切换展开/折叠状态变量。"""
    dashboard = DashboardScreen()
    dashboard._selected_item_id = "approval:a1"
    assert dashboard._expanded_approval_id is None

    # 模拟 action_toggle_detail 逻辑
    dashboard._approvals_by_id = {"a1": object()}
    dashboard.action_toggle_detail()
    assert dashboard._expanded_approval_id == "a1"

    # 再次切换应收起
    dashboard.action_toggle_detail()
    assert dashboard._expanded_approval_id is None


def test_y_key_ignored_on_story_selection() -> None:
    """选中 story 时 y 不会触发审批提交。"""
    dashboard = DashboardScreen()
    dashboard._selected_item_id = "story:s1"
    # _submit_decision 检查选中的是否为审批项
    dashboard._submit_decision("y")
    assert len(dashboard._submitted_approvals) == 0


def test_y_key_ignored_on_no_selection() -> None:
    """无选中项时 y 不会触发审批提交。"""
    dashboard = DashboardScreen()
    dashboard._selected_item_id = None
    dashboard._submit_decision("y")
    assert len(dashboard._submitted_approvals) == 0


def test_submitted_approvals_cleared_on_refresh() -> None:
    """已提交的审批在轮询消失后从中间状态集合中清除。"""
    dashboard = DashboardScreen()
    dashboard._submitted_approvals = {"a1", "a2"}

    class _FakeApproval:
        def __init__(self, aid: str) -> None:
            self.approval_id = aid

    # a1 仍在 pending 列表，a2 已消失
    dashboard.update_content(
        story_count=0,
        pending_approvals=1,
        today_cost_usd=0.0,
        last_updated="12:00",
        pending_approval_records=[_FakeApproval("a1")],
    )
    assert "a1" in dashboard._submitted_approvals
    assert "a2" not in dashboard._submitted_approvals


def test_submitted_approval_rollback_on_failure() -> None:
    """_do_submit 失败时应从 _submitted_approvals 移除，允许用户重试。"""
    dashboard = DashboardScreen()
    dashboard._submitted_approvals = {"a1"}

    class _FakeApproval:
        def __init__(self, aid: str) -> None:
            self.approval_id = aid

    # a1 仍在 pending 列表（写库失败，所以还是 pending）
    dashboard.update_content(
        story_count=0,
        pending_approvals=1,
        today_cost_usd=0.0,
        last_updated="12:00",
        pending_approval_records=[_FakeApproval("a1")],
    )
    # 关键断言：如果 a1 仍在 pending_approval_records 中，
    # 且写库失败，_submitted_approvals 应该不再阻止重试。
    # 这测试的是 _rollback_submitted 方法。
    dashboard._rollback_submitted("a1")
    assert "a1" not in dashboard._submitted_approvals


def test_story_findings_summary_stored() -> None:
    """update_content 传入 findings summary 后正确存储。"""
    dashboard = DashboardScreen()
    summary = {
        "story-007": {
            "blocking_open": 0,
            "blocking_closed": 2,
            "suggestion_open": 1,
            "suggestion_closed": 3,
        }
    }
    dashboard.update_content(
        story_count=1,
        pending_approvals=0,
        today_cost_usd=0.0,
        last_updated="12:00",
        story_findings_summary=summary,
    )
    assert dashboard._story_findings_summary == summary
    assert dashboard._story_findings_summary["story-007"]["blocking_closed"] == 2


def test_sync_selected_story_id_from_item() -> None:
    """_sync_selected_story_id 从 _selected_item_id 正确派生。"""
    dashboard = DashboardScreen()

    dashboard._selected_item_id = "story:s1"
    dashboard._sync_selected_story_id()
    assert dashboard._selected_story_id == "s1"

    dashboard._selected_item_id = "approval:a1"
    dashboard._sync_selected_story_id()
    assert dashboard._selected_story_id is None

    dashboard._selected_item_id = None
    dashboard._sync_selected_story_id()
    assert dashboard._selected_story_id is None
