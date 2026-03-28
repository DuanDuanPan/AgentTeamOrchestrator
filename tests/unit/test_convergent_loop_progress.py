"""ConvergentLoopProgress 单元测试。

验证轮次可视化、去重后 findings 统计、收敛率计算。
"""

from __future__ import annotations

from ato.tui.widgets.convergent_loop_progress import ConvergentLoopProgress


def test_empty_round_returns_empty() -> None:
    """current_round=0 时渲染空字符串。"""
    w = ConvergentLoopProgress()
    w.update_progress(current_round=0, max_rounds=3, findings_summary={})
    assert str(w.render()) == ""


def test_single_round_renders_current() -> None:
    """单轮显示 ◐ 当前。"""
    w = ConvergentLoopProgress()
    w.update_progress(current_round=1, max_rounds=3, findings_summary={})
    text = str(w.render())
    assert "◐" in text
    assert "R1/3" in text


def test_completed_rounds_show_filled() -> None:
    """已完成轮次显示 ●。"""
    w = ConvergentLoopProgress()
    w.update_progress(current_round=3, max_rounds=3, findings_summary={})
    text = str(w.render())
    assert text.count("●") == 2  # round 1, 2 completed
    assert "◐" in text  # round 3 is current


def test_unexecuted_rounds_show_empty() -> None:
    """未执行轮次显示 ○。"""
    w = ConvergentLoopProgress()
    w.update_progress(current_round=1, max_rounds=4, findings_summary={})
    text = str(w.render())
    assert text.count("○") == 3  # rounds 2, 3, 4


def test_convergence_rate_zero_open() -> None:
    """全部 closed → 100% 收敛 + 已收敛标签。"""
    w = ConvergentLoopProgress()
    w.update_progress(
        current_round=2,
        max_rounds=3,
        findings_summary={"blocking_closed": 2, "suggestion_closed": 3},
    )
    text = str(w.render())
    assert "100%" in text
    assert "已收敛" in text


def test_convergence_rate_mixed() -> None:
    """混合 open/closed → 正确百分比。"""
    w = ConvergentLoopProgress()
    # 2 open + 3 closed = 5 total → 60%
    w.update_progress(
        current_round=2,
        max_rounds=3,
        findings_summary={"blocking_open": 2, "suggestion_closed": 3},
    )
    text = str(w.render())
    assert "60%" in text


def test_convergence_rate_all_open() -> None:
    """全部 open → 0% 收敛。"""
    w = ConvergentLoopProgress()
    w.update_progress(
        current_round=1,
        max_rounds=3,
        findings_summary={"blocking_open": 5},
    )
    text = str(w.render())
    assert "0%" in text
    assert "blocking" in text


def test_no_findings_no_rate() -> None:
    """无 findings 时不显示收敛率。"""
    w = ConvergentLoopProgress()
    w.update_progress(current_round=1, max_rounds=3, findings_summary={})
    text = str(w.render())
    assert "%" not in text


def test_update_progress_method() -> None:
    """update_progress 更新内部状态。"""
    w = ConvergentLoopProgress()
    w.update_progress(
        current_round=2,
        max_rounds=5,
        findings_summary={"blocking_open": 1},
    )
    assert w._current_round == 2
    assert w._max_rounds == 5
    assert w._findings_summary == {"blocking_open": 1}
