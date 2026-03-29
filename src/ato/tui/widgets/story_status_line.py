"""story_status_line — Story 状态行 Widget。

一行浓缩显示 story 关键信息：状态图标 + story ID + 阶段 + 进度条 + 耗时 + 成本。
数据由 ATOApp 通过 update_data() 推送，不自行创建 SQLite 连接。
"""

from __future__ import annotations

from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget

from ato.state_machine import CANONICAL_PHASES
from ato.tui.theme import RICH_COLORS, format_status, map_story_to_visual_status

# 阶段顺序——与状态机 CANONICAL_PHASES 对齐，首尾加上 queued / done
PHASE_ORDER: list[str] = ["queued", *CANONICAL_PHASES, "done"]

# 进度条字符
_BAR_FILLED = "\u2588"  # █
_BAR_EMPTY = "\u2591"  # ░
_BAR_WIDTH = 10


def _compute_progress(phase: str) -> float:
    """根据阶段在 PHASE_ORDER 中的位置计算进度比例 [0.0, 1.0]。"""
    try:
        idx = PHASE_ORDER.index(phase)
    except ValueError:
        return 0.0
    max_idx = len(PHASE_ORDER) - 1
    if max_idx == 0:
        return 1.0
    return idx / max_idx


def _render_progress_bar(progress: float) -> str:
    """渲染进度条字符串（总宽度 _BAR_WIDTH）。"""
    filled = round(progress * _BAR_WIDTH)
    return _BAR_FILLED * filled + _BAR_EMPTY * (_BAR_WIDTH - filled)


def _format_elapsed(seconds: int) -> str:
    """格式化经过时间：<60 显示 Xs，≥60 显示 Xm，≥3600 显示 Xh Xm。"""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    hours = seconds // 3600
    mins = (seconds % 3600) // 60
    return f"{hours}h {mins}m"


class StoryStatusLine(Widget):
    """Story 状态行：图标 + story ID + 阶段 + 进度条 + 耗时 + 成本。

    数据由 ATOApp 通过 ``update_data()`` 推送，不自行创建 SQLite 连接。
    """

    # Reactive 属性驱动 UI 更新
    story_id: reactive[str] = reactive("")
    status: reactive[str] = reactive("backlog")
    current_phase: reactive[str] = reactive("queued")
    cost_usd: reactive[float] = reactive(0.0)
    elapsed_seconds: reactive[int] = reactive(0)
    cl_round: reactive[int] = reactive(0)
    cl_max_rounds: reactive[int] = reactive(3)

    _activity_type: str = ""
    _activity_summary: str = ""

    def update_data(
        self,
        *,
        story_id: str,
        status: str,
        current_phase: str,
        cost_usd: float,
        elapsed_seconds: int,
        cl_round: int,
        cl_max_rounds: int,
        activity_type: str = "",
        activity_summary: str = "",
    ) -> None:
        """批量更新 reactive 属性。"""
        self.story_id = story_id
        self.status = status
        self.current_phase = current_phase
        self.cost_usd = cost_usd
        self.elapsed_seconds = elapsed_seconds
        self.cl_round = cl_round
        self.cl_max_rounds = cl_max_rounds
        self._activity_type = activity_type
        self._activity_summary = activity_summary

    def render(self) -> Text:
        """渲染格式：{icon} {story_id}  {phase}  {progress_bar}  {elapsed}  ${cost}。"""
        visual = map_story_to_visual_status(self.status)
        sc = format_status(visual)
        color = RICH_COLORS.get(sc.color_var, RICH_COLORS["$text"])

        progress = _compute_progress(self.current_phase)
        bar = _render_progress_bar(progress)
        elapsed = _format_elapsed(self.elapsed_seconds)
        cost = f"${self.cost_usd:.2f}"

        result = Text()
        result.append(sc.icon, style=color)
        result.append(f" {self.story_id}  ", style=RICH_COLORS["$text"])
        result.append(f"{self.current_phase:<12}", style=color)
        result.append(f"  {bar}  ", style=RICH_COLORS["$text"])
        result.append(f"{elapsed:>6}", style=RICH_COLORS["$muted"])
        result.append(f"  {cost}", style=RICH_COLORS["$text"])
        if self._activity_summary:
            result.append(f"  {self._activity_summary[:40]}", style=RICH_COLORS["$accent"])
        return result
