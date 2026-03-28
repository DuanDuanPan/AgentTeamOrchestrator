"""dashboard — 仪表盘布局容器。

包含 ContentSwitcher 管理三种布局模式：
- three-panel: ≥140 列三面板 lazygit 风格
- tabbed: 100-139 列 Tab 切换
- degraded: <100 列降级警告
"""

from __future__ import annotations

import contextlib
import json
import time
from datetime import UTC, datetime
from typing import ClassVar

import structlog
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widget import Widget
from textual.widgets import ContentSwitcher, Static, TabbedContent, TabPane

from ato.approval_helpers import (
    format_approval_summary,
    get_binary_approval_labels,
    get_options_for_approval,
    is_binary_approval,
    resolve_binary_decision,
    resolve_multi_decision,
)
from ato.models.schemas import APPROVAL_TYPE_ICONS
from ato.tui.story_detail import StoryDetailView
from ato.tui.theme import (
    RICH_COLORS,
    format_status,
    map_risk_to_color,
    map_story_to_visual_status,
    sort_stories_by_status,
)
from ato.tui.widgets.approval_card import ApprovalCard
from ato.tui.widgets.exception_approval_panel import ExceptionApprovalPanel
from ato.tui.widgets.heartbeat_indicator import HeartbeatIndicator
from ato.tui.widgets.story_status_line import StoryStatusLine, _format_elapsed

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


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
        ("d", "toggle_detail", "展开/折叠"),
        ("y", "approve", "批准"),
        ("n", "reject", "拒绝"),
        ("enter", "drill_in", "详情"),
        ("escape", "back", "返回"),
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
        # Approval 数据（Story 6.3a）
        self._approval_records: list[object] = []
        self._approvals_by_id: dict[str, object] = {}
        # Findings 摘要（Story 6.3a AC2 — QA 结果）
        self._story_findings_summary: dict[str, dict[str, int]] = {}
        # 统一选中状态（审批在前 + story 在后）
        self._selected_item_id: str | None = None
        self._selected_index: int = 0
        self._sorted_item_ids: list[str] = []
        # 向后兼容属性（测试用）
        self._selected_story_id: str | None = None
        self._sorted_story_ids: list[str] = []
        # 审批展开态
        self._expanded_approval_id: str | None = None
        # 审批已提交中间状态（approval_id → True）
        self._submitted_approvals: set[str] = set()
        # 当前已渲染的快照（用于增量更新判断）——每个容器独立
        self._rendered_snapshot: list[tuple[str, str, str]] = []
        self._rendered_snapshot_tab: list[tuple[str, str, str]] = []
        self._rendered_approvals_snapshot_tab: list[tuple[str, str, str, str]] = []
        # 重建代数计数器（避免 Textual async remove + mount 时 DuplicateIds）
        self._rebuild_gen: int = 0
        self._rebuild_gen_tab: int = 0
        self._rebuild_gen_approvals_tab: int = 0
        # 每个模式最近聚焦的 widget id，用于切换时恢复焦点
        self._saved_focus: dict[str, str | None] = {}
        # Story 详情钻入模式 (Story 6.4)
        self._in_detail_mode: bool = False
        self._detail_story_id: str | None = None
        # Task 计数（轻量轮询）
        self._story_task_counts: dict[str, int] = {}

    def compose(self) -> ComposeResult:
        with ContentSwitcher(initial="three-panel"):
            # 三面板模式
            with Horizontal(id="three-panel", classes="three-panel-container"):
                with _FocusablePanel(id="panel-left", classes="left-panel"):
                    scroll = VerticalScroll(id="story-list-container")
                    scroll.can_focus = False
                    yield scroll
                with _FocusablePanel(id="panel-right", classes="right-panel"):
                    with ContentSwitcher(
                        initial="right-top-content",
                        id="right-top-switcher",
                    ):
                        yield Static(
                            "选择左面板的 story 查看详情",
                            id="right-top-content",
                        )
                        yield ExceptionApprovalPanel(id="right-top-exception")
                        yield StoryDetailView(id="right-top-detail")
                    yield Static(
                        "",
                        id="right-bottom-content",
                    )

            # Tab 模式
            with Vertical(id="tabbed", classes="tabbed-container"), TabbedContent():
                with TabPane("[1]审批", id="tab-approvals"):
                    tab_approval_scroll = VerticalScroll(id="tab-approvals-container")
                    tab_approval_scroll.can_focus = False
                    yield tab_approval_scroll
                with (
                    TabPane("[2]Stories", id="tab-stories"),
                    ContentSwitcher(
                        initial="tab-story-list-container",
                        id="tab-stories-switcher",
                    ),
                ):
                    tab_scroll = VerticalScroll(id="tab-story-list-container")
                    tab_scroll.can_focus = False
                    yield tab_scroll
                    yield StoryDetailView(id="tab-story-detail")
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
    # ↑↓ 选择导航（统一管理审批 + story）
    # ------------------------------------------------------------------

    def action_select_prev(self) -> None:
        """左面板获焦时上移选中项。"""
        if not self._is_left_panel_focused():
            return
        if self._sorted_item_ids and self._selected_index > 0:
            self._selected_index -= 1
            self._selected_item_id = self._sorted_item_ids[self._selected_index]
            self._sync_selected_story_id()
            self._highlight_selected()
            self._update_detail_panel()
            self._update_action_panel()

    def action_select_next(self) -> None:
        """左面板获焦时下移选中项。"""
        if not self._is_left_panel_focused():
            return
        if self._sorted_item_ids and self._selected_index < len(self._sorted_item_ids) - 1:
            self._selected_index += 1
            self._selected_item_id = self._sorted_item_ids[self._selected_index]
            self._sync_selected_story_id()
            self._highlight_selected()
            self._update_detail_panel()
            self._update_action_panel()

    def _sync_selected_story_id(self) -> None:
        """同步 _selected_story_id 以保持向后兼容。"""
        if self._selected_item_id and self._selected_item_id.startswith("story:"):
            self._selected_story_id = self._selected_item_id.removeprefix("story:")
        else:
            self._selected_story_id = None

    # ------------------------------------------------------------------
    # y/n/d 审批操作 (Story 6.3a)
    # ------------------------------------------------------------------

    def action_approve(self) -> None:
        """y 键：对当前选中的二选一审批提交 approve 方向决策。"""
        if self._in_detail_mode:
            return  # 详情模式下不消费审批键
        self._submit_decision("y")

    def action_reject(self) -> None:
        """n 键：对当前选中的二选一审批提交 reject 方向决策。"""
        if self._in_detail_mode:
            return  # 详情模式下不消费审批键
        self._submit_decision("n")

    def _submit_decision(self, key: str) -> None:
        """统一处理 y/n 键按下。"""
        if not self._selected_item_id or not self._selected_item_id.startswith("approval:"):
            # 选中的不是审批项——忽略
            return

        aid = self._selected_item_id.removeprefix("approval:")

        # 已提交的审批不允许二次提交
        if aid in self._submitted_approvals:
            return

        approval = self._approvals_by_id.get(aid)
        if approval is None:
            return

        approval_type = getattr(approval, "approval_type", "")
        payload = getattr(approval, "payload", None)

        # 多选异常审批 — y/n 无效（Story 6.3b）
        if not is_binary_approval(approval_type, payload):
            return

        result = resolve_binary_decision(approval_type, key)  # type: ignore[arg-type]
        if result is None:
            return

        decision, status = result
        decision_reason = f"tui:{key} -> {decision}"

        # 标记已提交中间状态
        self._submitted_approvals.add(aid)
        self._update_action_panel()

        # 异步写入 SQLite + nudge
        from ato.tui.app import ATOApp

        app = self.app
        if isinstance(app, ATOApp):

            async def _do_submit() -> None:
                try:
                    ok = await app.submit_approval_decision(
                        approval_id=aid,
                        status=status,
                        decision=decision,
                        decision_reason=decision_reason,
                    )
                    if not ok:
                        self._rollback_submitted(aid)
                except Exception:
                    self._rollback_submitted(aid)

            self.run_worker(_do_submit(), exclusive=False)

    def _rollback_submitted(self, aid: str) -> None:
        """写库失败时回退已提交状态，允许用户重试。"""
        self._submitted_approvals.discard(aid)
        self._update_action_panel()

    # ------------------------------------------------------------------
    # 数字键多选异常审批提交 (Story 6.3b)
    # ------------------------------------------------------------------

    def _handle_option_key(self, index: int) -> None:
        """处理数字键选择异常审批选项。

        Args:
            index: 0-based 选项索引（按键 1 → index 0）。
        """
        # 必须选中一个审批项
        if not self._selected_item_id or not self._selected_item_id.startswith("approval:"):
            return

        aid = self._selected_item_id.removeprefix("approval:")

        # 已提交的审批不允许二次提交
        if aid in self._submitted_approvals:
            return

        approval = self._approvals_by_id.get(aid)
        if approval is None:
            return

        approval_type = getattr(approval, "approval_type", "")
        payload = getattr(approval, "payload", None)

        # 仅对多选异常审批生效（二选一审批用 y/n）
        if is_binary_approval(approval_type, payload):
            return

        # 尝试解析决策
        try:
            decision, status = resolve_multi_decision(approval_type, index, payload)
        except ValueError:
            # 超出范围——no-op，不写 SQLite、不给假成功提示
            return

        decision_reason = f"tui:{index + 1} -> {decision}"

        # 标记已提交中间状态
        self._submitted_approvals.add(aid)
        self._update_action_panel()

        # 异步写入 SQLite + nudge
        from ato.tui.app import ATOApp

        try:
            app = self.app
        except Exception:
            return
        if not isinstance(app, ATOApp):
            return

        async def _do_submit() -> None:
            try:
                ok = await app.submit_approval_decision(
                    approval_id=aid,
                    status=status,
                    decision=decision,
                    decision_reason=decision_reason,
                )
                if not ok:
                    self._rollback_submitted(aid)
            except Exception:
                self._rollback_submitted(aid)

        self.run_worker(_do_submit(), exclusive=False)

    def on_key(self, event: events.Key) -> None:
        """捕获数字键用于异常审批选择。

        仅在 three-panel 模式下、选中多选异常审批时生效。
        tabbed 模式下数字键由 ATOApp.action_switch_tab() 处理。
        """
        if event.key in ("1", "2", "3", "4", "5", "6", "7", "8", "9"):
            # 检查是否在 three-panel 模式
            app = self.app
            if app is None:
                return
            from ato.tui.app import ATOApp

            if not isinstance(app, ATOApp):
                return
            if app.layout_mode != "three-panel":
                return

            # 检查是否选中了多选异常审批
            if self._selected_item_id and self._selected_item_id.startswith("approval:"):
                aid = self._selected_item_id.removeprefix("approval:")
                approval = self._approvals_by_id.get(aid)
                if approval:
                    approval_type = getattr(approval, "approval_type", "")
                    payload = getattr(approval, "payload", None)
                    if not is_binary_approval(approval_type, payload):
                        index = int(event.key) - 1
                        self._handle_option_key(index)
                        event.prevent_default()
                        event.stop()

    def action_toggle_detail(self) -> None:
        """d 键：切换审批展开/折叠态。"""
        if self._in_detail_mode:
            return  # 详情模式下不消费审批键
        if not self._selected_item_id or not self._selected_item_id.startswith("approval:"):
            return
        aid = self._selected_item_id.removeprefix("approval:")
        if self._expanded_approval_id == aid:
            self._expanded_approval_id = None
        else:
            self._expanded_approval_id = aid
        self._update_detail_panel()
        self._update_action_panel()

    # ------------------------------------------------------------------
    # Enter / ESC 导航 (Story 6.4 — Task 4)
    # ------------------------------------------------------------------

    def action_drill_in(self) -> None:
        """Enter 键：从主屏进入 Story 详情页（第 2 层）。"""
        if self._in_detail_mode:
            return
        if not self._selected_item_id or not self._selected_item_id.startswith("story:"):
            return
        sid = self._selected_item_id.removeprefix("story:")
        self._enter_detail_mode(sid)

    def action_back(self) -> None:
        """ESC 键：从详情页返回主屏。"""
        if not self._in_detail_mode:
            return
        self._exit_detail_mode()

    def _enter_detail_mode(self, story_id: str) -> None:
        """进入 story 详情钻入模式。"""
        self._in_detail_mode = True
        self._detail_story_id = story_id

        # 加载详情数据并更新视图
        async def _load_and_show() -> None:
            from ato.tui.app import ATOApp

            app = self.app
            if not isinstance(app, ATOApp):
                return

            detail_data = await app.load_story_detail(story_id)
            story = self._stories_by_id.get(story_id, {})

            # 确定当前活跃的布局模式
            if app.layout_mode == "three-panel":
                self._show_detail_three_panel(story, detail_data, story_id)
            elif app.layout_mode == "tabbed":
                self._show_detail_tabbed(story, detail_data, story_id)

        self.run_worker(_load_and_show(), exclusive=False)

    def _show_detail_three_panel(
        self,
        story: dict[str, object],
        detail_data: dict[str, object],
        story_id: str,
    ) -> None:
        """三面板模式：切换 #right-top-switcher 到 StoryDetailView。"""
        try:
            detail_view = self.query_one("#right-top-detail", StoryDetailView)
            self._populate_detail_view(detail_view, story, detail_data, story_id)
            switcher = self.query_one("#right-top-switcher", ContentSwitcher)
            switcher.current = "right-top-detail"
            detail_view.focus()
        except Exception as exc:
            logger.debug(
                "detail_view_render_failed",
                layout="three-panel",
                story_id=story_id,
                error=str(exc),
                exc_info=True,
            )

    def _show_detail_tabbed(
        self,
        story: dict[str, object],
        detail_data: dict[str, object],
        story_id: str,
    ) -> None:
        """Tabbed 模式：在 tab-stories 内切换到 StoryDetailView。"""
        try:
            detail_view = self.query_one("#tab-story-detail", StoryDetailView)
            self._populate_detail_view(detail_view, story, detail_data, story_id)
            switcher = self.query_one("#tab-stories-switcher", ContentSwitcher)
            switcher.current = "tab-story-detail"
            detail_view.focus()
        except Exception as exc:
            logger.debug(
                "detail_view_render_failed",
                layout="tabbed",
                story_id=story_id,
                error=str(exc),
                exc_info=True,
            )

    def _populate_detail_view(
        self,
        view: StoryDetailView,
        story: dict[str, object],
        detail_data: dict[str, object],
        story_id: str,
    ) -> None:
        """填充 StoryDetailView 数据。"""
        view.update_detail(
            story=story,
            findings_summary=detail_data.get("findings_summary", {}),  # type: ignore[arg-type]
            findings_detail=detail_data.get("findings", []),  # type: ignore[arg-type]
            cost_logs=detail_data.get("cost_logs", []),  # type: ignore[arg-type]
            tasks=detail_data.get("tasks", []),  # type: ignore[arg-type]
            cl_round=self._story_cl_rounds.get(story_id, 0),
            cl_max_rounds=self._convergent_loop_max_rounds,
            cost_usd=self._story_costs.get(story_id, 0.0),
        )

    def _exit_detail_mode(self) -> None:
        """退出���情模式，返回主屏概览。"""
        self._in_detail_mode = False
        self._detail_story_id = None

        from ato.tui.app import ATOApp

        app = self.app
        layout = app.layout_mode if isinstance(app, ATOApp) else "three-panel"

        if layout == "three-panel":
            # 切换回常规概览
            self._show_right_top_static("选择左面板的 story 查看详情")
            self._update_detail_panel()
            self._restore_focus("three-panel")
        elif layout == "tabbed":
            try:
                switcher = self.query_one("#tab-stories-switcher", ContentSwitcher)
                switcher.current = "tab-story-list-container"
            except Exception:
                pass
            # TabbedContent 自身不可聚焦，回退到其第一个可聚焦子控件。
            self._restore_focus("tabbed")

    def on_story_detail_view_back_requested(self, _: StoryDetailView.BackRequested) -> None:
        """处理 StoryDetailView 发出的 ESC 返回请求。"""
        self._exit_detail_mode()

    def _is_left_panel_focused(self) -> bool:
        """检查导航列表区域是否获焦（three-panel 或 tabbed 模式）。"""
        app = self.app
        if app is None:
            return False
        focused = app.focused
        if focused is None:
            return False
        layout = getattr(app, "layout_mode", "three-panel")

        if layout == "three-panel":
            with contextlib.suppress(Exception):
                return self._is_widget_within_focus_path(focused, self.query_one("#panel-left"))
            return False

        if layout == "tabbed":
            try:
                tabbed = self.query_one(TabbedContent)
                switcher = self.query_one("#tab-stories-switcher", ContentSwitcher)
            except Exception:
                return False

            if tabbed.active != "tab-stories":
                return False
            if switcher.current != "tab-story-list-container":
                return False

            return self._is_widget_within_focus_path(focused, tabbed)

        return False

    @staticmethod
    def _is_widget_within_focus_path(focused: object, container: object) -> bool:
        """判断当前焦点是否位于指定容器子树内。"""
        node: object = focused
        while node is not None:
            if node is container:
                return True
            node = getattr(node, "parent", None)
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
        pending_approval_records: list[object] | None = None,
        story_findings_summary: dict[str, dict[str, int]] | None = None,
        story_task_counts: dict[str, int] | None = None,
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
        if pending_approval_records is not None:
            self._approval_records = pending_approval_records
            self._approvals_by_id = {
                getattr(a, "approval_id", ""): a for a in pending_approval_records
            }
            # 清除已消失的已提交审批
            current_ids = set(self._approvals_by_id.keys())
            self._submitted_approvals &= current_ids
        if story_findings_summary is not None:
            self._story_findings_summary = story_findings_summary
        if story_task_counts is not None:
            self._story_task_counts = story_task_counts

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

        # 三面板模式 — 审批 + Story 列表
        self._update_story_list("#story-list-container")

        # 右上面板 — 联动详情
        self._update_detail_panel()

        # 右下面板 — 操作提示
        self._update_action_panel()

        # Tab 模式 — [2]Stories Tab
        self._update_story_list("#tab-story-list-container")

        # Tab 模式 — [1]审批 Tab
        self._update_approvals_tab()

        # Tab 模式 — 其他 Tab
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
        """更新指定容器中的审批 + story 列表。

        三面板模式（primary）：审批在前 + story 在后
        Tab 模式：仅 story（审批在独立 [1]审批 Tab 中）
        """
        try:
            container = self.query_one(container_selector, VerticalScroll)
        except Exception:
            return

        is_primary = container_selector == "#story-list-container"
        sorted_stories = sort_stories_by_status(self._stories)
        sorted_story_ids = [str(s.get("story_id", "")) for s in sorted_stories]

        # 构建统一的 item ID 列表
        new_item_ids: list[str] = []
        # 仅主面板包含审批项（Tab 模式审批在独立 Tab）
        # 异常审批排在常规审批之前 (AC4)
        if is_primary:
            sorted_approvals = self._sort_approvals(self._approval_records)
            for a in sorted_approvals:
                new_item_ids.append(f"approval:{getattr(a, 'approval_id', '')}")
        for sid in sorted_story_ids:
            new_item_ids.append(f"story:{sid}")

        # 构建快照用于增量判断
        story_snapshot = [
            (str(s.get("story_id", "")), str(s.get("status", "")), str(s.get("current_phase", "")))
            for s in sorted_stories
        ]
        if is_primary:
            approval_snapshot: list[tuple[str, str, str]] = [
                (
                    getattr(a, "approval_id", ""),
                    getattr(a, "approval_type", ""),
                    getattr(a, "story_id", ""),
                )
                for a in sorted_approvals
            ]
            new_snapshot: list[tuple[str, str, str]] = approval_snapshot + story_snapshot
        else:
            new_snapshot = story_snapshot

        # 清除旧的空状态 widget（如果有）
        for empty_w in list(container.query(".empty-state")):
            empty_w.remove()

        # 空状态处理（tab 容器不含审批，检查条件简化）
        has_content = bool(sorted_stories) or (is_primary and bool(self._approval_records))
        if not has_content:
            if is_primary:
                self._sorted_item_ids = []
                self._sorted_story_ids = []
                self._selected_item_id = None
                self._selected_story_id = None
                self._selected_index = 0
                self._rendered_snapshot = []
            else:
                self._rendered_snapshot_tab = []
                self._rendered_approvals_snapshot_tab = []
            # 已经在空状态则跳过（刚清理完不需要再挂载）
            if not list(container.query(".empty-state")):
                # 清除所有 widgets
                container.remove_children()
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
            self._sorted_item_ids = new_item_ids
            self._sorted_story_ids = sorted_story_ids

            # 向后兼容：外部直接设置 _selected_story_id 时同步到 _selected_item_id
            if self._selected_story_id:
                expected_item = f"story:{self._selected_story_id}"
                if self._selected_item_id != expected_item and expected_item in new_item_ids:
                    self._selected_item_id = expected_item

            # 保持选中项（refresh 后恢复焦点）
            if self._selected_item_id and self._selected_item_id in new_item_ids:
                self._selected_index = new_item_ids.index(self._selected_item_id)
            else:
                self._selected_index = 0
                self._selected_item_id = new_item_ids[0] if new_item_ids else None
            self._sync_selected_story_id()

        # 判断是否需要重建
        snapshot_ref = self._rendered_snapshot if is_primary else self._rendered_snapshot_tab
        needs_rebuild = new_snapshot != snapshot_ref

        if needs_rebuild:
            container.remove_children()

            # 更新代数计数器（避免 async removal 时 DuplicateIds）
            if is_primary:
                self._rebuild_gen += 1
                gen = self._rebuild_gen
            else:
                self._rebuild_gen_tab += 1
                gen = self._rebuild_gen_tab

            # 先渲染审批卡片（仅主面板，异常审批在前）
            if is_primary:
                for a_record in sorted_approvals:
                    aid = getattr(a_record, "approval_id", "")
                    ac_w = ApprovalCard(id=f"ac{gen}-{aid}", classes="approval-row")
                    container.mount(ac_w)
                    ac_w.update_data(
                        approval_id=aid,
                        story_id=getattr(a_record, "story_id", ""),
                        approval_type=getattr(a_record, "approval_type", ""),
                        payload=getattr(a_record, "payload", None),
                        recommended_action=getattr(a_record, "recommended_action", None),
                        risk_level=getattr(a_record, "risk_level", None),
                    )

            # 再渲染 story
            prefix_hb = f"hb{gen}" if is_primary else f"thb{gen}"
            prefix_ssl = f"ssl{gen}" if is_primary else f"tssl{gen}"
            for story in sorted_stories:
                sid = str(story.get("story_id", ""))
                status = str(story.get("status", ""))
                phase = str(story.get("current_phase", ""))
                cost = self._story_costs.get(sid, 0.0)
                cl_round = self._story_cl_rounds.get(sid, 0)
                elapsed = self._compute_elapsed(sid)

                if status == "in_progress" and sid in self._story_started_at:
                    hb_w = HeartbeatIndicator(id=f"{prefix_hb}-{sid}", classes="story-row")
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
                    ssl_w = StoryStatusLine(id=f"{prefix_ssl}-{sid}", classes="story-row")
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
            gen = self._rebuild_gen if is_primary else self._rebuild_gen_tab

            if is_primary:
                for a_record in self._approval_records:
                    aid = getattr(a_record, "approval_id", "")
                    try:
                        ac = container.query_one(f"#ac{gen}-{aid}", ApprovalCard)
                        ac.update_data(
                            approval_id=aid,
                            story_id=getattr(a_record, "story_id", ""),
                            approval_type=getattr(a_record, "approval_type", ""),
                            payload=getattr(a_record, "payload", None),
                            recommended_action=getattr(a_record, "recommended_action", None),
                            risk_level=getattr(a_record, "risk_level", None),
                        )
                    except Exception:
                        pass

            prefix = f"hb{gen}" if is_primary else f"thb{gen}"
            ssl_prefix = f"ssl{gen}" if is_primary else f"tssl{gen}"
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
        """更新选中项的高亮样式。"""
        try:
            container = self.query_one("#story-list-container", VerticalScroll)
        except Exception:
            return

        gen = self._rebuild_gen
        for child in container.children:
            if not hasattr(child, "remove_class"):
                continue
            child.remove_class("selected-story")
            child.remove_class("selected-approval")

            if not self._selected_item_id or not child.id:
                continue

            if self._selected_item_id.startswith("approval:"):
                aid = self._selected_item_id.removeprefix("approval:")
                if child.id == f"ac{gen}-{aid}":
                    child.add_class("selected-approval")
            elif self._selected_item_id.startswith("story:"):
                sid = self._selected_item_id.removeprefix("story:")
                if child.id == f"ssl{gen}-{sid}" or child.id == f"hb{gen}-{sid}":
                    child.add_class("selected-story")

    def _update_detail_panel(self) -> None:
        """右上面板联动——区分审批和 story。"""
        # 详情模式下跳过——轮询不应踢回概览
        if self._in_detail_mode:
            return
        if not self._selected_item_id:
            self._show_right_top_static("选择左面板的 story 查看详情")
            return

        if self._selected_item_id.startswith("approval:"):
            aid = self._selected_item_id.removeprefix("approval:")
            approval = self._approvals_by_id.get(aid)
            if approval:
                approval_type = getattr(approval, "approval_type", "")
                payload = getattr(approval, "payload", None)
                if not is_binary_approval(approval_type, payload):
                    self._render_exception_approval_context(approval)
                else:
                    self._render_approval_context(approval)
            else:
                self._show_right_top_static("选择左面板的 story 查看详情")
        else:
            sid = self._selected_item_id.removeprefix("story:")
            self._render_story_detail(sid)

    def _render_approval_context(self, approval: object) -> None:
        """渲染审批上下文详情到右上面板。"""
        aid = getattr(approval, "approval_id", "")
        approval_type = getattr(approval, "approval_type", "")
        story_id = getattr(approval, "story_id", "")
        payload_str = getattr(approval, "payload", None)
        recommended = getattr(approval, "recommended_action", "") or ""
        risk = getattr(approval, "risk_level", "") or ""
        icon = APPROVAL_TYPE_ICONS.get(approval_type, "?")
        risk_color = RICH_COLORS.get(map_risk_to_color(risk or None), RICH_COLORS["$muted"])

        # 展开态或折叠态显示不同详情
        detail = Text()
        detail.append(f"{icon} ", style=RICH_COLORS["$warning"])
        detail.append(f"{story_id}", style=f"bold {RICH_COLORS['$accent']}")
        detail.append(f" — {approval_type}\n", style=RICH_COLORS["$text"])

        if self._expanded_approval_id == aid:
            # 展开态 — 完整审批上下文
            summary = format_approval_summary(approval_type, payload_str)
            detail.append(f"\n{summary}\n\n", style=RICH_COLORS["$text"])

            # 解析 payload 详情
            pd: dict[str, object] = {}
            if payload_str:
                with contextlib.suppress(json.JSONDecodeError, TypeError):
                    pd = json.loads(payload_str)

            # 阶段转换（payload 或 story 当前阶段）
            if "from_phase" in pd or "to_phase" in pd:
                detail.append("阶段: ", style=RICH_COLORS["$muted"])
                detail.append(
                    f"{pd.get('from_phase', '?')} → {pd.get('to_phase', '?')}\n",
                    style=RICH_COLORS["$info"],
                )
            elif story_id in self._stories_by_id:
                phase = str(self._stories_by_id[story_id].get("current_phase", ""))
                if phase:
                    detail.append("当前阶段: ", style=RICH_COLORS["$muted"])
                    detail.append(f"{phase}\n", style=RICH_COLORS["$info"])

            # 成本（payload 优先，回退到 dashboard story 级数据）
            if "cost_usd" in pd:
                detail.append("成本: ", style=RICH_COLORS["$muted"])
                detail.append(f"${pd['cost_usd']:.2f}\n", style=RICH_COLORS["$text"])
            elif story_id in self._story_costs:
                cost = self._story_costs[story_id]
                detail.append("累计成本: ", style=RICH_COLORS["$muted"])
                detail.append(f"${cost:.2f}\n", style=RICH_COLORS["$text"])

            # 耗时（payload 优先，回退到 dashboard story 级数据）
            if "elapsed_seconds" in pd:
                detail.append("耗时: ", style=RICH_COLORS["$muted"])
                detail.append(
                    f"{_format_elapsed(int(str(pd['elapsed_seconds'])))}\n",
                    style=RICH_COLORS["$text"],
                )
            elif story_id in self._story_started_at:
                elapsed = self._compute_elapsed(story_id)
                detail.append("耗时: ", style=RICH_COLORS["$muted"])
                detail.append(f"{_format_elapsed(elapsed)}\n", style=RICH_COLORS["$text"])

            # CL 轮次（payload 优先，回退到 dashboard story 级数据）
            if "cl_round" in pd:
                detail.append("CL 轮次: ", style=RICH_COLORS["$muted"])
                detail.append(f"R{pd['cl_round']}\n", style=RICH_COLORS["$info"])
            elif story_id in self._story_cl_rounds:
                cl_round = self._story_cl_rounds[story_id]
                if cl_round > 0:
                    detail.append("CL 轮次: ", style=RICH_COLORS["$muted"])
                    detail.append(
                        f"R{cl_round}/{self._convergent_loop_max_rounds}\n",
                        style=RICH_COLORS["$info"],
                    )

            if "blocking_count" in pd:
                detail.append("Blocking: ", style=RICH_COLORS["$muted"])
                threshold = pd.get("threshold", "?")
                detail.append(
                    f"{pd['blocking_count']}/{threshold}\n",
                    style=RICH_COLORS["$error"],
                )

            # Review Findings 摘要（AC2 — QA 结果）
            fs = self._story_findings_summary.get(story_id)
            if fs:
                detail.append("\nReview Findings:\n", style=RICH_COLORS["$muted"])
                b_open = fs.get("blocking_open", 0)
                b_closed = fs.get("blocking_closed", 0)
                s_open = fs.get("suggestion_open", 0)
                s_closed = fs.get("suggestion_closed", 0)
                if b_closed or b_open:
                    icon = "✔" if b_open == 0 else "✖"
                    style = RICH_COLORS["$success"] if b_open == 0 else RICH_COLORS["$error"]
                    detail.append(f"  {icon} ", style=style)
                    detail.append(f"{b_closed + b_open} blocking", style=RICH_COLORS["$text"])
                    if b_open > 0:
                        detail.append(f" ({b_open} open)", style=RICH_COLORS["$error"])
                    else:
                        detail.append(" (closed)", style=RICH_COLORS["$success"])
                    detail.append("\n")
                if s_closed or s_open:
                    icon = "✔" if s_open == 0 else "!"
                    style = RICH_COLORS["$success"] if s_open == 0 else RICH_COLORS["$warning"]
                    detail.append(f"  {icon} ", style=style)
                    detail.append(f"{s_closed + s_open} suggestions", style=RICH_COLORS["$text"])
                    if s_open > 0:
                        detail.append(f" ({s_open} open)", style=RICH_COLORS["$warning"])
                    else:
                        detail.append(" (closed)", style=RICH_COLORS["$success"])
                    detail.append("\n")

            detail.append("\n推荐: ", style=RICH_COLORS["$muted"])
            detail.append(f"{recommended}", style=RICH_COLORS["$info"])
            detail.append(f" [{risk or '-'}风险]", style=risk_color)
        else:
            # 折叠态 — 简要信息
            summary = format_approval_summary(approval_type, payload_str)
            detail.append(f"\n{summary}\n", style=RICH_COLORS["$text"])
            detail.append("推荐: ", style=RICH_COLORS["$muted"])
            detail.append(f"{recommended}", style=RICH_COLORS["$info"])
            detail.append(f" [{risk or '-'}]", style=risk_color)

        self._show_right_top_static(detail)

    def _render_exception_approval_context(self, approval: object) -> None:
        """渲染异常审批多选面板内容到右上面板（Story 6.3b）。"""
        aid = getattr(approval, "approval_id", "")
        approval_type = getattr(approval, "approval_type", "")
        story_id = getattr(approval, "story_id", "")
        payload_str = getattr(approval, "payload", None)
        risk_level = getattr(approval, "risk_level", "") or ""

        payload_for_panel = self._build_exception_panel_payload(
            approval_type=approval_type,
            story_id=story_id,
            payload_str=payload_str,
        )

        self._show_right_top_exception(
            approval_id=aid,
            story_id=story_id,
            approval_type=approval_type,
            risk_level=risk_level,
            payload=payload_for_panel,
            expanded_context=self._expanded_approval_id == aid,
        )

    def _render_story_detail(self, story_id: str) -> None:
        """渲染 story 概览到右上面板（第 1 层）。"""
        story = self._stories_by_id.get(story_id)
        if not story:
            self._show_right_top_static("选择左面板的 story 查看详情")
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

        # Task 1.1: Findings 摘要
        fs = self._story_findings_summary.get(sid, {})
        b_open = fs.get("blocking_open", 0)
        b_closed = fs.get("blocking_closed", 0)
        s_open = fs.get("suggestion_open", 0)
        s_closed = fs.get("suggestion_closed", 0)
        total_findings = b_open + b_closed + s_open + s_closed
        if total_findings > 0:
            detail.append("Findings: ", style=RICH_COLORS["$muted"])
            if b_open > 0:
                detail.append(f"{b_open}B↑", style=RICH_COLORS["$error"])
            if b_closed > 0:
                detail.append(f" {b_closed}B↓", style=RICH_COLORS["$success"])
            if s_open > 0:
                detail.append(f" {s_open}S↑", style=RICH_COLORS["$warning"])
            if s_closed > 0:
                detail.append(f" {s_closed}S↓", style=RICH_COLORS["$success"])
            detail.append("\n")

        # Task 1.2: 执行记录轻量摘要
        task_count = self._story_task_counts.get(sid, 0)
        if task_count > 0:
            detail.append("执行记录: ", style=RICH_COLORS["$muted"])
            detail.append(f"{task_count} tasks\n", style=RICH_COLORS["$info"])

        # Task 1.3: CL 收敛信息
        if cl_round > 0:
            detail.append("CL: ", style=RICH_COLORS["$muted"])
            detail.append(
                f"R{cl_round}/{self._convergent_loop_max_rounds}",
                style=RICH_COLORS["$info"],
            )
            if total_findings > 0:
                closed = b_closed + s_closed
                rate = closed / total_findings * 100
                detail.append(f"  收敛率 {rate:.0f}%", style=RICH_COLORS["$info"])
            detail.append("\n")

        detail.append("\n[Enter] 详情", style=RICH_COLORS["$muted"])

        self._show_right_top_static(detail)

    def _update_action_panel(self) -> None:
        """右下面板——审批操作提示或通用提示。"""
        if not self._selected_item_id:
            self._update_static(
                "#right-bottom-content",
                Text("↑↓ 选择  y/n 审批  d 详情  q 退出", style=RICH_COLORS["$muted"]),
            )
            return

        if self._selected_item_id.startswith("approval:"):
            aid = self._selected_item_id.removeprefix("approval:")
            approval = self._approvals_by_id.get(aid)

            # 已提交中间状态
            if aid in self._submitted_approvals:
                self._update_static(
                    "#right-bottom-content",
                    Text("已提交，等待处理", style=RICH_COLORS["$muted"]),
                )
                return

            if approval is None:
                self._update_static("#right-bottom-content", "")
                return

            approval_type = getattr(approval, "approval_type", "")
            payload = getattr(approval, "payload", None)

            if not is_binary_approval(approval_type, payload):
                # 多选异常审批 — 数字键动作提示 (Story 6.3b)
                options = get_options_for_approval(approval_type, payload)
                action_text = Text()
                for i, opt in enumerate(options, start=1):
                    action_text.append(f"[{i}] ", style=f"bold {RICH_COLORS['$info']}")
                    action_text.append(f"{opt}  ", style=RICH_COLORS["$text"])
                action_text.append("[d] ", style=RICH_COLORS["$muted"])
                if self._expanded_approval_id == aid:
                    action_text.append("收起上下文", style=RICH_COLORS["$muted"])
                else:
                    action_text.append("更多上下文", style=RICH_COLORS["$muted"])
                self._update_static("#right-bottom-content", action_text)
                return

            # 二选一审批——显示动作标签 + 快捷键
            labels = get_binary_approval_labels(approval_type)
            if labels:
                y_label, n_label = labels
            else:
                y_label, n_label = "批准", "拒绝"

            action_text = Text()
            action_text.append(f"[y] {y_label}  ", style=RICH_COLORS["$success"])
            action_text.append(f"[n] {n_label}  ", style=RICH_COLORS["$error"])
            if self._expanded_approval_id == aid:
                action_text.append("[d] 收起详情", style=RICH_COLORS["$muted"])
            else:
                action_text.append("[d] 详情", style=RICH_COLORS["$muted"])
            self._update_static("#right-bottom-content", action_text)
        else:
            # 选中 story 时——通用提示 + Enter 钻入
            self._update_static(
                "#right-bottom-content",
                Text("↑↓ 选择  [Enter] 详情  q 退出", style=RICH_COLORS["$muted"]),
            )

    def _update_approvals_tab(self) -> None:
        """Tab 模式 [1]审批 Tab 渲染。"""
        try:
            container = self.query_one("#tab-approvals-container", VerticalScroll)
        except Exception:
            return

        # 异常审批排在常规审批之前 (AC4)
        sorted_tab_approvals = self._sort_approvals(self._approval_records)
        approval_snapshot = [
            (
                getattr(a, "approval_id", ""),
                getattr(a, "approval_type", ""),
                getattr(a, "story_id", ""),
                "binary"
                if is_binary_approval(getattr(a, "approval_type", ""), getattr(a, "payload", None))
                else "exception",
            )
            for a in sorted_tab_approvals
        ]

        if not self._approval_records:
            self._rendered_approvals_snapshot_tab = []
            container.remove_children()
            self._rebuild_gen_approvals_tab += 1
            gen = self._rebuild_gen_approvals_tab
            container.mount(Static("✔ 无待处理审批", id=f"atab-empty-{gen}", classes="empty-state"))
            return

        needs_rebuild = approval_snapshot != self._rendered_approvals_snapshot_tab
        if needs_rebuild:
            container.remove_children()
            self._rebuild_gen_approvals_tab += 1
            gen = self._rebuild_gen_approvals_tab

            for a_record in sorted_tab_approvals:
                aid = getattr(a_record, "approval_id", "")
                approval_type = getattr(a_record, "approval_type", "")
                payload = getattr(a_record, "payload", None)

                ac_w = ApprovalCard(id=f"atab{gen}-{aid}", classes="approval-row")
                container.mount(ac_w)
                ac_w.update_data(
                    approval_id=aid,
                    story_id=getattr(a_record, "story_id", ""),
                    approval_type=approval_type,
                    payload=payload,
                    recommended_action=getattr(a_record, "recommended_action", None),
                    risk_level=getattr(a_record, "risk_level", None),
                )

            self._rendered_approvals_snapshot_tab = approval_snapshot
            return

        gen = self._rebuild_gen_approvals_tab
        for a_record in sorted_tab_approvals:
            aid = getattr(a_record, "approval_id", "")
            try:
                ac = container.query_one(f"#atab{gen}-{aid}", ApprovalCard)
                ac.update_data(
                    approval_id=aid,
                    story_id=getattr(a_record, "story_id", ""),
                    approval_type=getattr(a_record, "approval_type", ""),
                    payload=getattr(a_record, "payload", None),
                    recommended_action=getattr(a_record, "recommended_action", None),
                    risk_level=getattr(a_record, "risk_level", None),
                )
            except Exception:
                pass

    def _build_exception_panel_payload(
        self,
        *,
        approval_type: str,
        story_id: str,
        payload_str: str | None,
    ) -> str | None:
        """为异常审批面板补齐 dashboard 已知的 story 级上下文。"""
        payload_dict: dict[str, object] = {}
        if payload_str:
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                payload_dict = json.loads(payload_str)

        if approval_type == "rebase_conflict":
            story = self._stories_by_id.get(story_id, {})
            worktree_path = story.get("worktree_path")
            if worktree_path and "worktree_path" not in payload_dict:
                payload_dict = {
                    **payload_dict,
                    "worktree_path": str(worktree_path),
                }

        if payload_dict:
            return json.dumps(payload_dict, ensure_ascii=False)
        return payload_str

    def _sort_approvals(self, approvals: list[object]) -> list[object]:
        """将异常审批排在常规审批之前 (AC4)。

        异常审批组和常规审批组内部保持原始顺序，避免刷新时列表跳动。
        """
        exception_approvals: list[object] = []
        binary_approvals: list[object] = []
        for a in approvals:
            atype = getattr(a, "approval_type", "")
            payload = getattr(a, "payload", None)
            if is_binary_approval(atype, payload):
                binary_approvals.append(a)
            else:
                exception_approvals.append(a)
        return exception_approvals + binary_approvals

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

    def _show_right_top_static(self, text: str | Text) -> None:
        """切换右上区域到 Static 详情视图。"""
        with contextlib.suppress(Exception):
            switcher = self.query_one("#right-top-switcher", ContentSwitcher)
            switcher.current = "right-top-content"
        self._update_static("#right-top-content", text)

    def _show_right_top_exception(
        self,
        *,
        approval_id: str,
        story_id: str,
        approval_type: str,
        risk_level: str,
        payload: str | None,
        expanded_context: bool,
    ) -> None:
        """切换右上区域到真实 ExceptionApprovalPanel。"""
        try:
            panel = self.query_one("#right-top-exception", ExceptionApprovalPanel)
            panel.update_data(
                approval_id=approval_id,
                story_id=story_id,
                approval_type=approval_type,
                risk_level=risk_level,
                payload=payload,
                expanded_context=expanded_context,
            )
            switcher = self.query_one("#right-top-switcher", ContentSwitcher)
            switcher.current = "right-top-exception"
        except Exception:
            pass
