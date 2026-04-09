"""Story 排序逻辑单元测试。

测试 awaiting → active → running → frozen → done → info 排序，
以及同状态内按 updated_at 降序排列。
"""

from __future__ import annotations

from ato.tui.theme import (
    VISUAL_STATUS_SORT_ORDER,
    sort_stories_by_status,
)

# ---------------------------------------------------------------------------
# VISUAL_STATUS_SORT_ORDER 常量
# ---------------------------------------------------------------------------


class TestSortOrderConstants:
    """排序优先级常量测试。"""

    def test_awaiting_highest_priority(self) -> None:
        assert VISUAL_STATUS_SORT_ORDER["awaiting"] == 0

    def test_info_lowest_priority(self) -> None:
        assert VISUAL_STATUS_SORT_ORDER["info"] == 5

    def test_running_after_active(self) -> None:
        assert VISUAL_STATUS_SORT_ORDER["running"] > VISUAL_STATUS_SORT_ORDER["active"]

    def test_running_before_frozen(self) -> None:
        """running 必须紧邻 active，不得落到 frozen 之后。"""
        assert VISUAL_STATUS_SORT_ORDER["running"] < VISUAL_STATUS_SORT_ORDER["frozen"]

    def test_full_order(self) -> None:
        order = sorted(VISUAL_STATUS_SORT_ORDER.items(), key=lambda x: x[1])
        names = [name for name, _ in order]
        assert names == ["awaiting", "active", "running", "frozen", "done", "info"]


# ---------------------------------------------------------------------------
# sort_stories_by_status
# ---------------------------------------------------------------------------


class TestSortStoriesByStatus:
    """sort_stories_by_status 排序测试。"""

    def test_basic_ordering(self) -> None:
        stories: list[dict[str, object]] = [
            {"story_id": "s1", "status": "done", "updated_at": "2026-01-01T00:00:00"},
            {"story_id": "s2", "status": "in_progress", "updated_at": "2026-01-01T00:00:00"},
            {"story_id": "s3", "status": "ready", "updated_at": "2026-01-01T00:00:00"},
            {"story_id": "s4", "status": "blocked", "updated_at": "2026-01-01T00:00:00"},
            {"story_id": "s5", "status": "backlog", "updated_at": "2026-01-01T00:00:00"},
        ]
        sorted_stories = sort_stories_by_status(stories)
        ids = [s["story_id"] for s in sorted_stories]
        # ready=awaiting(0) in_progress=running(2) blocked=frozen(3) done(4) backlog=info(5)
        assert ids == ["s3", "s2", "s4", "s1", "s5"]

    def test_awaiting_before_active(self) -> None:
        stories: list[dict[str, object]] = [
            {"story_id": "s1", "status": "review", "updated_at": "2026-01-01T00:00:00"},
            {"story_id": "s2", "status": "uat", "updated_at": "2026-01-01T00:00:00"},
        ]
        sorted_stories = sort_stories_by_status(stories)
        # uat=awaiting(0), review=active(1)
        assert sorted_stories[0]["story_id"] == "s2"
        assert sorted_stories[1]["story_id"] == "s1"

    def test_running_before_frozen(self) -> None:
        """running 排在 frozen 之前。"""
        stories: list[dict[str, object]] = [
            {"story_id": "s1", "status": "blocked", "updated_at": "2026-01-01T00:00:00"},
            {"story_id": "s2", "status": "in_progress", "updated_at": "2026-01-01T00:00:00"},
        ]
        sorted_stories = sort_stories_by_status(stories)
        # in_progress=running(2), blocked=frozen(3)
        assert sorted_stories[0]["story_id"] == "s2"
        assert sorted_stories[1]["story_id"] == "s1"

    def test_same_status_ordered_by_updated_at_desc(self) -> None:
        stories: list[dict[str, object]] = [
            {"story_id": "s1", "status": "in_progress", "updated_at": "2026-01-01T00:00:00"},
            {"story_id": "s2", "status": "in_progress", "updated_at": "2026-01-03T00:00:00"},
            {"story_id": "s3", "status": "in_progress", "updated_at": "2026-01-02T00:00:00"},
        ]
        sorted_stories = sort_stories_by_status(stories)
        ids = [s["story_id"] for s in sorted_stories]
        # 降序：最近更新的在前
        assert ids == ["s2", "s3", "s1"]

    def test_empty_list(self) -> None:
        assert sort_stories_by_status([]) == []

    def test_single_story(self) -> None:
        stories: list[dict[str, object]] = [
            {"story_id": "s1", "status": "done", "updated_at": "2026-01-01T00:00:00"}
        ]
        result = sort_stories_by_status(stories)
        assert len(result) == 1
        assert result[0]["story_id"] == "s1"

    def test_unknown_status_falls_to_end(self) -> None:
        stories: list[dict[str, object]] = [
            {"story_id": "s1", "status": "unknown", "updated_at": "2026-01-01T00:00:00"},
            {"story_id": "s2", "status": "ready", "updated_at": "2026-01-01T00:00:00"},
        ]
        sorted_stories = sort_stories_by_status(stories)
        # unknown→info fallback(5), ready→awaiting(0)
        assert sorted_stories[0]["story_id"] == "s2"

    def test_comprehensive_sort(self) -> None:
        """完整排序验证：所有状态 + 时间。"""
        stories: list[dict[str, object]] = [
            {"story_id": "s1", "status": "backlog", "updated_at": "2026-01-05T00:00:00"},
            {"story_id": "s2", "status": "done", "updated_at": "2026-01-04T00:00:00"},
            {"story_id": "s3", "status": "in_progress", "updated_at": "2026-01-03T00:00:00"},
            {"story_id": "s4", "status": "uat", "updated_at": "2026-01-02T00:00:00"},
            {"story_id": "s5", "status": "ready", "updated_at": "2026-01-01T00:00:00"},
            {"story_id": "s6", "status": "blocked", "updated_at": "2026-01-06T00:00:00"},
            {"story_id": "s7", "status": "review", "updated_at": "2026-01-07T00:00:00"},
        ]
        sorted_stories = sort_stories_by_status(stories)
        ids = [s["story_id"] for s in sorted_stories]
        # awaiting: s4(uat), s5(ready) → active: s7(review) → running: s3(in_progress)
        # → frozen: s6(blocked) → done: s2(done) → info: s1(backlog)
        assert ids == ["s4", "s5", "s7", "s3", "s6", "s2", "s1"]

    def test_does_not_mutate_input(self) -> None:
        stories: list[dict[str, object]] = [
            {"story_id": "s1", "status": "done", "updated_at": "2026-01-01T00:00:00"},
            {"story_id": "s2", "status": "ready", "updated_at": "2026-01-01T00:00:00"},
        ]
        original = list(stories)
        sort_stories_by_status(stories)
        assert stories == original
