"""dashboard — 仪表盘布局容器。

包含 ContentSwitcher 管理三种布局模式：
- three-panel: ≥140 列三面板 lazygit 风格
- tabbed: 100-139 列 Tab 切换
- degraded: <100 列降级警告
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widget import Widget
from textual.widgets import ContentSwitcher, Static, TabbedContent, TabPane

from ato.tui.theme import (
    RICH_COLORS,
    format_status,
    map_story_to_visual_status,
    sort_stories_by_status,
)
from ato.tui.widgets.heartbeat_indicator import HeartbeatIndicator
from ato.tui.widgets.story_status_line import StoryStatusLine, _format_elapsed


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

    BINDINGS: ClassVar[list[BindingType]] = [
        ("up", "select_prev", "上移"),
        ("down", "select_next", "下移"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._story_count = 0
        self._pending_approvals = 0
        self._today_cost_usd = 0.0
        self._last_updated = ""
        # Story 列表数据
        self._stories: list[dict[str, object]] = []
        self._stories_by_id: dict[str, dict[str, object]] = {}
        self._story_costs: dict[str, float] = {}
        self._story_started_at: dict[str, str] = {}
        self._story_cl_rounds: dict[str, int] = {}
        self._convergent_loop_max_rounds = 3
        # 选中状态
        self._selected_story_id: str | None = None
        self._selected_index: int = 0
        self._sorted_story_ids: list[str] = []
        # 当前已渲染的 story 快照（用于增量更新判断）——每个容器独立
        self._rendered_snapshot: list[tuple[str, str, str]] = []
        self._rendered_snapshot_tab: list[tuple[str, str, str]] = []
        # 每个模式最近聚焦的 widget id，用于切换时恢复焦点
        self._saved_focus: dict[str, str | None] = {}

    def compose(self) -> ComposeResult:
        with ContentSwitcher(initial="three-panel"):
            # 三面板模式
            with Horizontal(id="three-panel", classes="three-panel-container"):
                with _FocusablePanel(id="panel-left", classes="left-panel"):
                    scroll = VerticalScroll(id="story-list-container")
                    scroll.can_focus = False
                    yield scroll
                with _FocusablePanel(id="panel-right", classes="right-panel"):
                    yield Static(
                        "选择左面板的 story 查看详情",
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
                    tab_scroll = VerticalScroll(id="tab-story-list-container")
                    tab_scroll.can_focus = False
                    yield tab_scroll
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

    # ------------------------------------------------------------------
    # ↑↓ 选择导航
    # ------------------------------------------------------------------

    def action_select_prev(self) -> None:
        """左面板获焦时上移选中 story。"""
        if not self._is_left_panel_focused():
            return
        if self._sorted_story_ids and self._selected_index > 0:
            self._selected_index -= 1
            self._selected_story_id = self._sorted_story_ids[self._selected_index]
            self._highlight_selected()
            self._update_detail_panel()

    def action_select_next(self) -> None:
        """左面板获焦时下移选中 story。"""
        if not self._is_left_panel_focused():
            return
        if self._sorted_story_ids and self._selected_index < len(self._sorted_story_ids) - 1:
            self._selected_index += 1
            self._selected_story_id = self._sorted_story_ids[self._selected_index]
            self._highlight_selected()
            self._update_detail_panel()

    def _is_left_panel_focused(self) -> bool:
        """检查左面板是否获焦。"""
        app = self.app
        if app is None:
            return False
        focused = app.focused
        if focused is None:
            return False
        # 检查焦点是否在 panel-left 或其子控件中
        try:
            left_panel = self.query_one("#panel-left")
            # 检查 focused 是 left_panel 本身或其子节点
            node = focused
            while node is not None:
                if node is left_panel:
                    return True
                node = node.parent  # type: ignore[assignment]
            return False
        except Exception:
            return False

    # ------------------------------------------------------------------
    # 数据更新接口
    # ------------------------------------------------------------------

    def update_content(
        self,
        *,
        story_count: int,
        pending_approvals: int,
        today_cost_usd: float,
        last_updated: str,
        stories: list[dict[str, object]] | None = None,
        story_costs: dict[str, float] | None = None,
        story_started_at: dict[str, str] | None = None,
        story_cl_rounds: dict[str, int] | None = None,
        convergent_loop_max_rounds: int | None = None,
    ) -> None:
        """更新仪表盘数据（所有模式同步）。保持向后兼容。"""
        self._story_count = story_count
        self._pending_approvals = pending_approvals
        self._today_cost_usd = today_cost_usd
        self._last_updated = last_updated

        if stories is not None:
            self._stories = stories
            self._stories_by_id = {str(s.get("story_id", "")): s for s in stories}
        if story_costs is not None:
            self._story_costs = story_costs
        if story_started_at is not None:
            self._story_started_at = story_started_at
        if story_cl_rounds is not None:
            self._story_cl_rounds = story_cl_rounds
        if convergent_loop_max_rounds is not None:
            self._convergent_loop_max_rounds = convergent_loop_max_rounds

        self._refresh_placeholders()

    # ------------------------------------------------------------------
    # 渲染逻辑
    # ------------------------------------------------------------------

    def _refresh_placeholders(self) -> None:
        """刷新所有模式的内容。"""
        summary = (
            f"Stories: {self._story_count} | "
            f"待审批: {self._pending_approvals} | "
            f"今日成本: ${self._today_cost_usd:.2f} | "
            f"更新: {self._last_updated}"
        )

        # 三面板模式 — Story 列表
        self._update_story_list("#story-list-container")

        # 右上面板 — 联动详情
        self._update_detail_panel()

        # Tab 模式 — [2]Stories Tab
        self._update_story_list("#tab-story-list-container")

        # Tab 模式 — 其他 Tab
        self._update_static(
            "#tab-approvals-content",
            f"待审批: {self._pending_approvals}",
        )
        self._update_static(
            "#tab-cost-content",
            f"今日成本: ${self._today_cost_usd:.2f}",
        )
        self._update_static(
            "#tab-log-content",
            f"更新: {self._last_updated}",
        )

        # 降级模式
        self._update_static(
            "#degraded",
            f"终端宽度不足 100 列，请扩大终端窗口或使用 CLI 命令\n{summary}",
        )

    def _update_story_list(self, container_selector: str) -> None:
        """更新指定容器中的 story 列表。"""
        try:
            container = self.query_one(container_selector, VerticalScroll)
        except Exception:
            return

        is_primary = container_selector == "#story-list-container"
        sorted_stories = sort_stories_by_status(self._stories)
        sorted_ids = [str(s.get("story_id", "")) for s in sorted_stories]

        # 构建快照用于增量判断：(story_id, status, current_phase)
        new_snapshot = [
            (str(s.get("story_id", "")), str(s.get("status", "")), str(s.get("current_phase", "")))
            for s in sorted_stories
        ]

        # 清除旧的空状态 widget（如果有）
        for empty_w in list(container.query(".empty-state")):
            empty_w.remove()

        # 空状态处理
        if not sorted_stories:
            if is_primary:
                self._sorted_story_ids = []
                self._selected_story_id = None
                self._selected_index = 0
                self._rendered_snapshot = []
            else:
                self._rendered_snapshot_tab = []
            # 已经在空状态则跳过（刚清理完不需要再挂载）
            if not list(container.query(".empty-state")):
                # 清除所有 story widgets
                for child in list(container.children):
                    child.remove()
                empty_id = "empty-state" if is_primary else "tab-empty-state"
                container.mount(
                    Static(
                        "尚无 story。运行 `ato batch select` 选择第一个 batch",
                        id=empty_id,
                        classes="empty-state",
                    )
                )
            return

        if is_primary:
            self._sorted_story_ids = sorted_ids
            # 保持选中 story（refresh 后恢复焦点）
            if self._selected_story_id and self._selected_story_id in sorted_ids:
                self._selected_index = sorted_ids.index(self._selected_story_id)
            else:
                self._selected_index = 0
                self._selected_story_id = sorted_ids[0] if sorted_ids else None

        # 判断是否需要重建——story 列表结构变化时才重建
        snapshot_ref = self._rendered_snapshot if is_primary else self._rendered_snapshot_tab
        needs_rebuild = new_snapshot != snapshot_ref

        if needs_rebuild:
            for child in list(container.children):
                child.remove()
            for story in sorted_stories:
                sid = str(story.get("story_id", ""))
                status = str(story.get("status", ""))
                phase = str(story.get("current_phase", ""))
                cost = self._story_costs.get(sid, 0.0)
                cl_round = self._story_cl_rounds.get(sid, 0)
                elapsed = self._compute_elapsed(sid)
                prefix = "hb" if is_primary else "thb"
                ssl_prefix = "ssl" if is_primary else "tssl"

                if status == "in_progress" and sid in self._story_started_at:
                    hb_w = HeartbeatIndicator(id=f"{prefix}-{sid}", classes="story-row")
                    container.mount(hb_w)
                    started_mono = self._iso_to_monotonic(self._story_started_at[sid])
                    hb_w.update_heartbeat(
                        story_id=sid,
                        current_phase=phase,
                        round_num=cl_round,
                        max_rounds=self._convergent_loop_max_rounds,
                        cost_usd=cost,
                        started_at=started_mono,
                    )
                else:
                    ssl_w = StoryStatusLine(id=f"{ssl_prefix}-{sid}", classes="story-row")
                    container.mount(ssl_w)
                    ssl_w.update_data(
                        story_id=sid,
                        status=status,
                        current_phase=phase,
                        cost_usd=cost,
                        elapsed_seconds=elapsed,
                        cl_round=cl_round,
                        cl_max_rounds=self._convergent_loop_max_rounds,
                    )

            if is_primary:
                self._rendered_snapshot = new_snapshot
            else:
                self._rendered_snapshot_tab = new_snapshot
        else:
            # 仅更新数据，不重建 widgets
            prefix = "hb" if is_primary else "thb"
            ssl_prefix = "ssl" if is_primary else "tssl"
            for story in sorted_stories:
                sid = str(story.get("story_id", ""))
                status = str(story.get("status", ""))
                phase = str(story.get("current_phase", ""))
                cost = self._story_costs.get(sid, 0.0)
                cl_round = self._story_cl_rounds.get(sid, 0)
                elapsed = self._compute_elapsed(sid)

                if status == "in_progress" and sid in self._story_started_at:
                    try:
                        hb = container.query_one(f"#{prefix}-{sid}", HeartbeatIndicator)
                        started_mono = self._iso_to_monotonic(self._story_started_at[sid])
                        hb.update_heartbeat(
                            story_id=sid,
                            current_phase=phase,
                            round_num=cl_round,
                            max_rounds=self._convergent_loop_max_rounds,
                            cost_usd=cost,
                            started_at=started_mono,
                        )
                    except Exception:
                        pass
                else:
                    try:
                        ssl = container.query_one(f"#{ssl_prefix}-{sid}", StoryStatusLine)
                        ssl.update_data(
                            story_id=sid,
                            status=status,
                            current_phase=phase,
                            cost_usd=cost,
                            elapsed_seconds=elapsed,
                            cl_round=cl_round,
                            cl_max_rounds=self._convergent_loop_max_rounds,
                        )
                    except Exception:
                        pass

        if is_primary:
            self._highlight_selected()

    def _highlight_selected(self) -> None:
        """更新选中 story 的高亮样式。"""
        try:
            container = self.query_one("#story-list-container", VerticalScroll)
        except Exception:
            return

        for child in container.children:
            if hasattr(child, "remove_class"):
                child.remove_class("selected-story")
                if (
                    self._selected_story_id
                    and child.id
                    and child.id.endswith(f"-{self._selected_story_id}")
                ):
                    child.add_class("selected-story")

    def _update_detail_panel(self) -> None:
        """右上面板联动显示选中 story 详情。"""
        if not self._selected_story_id:
            self._update_static("#right-top-content", "选择左面板的 story 查看详情")
            return

        story = self._stories_by_id.get(self._selected_story_id)
        if not story:
            self._update_static("#right-top-content", "选择左面板的 story 查看详情")
            return

        sid = str(story.get("story_id", ""))
        title = str(story.get("title", ""))
        status = str(story.get("status", ""))
        phase = str(story.get("current_phase", ""))
        cost = self._story_costs.get(sid, 0.0)
        cl_round = self._story_cl_rounds.get(sid, 0)
        elapsed = self._compute_elapsed(sid)

        visual = map_story_to_visual_status(status)
        sc = format_status(visual)

        # 构建 Rich Text 详情
        detail = Text()
        detail.append(f"{sc.icon} ", style=RICH_COLORS.get(sc.color_var, ""))
        detail.append(f"{sid}", style=f"bold {RICH_COLORS['$accent']}")
        detail.append(f"  {title}\n", style=RICH_COLORS["$text"])
        detail.append("阶段: ", style=RICH_COLORS["$muted"])
        detail.append(f"{phase}\n", style=RICH_COLORS.get(sc.color_var, ""))
        detail.append("成本: ", style=RICH_COLORS["$muted"])
        detail.append(f"${cost:.2f}\n", style=RICH_COLORS["$text"])
        detail.append("耗时: ", style=RICH_COLORS["$muted"])
        detail.append(f"{_format_elapsed(elapsed)}\n", style=RICH_COLORS["$text"])
        if cl_round > 0:
            detail.append("CL 轮次: ", style=RICH_COLORS["$muted"])
            detail.append(
                f"R{cl_round}/{self._convergent_loop_max_rounds}",
                style=RICH_COLORS["$info"],
            )

        try:
            widget = self.query_one("#right-top-content", Static)
            widget.update(detail)
        except Exception:
            pass

    def _compute_elapsed(self, story_id: str) -> int:
        """计算 story 的经过时间（秒）。"""
        started_iso = self._story_started_at.get(story_id)
        if not started_iso:
            return 0
        try:
            started_dt = datetime.fromisoformat(started_iso)
            if started_dt.tzinfo is None:
                started_dt = started_dt.replace(tzinfo=UTC)
            delta = datetime.now(tz=UTC) - started_dt
            return max(0, int(delta.total_seconds()))
        except (ValueError, TypeError):
            return 0

    def _iso_to_monotonic(self, iso_str: str) -> float:
        """将 ISO 时间戳转换为 monotonic 时间戳（用于 HeartbeatIndicator）。"""
        try:
            started_dt = datetime.fromisoformat(iso_str)
            if started_dt.tzinfo is None:
                started_dt = started_dt.replace(tzinfo=UTC)
            elapsed_seconds = (datetime.now(tz=UTC) - started_dt).total_seconds()
            return time.monotonic() - max(0.0, elapsed_seconds)
        except (ValueError, TypeError):
            return 0.0

    def _update_static(self, selector: str, text: str | Text) -> None:
        """安全更新指定 Static 内容。"""
        try:
            widget = self.query_one(selector, Static)
            widget.update(text)
        except Exception:
            pass
