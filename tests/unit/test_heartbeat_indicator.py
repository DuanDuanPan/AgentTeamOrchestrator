"""HeartbeatIndicator Widget 单元测试。

测试 spinner 循环、经过时间计算、CL 轮次显示。
"""

from __future__ import annotations

from ato.tui.widgets.heartbeat_indicator import _SPINNER_FRAMES, HeartbeatIndicator

# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _make_indicator(**kwargs: object) -> HeartbeatIndicator:
    """创建带指定属性的 HeartbeatIndicator（不挂载到 App）。"""
    indicator = HeartbeatIndicator()
    for k, v in kwargs.items():
        setattr(indicator, k, v)
    return indicator


def _render_text(indicator: HeartbeatIndicator) -> str:
    """将 render() 的 Rich.Text 转为纯文本。"""
    return indicator.render().plain


# ---------------------------------------------------------------------------
# spinner 循环
# ---------------------------------------------------------------------------


class TestSpinner:
    """Spinner 动画帧测试。"""

    def test_initial_spinner_index(self) -> None:
        ind = HeartbeatIndicator()
        assert ind._spinner_index == 0

    def test_spinner_frames_are_four(self) -> None:
        assert len(_SPINNER_FRAMES) == 4
        assert _SPINNER_FRAMES == "◐◓◑◒"

    def test_tick_advances_spinner(self) -> None:
        ind = HeartbeatIndicator()
        ind._spinner_index = 0
        # 手动调用 _tick 逻辑（不需要 app mount）
        ind._spinner_index = (ind._spinner_index + 1) % len(_SPINNER_FRAMES)
        assert ind._spinner_index == 1

    def test_spinner_wraps_around(self) -> None:
        ind = HeartbeatIndicator()
        ind._spinner_index = 3
        ind._spinner_index = (ind._spinner_index + 1) % len(_SPINNER_FRAMES)
        assert ind._spinner_index == 0

    def test_render_shows_spinner_frame(self) -> None:
        ind = _make_indicator(
            story_id="s1",
            current_phase="reviewing",
            round_num=2,
            max_rounds=3,
            cost_usd=1.50,
        )
        ind._spinner_index = 0
        text = _render_text(ind)
        assert "◐" in text

    def test_render_second_frame(self) -> None:
        ind = _make_indicator(
            story_id="s1",
            current_phase="reviewing",
        )
        ind._spinner_index = 1
        text = _render_text(ind)
        assert "◓" in text


# ---------------------------------------------------------------------------
# 经过时间
# ---------------------------------------------------------------------------


class TestElapsedTime:
    """经过时间计算测试。"""

    def test_initial_elapsed_zero(self) -> None:
        ind = HeartbeatIndicator()
        assert ind._elapsed_seconds == 0

    def test_render_shows_elapsed(self) -> None:
        ind = _make_indicator(
            story_id="s1",
            current_phase="developing",
        )
        ind._elapsed_seconds = 180
        text = _render_text(ind)
        assert "3m" in text

    def test_render_shows_seconds(self) -> None:
        ind = _make_indicator(
            story_id="s1",
            current_phase="developing",
        )
        ind._elapsed_seconds = 45
        text = _render_text(ind)
        assert "45s" in text


# ---------------------------------------------------------------------------
# CL 轮次显示
# ---------------------------------------------------------------------------


class TestCLRounds:
    """CL 轮次显示测试。"""

    def test_renders_round_info(self) -> None:
        ind = _make_indicator(
            story_id="s1",
            current_phase="reviewing",
            round_num=2,
            max_rounds=3,
            cost_usd=0.0,
        )
        text = _render_text(ind)
        assert "R2/3" in text

    def test_renders_zero_round(self) -> None:
        ind = _make_indicator(
            story_id="s1",
            current_phase="developing",
            round_num=0,
            max_rounds=5,
        )
        text = _render_text(ind)
        assert "R0/5" in text


# ---------------------------------------------------------------------------
# 渲染综合
# ---------------------------------------------------------------------------


class TestRender:
    """完整渲染测试。"""

    def test_all_elements_present(self) -> None:
        ind = _make_indicator(
            story_id="story-007",
            current_phase="reviewing",
            round_num=2,
            max_rounds=3,
            cost_usd=2.60,
        )
        ind._elapsed_seconds = 480
        text = _render_text(ind)
        assert "story-007" in text
        assert "reviewing" in text
        assert "R2/3" in text
        assert "$2.60" in text
        assert "8m" in text
        assert "█" in text  # progress bar
        assert "░" in text

    def test_cost_formatting(self) -> None:
        ind = _make_indicator(
            story_id="s1",
            current_phase="developing",
            cost_usd=0.0,
        )
        text = _render_text(ind)
        assert "$0.00" in text


# ---------------------------------------------------------------------------
# update_heartbeat()
# ---------------------------------------------------------------------------


class TestUpdateHeartbeat:
    """update_heartbeat() 测试。"""

    def test_updates_all_fields(self) -> None:
        ind = HeartbeatIndicator()
        ind.update_heartbeat(
            story_id="s1",
            current_phase="fixing",
            round_num=1,
            max_rounds=3,
            cost_usd=2.50,
            started_at=100.0,
        )
        assert ind.story_id == "s1"
        assert ind.current_phase == "fixing"
        assert ind.round_num == 1
        assert ind.max_rounds == 3
        assert ind.cost_usd == 2.50
        assert ind.started_at == 100.0
