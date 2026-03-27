"""多选决策 helper 单元测试。

测试 resolve_multi_decision()、get_options_for_approval() 等共享函数。
"""

from __future__ import annotations

import json

import pytest

from ato.approval_helpers import (
    get_options_for_approval,
    resolve_multi_decision,
)
from ato.models.schemas import APPROVAL_DEFAULT_VALID_OPTIONS

# ---------------------------------------------------------------------------
# resolve_multi_decision 测试
# ---------------------------------------------------------------------------


def test_resolve_multi_decision_valid_index() -> None:
    """有效索引返回正确 decision。"""
    payload = json.dumps({"options": ["revert", "fix_forward", "pause"]})
    decision, status = resolve_multi_decision("regression_failure", 0, payload)
    assert decision == "revert"
    assert status == "approved"

    decision, status = resolve_multi_decision("regression_failure", 1, payload)
    assert decision == "fix_forward"
    assert status == "approved"

    decision, status = resolve_multi_decision("regression_failure", 2, payload)
    assert decision == "pause"
    assert status == "approved"


def test_resolve_multi_decision_out_of_range() -> None:
    """超出范围抛出 ValueError。"""
    payload = json.dumps({"options": ["revert", "fix_forward", "pause"]})
    with pytest.raises(ValueError, match="out of range"):
        resolve_multi_decision("regression_failure", 3, payload)

    with pytest.raises(ValueError, match="out of range"):
        resolve_multi_decision("regression_failure", -1, payload)


def test_resolve_multi_decision_uses_payload_options_when_whitelisted() -> None:
    """payload.options 中的值全部在白名单中时优先使用。"""
    # regression_failure 白名单: ["revert", "fix_forward", "pause"]
    # payload 只提供子集（白名单内的重新排序）
    payload = json.dumps({"options": ["pause", "revert"]})
    decision, _ = resolve_multi_decision("regression_failure", 0, payload)
    assert decision == "pause"

    decision, _ = resolve_multi_decision("regression_failure", 1, payload)
    assert decision == "revert"


def test_resolve_multi_decision_rejects_non_whitelisted_options() -> None:
    """payload.options 包含白名单外的值时整体 fallback 到默认。"""
    payload = json.dumps({"options": ["custom_a", "custom_b"]})
    # custom_a/custom_b 不在 regression_failure 白名单中 → fallback
    decision, _ = resolve_multi_decision("regression_failure", 0, payload)
    assert decision == "revert"  # 默认第一个


def test_resolve_multi_decision_falls_back_to_defaults() -> None:
    """payload 无 options 时使用默认。"""
    decision, status = resolve_multi_decision("regression_failure", 0, None)
    # 默认 regression_failure: ["revert", "fix_forward", "pause"]
    assert decision == "revert"
    assert status == "approved"

    decision, status = resolve_multi_decision("session_timeout", 2, None)
    # 默认 session_timeout: ["restart", "resume", "abandon"]
    assert decision == "abandon"
    assert status == "approved"


def test_resolve_multi_decision_uses_shared_option_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """resolve_multi_decision 应复用 get_options_for_approval，避免逻辑分叉。"""
    calls: list[tuple[str, str | None]] = []

    def fake_get_options(approval_type: str, payload: str | None = None) -> list[str]:
        calls.append((approval_type, payload))
        return ["shared-a", "shared-b"]

    monkeypatch.setattr(
        "ato.approval_helpers.get_options_for_approval",
        fake_get_options,
    )

    decision, status = resolve_multi_decision(
        "regression_failure",
        1,
        '{"options":["ignored-by-test"]}',
    )

    assert calls == [("regression_failure", '{"options":["ignored-by-test"]}')]
    assert decision == "shared-b"
    assert status == "approved"


def test_needs_human_review_options_align_schema() -> None:
    """`needs_human_review` 对齐 `retry/skip/escalate`。"""
    defaults = APPROVAL_DEFAULT_VALID_OPTIONS["needs_human_review"]
    assert defaults == ["retry", "skip", "escalate"]

    # 通过 resolve_multi_decision 验证
    for i, expected in enumerate(defaults):
        decision, _ = resolve_multi_decision("needs_human_review", i, None)
        assert decision == expected


def test_convergent_loop_escalation_options_align_schema() -> None:
    """`convergent_loop_escalation` 对齐 `retry/skip/escalate`。"""
    defaults = APPROVAL_DEFAULT_VALID_OPTIONS["convergent_loop_escalation"]
    assert defaults == ["retry", "skip", "escalate"]

    for i, expected in enumerate(defaults):
        decision, _ = resolve_multi_decision("convergent_loop_escalation", i, None)
        assert decision == expected


# ---------------------------------------------------------------------------
# get_options_for_approval 测试
# ---------------------------------------------------------------------------


def test_get_options_prefers_whitelisted_payload() -> None:
    """payload.options 全在白名单内时优先使用。"""
    payload = json.dumps({"options": ["pause", "revert"]})
    assert get_options_for_approval("regression_failure", payload) == ["pause", "revert"]


def test_get_options_rejects_non_whitelisted_payload() -> None:
    """payload.options 包含白名单外的值时 fallback 到默认。"""
    payload = json.dumps({"options": ["x", "y", "z"]})
    assert get_options_for_approval(
        "regression_failure",
        payload,
    ) == ["revert", "fix_forward", "pause"]


def test_get_options_falls_back_to_defaults() -> None:
    """无 payload 时使用默认。"""
    assert get_options_for_approval("regression_failure", None) == [
        "revert",
        "fix_forward",
        "pause",
    ]
    assert get_options_for_approval("session_timeout", None) == ["restart", "resume", "abandon"]


def test_get_options_all_exception_types_have_defaults() -> None:
    """所有异常审批类型都有默认选项。"""
    exception_types = [
        "session_timeout",
        "crash_recovery",
        "regression_failure",
        "rebase_conflict",
        "precommit_failure",
        "needs_human_review",
        "convergent_loop_escalation",
    ]
    for atype in exception_types:
        options = get_options_for_approval(atype, None)
        assert len(options) >= 2, f"{atype} should have at least 2 options"


# ---------------------------------------------------------------------------
# DashboardScreen 数字键处理（非 DOM 逻辑）
# ---------------------------------------------------------------------------


def test_handle_option_key_ignored_on_story_selection() -> None:
    """选中 story 时数字键不触发异常审批提交。"""
    from ato.tui.dashboard import DashboardScreen

    dashboard = DashboardScreen()
    dashboard._selected_item_id = "story:s1"
    # _handle_option_key 检查选中的是否为 approval
    dashboard._handle_option_key(0)
    assert len(dashboard._submitted_approvals) == 0


def test_handle_option_key_ignored_on_no_selection() -> None:
    """无选中项时数字键不触发。"""
    from ato.tui.dashboard import DashboardScreen

    dashboard = DashboardScreen()
    dashboard._selected_item_id = None
    dashboard._handle_option_key(0)
    assert len(dashboard._submitted_approvals) == 0
