"""dashboard — 仪表盘布局容器。

包含 ContentSwitcher 管理三种布局模式：
- three-panel: ≥140 列三面板 lazygit 风格
- tabbed: 100-139 列 Tab 切换
- degraded: <100 列降级警告
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import ContentSwitcher, Static, TabbedContent, TabPane


class _FocusablePanel(Vertical):
    """可聚焦的面板容器。

    Textual 8.1.1 中 Vertical 不读子类的 CAN_FOCUS，
    必须在 __init__ 后显式设置 can_focus。
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.can_focus = True


class DashboardScreen(Widget):
    """TUI 仪表盘布局容器。

    通过 ContentSwitcher 管理三种响应式布局模式。
    保留 update_content() 接口兼容 ATOApp 数据刷新。
    """

    DEFAULT_CSS = ""

    def __init__(self) -> None:
        super().__init__()
        self._story_count = 0
        self._pending_approvals = 0
        self._today_cost_usd = 0.0
        self._last_updated = ""
        # 每个模式最近聚焦的 widget id，用于切换时恢复焦点
        self._saved_focus: dict[str, str | None] = {}

    def compose(self) -> ComposeResult:
        with ContentSwitcher(initial="three-panel"):
            # 三面板模式
            with Horizontal(id="three-panel", classes="three-panel-container"):
                with _FocusablePanel(id="panel-left", classes="left-panel"):
                    yield Static(
                        "Stories 列表（占位）",
                        id="left-panel-content",
                    )
                with _FocusablePanel(id="panel-right", classes="right-panel"):
                    yield Static(
                        "详情（占位）",
                        id="right-top-content",
                    )
                    yield Static(
                        "操作区域（占位）",
                        id="right-bottom-content",
                    )

            # Tab 模式
            with Vertical(id="tabbed", classes="tabbed-container"), TabbedContent():
                with TabPane("[1]审批", id="tab-approvals"):
                    yield Static("审批面板（占位）", id="tab-approvals-content")
                with TabPane("[2]Stories", id="tab-stories"):
                    yield Static("Stories 面板（占位）", id="tab-stories-content")
                with TabPane("[3]成本", id="tab-cost"):
                    yield Static("成本面板（占位）", id="tab-cost-content")
                with TabPane("[4]日志", id="tab-log"):
                    yield Static("日志面板（占位）", id="tab-log-content")

            # 降级模式
            yield Static(
                "终端宽度不足 100 列，请扩大终端窗口或使用 CLI 命令",
                id="degraded",
                classes="degraded-container",
            )

    def on_mount(self) -> None:
        """初始化时同步焦点链状态。"""
        self._sync_focus_chain("three-panel")

    def set_layout_mode(self, mode: str) -> None:
        """切换布局模式，保存/恢复焦点上下文。"""
        switcher = self.query_one(ContentSwitcher)
        old_mode = switcher.current or ""

        # 保存离开模式的焦点
        if old_mode != mode:
            app = self.app
            focused = app.focused if app else None
            if focused is not None:
                self._saved_focus[old_mode] = focused.id
            else:
                self._saved_focus.setdefault(old_mode, None)

        switcher.current = mode
        self._sync_focus_chain(mode)
        self._refresh_placeholders()

        # 恢复目标模式的焦点
        if old_mode != mode:
            self._restore_focus(mode)

    def adjust_panel_ratio(self, terminal_width: int) -> None:
        """根据终端宽度动态调整三面板比例。

        ≥180 列: 30%/70% (超宽屏)
        140-179 列: 40%/60% (标准终端)
        """
        if terminal_width < 140:
            return
        panels = list(self.query(_FocusablePanel))
        if len(panels) < 2:
            return
        left, right = panels[0], panels[1]
        if terminal_width >= 180:
            left.styles.width = "30%"
            right.styles.width = "70%"
        else:
            left.styles.width = "40%"
            right.styles.width = "60%"

    def _sync_focus_chain(self, active_mode: str) -> None:
        """确保只有当前活跃模式中的控件参与焦点链。

        隐藏模式中的可聚焦控件（如 TabbedContent 内部 ContentTabs、
        面板 _FocusablePanel）设为 disabled 使其退出焦点链。
        """
        # 三面板面板
        for panel in self.query(_FocusablePanel):
            panel.disabled = active_mode != "three-panel"

        # Tab 模式的 TabbedContent
        for tc in self.query(TabbedContent):
            tc.disabled = active_mode != "tabbed"

    def _restore_focus(self, mode: str) -> None:
        """恢复指定模式之前保存的焦点。

        如果该模式没有保存焦点，聚焦到模式内第一个可聚焦控件。
        """
        app = self.app
        if app is None:
            return

        saved_id = self._saved_focus.get(mode)
        if saved_id is not None:
            try:
                widget = self.query_one(f"#{saved_id}")
                if widget.focusable:
                    app.set_focus(widget)
                    return
            except Exception:
                pass

        # 回退：聚焦到模式内第一个可聚焦控件
        if mode == "three-panel":
            panels = list(self.query(_FocusablePanel))
            for panel in panels:
                if panel.focusable:
                    app.set_focus(panel)
                    return
        elif mode == "tabbed":
            # TabbedContent 自身不可聚焦，需要找其内部可聚焦子控件
            for tc in self.query(TabbedContent):
                for child in tc.query("*"):
                    if child.focusable:
                        app.set_focus(child)
                        return

    def update_content(
        self,
        *,
        story_count: int,
        pending_approvals: int,
        today_cost_usd: float,
        last_updated: str,
    ) -> None:
        """更新仪表盘数据（所有模式同步）。保持向后兼容。"""
        self._story_count = story_count
        self._pending_approvals = pending_approvals
        self._today_cost_usd = today_cost_usd
        self._last_updated = last_updated
        self._refresh_placeholders()

    def _refresh_placeholders(self) -> None:
        """刷新所有模式的占位 Static 内容。"""
        summary = (
            f"Stories: {self._story_count} | "
            f"待审批: {self._pending_approvals} | "
            f"今日成本: ${self._today_cost_usd:.2f} | "
            f"更新: {self._last_updated}"
        )

        # 三面板模式
        self._update_static(
            "#left-panel-content",
            f"Stories: {self._story_count} | 待审批: {self._pending_approvals}",
        )
        self._update_static(
            "#right-top-content",
            f"今日成本: ${self._today_cost_usd:.2f} | 更新: {self._last_updated}",
        )

        # Tab 模式 — 各 Tab 显示对应维度数据
        self._update_static(
            "#tab-approvals-content",
            f"待审批: {self._pending_approvals}",
        )
        self._update_static(
            "#tab-stories-content",
            f"Stories: {self._story_count}",
        )
        self._update_static(
            "#tab-cost-content",
            f"今日成本: ${self._today_cost_usd:.2f}",
        )
        self._update_static(
            "#tab-log-content",
            f"更新: {self._last_updated}",
        )

        # 降级模式 — 在警告下追加摘要
        self._update_static(
            "#degraded",
            f"终端宽度不足 100 列，请扩大终端窗口或使用 CLI 命令\n{summary}",
        )

    def _update_static(self, selector: str, text: str) -> None:
        """安全更新指定 Static 内容。"""
        try:
            widget = self.query_one(selector, Static)
            widget.update(text)
        except Exception:
            pass
