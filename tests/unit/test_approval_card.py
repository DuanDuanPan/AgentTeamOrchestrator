"""ApprovalCard Widget 单元测试。

直接实例化 Widget，调用 update_data() 后验证 render() 输出。
使用 Rich.Text.plain 属性提取纯文本内容进行断言。
"""

from __future__ import annotations

import pytest

from ato.approval_helpers import (
    format_approval_summary,
    get_binary_approval_labels,
    is_binary_approval,
    resolve_binary_decision,
)
from ato.models.schemas import APPROVAL_TYPE_ICONS
from ato.tui.theme import map_risk_to_color
from ato.tui.widgets.approval_card import ApprovalCard

# ---------------------------------------------------------------------------
# ApprovalCard 渲染测试
# ---------------------------------------------------------------------------


def test_approval_card_render_collapsed() -> None:
    """折叠态单行渲染包含图标、story ID、摘要、推荐、风险。"""
    card = ApprovalCard()
    card.update_data(
        approval_id="abc-123",
        story_id="story-007",
        approval_type="merge_authorization",
        payload=None,
        recommended_action="approve",
        risk_level="low",
    )
    rendered = card.render()
    text = rendered.plain

    assert "🔀" in text
    assert "story-007" in text
    assert "Merge 授权请求" in text
    assert "[approve]" in text
    assert "[low]" in text


def test_approval_card_render_no_risk() -> None:
    """风险级别为 None 时显示 [-]。"""
    card = ApprovalCard()
    card.update_data(
        approval_id="abc-123",
        story_id="story-001",
        approval_type="batch_confirmation",
        payload=None,
        recommended_action="confirm",
        risk_level=None,
    )
    rendered = card.render()
    text = rendered.plain
    assert "[-]" in text


# ---------------------------------------------------------------------------
# 风险颜色映射测试
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("risk_level", "expected_color_var"),
    [
        ("high", "$error"),
        ("medium", "$warning"),
        ("low", "$success"),
        (None, "$muted"),
        ("", "$muted"),
    ],
)
def test_approval_card_risk_level_colors(risk_level: str | None, expected_color_var: str) -> None:
    """high/medium/low/None 颜色映射正确。"""
    assert map_risk_to_color(risk_level) == expected_color_var


# ---------------------------------------------------------------------------
# 审批类型图标映射测试
# ---------------------------------------------------------------------------


def test_approval_card_type_icons() -> None:
    """各审批类型图标映射正确（含 rebase_conflict）。"""
    assert APPROVAL_TYPE_ICONS["merge_authorization"] == "🔀"
    assert APPROVAL_TYPE_ICONS["blocking_abnormal"] == "⚠"
    assert APPROVAL_TYPE_ICONS["budget_exceeded"] == "💰"
    assert APPROVAL_TYPE_ICONS["timeout"] == "⏳"
    assert APPROVAL_TYPE_ICONS["batch_confirmation"] == "📦"
    assert APPROVAL_TYPE_ICONS["rebase_conflict"] == "⚡"
    assert APPROVAL_TYPE_ICONS["session_timeout"] == "⏱"
    assert APPROVAL_TYPE_ICONS["crash_recovery"] == "↩"
    assert APPROVAL_TYPE_ICONS["regression_failure"] == "✖"
    assert APPROVAL_TYPE_ICONS["convergent_loop_escalation"] == "🔄"
    assert APPROVAL_TYPE_ICONS["precommit_failure"] == "🔧"
    assert APPROVAL_TYPE_ICONS["needs_human_review"] == "👁"


# ---------------------------------------------------------------------------
# 摘要模板生成测试
# ---------------------------------------------------------------------------


def test_approval_card_summary_generation() -> None:
    """摘要模板拼接正确（各审批类型）。"""
    assert format_approval_summary("merge_authorization", None) == "Merge 授权请求"
    assert format_approval_summary("blocking_abnormal", None) == "Blocking 异常数量超阈值"
    assert format_approval_summary("budget_exceeded", None) == "预算超限"
    assert format_approval_summary("timeout", None) == "任务超时"
    assert format_approval_summary("batch_confirmation", None) == "Batch 确认"
    assert format_approval_summary("rebase_conflict", None) == "Rebase 冲突需处理"


def test_approval_card_summary_with_payload() -> None:
    """摘要附加 payload 关键信息。"""
    import json

    payload = json.dumps({"elapsed_seconds": 120})
    summary = format_approval_summary("session_timeout", payload)
    assert "(120s)" in summary

    payload = json.dumps({"blocking_count": 4, "threshold": 2})
    summary = format_approval_summary("blocking_abnormal", payload)
    assert "(4/2)" in summary

    payload = json.dumps({"phase": "developing"})
    summary = format_approval_summary("crash_recovery", payload)
    assert "(phase: developing)" in summary


