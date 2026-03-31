"""convergent_loop_progress — Convergent Loop 进度组件。

轮次可视化（● 已完成 / ◐ 当前轮 / ○ 未执行）+ 当前去重后 findings
统计 + 收敛率 + 当前状态。
"""

from __future__ import annotations

from typing import Any, Literal

from rich.text import Text
from textual.widget import Widget

from ato.tui.theme import RICH_COLORS

LoopStageDisplay = Literal["standard", "escalated"]


class ConvergentLoopProgress(Widget):
    """Convergent Loop 进度可视化组件 (UX-DR4)。

    ``still_open`` 视作 ``open``。
    findings 统计基于去重后的最新轮次数据（不跨轮累计）。
    """

    DEFAULT_CSS = ""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._current_round: int = 0
        self._max_rounds: int = 3
        self._findings_summary: dict[str, int] = {}
        self._stage: LoopStageDisplay = "standard"

    def update_progress(
        self,
        *,
        current_round: int,
        max_rounds: int,
        findings_summary: dict[str, int],
        stage: LoopStageDisplay = "standard",
    ) -> None:
        """更新进度数据并刷新渲染。"""
        self._current_round = current_round
        self._max_rounds = max_rounds
        self._findings_summary = findings_summary
        self._stage = stage
        self.refresh()

    def render(self) -> Text:
        """渲染轮次进度 + findings 统计 + 收敛率。"""
        if self._current_round <= 0:
            return Text("")

        text = Text()
        # Stage-aware prefix: CL: for standard, CL↑: for escalated
        prefix = "CL↑: " if self._stage == "escalated" else "CL: "
        text.append(prefix, style=f"bold {RICH_COLORS['$accent']}")

        # 轮次可视化
        for r in range(1, self._max_rounds + 1):
            if r < self._current_round:
                text.append("● ", style=RICH_COLORS["$success"])
            elif r == self._current_round:
                text.append("◐ ", style=RICH_COLORS["$warning"])
            else:
                text.append("○ ", style=RICH_COLORS["$muted"])

        text.append(f"R{self._current_round}/{self._max_rounds}", style=RICH_COLORS["$info"])

        # Findings 统计
        fs = self._findings_summary
        b_open = fs.get("blocking_open", 0)
        b_closed = fs.get("blocking_closed", 0)
        s_open = fs.get("suggestion_open", 0)
        s_closed = fs.get("suggestion_closed", 0)
        total = b_open + b_closed + s_open + s_closed
        closed = b_closed + s_closed
        open_count = b_open + s_open

        if total > 0:
            rate = closed / total * 100
            text.append(f"  {rate:.0f}%", style=RICH_COLORS["$info"])
            text.append(f" ({open_count}↑ {closed}↓)", style=RICH_COLORS["$muted"])

            if open_count == 0:
                text.append("  已收敛", style=RICH_COLORS["$success"])
            elif b_open > 0:
                text.append("  blocking待解决", style=RICH_COLORS["$error"])
            else:
                text.append("  进行中", style=RICH_COLORS["$warning"])

        return text
