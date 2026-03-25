"""three_question_header — 三问顶栏 Widget。

一眼回答"系统正常吗？需要我做什么？花了多少？"
四区域：系统状态 │ 审批计数 │ 成本摘要 │ 更新时间
"""

from __future__ import annotations

from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget

from ato.tui.theme import RICH_COLORS

# 区域分隔符
_SEP = " │ "


class ThreeQuestionHeader(Widget):
    """三问顶栏：系统状态 │ 审批计数 │ 成本摘要 │ 更新时间。

    数据由 ATOApp 通过 ``update_data()`` 推送，不自行创建 SQLite 连接。
    """

    # Reactive 属性驱动 UI 更新
    running_count: reactive[int] = reactive(0)
    error_count: reactive[int] = reactive(0)
    pending_approvals: reactive[int] = reactive(0)
    today_cost_usd: reactive[float] = reactive(0.0)
    seconds_ago: reactive[int] = reactive(0)
    display_mode: reactive[str] = reactive("full")

    def update_data(
        self,
        *,
        running_count: int,
        error_count: int,
        pending_approvals: int,
        today_cost_usd: float,
        seconds_ago: int,
    ) -> None:
        """接收 ATOApp 推送的数据并更新 reactive 属性。"""
        self.running_count = running_count
        self.error_count = error_count
        self.pending_approvals = pending_approvals
        self.today_cost_usd = today_cost_usd
        self.seconds_ago = seconds_ago

    def set_display_mode(self, mode: str) -> None:
        """设置显示模式（由 ATOApp 的 on_resize 转发）。"""
        if mode in ("full", "compact", "minimal"):
            self.display_mode = mode

    def render(self) -> Text:
        """根据 display_mode 渲染四区域内容。"""
        mode = self.display_mode
        if mode == "full":
            return self._render_full()
        if mode == "compact":
            return self._render_compact()
        return self._render_minimal()

    # ------------------------------------------------------------------
    # 渲染模板
    # ------------------------------------------------------------------

    def _render_full(self) -> Text:
        """180+ 列完整文字。"""
        result = Text()
        self._append_system_status_full(result)
        result.append(_SEP)
        self._append_approval_status_full(result)
        result.append(_SEP)
        self._append_cost_full(result)
        result.append(_SEP)
        self._append_time_full(result)
        return result

    def _render_compact(self) -> Text:
        """140-179 列缩略标签。"""
        result = Text()
        self._append_system_status_compact(result)
        result.append(_SEP)
        self._append_approval_status_compact(result)
        result.append(_SEP)
        self._append_cost_compact(result)
        result.append(_SEP)
        self._append_time_compact(result)
        return result

    def _render_minimal(self) -> Text:
        """100-139 列仅图标+数字。"""
        result = Text()
        self._append_system_status_minimal(result)
        result.append(" ")
        self._append_approval_status_minimal(result)
        result.append(" ")
        self._append_cost_compact(result)
        result.append(" ")
        self._append_time_compact(result)
        return result

    # ------------------------------------------------------------------
    # 系统状态区域
    # ------------------------------------------------------------------

    def _append_system_status_full(self, text: Text) -> None:
        if self.error_count > 0:
            text.append(
                f"✖ {self.error_count} 项异常",
                style=RICH_COLORS["$error"],
            )
        elif self.running_count > 0:
            text.append(
                f"● {self.running_count} 项运行中",
                style=RICH_COLORS["$success"],
            )
        else:
            text.append("● 空闲", style=RICH_COLORS["$muted"])

    def _append_system_status_compact(self, text: Text) -> None:
        if self.error_count > 0:
            text.append(
                f"✖ {self.error_count}异常",
                style=RICH_COLORS["$error"],
            )
        elif self.running_count > 0:
            text.append(
                f"● {self.running_count}运行",
                style=RICH_COLORS["$success"],
            )
        else:
            text.append("● 空闲", style=RICH_COLORS["$muted"])

    def _append_system_status_minimal(self, text: Text) -> None:
        if self.error_count > 0:
            text.append(
                f"✖ {self.error_count}",
                style=RICH_COLORS["$error"],
            )
        elif self.running_count > 0:
            text.append(
                f"● {self.running_count}",
                style=RICH_COLORS["$success"],
            )
        else:
            text.append("● 0", style=RICH_COLORS["$muted"])

    # ------------------------------------------------------------------
    # 审批状态区域
    # ------------------------------------------------------------------

    def _append_approval_status_full(self, text: Text) -> None:
        if self.pending_approvals > 0:
            text.append(
                f"◆ {self.pending_approvals} 审批等待",
                style=RICH_COLORS["$warning"],
            )
        else:
            text.append("✔ 无待处理", style=RICH_COLORS["$success"])

    def _append_approval_status_compact(self, text: Text) -> None:
        if self.pending_approvals > 0:
            text.append(
                f"◆ {self.pending_approvals}审批",
                style=RICH_COLORS["$warning"],
            )
        else:
            text.append("✔ 无待处理", style=RICH_COLORS["$success"])

    def _append_approval_status_minimal(self, text: Text) -> None:
        if self.pending_approvals > 0:
            text.append(
                f"◆ {self.pending_approvals}",
                style=RICH_COLORS["$warning"],
            )
        else:
            text.append("✔ 0", style=RICH_COLORS["$success"])

    # ------------------------------------------------------------------
    # 成本区域
    # ------------------------------------------------------------------

    def _append_cost_full(self, text: Text) -> None:
        text.append(f"${self.today_cost_usd:.2f} 今日")

    def _append_cost_compact(self, text: Text) -> None:
        text.append(f"${self.today_cost_usd:.2f}")

    # ------------------------------------------------------------------
    # 更新时间区域
    # ------------------------------------------------------------------

    def _append_time_full(self, text: Text) -> None:
        text.append(f"更新 {self.seconds_ago}s前")

    def _append_time_compact(self, text: Text) -> None:
        text.append(f"{self.seconds_ago}s")
