"""ExceptionApprovalPanel Widget 单元测试。

直接实例化 Widget，调用 update_data() 后验证 render() 输出。
使用 Rich.Text.plain 属性提取纯文本内容进行断言。
"""

from __future__ import annotations

import json

from ato.approval_helpers import (
    get_exception_context,
    get_exception_type_title,
)
from ato.tui.widgets.exception_approval_panel import ExceptionApprovalPanel

# ---------------------------------------------------------------------------
# AC1 / AC5: 三要素渲染
# ---------------------------------------------------------------------------


def test_regression_failure_panel_renders_three_elements() -> None:
    """三要素渲染：发生了什么 + 影响范围 + 选项。"""
    panel = ExceptionApprovalPanel()
    panel.update_data(
        approval_id="a1",
        story_id="story-005",
        approval_type="regression_failure",
        risk_level="high",
        payload=json.dumps(
            {"options": ["revert", "fix_forward", "pause"], "reason": "test failed"}
        ),
    )
    rendered = panel.render()
    text = rendered.plain

    # 发生了什么
    assert "Regression" in text
    assert "冻结" in text
    # 影响范围
    assert "reason: test failed" in text
    # 选项
    assert "[1]" in text
    assert "[2]" in text
    assert "[3]" in text
    assert "Revert" in text
    assert "Fix Forward" in text
    assert "Pause" in text


def test_regression_failure_panel_red_border() -> None:
    """risk_level=high → $error 边框 (via CSS class)。"""
    panel = ExceptionApprovalPanel()
    panel.update_data(
        approval_id="a1",
        story_id="story-005",
        approval_type="regression_failure",
        risk_level="high",
        payload=json.dumps({"options": ["revert", "fix_forward", "pause"]}),
    )
    assert panel.has_class("exception-approval-high")
    assert not panel.has_class("exception-approval-medium")


def test_session_timeout_panel_yellow_border() -> None:
    """risk_level=medium → $warning 边框 (via CSS class)。"""
    panel = ExceptionApprovalPanel()
    panel.update_data(
        approval_id="a2",
        story_id="story-010",
        approval_type="session_timeout",
        risk_level="medium",
        payload=json.dumps(
            {
                "task_id": "t1",
                "elapsed_seconds": 300,
                "options": ["restart", "resume", "abandon"],
            }
        ),
    )
    assert panel.has_class("exception-approval-medium")
    assert not panel.has_class("exception-approval-high")


def test_panel_title_includes_icon_and_type() -> None:
    """标题包含图标 + 类型描述。"""
    panel = ExceptionApprovalPanel()
    panel.update_data(
        approval_id="a1",
        story_id="story-005",
        approval_type="regression_failure",
        risk_level="high",
        payload=json.dumps({"options": ["revert", "fix_forward", "pause"]}),
    )
    rendered = panel.render()
    text = rendered.plain
    assert "✖" in text
    assert "REGRESSION FAILURE" in text
    assert "story-005" in text


def test_options_numbered_correctly() -> None:
    """选项带正确数字键前缀。"""
    panel = ExceptionApprovalPanel()
    panel.update_data(
        approval_id="a1",
        story_id="story-005",
        approval_type="session_timeout",
        risk_level="medium",
        payload=json.dumps({"options": ["restart", "resume", "abandon"]}),
    )
    rendered = panel.render()
    text = rendered.plain
    assert "[1]" in text
    assert "[2]" in text
    assert "[3]" in text
    assert "Restart" in text
    assert "Resume" in text
    assert "Abandon" in text


# ---------------------------------------------------------------------------
# AC5: 真实 payload 字段上下文格式化
# ---------------------------------------------------------------------------


def test_format_context_rebase_conflict_uses_conflict_files_and_stderr() -> None:
    """rebase 冲突上下文使用真实 payload 字段。"""
    payload: dict[str, object] = {
        "conflict_files": ["src/main.py", "tests/test_main.py"],
        "stderr": "CONFLICT (content): Merge conflict in src/main.py",
    }
    what, impact = get_exception_context("rebase_conflict", payload)
    assert "合并冲突" in what
    assert "conflict_files" in impact
    assert "src/main.py" in impact
    assert "stderr" in impact
    assert "CONFLICT" in impact


