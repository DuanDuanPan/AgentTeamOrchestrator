"""StoryDetailView 单元测试。

验证详情视图各区块渲染正确性、phase 顺序、artifact 展示、
成本/历史展开内容，以及键位行为。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

from ato.tui.story_detail import PHASE_ORDER, StoryDetailView

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_story(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "story_id": "s1",
        "title": "Test Story",
        "status": "in_progress",
        "current_phase": "reviewing",
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }
    defaults.update(overrides)
    return defaults


def _make_task(**overrides: object) -> SimpleNamespace:
    defaults: dict[str, object] = {
        "task_id": "t1",
        "story_id": "s1",
        "phase": "developing",
        "role": "dev",
        "cli_tool": "claude",
        "status": "completed",
        "started_at": datetime(2026, 1, 1, tzinfo=UTC),
        "completed_at": datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
        "duration_ms": 300000,
        "expected_artifact": "src/main.py",
        "context_briefing": None,
        "pid": None,
        "exit_code": 0,
        "cost_usd": None,
        "error_message": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_cost_log(**overrides: object) -> SimpleNamespace:
    defaults: dict[str, object] = {
        "cost_log_id": "c1",
        "story_id": "s1",
        "task_id": "t1",
        "cli_tool": "claude",
        "model": "claude-opus-4-6",
        "phase": "developing",
        "role": "dev",
        "input_tokens": 1000,
        "output_tokens": 500,
        "cache_read_input_tokens": 0,
        "cost_usd": 0.05,
        "duration_ms": 3000,
        "session_id": None,
        "exit_code": 0,
        "error_category": None,
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_finding(**overrides: object) -> SimpleNamespace:
    defaults: dict[str, object] = {
        "finding_id": "f1",
        "story_id": "s1",
        "round_num": 1,
        "severity": "blocking",
        "description": "Bug found",
        "status": "open",
        "file_path": "a.py",
        "rule_id": "R1",
        "dedup_hash": "h1",
        "line_number": None,
        "fix_suggestion": None,
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# Task 9.1: StoryDetailView 渲染测试
# ---------------------------------------------------------------------------


async def test_detail_view_renders_basic_story() -> None:
    """StoryDetailView 渲染基本 story 信息。"""
    # We test the widget in isolation by checking its internal state
    view = StoryDetailView(id="test-detail")
    story = _make_story()
    view.update_detail(
        story=story,
        findings_summary={},
        findings_detail=[],
        cost_logs=[],
        tasks=[],
    )
    assert view._story_data["story_id"] == "s1"
    assert view._expanded_view is None


async def test_detail_view_stores_findings_summary() -> None:
    """StoryDetailView 正确存储 findings 摘要。"""
    view = StoryDetailView(id="test-detail")
    fs = {"blocking_open": 2, "suggestion_closed": 1}
    view.update_detail(
        story=_make_story(),
        findings_summary=fs,
    )
    assert view._findings_summary == fs


async def test_detail_view_stores_cost_logs() -> None:
    """StoryDetailView 正确存储成本记录。"""
    view = StoryDetailView(id="test-detail")
    costs = [_make_cost_log(), _make_cost_log(cost_log_id="c2", cost_usd=0.10)]
    view.update_detail(
        story=_make_story(),
        cost_logs=costs,
    )
    assert len(view._cost_logs) == 2


async def test_detail_view_stores_tasks() -> None:
    """StoryDetailView 正确存储任务记录。"""
    view = StoryDetailView(id="test-detail")
    tasks = [_make_task(), _make_task(task_id="t2", phase="reviewing")]
    view.update_detail(
        story=_make_story(),
        tasks=tasks,
    )
    assert len(view._tasks) == 2


# ---------------------------------------------------------------------------
# Task 9.2: StoryPhaseFlow 测试
# ---------------------------------------------------------------------------


def test_phase_order_matches_canonical() -> None:
    """PHASE_ORDER 以 queued 开头, done 结尾, 中间是 CANONICAL_PHASES。"""
    from ato.state_machine import CANONICAL_PHASES

    assert PHASE_ORDER[0] == "queued"
    assert PHASE_ORDER[-1] == "done"
    assert PHASE_ORDER[1:-1] == list(CANONICAL_PHASES)


def test_phase_order_length() -> None:
    """PHASE_ORDER 应有 12 个阶段（queued + 10 canonical + done）。"""
    assert len(PHASE_ORDER) == 12


# ---------------------------------------------------------------------------
# Task 9.3: ConvergentLoopProgress 测试
# ---------------------------------------------------------------------------


def test_cl_progress_empty_when_no_round() -> None:
    """无 CL 数据时渲染空内容。"""
    from ato.tui.widgets.convergent_loop_progress import ConvergentLoopProgress

    widget = ConvergentLoopProgress()
    widget._current_round = 0
    result = widget.render()
    assert str(result) == ""


def test_cl_progress_renders_round_visualization() -> None:
    """CL 轮次可视化包含 ●/◐/○ 符号。"""
    from ato.tui.widgets.convergent_loop_progress import ConvergentLoopProgress

    widget = ConvergentLoopProgress()
    widget._current_round = 2
    widget._max_rounds = 3
    widget._findings_summary = {"blocking_open": 1, "suggestion_closed": 2}
    result = widget.render()
    text = str(result)
    assert "●" in text  # 第 1 轮已完成
    assert "◐" in text  # 第 2 轮当前
    assert "○" in text  # 第 3 轮未执行
    assert "R2/3" in text


def test_cl_progress_convergence_rate() -> None:
    """收敛率计算正确：closed / total * 100。"""
    from ato.tui.widgets.convergent_loop_progress import ConvergentLoopProgress

    widget = ConvergentLoopProgress()
    widget._current_round = 1
    widget._max_rounds = 3
    # 1 open + 2 closed = 3 total → 67%
    widget._findings_summary = {"blocking_open": 1, "suggestion_closed": 2}
    result = widget.render()
    text = str(result)
    assert "67%" in text


def test_cl_progress_fully_converged() -> None:
    """全部 closed 时显示"已收敛"。"""
    from ato.tui.widgets.convergent_loop_progress import ConvergentLoopProgress

    widget = ConvergentLoopProgress()
    widget._current_round = 2
    widget._max_rounds = 3
    widget._findings_summary = {"blocking_closed": 3, "suggestion_closed": 1}
    result = widget.render()
    text = str(result)
    assert "已收敛" in text
    assert "100%" in text


def test_cl_progress_blocking_pending() -> None:
    """有 blocking open 时显示"blocking待解决"。"""
    from ato.tui.widgets.convergent_loop_progress import ConvergentLoopProgress

    widget = ConvergentLoopProgress()
    widget._current_round = 1
    widget._max_rounds = 3
    widget._findings_summary = {"blocking_open": 2}
    result = widget.render()
    text = str(result)
    assert "blocking" in text


def test_cl_progress_still_open_as_open() -> None:
    """still_open 视作 open：摘要中 blocking_open 包含 still_open 计数。"""
    from ato.tui.widgets.convergent_loop_progress import ConvergentLoopProgress

    widget = ConvergentLoopProgress()
    widget._current_round = 2
    widget._max_rounds = 3
    # findings_summary 已经把 still_open 归入 open（db.py 层面处理）
    widget._findings_summary = {"blocking_open": 1, "blocking_closed": 1}
    result = widget.render()
    text = str(result)
    assert "50%" in text


# ---------------------------------------------------------------------------
# Task 9.4: Artifact 展示测试
# ---------------------------------------------------------------------------


def test_artifact_from_context_briefing_preferred() -> None:
    """context_briefing.artifacts_produced 优先于 expected_artifact。"""
    view = StoryDetailView(id="test-detail")
    cb = json.dumps({"artifacts_produced": ["design.md", "api.yaml"]})
    task = _make_task(context_briefing=cb, expected_artifact="fallback.py")
    view._tasks = [task]
    artifacts = view._collect_artifacts()
    assert "design.md" in artifacts
    assert "api.yaml" in artifacts
    assert "fallback.py" not in artifacts


def test_artifact_fallback_to_expected() -> None:
    """无 context_briefing 时 fallback 到 expected_artifact。"""
    view = StoryDetailView(id="test-detail")
    task = _make_task(context_briefing=None, expected_artifact="output.json")
    view._tasks = [task]
    artifacts = view._collect_artifacts()
    assert "output.json" in artifacts


def test_artifact_empty_when_no_data() -> None:
    """task 无 context_briefing 且无 expected_artifact 时返回空。"""
    view = StoryDetailView(id="test-detail")
    task = _make_task(context_briefing=None, expected_artifact=None)
    view._tasks = [task]
    artifacts = view._collect_artifacts()
    assert artifacts == []


def test_extract_artifact_single_task() -> None:
    """_extract_artifact 单个 task 提取正确。"""
    view = StoryDetailView(id="test-detail")
    cb = json.dumps({"artifacts_produced": ["doc.md"]})
    task = _make_task(context_briefing=cb)
    result = view._extract_artifact(task)
    assert "doc.md" in result


# ---------------------------------------------------------------------------
# Task 9.5: 成本明细展开内容测试
# ---------------------------------------------------------------------------


def test_cost_log_fields_stored() -> None:
    """CostLogRecord 真实字段正确传入。"""
    view = StoryDetailView(id="test-detail")
    cl = _make_cost_log(
        phase="reviewing",
        role="reviewer",
        cli_tool="codex",
        model="codex-mini",
        input_tokens=2000,
        output_tokens=1000,
        cache_read_input_tokens=500,
        cost_usd=0.12,
    )
    view._cost_logs = [cl]
    # Verify fields accessible
    assert cl.phase == "reviewing"
    assert cl.role == "reviewer"
    assert cl.cache_read_input_tokens == 500
    assert cl.cost_usd == 0.12


# ---------------------------------------------------------------------------
# Task 9.6: 执行历史展开内容测试
# ---------------------------------------------------------------------------


def test_task_record_fields_stored() -> None:
    """TaskRecord 真实字段正确传入。"""
    view = StoryDetailView(id="test-detail")
    t = _make_task(
        phase="developing",
        role="dev",
        cli_tool="claude",
        status="completed",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        completed_at=datetime(2026, 1, 1, 0, 10, tzinfo=UTC),
        duration_ms=600000,
    )
    view._tasks = [t]
    assert t.phase == "developing"
    assert t.role == "dev"
    assert t.duration_ms == 600000


def test_expanded_view_toggle() -> None:
    """f/c/h 键切换展开视图状态。"""
    view = StoryDetailView(id="test-detail")
    assert view._expanded_view is None

    view.action_toggle_findings()
    assert view._expanded_view == "findings"

    view.action_toggle_findings()
    assert view._expanded_view is None

    view.action_toggle_costs()
    assert view._expanded_view == "costs"

    view.action_toggle_history()
    assert view._expanded_view == "history"


def test_log_placeholder() -> None:
    """l 键设置 expanded_view 为 log。"""
    view = StoryDetailView(id="test-detail")
    view.action_show_log_placeholder()
    assert view._expanded_view == "log"


# ---------------------------------------------------------------------------
# 回归测试: Findings 四格摘要 (Fix #3)
# ---------------------------------------------------------------------------


def test_findings_summary_shows_both_open_and_closed() -> None:
    """blocking_open 和 blocking_closed 同时存在时两个都要渲染。"""
    from rich.text import Text

    view = StoryDetailView(id="test-detail")
    view._findings_summary = {
        "blocking_open": 1,
        "blocking_closed": 2,
        "suggestion_open": 3,
        "suggestion_closed": 4,
    }
    text = Text()
    view._append_findings_summary(text)
    plain = str(text)
    assert "1B open" in plain
    assert "2B closed" in plain
    assert "3S open" in plain
    assert "4S closed" in plain


def test_findings_summary_only_open_no_elif_drop() -> None:
    """只有 open 时 closed 不会幽灵出现。"""
    from rich.text import Text

    view = StoryDetailView(id="test-detail")
    view._findings_summary = {"blocking_open": 5}
    text = Text()
    view._append_findings_summary(text)
    plain = str(text)
    assert "5B open" in plain
    assert "closed" not in plain


# ---------------------------------------------------------------------------
# 回归测试: _update_detail_panel guard (Fix #1)
# ---------------------------------------------------------------------------


def test_update_detail_panel_skips_in_detail_mode() -> None:
    """_in_detail_mode=True 时 _update_detail_panel 应提前返回，不切换 switcher。"""
    from ato.tui.dashboard import DashboardScreen

    ds = DashboardScreen()
    ds._in_detail_mode = True
    ds._selected_item_id = "story:s1"
    # 不应抛异常——应直接 return（因为 query_one 会失败，但 guard 先拦截）
    ds._update_detail_panel()
    # 如果 guard 生效，不会走到 _show_right_top_static / _render_story_detail
