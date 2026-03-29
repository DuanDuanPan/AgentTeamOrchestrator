"""agent_activity — Agent 活动指示器 Widget。

实时展示 LLM agent 当前活动状态（初始化、文本、工具调用等）。
数据由外层推送，组件不读 SQLite。
"""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.widget import Widget

from ato.tui.theme import RICH_COLORS

_ACTIVITY_ICONS: dict[str, str] = {
    "init": "◈",
    "text": "▸",
    "tool_use": "⚙",
    "tool_result": "✓",
    "turn_end": "↻",
    "result": "●",
    "error": "✗",
    "other": "·",
}


class AgentActivityWidget(Widget):
    """实时 Agent 活动指示器。"""

    DEFAULT_CSS = ""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._activity_type: str = ""
        self._activity_summary: str = ""

    def update_activity(self, *, activity_type: str, activity_summary: str) -> None:
        """更新活动显示内容。"""
        self._activity_type = activity_type
        self._activity_summary = activity_summary
        self.refresh()

    def clear_activity(self) -> None:
        """清除活动显示。"""
        self._activity_type = ""
        self._activity_summary = ""
        self.refresh()

    def render(self) -> Text:
        """渲染活动指示器。"""
        if not self._activity_summary:
            return Text("")
        icon = _ACTIVITY_ICONS.get(self._activity_type, "·")
        text = Text()
        text.append(f" {icon} ", style=RICH_COLORS["$accent"])
        text.append(self._activity_summary[:80], style=RICH_COLORS["$text"])
        return text