def test_format_context_convergent_loop_uses_round_payload() -> None:
    """escalation 上下文含 rounds/open_blocking/convergence/unresolved_findings。"""
    payload = {
        "rounds_completed": 3,
        "open_blocking_count": 2,
        "final_convergence_rate": 0.6,
        "unresolved_findings": ["f1", "f2", "f3"],
    }
    what, impact = get_exception_context("convergent_loop_escalation", payload)
    assert "Convergent Loop" in what
    assert "rounds_completed: 3" in impact
    assert "open_blocking_count: 2" in impact
    assert "final_convergence_rate: 0.6" in impact
    assert "unresolved_findings: 3" in impact


def test_format_context_needs_human_review_includes_task_id() -> None:
    """needs_human_review 包含 task_id（若存在）。"""
    payload: dict[str, object] = {
        "skill_type": "code_review",
        "parser_mode": "deterministic",
        "task_id": "task-42",
    }
    _what, impact = get_exception_context("needs_human_review", payload)
    assert "task_id: task-42" in impact
    assert "skill_type: code_review" in impact


def test_format_context_needs_human_review_design_gate_payload() -> None:
    """needs_human_review 收到 design gate payload 时展示 failure_codes/missing_files/reason。"""
    payload: dict[str, object] = {
        "task_id": "t-99",
        "artifact_dir": "/tmp/proj/s1-ux",
        "failure_codes": ["PEN_MISSING", "EXPORTS_PNG_MISSING"],
        "missing_files": ["/tmp/proj/s1-ux/prototype.pen"],
        "reason": "Design gate failed: PEN_MISSING; EXPORTS_PNG_MISSING",
        "save_report_summary": {
            "json_parse_verified": True,
            "reopen_verified": False,
        },
    }
    what, impact = get_exception_context("needs_human_review", payload)
    assert "Design gate" in what
    assert "BMAD" not in what
    assert "task_id: t-99" in impact
    assert "PEN_MISSING" in impact
    assert "EXPORTS_PNG_MISSING" in impact
    assert "missing_files:" in impact
    assert "prototype.pen" in impact
    assert "save_report_summary:" in impact
    assert "reason:" in impact


def test_format_context_needs_human_review_qa_protocol_invalid_payload() -> None:
    """needs_human_review 收到 qa_protocol_invalid payload 时展示协议违规上下文。"""
    payload: dict[str, object] = {
        "reason": "qa_protocol_invalid",
        "task_id": "t-qa-1",
        "audit_status": "invalid",
        "violation_code": "OPTIONAL_PRIORITY_VIOLATION",
        "detail": "optional commands must run first",
        "raw_output_preview": "Recommendation: Request Changes",
        "commands_executed_preview": [
            "- `uv run pytest tests/unit/` | source=project_defined | "
            "trigger=required_layer:unit | exit_code=0"
        ],
    }
    what, impact = get_exception_context("needs_human_review", payload)
    assert "QA command audit" in what
    assert "BMAD" not in what
    assert "task_id: t-qa-1" in impact
    assert "audit_status: invalid" in impact
    assert "OPTIONAL_PRIORITY_VIOLATION" in impact
    assert "raw_output_preview:" in impact
    assert "commands_executed_preview:" in impact


def test_format_context_rebase_conflict_includes_worktree_path_when_present() -> None:
    """rebase_conflict 若有 story.worktree_path，则展示到影响范围。"""
    payload: dict[str, object] = {
        "conflict_files": ["src/main.py"],
        "worktree_path": "/tmp/wt/story-1",
    }
    what, impact = get_exception_context("rebase_conflict", payload)
    assert "合并冲突" in what
    assert "worktree_path: /tmp/wt/story-1" in impact


def test_format_context_gracefully_handles_missing_fields() -> None:
    """缺失字段时省略对应行，不伪造占位符 (AC5)。"""
    # regression_failure 无 reason 字段 → impact 应为空
    what, impact = get_exception_context("regression_failure", {})
    assert "Regression" in what
    assert impact == ""
    assert "failed_test" not in impact
    assert "blocked_count" not in impact
    assert "worktree_path" not in impact

    # session_timeout 无 elapsed_seconds → 只有 task_id 一行
    what, impact = get_exception_context("session_timeout", {"task_id": "t1"})
    assert "task_id: t1" in impact
    assert "elapsed_seconds" not in impact

    # session_timeout 完全空 payload → impact 为空
    what, impact = get_exception_context("session_timeout", {})
    assert impact == ""
    assert "未知" not in impact  # 不应有占位符

    # crash_recovery 完全空 → 无占位符
    what, impact = get_exception_context("crash_recovery", {})
    assert impact == ""
    assert "未知" not in impact

    # needs_human_review 完全空 → 无占位符
    what, impact = get_exception_context("needs_human_review", {})
    assert impact == ""
    assert "未知" not in impact

    # convergent_loop_escalation 完全空 → 无 "?" 占位符
    what, impact = get_exception_context("convergent_loop_escalation", {})
    assert impact == ""
    assert "?" not in impact

    # precommit_failure 完全空 → 无空 error_output 行
    what, impact = get_exception_context("precommit_failure", {})
    assert impact == ""
    assert "error_output:" not in impact


