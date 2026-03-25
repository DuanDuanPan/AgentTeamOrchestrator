"""ThreeQuestionHeader Widget 单元测试。

测试四区域渲染、可判定状态逻辑、三种 display_mode 格式和 update_data()。
"""

from __future__ import annotations

from ato.tui.widgets.three_question_header import ThreeQuestionHeader

# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _make_header(**kwargs: object) -> ThreeQuestionHeader:
    """创建带指定属性的 ThreeQuestionHeader（不挂载到 App）。"""
    header = ThreeQuestionHeader()
    for k, v in kwargs.items():
        setattr(header, k, v)
    return header


def _render_text(header: ThreeQuestionHeader) -> str:
    """将 render() 的 Rich.Text 转为纯文本。"""
    return header.render().plain


# ---------------------------------------------------------------------------
# Task 6.2: 四区域内容正确渲染
# ---------------------------------------------------------------------------


class TestFullModeRendering:
    """full 模式（180+ 列）渲染测试。"""

    def test_running_state(self) -> None:
        h = _make_header(
            running_count=3,
            error_count=0,
            pending_approvals=2,
            today_cost_usd=12.50,
            seconds_ago=2,
            display_mode="full",
        )
        text = _render_text(h)
        assert "● 3 项运行中" in text
        assert "◆ 2 审批等待" in text
        assert "$12.50 今日" in text
        assert "更新 2s前" in text
        assert "│" in text

    def test_error_state(self) -> None:
        h = _make_header(
            running_count=1,
            error_count=2,
            pending_approvals=0,
            today_cost_usd=5.0,
            seconds_ago=10,
            display_mode="full",
        )
        text = _render_text(h)
        assert "✖ 2 项异常" in text
        assert "✔ 无待处理" in text

    def test_idle_state(self) -> None:
        h = _make_header(
            running_count=0,
            error_count=0,
            pending_approvals=0,
            today_cost_usd=0.0,
            seconds_ago=0,
            display_mode="full",
        )
        text = _render_text(h)
        assert "● 空闲" in text
        assert "✔ 无待处理" in text
        assert "$0.00 今日" in text

    def test_zero_cost(self) -> None:
        h = _make_header(
            running_count=1,
            error_count=0,
            pending_approvals=0,
            today_cost_usd=0.0,
            seconds_ago=5,
            display_mode="full",
        )
        text = _render_text(h)
        assert "$0.00 今日" in text


# ---------------------------------------------------------------------------
# Task 6.3: 可判定状态显示逻辑
# ---------------------------------------------------------------------------


class TestStatusLogic:
    """可判定状态测试：全正常/有异常/有审批/无审批/空闲。"""

    def test_all_normal_shows_running(self) -> None:
        h = _make_header(running_count=5, error_count=0, display_mode="full")
        text = _render_text(h)
        assert "● 5 项运行中" in text
        assert "✖" not in text

    def test_error_takes_precedence(self) -> None:
        """error_count > 0 时，即使 running_count > 0 也显示异常。"""
        h = _make_header(running_count=3, error_count=1, display_mode="full")
        text = _render_text(h)
        assert "✖ 1 项异常" in text
        assert "● 3 项运行中" not in text

    def test_idle_not_paused(self) -> None:
        """running=0 且 error=0 不显示暂停，只显示空闲。"""
        h = _make_header(running_count=0, error_count=0, display_mode="full")
        text = _render_text(h)
        assert "● 空闲" in text
        assert "⏸" not in text

    def test_has_approvals(self) -> None:
        h = _make_header(pending_approvals=3, display_mode="full")
        text = _render_text(h)
        assert "◆ 3 审批等待" in text

    def test_no_approvals(self) -> None:
        h = _make_header(pending_approvals=0, display_mode="full")
        text = _render_text(h)
        assert "✔ 无待处理" in text


# ---------------------------------------------------------------------------
# Task 6.4: 三种 display_mode 格式
# ---------------------------------------------------------------------------


class TestDisplayModes:
    """测试 full/compact/minimal 三种模式的格式输出。"""

    def test_compact_mode(self) -> None:
        h = _make_header(
            running_count=3,
            error_count=0,
            pending_approvals=2,
            today_cost_usd=12.50,
            seconds_ago=2,
            display_mode="compact",
        )
        text = _render_text(h)
        assert "● 3运行" in text
        assert "◆ 2审批" in text
        assert "$12.50" in text
        assert "2s" in text
        # compact 不该有完整标签
        assert "项运行中" not in text

    def test_minimal_mode(self) -> None:
        h = _make_header(
            running_count=3,
            error_count=0,
            pending_approvals=2,
            today_cost_usd=12.50,
            seconds_ago=2,
            display_mode="minimal",
        )
        text = _render_text(h)
        assert "● 3" in text
        assert "◆ 2" in text
        assert "$12.50" in text
        assert "2s" in text
        # minimal 不该有竖线分隔
        assert "│" not in text

    def test_compact_error_state(self) -> None:
        h = _make_header(
            running_count=1, error_count=2, pending_approvals=0, display_mode="compact"
        )
        text = _render_text(h)
        assert "✖ 2异常" in text
        assert "✔ 无待处理" in text

    def test_minimal_error_state(self) -> None:
        h = _make_header(
            running_count=1, error_count=2, pending_approvals=0, display_mode="minimal"
        )
        text = _render_text(h)
        assert "✖ 2" in text
        assert "✔ 0" in text

    def test_minimal_idle_state(self) -> None:
        h = _make_header(
            running_count=0, error_count=0, pending_approvals=0, display_mode="minimal"
        )
        text = _render_text(h)
        assert "● 0" in text
        assert "✔ 0" in text


# ---------------------------------------------------------------------------
# Task 6.5: update_data() 正确更新 reactive 属性
# ---------------------------------------------------------------------------


class TestUpdateData:
    """update_data() 方法正确更新所有 reactive 属性。"""

    def test_update_data_sets_all_fields(self) -> None:
        h = ThreeQuestionHeader()
        h.update_data(
            running_count=5,
            error_count=2,
            pending_approvals=3,
            today_cost_usd=42.50,
            seconds_ago=10,
        )
        assert h.running_count == 5
        assert h.error_count == 2
        assert h.pending_approvals == 3
        assert h.today_cost_usd == 42.50
        assert h.seconds_ago == 10

    def test_update_data_reflects_in_render(self) -> None:
        h = ThreeQuestionHeader()
        h.update_data(
            running_count=1,
            error_count=0,
            pending_approvals=0,
            today_cost_usd=7.25,
            seconds_ago=5,
        )
        text = _render_text(h)
        assert "● 1 项运行中" in text
        assert "$7.25 今日" in text

    def test_set_display_mode_valid(self) -> None:
        h = ThreeQuestionHeader()
        h.set_display_mode("compact")
        assert h.display_mode == "compact"
        h.set_display_mode("minimal")
        assert h.display_mode == "minimal"
        h.set_display_mode("full")
        assert h.display_mode == "full"

    def test_set_display_mode_invalid_ignored(self) -> None:
        h = ThreeQuestionHeader()
        h.set_display_mode("invalid")
        assert h.display_mode == "full"  # default unchanged
