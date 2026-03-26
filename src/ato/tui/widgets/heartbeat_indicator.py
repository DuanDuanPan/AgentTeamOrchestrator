"""heartbeat_indicator — 活跃 Story 心跳 Widget。

为 in_progress 状态的 story 显示动画 spinner + 经过时间 + CL 轮次 + 成本。
spinner 由组件自身 set_interval(1.0, ...) 在 on_mount() 中创建。
"""

from __future__ import annotations

import time

from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget

from ato.tui.theme import RICH_COLORS
from ato.tui.widgets.story_status_line import (
    _compute_progress,
    _format_elapsed,
    _render_progress_bar,
)

_SPINNER_FRAMES = "\u25d0\u25d3\u25d1\u25d2"  # ◐◓◑◒


class HeartbeatIndicator(Widget):
    """活跃 Story 心跳指示器。

    显示动画 spinner + story ID + 阶段 + CL 轮次 + 进度条 + 成本 + 经过时间。
    数据由 ATOApp 通过 ``update_heartbeat()`` 推送。
    """

    story_id: reactive[str] = reactive("")
    current_phase: reactive[str] = reactive("developing")
    round_num: reactive[int] = reactive(0)
    max_rounds: reactive[int] = reactive(3)
    cost_usd: reactive[float] = reactive(0.0)
    started_at: reactive[float] = reactive(0.0)

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._spinner_index = 0
        self._elapsed_seconds = 0

    def on_mount(self) -> None:
        """启动 1 秒定时器驱动 spinner 和经过时间更新。"""
        self._update_elapsed()
        self.set_interval(1.0, self._tick)

    def _tick(self) -> None:
        """定时器回调：推进 spinner 帧 + 更新经过时间。"""
        self._spinner_index = (self._spinner_index + 1) % len(_SPINNER_FRAMES)
        self._update_elapsed()
        self.refresh()

    def _update_elapsed(self) -> None:
        """从 started_at 本地计算经过时间。"""
        if self.started_at > 0:
            self._elapsed_seconds = max(0, int(time.monotonic() - self.started_at))
        else:
            self._elapsed_seconds = 0

    def update_heartbeat(
        self,
        *,
        story_id: str,
        current_phase: str,
        round_num: int,
        max_rounds: int,
        cost_usd: float,
        started_at: float,
    ) -> None:
        """接收 ATOApp 推送的心跳数据。

        Args:
            started_at: monotonic 时间戳（由 ATOApp 从 DB 的 ISO 时间转换）。
        """
        self.story_id = story_id
        self.current_phase = current_phase
        self.round_num = round_num
        self.max_rounds = max_rounds
        self.cost_usd = cost_usd
        self.started_at = started_at

    def render(self) -> Text:
        """渲染：◐ {story_id}  {phase}  R{round}/{max}  {progress}  ${cost}  {elapsed} ◐。"""
        spinner = _SPINNER_FRAMES[self._spinner_index]
        progress = _compute_progress(self.current_phase)
        bar = _render_progress_bar(progress)
        elapsed = _format_elapsed(self._elapsed_seconds)
        cost = f"${self.cost_usd:.2f}"
        cl_info = f"R{self.round_num}/{self.max_rounds}"

        color = RICH_COLORS["$success"]
        result = Text()
        result.append(spinner, style=color)
        result.append(f" {self.story_id}  ", style=RICH_COLORS["$text"])
        result.append(f"{self.current_phase:<12}", style=color)
        result.append(f"  {cl_info}  ", style=RICH_COLORS["$info"])
        result.append(f"{bar}  ", style=RICH_COLORS["$text"])
        result.append(cost, style=RICH_COLORS["$text"])
        result.append(f"  {elapsed:>6}", style=RICH_COLORS["$muted"])
        result.append(f" {spinner}", style=color)
        return result
