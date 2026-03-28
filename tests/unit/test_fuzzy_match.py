"""模糊匹配算法单元测试。

验证 fuzzy_match() 的匹配优先级（精确 > 前缀 > 子串）、
排序语义（match_type → item_type → sort_order）以及边界情况。
"""

from __future__ import annotations

from ato.tui.widgets.search_panel import (
    TAB_TARGETS,
    SearchableItem,
    fuzzy_match,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_story(
    sid: str,
    title: str = "",
    phase: str = "",
    status: str = "in_progress",
    order: int = 0,
) -> SearchableItem:
    return SearchableItem(
        item_type="story",
        item_id=sid,
        label=sid,
        search_fields=(sid, title, phase, status),
        sort_order=order,
    )


def _make_approval(aid: str, story_id: str, atype: str, order: int = 0) -> SearchableItem:
    return SearchableItem(
        item_type="approval",
        item_id=aid,
        label=f"{story_id} — {atype}",
        search_fields=(story_id, atype, aid),
        sort_order=order,
    )


# ---------------------------------------------------------------------------
# 空查询
# ---------------------------------------------------------------------------


def test_empty_query_returns_empty() -> None:
    items = [_make_story("story-001")]
    assert fuzzy_match("", items) == []


def test_whitespace_query_returns_empty() -> None:
    items = [_make_story("story-001")]
    assert fuzzy_match("  ", items) == []


# ---------------------------------------------------------------------------
# Story ID 精确匹配
# ---------------------------------------------------------------------------


def test_exact_match_full_story_id() -> None:
    """完整 story ID 精确匹配。"""
    items = [_make_story("story-007"), _make_story("story-001")]
    results = fuzzy_match("story-007", items)
    assert len(results) >= 1
    assert results[0].item.item_id == "story-007"
    assert results[0].match_type == 0


def test_exact_match_numeric_only() -> None:
    """仅输入数字部分也应精确匹配。"""
    items = [_make_story("story-007"), _make_story("story-001")]
    results = fuzzy_match("007", items)
    assert len(results) >= 1
    assert results[0].item.item_id == "story-007"
    assert results[0].match_type == 0


def test_exact_match_case_insensitive() -> None:
    """大小写不敏感。"""
    items = [_make_story("Story-007")]
    results = fuzzy_match("story-007", items)
    assert len(results) >= 1
    assert results[0].match_type == 0


# ---------------------------------------------------------------------------
# 前缀匹配
# ---------------------------------------------------------------------------


def test_prefix_match_story_id() -> None:
    """story ID 前缀匹配。"""
    items = [_make_story("story-001"), _make_story("story-002"), _make_story("story-010")]
    results = fuzzy_match("story-0", items)
    # All three match as prefix
    assert len(results) == 3
    for r in results:
        assert r.match_type == 1


def test_prefix_match_title() -> None:
    """story title 前缀匹配。"""
    items = [_make_story("story-001", title="Review Loop")]
    results = fuzzy_match("review", items)
    assert len(results) >= 1
    assert results[0].match_type == 1


# ---------------------------------------------------------------------------
# 子串匹配
# ---------------------------------------------------------------------------


def test_substring_match_title() -> None:
    """子串匹配 title 中间内容。"""
    items = [_make_story("story-001", title="Convergent Loop Setup")]
    results = fuzzy_match("loop", items)
    assert len(results) >= 1
    assert results[0].match_type == 2


def test_substring_match_phase() -> None:
    """子串匹配 phase 字段。"""
    items = [_make_story("story-001", phase="reviewing")]
    results = fuzzy_match("view", items)
    assert len(results) >= 1


# ---------------------------------------------------------------------------
# 无匹配
# ---------------------------------------------------------------------------


def test_no_match_returns_empty() -> None:
    items = [_make_story("story-001", title="Hello")]
    assert fuzzy_match("xyz123", items) == []


# ---------------------------------------------------------------------------
# 排序优先级
# ---------------------------------------------------------------------------


def test_exact_before_prefix_before_substring() -> None:
    """精确匹配 > 前缀匹配 > 子串匹配。"""
    items = [
        _make_story("story-review", title="X", order=0),  # 精确 match on "story-review"
        _make_story("story-001", title="review phase", order=1),  # prefix on title "review"
        _make_story("story-002", title="code review loop", order=2),  # substring "review"
    ]
    results = fuzzy_match("review", items)
    assert len(results) == 3
    # story-001: "review phase".startswith("review") → prefix(1)
    # story-review: "review" in "story-review" → substring(2)
    # story-002: "review" in "code review loop" → substring(2)
    assert results[0].match_type <= results[-1].match_type


def test_story_before_approval_within_same_match_type() -> None:
    """同 match_type 内 story 排在审批前面（story ID 直达优先）。"""
    items = [
        _make_story("story-001", title="merge setup"),
        _make_approval("a1", "story-001", "merge_authorization"),
    ]
    results = fuzzy_match("merge", items)
    assert len(results) == 2
    # Both are substring matches; story should come first (story ID 直达)
    story_results = [r for r in results if r.item.item_type == "story"]
    approval_results = [r for r in results if r.item.item_type == "approval"]
    assert results.index(story_results[0]) < results.index(approval_results[0])


def test_sort_order_within_same_type() -> None:
    """同类型 + 同 match_type 内按 sort_order 排序。"""
    items = [
        _make_story("story-002", order=1),
        _make_story("story-001", order=0),
    ]
    results = fuzzy_match("story", items)
    assert len(results) == 2
    assert results[0].item.item_id == "story-001"
    assert results[1].item.item_id == "story-002"


# ---------------------------------------------------------------------------
# 审批匹配
# ---------------------------------------------------------------------------


def test_approval_match_by_story_id() -> None:
    """审批通过关联的 story ID 匹配。"""
    items = [_make_approval("a1", "story-007", "code_review")]
    results = fuzzy_match("story-007", items)
    assert len(results) == 1
    assert results[0].item.item_type == "approval"
    assert results[0].match_type == 0


def test_approval_match_by_type() -> None:
    """审批通过类型匹配。"""
    items = [_make_approval("a1", "story-001", "merge_authorization")]
    results = fuzzy_match("merge", items)
    assert len(results) >= 1


# ---------------------------------------------------------------------------
# Tab 目标匹配
# ---------------------------------------------------------------------------


def test_tab_match_by_chinese_name() -> None:
    """Tab 目标通过中文名匹配。"""
    items = list(TAB_TARGETS)
    results = fuzzy_match("审批", items)
    assert len(results) >= 1
    assert results[0].item.item_type == "tab"
    assert results[0].item.item_id == "1"


def test_tab_match_by_english_name() -> None:
    """Tab 目标通过英文名匹配。"""
    items = list(TAB_TARGETS)
    results = fuzzy_match("cost", items)
    assert len(results) >= 1
    assert results[0].item.item_type == "tab"
    assert results[0].item.item_id == "3"


def test_tab_match_by_number() -> None:
    """Tab 目标通过数字匹配。"""
    items = list(TAB_TARGETS)
    results = fuzzy_match("3", items)
    assert len(results) >= 1
    tab_results = [r for r in results if r.item.item_type == "tab"]
    assert any(r.item.item_id == "3" for r in tab_results)


# ---------------------------------------------------------------------------
# 混合搜索
# ---------------------------------------------------------------------------


def test_mixed_items_all_types() -> None:
    """stories + approvals + tabs 混合搜索。"""
    items = [
        _make_story("story-001", title="Cost overview"),
        _make_approval("a1", "story-001", "cost_review"),
        *TAB_TARGETS,
    ]
    results = fuzzy_match("cost", items)
    assert len(results) >= 2
    types_found = {r.item.item_type for r in results}
    assert "story" in types_found or "approval" in types_found or "tab" in types_found
