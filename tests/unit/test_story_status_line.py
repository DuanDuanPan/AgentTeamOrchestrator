"""StoryStatusLine Widget 单元测试。

测试状态图标/颜色正确渲染、进度条计算、耗时/成本格式。
"""

from __future__ import annotations

from ato.tui.widgets.story_status_line import (
    PHASE_ORDER,
    StoryStatusLine,
    _compute_progress,
    _format_elapsed,
    _render_progress_bar,
)

# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _make_line(**kwargs: object) -> StoryStatusLine:
    """创建带指定属性的 StoryStatusLine（不挂载到 App）。"""
    line = StoryStatusLine()
    for k, v in kwargs.items():
        setattr(line, k, v)
    return line


def _render_text(line: StoryStatusLine) -> str:
    """将 render() 的 Rich.Text 转为纯文本。"""
    return line.render().plain


# ---------------------------------------------------------------------------
# 进度条计算
# ---------------------------------------------------------------------------


class TestComputeProgress:
    """_compute_progress 测试。"""

    def test_queued_is_zero(self) -> None:
        assert _compute_progress("queued") == 0.0

    def test_done_is_one(self) -> None:
        assert _compute_progress("done") == 1.0

    def test_developing_is_midway(self) -> None:
        idx = PHASE_ORDER.index("developing")
        expected = idx / (len(PHASE_ORDER) - 1)
        assert _compute_progress("developing") == expected

    def test_unknown_phase_returns_zero(self) -> None:
        assert _compute_progress("unknown_phase") == 0.0

    def test_all_phases_monotonically_increase(self) -> None:
        values = [_compute_progress(p) for p in PHASE_ORDER]
        for i in range(1, len(values)):
            assert values[i] >= values[i - 1], f"{PHASE_ORDER[i]} should >= {PHASE_ORDER[i - 1]}"


# ---------------------------------------------------------------------------
# 进度条渲染
# ---------------------------------------------------------------------------


class TestRenderProgressBar:
    """_render_progress_bar 测试。"""

    def test_zero_progress(self) -> None:
        bar = _render_progress_bar(0.0)
        assert len(bar) == 10
        assert bar == "░" * 10

    def test_full_progress(self) -> None:
        bar = _render_progress_bar(1.0)
        assert len(bar) == 10
        assert bar == "█" * 10

    def test_half_progress(self) -> None:
        bar = _render_progress_bar(0.5)
        assert len(bar) == 10
        assert bar.count("█") == 5
        assert bar.count("░") == 5


# ---------------------------------------------------------------------------
# 耗时格式
# ---------------------------------------------------------------------------


class TestFormatElapsed:
    """_format_elapsed 测试。"""

    def test_seconds(self) -> None:
        assert _format_elapsed(30) == "30s"

    def test_zero(self) -> None:
        assert _format_elapsed(0) == "0s"

    def test_minutes(self) -> None:
        assert _format_elapsed(120) == "2m"

    def test_hours_and_minutes(self) -> None:
        assert _format_elapsed(3720) == "1h 2m"

    def test_exactly_one_minute(self) -> None:
        assert _format_elapsed(60) == "1m"

    def test_exactly_one_hour(self) -> None:
        assert _format_elapsed(3600) == "1h 0m"


# ---------------------------------------------------------------------------
# render() 渲染
# ---------------------------------------------------------------------------


class TestRender:
    """StoryStatusLine.render() 测试。"""

    def test_in_progress_shows_running_icon(self) -> None:
        line = _make_line(
            story_id="story-001",
            status="in_progress",
            current_phase="developing",
            cost_usd=2.50,
            elapsed_seconds=300,
        )
        text = _render_text(line)
        assert "●" in text  # running icon
        assert "story-001" in text
        assert "developing" in text
        assert "$2.50" in text
        assert "5m" in text

    def test_done_shows_checkmark(self) -> None:
        line = _make_line(
            story_id="story-002",
            status="done",
            current_phase="done",
            cost_usd=3.20,
            elapsed_seconds=1500,
        )
        text = _render_text(line)
        assert "✔" in text
        assert "story-002" in text
        assert "$3.20" in text

    def test_blocked_shows_frozen_icon(self) -> None:
        line = _make_line(
            story_id="story-003",
            status="blocked",
            current_phase="reviewing",
            cost_usd=5.10,
            elapsed_seconds=2520,
        )
        text = _render_text(line)
        assert "⏸" in text  # frozen icon
        assert "story-003" in text

    def test_backlog_shows_info_icon(self) -> None:
        line = _make_line(
            story_id="story-004",
            status="backlog",
            current_phase="queued",
            cost_usd=0.0,
            elapsed_seconds=0,
        )
        text = _render_text(line)
        assert "ℹ" in text

    def test_uat_shows_awaiting_icon(self) -> None:
        line = _make_line(
            story_id="story-005",
            status="uat",
            current_phase="uat",
            cost_usd=1.00,
            elapsed_seconds=600,
        )
        text = _render_text(line)
        assert "◆" in text  # awaiting icon

    def test_progress_bar_in_output(self) -> None:
        line = _make_line(
            story_id="s1",
            status="in_progress",
            current_phase="reviewing",
        )
        text = _render_text(line)
        assert "█" in text
        assert "░" in text


# ---------------------------------------------------------------------------
# update_data()
# ---------------------------------------------------------------------------


class TestUpdateData:
    """update_data() 批量更新测试。"""

    def test_updates_all_fields(self) -> None:
        line = StoryStatusLine()
        line.update_data(
            story_id="s1",
            status="in_progress",
            current_phase="developing",
            cost_usd=4.50,
            elapsed_seconds=120,
            cl_round=2,
            cl_max_rounds=5,
        )
        assert line.story_id == "s1"
        assert line.status == "in_progress"
        assert line.current_phase == "developing"
        assert line.cost_usd == 4.50
        assert line.elapsed_seconds == 120
        assert line.cl_round == 2
        assert line.cl_max_rounds == 5
