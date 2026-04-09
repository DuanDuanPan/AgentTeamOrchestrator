"""SearchPanel Widget 单元测试。

直接实例化 Widget，验证 compose 结构、update_items、
open/close 行为和 _format_result 输出。
"""

from __future__ import annotations

from ato.tui.widgets.search_panel import (
    SearchableItem,
    SearchPanel,
    SearchResult,
    _format_result,
)

# ---------------------------------------------------------------------------
# SearchPanel 基础结构
# ---------------------------------------------------------------------------


def test_search_panel_has_input_and_option_list() -> None:
    """SearchPanel 包含 Input 和 OptionList 子组件。"""
    panel = SearchPanel()
    children = list(panel.compose())
    # Should yield Input and OptionList
    from textual.widgets import Input, OptionList

    types = [type(c) for c in children]
    assert Input in types
    assert OptionList in types


def test_search_panel_initial_state() -> None:
    """初始 _items 和 _results 为空列表。"""
    panel = SearchPanel()
    assert panel._items == []
    assert panel._results == []


# ---------------------------------------------------------------------------
# update_items
# ---------------------------------------------------------------------------


def test_update_items_stories() -> None:
    """update_items 正确解析 story 数据。"""
    panel = SearchPanel()
    stories: list[dict[str, object]] = [
        {
            "story_id": "story-001",
            "title": "Test",
            "status": "in_progress",
            "current_phase": "developing",
        },
    ]
    panel.update_items(sorted_stories=stories, sorted_approvals=[])
    # 4 stories items + 4 TAB_TARGETS
    story_items = [i for i in panel._items if i.item_type == "story"]
    assert len(story_items) == 1
    assert story_items[0].item_id == "story-001"
    assert "Test" in story_items[0].search_fields


def test_update_items_approvals() -> None:
    """update_items 正确解析审批数据。"""

    class FakeApproval:
        def __init__(self, aid: str, sid: str, atype: str) -> None:
            self.approval_id = aid
            self.story_id = sid
            self.approval_type = atype

    panel = SearchPanel()
    panel.update_items(
        sorted_stories=[],
        sorted_approvals=[FakeApproval("a1", "story-007", "merge_authorization")],
    )
    approval_items = [i for i in panel._items if i.item_type == "approval"]
    assert len(approval_items) == 1
    assert approval_items[0].item_id == "a1"
    assert "story-007" in approval_items[0].search_fields


def test_update_items_includes_tab_targets() -> None:
    """update_items 总是包含 Tab 导航目标。"""
    panel = SearchPanel()
    panel.update_items(sorted_stories=[], sorted_approvals=[])
    tab_items = [i for i in panel._items if i.item_type == "tab"]
    assert len(tab_items) == 4


def test_update_items_preserves_sort_order() -> None:
    """story sort_order 与输入列表顺序对齐。"""
    panel = SearchPanel()
    stories: list[dict[str, object]] = [
        {"story_id": "story-002", "title": "B", "status": "done", "current_phase": "done"},
        {"story_id": "story-001", "title": "A", "status": "in_progress", "current_phase": "dev"},
    ]
    panel.update_items(sorted_stories=stories, sorted_approvals=[])
    story_items = [i for i in panel._items if i.item_type == "story"]
    assert story_items[0].sort_order == 0  # story-002 first (as passed)
    assert story_items[1].sort_order == 1  # story-001 second


# ---------------------------------------------------------------------------
# _format_result
# ---------------------------------------------------------------------------


def test_format_result_story() -> None:
    """story 结果包含 icon、story ID、phase、title。"""
    item = SearchableItem(
        item_type="story",
        item_id="story-007",
        label="story-007",
        search_fields=("story-007", "Merge Loop", "reviewing", "review"),
        sort_order=0,
    )
    result = SearchResult(item=item, match_type=0)
    text = _format_result(result)
    plain = text.plain
    assert "story-007" in plain
    assert "reviewing" in plain
    assert "Merge Loop" in plain


def test_format_result_approval() -> None:
    """审批结果包含 ◆ 图标和标签。"""
    item = SearchableItem(
        item_type="approval",
        item_id="a1",
        label="story-007 — merge_authorization",
        search_fields=("story-007", "merge_authorization", "a1"),
        sort_order=0,
    )
    result = SearchResult(item=item, match_type=0)
    text = _format_result(result)
    plain = text.plain
    assert "◆" in plain
    assert "story-007 — merge_authorization" in plain


def test_format_result_tab() -> None:
    """Tab 结果包含 ⇥ 图标和标签。"""
    item = SearchableItem(
        item_type="tab",
        item_id="3",
        label="[3] 成本",
        search_fields=("成本", "cost", "3"),
        sort_order=2,
    )
    result = SearchResult(item=item, match_type=0)
    text = _format_result(result)
    plain = text.plain
    assert "⇥" in plain
    assert "[3] 成本" in plain


# ---------------------------------------------------------------------------
# 成本 Tab / 日志 Tab 渲染 (在 DashboardScreen 中)
# ---------------------------------------------------------------------------
# 成本和日志 Tab 内容通过 DashboardScreen._update_cost_tab/log_tab 渲染，
# 相关测试在 test_tui_search.py 集成测试中验证。