# ---------------------------------------------------------------------------
# update_data 测试
# ---------------------------------------------------------------------------


def test_approval_card_update_data() -> None:
    """批量更新 reactive 属性正确反映。"""
    card = ApprovalCard()
    card.update_data(
        approval_id="id-1",
        story_id="story-001",
        approval_type="budget_exceeded",
        payload=None,
        recommended_action="increase_budget",
        risk_level="high",
    )
    assert card.approval_id == "id-1"
    assert card.story_id == "story-001"
    assert card.approval_type == "budget_exceeded"
    assert card.summary == "预算超限"
    assert card.recommended_action == "increase_budget"
    assert card.risk_level == "high"


# ---------------------------------------------------------------------------
# 二选一审批判断测试
# ---------------------------------------------------------------------------


def test_is_binary_approval_supported_types() -> None:
    """二选一审批类型判断正确。"""
    assert is_binary_approval("merge_authorization") is True
    assert is_binary_approval("blocking_abnormal") is True
    assert is_binary_approval("budget_exceeded") is True
    assert is_binary_approval("timeout") is True
    assert is_binary_approval("batch_confirmation") is True


def test_is_binary_approval_unsupported_types() -> None:
    """非二选一审批类型不误判。"""
    assert is_binary_approval("session_timeout") is False
    assert is_binary_approval("crash_recovery") is False
    assert is_binary_approval("regression_failure") is False
    assert is_binary_approval("precommit_failure") is False
    assert is_binary_approval("rebase_conflict") is False
    assert is_binary_approval("needs_human_review") is False
    assert is_binary_approval("convergent_loop_escalation") is False


def test_is_binary_approval_payload_options_override() -> None:
    """payload.options > 2 时二选一审批降级为多选。"""
    import json

    payload = json.dumps({"options": ["a", "b", "c"]})
    assert is_binary_approval("merge_authorization", payload) is False

    payload = json.dumps({"options": ["approve", "reject"]})
    assert is_binary_approval("merge_authorization", payload) is True


# ---------------------------------------------------------------------------
# 决策映射测试
# ---------------------------------------------------------------------------


def test_y_key_maps_merge_authorization_to_approve() -> None:
    """y 键对 merge_authorization 映射到 approve/approved。"""
    result = resolve_binary_decision("merge_authorization", "y")
    assert result is not None
    decision, status = result
    assert decision == "approve"
    assert status == "approved"


def test_n_key_maps_merge_authorization_to_reject() -> None:
    """n 键对 merge_authorization 映射到 reject/rejected。"""
    result = resolve_binary_decision("merge_authorization", "n")
    assert result is not None
    decision, status = result
    assert decision == "reject"
    assert status == "rejected"


def test_blocking_abnormal_y_n_mapping() -> None:
    """blocking_abnormal 的 y/n 分别映射到 confirm_fix / human_review。"""
    y_result = resolve_binary_decision("blocking_abnormal", "y")
    assert y_result is not None
    assert y_result[0] == "confirm_fix"
    assert y_result[1] == "approved"  # non-binary decision → approved

    n_result = resolve_binary_decision("blocking_abnormal", "n")
    assert n_result is not None
    assert n_result[0] == "human_review"
    assert n_result[1] == "approved"  # non-binary decision → approved


def test_budget_exceeded_y_n_mapping() -> None:
    """budget_exceeded 的 y/n 映射。"""
    assert resolve_binary_decision("budget_exceeded", "y") == ("increase_budget", "approved")
    assert resolve_binary_decision("budget_exceeded", "n") == ("reject", "rejected")


def test_timeout_y_n_mapping() -> None:
    """timeout 的 y/n 映射。"""
    assert resolve_binary_decision("timeout", "y") == ("continue_waiting", "approved")
    assert resolve_binary_decision("timeout", "n") == ("abandon", "approved")


def test_batch_confirmation_y_n_mapping() -> None:
    """batch_confirmation 的 y/n 映射。"""
    assert resolve_binary_decision("batch_confirmation", "y") == ("confirm", "approved")
    assert resolve_binary_decision("batch_confirmation", "n") == ("reject", "rejected")


def test_unsupported_type_returns_none() -> None:
    """不支持的审批类型返回 None。"""
    assert resolve_binary_decision("session_timeout", "y") is None
    assert resolve_binary_decision("crash_recovery", "n") is None


# ---------------------------------------------------------------------------
# 审批标签测试
# ---------------------------------------------------------------------------


def test_binary_approval_labels() -> None:
    """二选一审批的 y/n 标签正确。"""
    labels = get_binary_approval_labels("merge_authorization")
    assert labels == ("合并", "拒绝")

    labels = get_binary_approval_labels("blocking_abnormal")
    assert labels == ("确认修复", "人工审阅")

    labels = get_binary_approval_labels("session_timeout")
    assert labels is None  # 非二选一类型