def test_all_current_exception_types_covered() -> None:
    """所有当前多选异常类型都有 context 格式化。"""
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
        what, _impact = get_exception_context(atype, {})
        assert what, f"{atype} should have a 'what' text"
        # 每个类型的 what 不应等于 approval_type 本身（说明有专用格式化）
        assert what != atype, f"{atype} should have a human-readable 'what' text"


# ---------------------------------------------------------------------------
# 异常类型标题映射 (AC3)
# ---------------------------------------------------------------------------


def test_exception_type_titles() -> None:
    """所有异常类型的标题映射正确。"""
    assert get_exception_type_title("regression_failure") == "REGRESSION FAILURE"
    assert get_exception_type_title("session_timeout") == "SESSION TIMEOUT"
    assert get_exception_type_title("crash_recovery") == "CRASH RECOVERY"
    assert get_exception_type_title("precommit_failure") == "PRE-COMMIT FAILURE"
    assert get_exception_type_title("rebase_conflict") == "REBASE CONFLICT"
    assert get_exception_type_title("needs_human_review") == "NEEDS HUMAN REVIEW"
    assert get_exception_type_title("convergent_loop_escalation") == "CONVERGENT LOOP ESCALATION"


# ---------------------------------------------------------------------------
# ExceptionApprovalPanel 底部提示
# ---------------------------------------------------------------------------


def test_panel_footer_hint() -> None:
    """底部显示数字键选择 + d 键更多上下文。"""
    panel = ExceptionApprovalPanel()
    panel.update_data(
        approval_id="a1",
        story_id="story-005",
        approval_type="regression_failure",
        risk_level="high",
        payload=json.dumps({"options": ["revert", "fix_forward", "pause"]}),
    )
    rendered = panel.render()
    text = rendered.plain
    assert "按 1/2/3 选择" in text
    assert "[d] 查看更多上下文" in text


# ---------------------------------------------------------------------------
# 展开上下文
# ---------------------------------------------------------------------------


def test_expanded_context_shows_stderr() -> None:
    """展开态显示 stderr 内容。"""
    panel = ExceptionApprovalPanel()
    panel.update_data(
        approval_id="a1",
        story_id="story-005",
        approval_type="rebase_conflict",
        risk_level="high",
        payload=json.dumps(
            {
                "conflict_files": ["a.py"],
                "stderr": "CONFLICT in a.py",
                "options": ["manual_resolve", "skip", "abandon"],
            }
        ),
        expanded_context=True,
    )
    rendered = panel.render()
    text = rendered.plain
    assert "更多上下文" in text
    assert "CONFLICT in a.py" in text


# ---------------------------------------------------------------------------
# risk_level 默认无 class
# ---------------------------------------------------------------------------


def test_no_risk_class_for_low_risk() -> None:
    """risk_level=low 或 None 不附加 high/medium class。"""
    panel = ExceptionApprovalPanel()
    panel.update_data(
        approval_id="a1",
        story_id="story-005",
        approval_type="needs_human_review",
        risk_level="low",
        payload=json.dumps({"options": ["retry", "skip", "escalate"]}),
    )
    assert not panel.has_class("exception-approval-high")
    assert not panel.has_class("exception-approval-medium")


# ---------------------------------------------------------------------------
# 梯度降级 escalated stage 文案
# ---------------------------------------------------------------------------


def test_convergent_loop_escalated_context() -> None:
    """escalated stage 的 convergent_loop_escalation 显示梯度降级文案。"""
    what, impact = get_exception_context(
        "convergent_loop_escalation",
        {
            "stage": "escalated",
            "rounds_completed": 6,
            "open_blocking_count": 2,
            "standard_round_summaries": [{"round": 1}, {"round": 2}, {"round": 3}],
            "escalated_round_summaries": [{"round": 4}, {"round": 5}, {"round": 6}],
        },
    )
    assert "梯度降级" in what
    assert "stage: escalated" in impact
    assert "standard_rounds: 3" in impact
    assert "escalated_rounds: 3" in impact
