"""search_panel — 搜索面板 Widget。

Overlay 式搜索面板：Input 搜索框 + OptionList 结果列表。
数据由 DashboardScreen 通过 update_items() 推送，不自行创建 SQLite 连接。
``/`` 激活，ESC 关闭。模糊匹配在内存中执行（≤50ms 响应）。
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, OptionList
from textual.widgets.option_list import Option

from ato.tui.theme import (
    RICH_COLORS,
    format_status,
    map_story_to_visual_status,
)

# ---------------------------------------------------------------------------
# 搜索数据模型
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SearchableItem:
    """可搜索条目。"""

    item_type: str  # "story" | "approval" | "tab"
    item_id: str
    label: str
    search_fields: tuple[str, ...]
    sort_order: int = 0


@dataclass(frozen=True, slots=True)
class SearchResult:
    """搜索匹配结果。"""

    item: SearchableItem
    match_type: int  # 0=精确, 1=前缀, 2=子串


# 内置 Tab 导航目标
TAB_TARGETS: tuple[SearchableItem, ...] = (
    SearchableItem("tab", "1", "[1] 审批", ("审批", "approvals", "approval", "1"), 0),
    SearchableItem("tab", "2", "[2] Stories", ("stories", "story", "2"), 1),
    SearchableItem("tab", "3", "[3] 成本", ("成本", "cost", "3"), 2),
    SearchableItem("tab", "4", "[4] 日志", ("日志", "log", "4"), 3),
)

# 同 match_type 内的类型排序：story > approval > tab
# story 优先确保 "story ID 直达" (AC1/AC2)：输入 story-001 时 story 排在同 ID 的审批前面
_ITEM_TYPE_ORDER: dict[str, int] = {"story": 0, "approval": 1, "tab": 2}


# ---------------------------------------------------------------------------
# 模糊匹配算法
# ---------------------------------------------------------------------------


def fuzzy_match(query: str, items: list[SearchableItem]) -> list[SearchResult]:
    """模糊匹配搜索。

    匹配优先级：精确匹配(0) > 前缀匹配(1) > 子串匹配(2)。
    同优先级内：审批 > story > tab，各组内保持 sort_order。

    Args:
        query: 用户输入的搜索词。
        items: 可搜索条目列表。

    Returns:
        排序后的搜索结果列表。
    """
    q = query.strip().lower()
    if not q:
        return []

    results: list[SearchResult] = []
    for item in items:
        mt = _get_match_type(q, item)
        if mt is not None:
            results.append(SearchResult(item=item, match_type=mt))

    results.sort(
        key=lambda r: (
            r.match_type,
            _ITEM_TYPE_ORDER.get(r.item.item_type, 9),
            r.item.sort_order,
        )
    )
    return results


def _get_match_type(query: str, item: SearchableItem) -> int | None:
    """判断查询与条目的匹配类型。返回 None 表示不匹配。"""
    item_id_lower = item.item_id.lower()

    # --- 精确匹配 ---
    if item.item_type == "story":
        # "story-007" == "story-007", "007" == "story-007"
        if query == item_id_lower:
            return 0
        numeric = item_id_lower.removeprefix("story-")
        if query == numeric:
            return 0
    else:
        for field_val in item.search_fields:
            if query == field_val.lower():
                return 0

    # --- 前缀匹配 ---
    if item_id_lower.startswith(query):
        return 1
    for field_val in item.search_fields:
        if field_val.lower().startswith(query):
            return 1

    # --- 子串匹配 ---
    if query in item_id_lower:
        return 2
    for field_val in item.search_fields:
        if query in field_val.lower():
            return 2

    return None


# ---------------------------------------------------------------------------
# SearchPanel Widget
# ---------------------------------------------------------------------------


class SearchPanel(Widget):
    """搜索面板 — Input + OptionList overlay。

    push-based 数据流：DashboardScreen 通过 update_items() 推送数据，
    Input.Changed 触发内存中模糊匹配实时过滤。
    """

    class Selected(Message):
        """搜索结果被选中。"""

        def __init__(self, item_type: str, item_id: str) -> None:
            self.item_type = item_type
            self.item_id = item_id
            super().__init__()

    class Dismissed(Message):
        """搜索面板被关闭（ESC）。"""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._items: list[SearchableItem] = []
        self._results: list[SearchResult] = []

    def compose(self) -> ComposeResult:
        yield Input(placeholder="输入 story ID 或关键词搜索", id="search-input")
        yield OptionList(id="search-results")

    def update_items(
        self,
        sorted_stories: list[dict[str, object]],
        sorted_approvals: list[object],
    ) -> None:
        """更新可搜索条目列表。

        Args:
            sorted_stories: 已按 sort_stories_by_status() 排序的 story 列表。
            sorted_approvals: 已按 _sort_approvals() 排序的审批列表。
        """
        items: list[SearchableItem] = []

        for i, story in enumerate(sorted_stories):
            sid = str(story.get("story_id", ""))
            title = str(story.get("title", ""))
            status = str(story.get("status", ""))
            phase = str(story.get("current_phase", ""))
            items.append(
                SearchableItem(
                    item_type="story",
                    item_id=sid,
                    label=sid,
                    search_fields=(sid, title, phase, status),
                    sort_order=i,
                )
            )

        for i, a in enumerate(sorted_approvals):
            aid = getattr(a, "approval_id", "")
            asid = getattr(a, "story_id", "")
            atype = getattr(a, "approval_type", "")
            items.append(
                SearchableItem(
                    item_type="approval",
                    item_id=aid,
                    label=f"{asid} — {atype}",
                    search_fields=(asid, atype, aid),
                    sort_order=i,
                )
            )

        items.extend(TAB_TARGETS)
        self._items = items

    def open(self) -> None:
        """打开搜索面板，清空输入并聚焦。"""
        self.display = True
        inp = self.query_one("#search-input", Input)
        inp.value = ""
        inp.focus()
        self._results = []
        self._show_hint("输入 story ID 或关键词搜索")

    def close(self) -> None:
        """关闭搜索面板。"""
        self.display = False

    def on_input_changed(self, event: Input.Changed) -> None:
        """实时过滤搜索结果。"""
        query = event.value
        if not query.strip():
            self._results = []
            self._show_hint("输入 story ID 或关键词搜索")
            return

        self._results = fuzzy_match(query, self._items)
        self._render_results()

    def on_key(self, event: events.Key) -> None:
        """键盘事件：↑↓ 导航结果，Enter 选择，ESC 关闭。"""
        if event.key == "escape":
            self.post_message(self.Dismissed())
            event.prevent_default()
            event.stop()
            return

        option_list = self.query_one("#search-results", OptionList)

        if event.key == "down":
            option_list.action_cursor_down()
            event.prevent_default()
            event.stop()
        elif event.key == "up":
            option_list.action_cursor_up()
            event.prevent_default()
            event.stop()
        elif event.key == "enter":
            self._select_highlighted()
            event.prevent_default()
            event.stop()

    def _select_highlighted(self) -> None:
        """选择当前高亮的结果。"""
        option_list = self.query_one("#search-results", OptionList)
        highlighted = option_list.highlighted
        if highlighted is not None and 0 <= highlighted < len(self._results):
            result = self._results[highlighted]
            self.post_message(self.Selected(result.item.item_type, result.item.item_id))

    def _show_hint(self, message: str) -> None:
        """显示提示文本。"""
        option_list = self.query_one("#search-results", OptionList)
        option_list.clear_options()
        option_list.add_option(Option(Text(message, style=RICH_COLORS["$muted"]), disabled=True))

    def _render_results(self) -> None:
        """渲染搜索结果到 OptionList。"""
        option_list = self.query_one("#search-results", OptionList)
        option_list.clear_options()

        if not self._results:
            option_list.add_option(
                Option(Text("未找到匹配项", style=RICH_COLORS["$muted"]), disabled=True)
            )
            return

        for i, result in enumerate(self._results):
            prompt = _format_result(result)
            option_list.add_option(Option(prompt, id=f"sr-{i}"))

        option_list.highlighted = 0


def _format_result(result: SearchResult) -> Text:
    """格式化单个搜索结果的显示。"""
    item = result.item
    text = Text()

    if item.item_type == "story":
        # search_fields = (sid, title, phase, status)
        status = item.search_fields[3] if len(item.search_fields) > 3 else ""
        phase = item.search_fields[2] if len(item.search_fields) > 2 else ""
        title = item.search_fields[1] if len(item.search_fields) > 1 else ""
        visual = map_story_to_visual_status(status)
        sc = format_status(visual)
        color = RICH_COLORS.get(sc.color_var, RICH_COLORS["$muted"])

        text.append(f"  {sc.icon} ", style=color)
        text.append(f"{item.item_id}", style=f"bold {RICH_COLORS['$accent']}")
        if phase:
            text.append(f"  {phase}", style=color)
        if title:
            text.append(f"  {title}", style=RICH_COLORS["$text"])

    elif item.item_type == "approval":
        text.append("  ◆ ", style=RICH_COLORS["$warning"])
        text.append(f"{item.label}", style=RICH_COLORS["$accent"])

    elif item.item_type == "tab":
        text.append("  ⇥ ", style=RICH_COLORS["$info"])
        text.append(f"{item.label}", style=RICH_COLORS["$text"])

    return text
