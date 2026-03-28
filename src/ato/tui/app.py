"""app — TUI 应用入口。"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import structlog
from textual import events
from textual.app import App, ComposeResult
from textual.binding import BindingType
from textual.css.query import NoMatches
from textual.reactive import reactive
from textual.widgets import Footer, Header

from ato.tui.dashboard import DashboardScreen
from ato.tui.widgets.three_question_header import ThreeQuestionHeader

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


class ATOApp(App[None]):
    """ATO TUI 主应用。

    作为独立前台进程运行，通过 SQLite 与后台 Orchestrator 通信。
    不在 ``__init__`` 中读 SQLite——数据加载在 ``on_mount()``。
    """

    CSS_PATH = "app.tcss"
    TITLE = "Agent Team Orchestrator"
    BINDINGS: ClassVar[list[BindingType]] = [
        ("q", "quit", "退出"),
        ("1", "switch_tab(1)", "Tab 1"),
        ("2", "switch_tab(2)", "Tab 2"),
        ("3", "switch_tab(3)", "Tab 3"),
        ("4", "switch_tab(4)", "Tab 4"),
        ("5", "switch_tab(5)", ""),
        ("6", "switch_tab(6)", ""),
        ("7", "switch_tab(7)", ""),
        ("8", "switch_tab(8)", ""),
        ("9", "switch_tab(9)", ""),
    ]

    # Reactive 属性驱动 UI 更新
    story_count: reactive[int] = reactive(0)
    running_count: reactive[int] = reactive(0)
    error_count: reactive[int] = reactive(0)
    pending_approvals: reactive[int] = reactive(0)
    today_cost_usd: reactive[float] = reactive(0.0)
    last_updated: reactive[str] = reactive("")

    # 响应式布局模式 (AC3)
    layout_mode: reactive[str] = reactive("three-panel")

    def __init__(
        self,
        *,
        db_path: Path,
        orchestrator_pid: int | None = None,
        convergent_loop_max_rounds: int = 3,
    ) -> None:
        super().__init__()
        self._db_path = db_path
        self._orchestrator_pid = orchestrator_pid
        self._convergent_loop_max_rounds = convergent_loop_max_rounds
        self._last_refresh_time: float = time.monotonic()
        # Story 列表数据快照（_load_data 填充）
        self._stories: list[dict[str, object]] = []
        self._story_costs: dict[str, float] = {}
        self._story_started_at: dict[str, str] = {}
        self._story_cl_rounds: dict[str, int] = {}
        # Pending approval 完整记录（Story 6.3a）
        self._pending_approval_records: list[object] = []
        # Story 级 findings 摘要（Story 6.3a AC2）
        self._story_findings_summary: dict[str, dict[str, int]] = {}
        # 成本 Tab 数据（Story 6.5）
        self._story_call_counts: dict[str, int] = {}
        self._total_cost_usd: float = 0.0
        # 日志 Tab 数据（Story 6.5）
        self._recent_events: list[dict[str, str]] = []
        # Story 级 task 计数（Story 6.4 Task 1.2 — 轻量轮询）
        self._story_task_counts: dict[str, int] = {}

    def compose(self) -> ComposeResult:
        """骨架布局：Header + ThreeQuestionHeader + DashboardScreen + Footer。"""
        yield Header()
        yield ThreeQuestionHeader()
        yield DashboardScreen()
        yield Footer()

    def on_resize(self, event: events.Resize) -> None:
        """根据终端宽度切换布局模式 (AC3) 和 header display_mode。"""
        width = event.size.width
        if width >= 140:
            self.layout_mode = "three-panel"
        elif width >= 100:
            self.layout_mode = "tabbed"
        else:
            self.layout_mode = "degraded"
        # 宽度传给 DashboardScreen 以调整面板比例
        self._apply_layout(width)
        # ThreeQuestionHeader display_mode 使用独立断点
        self._apply_header_mode(width)

    def watch_layout_mode(self, new_mode: str) -> None:
        """将布局模式变化转发给 DashboardScreen。"""
        try:
            dashboard = self.query_one(DashboardScreen)
        except NoMatches:
            return
        dashboard.set_layout_mode(new_mode)

    def _apply_layout(self, width: int) -> None:
        """将终端宽度传给 DashboardScreen 以调整面板比例。"""
        try:
            dashboard = self.query_one(DashboardScreen)
        except NoMatches:
            return
        dashboard.adjust_panel_ratio(width)

    def _apply_header_mode(self, width: int) -> None:
        """根据终端宽度设置 ThreeQuestionHeader 的 display_mode。

        独立断点（不复用 layout_mode）：
        180+ → full, 140-179 → compact, <140 → minimal
        """
        if width >= 180:
            mode = "full"
        elif width >= 140:
            mode = "compact"
        else:
            mode = "minimal"
        try:
            header = self.query_one(ThreeQuestionHeader)
        except NoMatches:
            return
        header.set_display_mode(mode)

    def action_switch_tab(self, tab_number: int) -> None:
        """数字键切换 Tab（tabbed 模式）或异常审批选择（three-panel 模式）。

        搜索面板激活时短路，避免输入数字触发切页或审批。
        """
        try:
            dashboard = self.query_one(DashboardScreen)
            if dashboard._search_active:
                return
        except NoMatches:
            pass
        if self.layout_mode == "three-panel":
            # 三面板模式下委托给 DashboardScreen 处理异常审批数字键
            try:
                dashboard = self.query_one(DashboardScreen)
                dashboard._handle_option_key(tab_number - 1)
            except NoMatches:
                pass
            return
        if self.layout_mode != "tabbed":
            return
        try:
            from textual.widgets import TabbedContent

            dashboard = self.query_one(DashboardScreen)
            tabbed = dashboard.query_one(TabbedContent)
            tabs = list(tabbed.query("TabPane"))
            if 1 <= tab_number <= len(tabs):
                tabbed.active = tabs[tab_number - 1].id or ""
        except NoMatches:
            pass

    async def on_mount(self) -> None:
        """首次数据加载 + 启动 2 秒定时轮询。"""
        await self._load_data()
        self.set_interval(2.0, self.refresh_data)

    async def _load_data(self) -> None:
        """从 SQLite 加载仪表盘数据（短生命周期连接）。

        每次独立打开/关闭连接，不复用，最小化写锁持有时间。
        使用 ``get_connection()`` 确保 WAL + busy_timeout=5000 + foreign_keys=ON。
        """
        from ato.models.db import get_connection

        # seconds_ago: 先基于上次成功刷新时间计算差值
        now_mono = time.monotonic()
        seconds_ago = int(now_mono - self._last_refresh_time)

        db = await get_connection(self._db_path)
        try:
            # 1. Story 状态统计（分组查询获取 running/error 计数）
            cursor = await db.execute("SELECT status, COUNT(*) as cnt FROM stories GROUP BY status")
            rows = await cursor.fetchall()
            total = 0
            running = 0
            error = 0
            for row in rows:
                status_val = row[0]
                cnt = int(row[1])
                total += cnt
                if status_val == "in_progress":
                    running += cnt
                elif status_val == "blocked":
                    error += cnt
            self.story_count = total
            self.running_count = running
            self.error_count = error

            # 2. Pending approvals 计数
            cursor = await db.execute("SELECT COUNT(*) FROM approvals WHERE status = 'pending'")
            approval_row = await cursor.fetchone()
            self.pending_approvals = int(approval_row[0]) if approval_row else 0

            # 3. 今日成本汇总
            cursor = await db.execute(
                "SELECT COALESCE(SUM(cost_usd), 0.0) FROM cost_log "
                "WHERE date(created_at) = date('now')"
            )
            cost_row = await cursor.fetchone()
            self.today_cost_usd = float(cost_row[0]) if cost_row else 0.0

            # 4. 最后更新时间
            self.last_updated = datetime.now(tz=UTC).strftime("%H:%M:%S")

            # 5. Story 列表完整记录（Story 6.2b）
            cursor = await db.execute(
                "SELECT story_id, title, status, current_phase, worktree_path, "
                "created_at, updated_at FROM stories ORDER BY updated_at DESC"
            )
            story_rows = await cursor.fetchall()
            self._stories = [dict(row) for row in story_rows]

            # 6. 每个 story 的累计成本 + 调用次数（复用 get_cost_by_story）
            from ato.models.db import get_cost_by_story

            cost_data = await get_cost_by_story(db)
            self._story_costs = {
                str(r["story_id"]): float(str(r["total_cost_usd"])) for r in cost_data
            }
            self._story_call_counts = {
                str(r["story_id"]): int(str(r["call_count"])) for r in cost_data
            }

            # 6b. 累计总成本
            cursor = await db.execute("SELECT COALESCE(SUM(cost_usd), 0.0) FROM cost_log")
            total_row = await cursor.fetchone()
            self._total_cost_usd = float(total_row[0]) if total_row else 0.0

            # 7. 与 story.current_phase 对齐的最新 running task started_at
            cursor = await db.execute(
                "SELECT t.story_id, MAX(t.started_at) AS started_at "
                "FROM tasks t "
                "JOIN stories s ON s.story_id = t.story_id "
                "WHERE t.status = 'running' "
                "AND t.phase = s.current_phase "
                "AND t.started_at IS NOT NULL "
                "GROUP BY t.story_id"
            )
            self._story_started_at = {str(row[0]): str(row[1]) for row in await cursor.fetchall()}

            # 8. CL 当前轮次
            cursor = await db.execute(
                "SELECT story_id, MAX(round_num) as current_round FROM findings GROUP BY story_id"
            )
            self._story_cl_rounds = {str(row[0]): int(row[1]) for row in await cursor.fetchall()}

            # 9. Pending approval 完整记录（Story 6.3a）
            from ato.models.db import get_pending_approvals, get_story_findings_summary

            self._pending_approval_records = await get_pending_approvals(db)  # type: ignore[assignment]

            # 10. Story 级 findings 摘要（Story 6.3a AC2 — QA 结果）
            self._story_findings_summary = await get_story_findings_summary(db)

            # 11. 最近事件（tasks + approvals 合并，日志 Tab 用）
            cursor = await db.execute(
                "SELECT COALESCE(completed_at, started_at) as event_time, "
                "'task' as event_type, story_id, "
                "COALESCE(phase, '') || ' → ' || status as summary "
                "FROM tasks WHERE started_at IS NOT NULL "
                "ORDER BY event_time DESC LIMIT 50"
            )
            task_events = [
                {
                    "event_time": str(r[0] or ""),
                    "event_type": str(r[1]),
                    "story_id": str(r[2] or ""),
                    "summary": str(r[3] or ""),
                }
                for r in await cursor.fetchall()
            ]
            cursor = await db.execute(
                "SELECT COALESCE(decided_at, created_at) as event_time, "
                "'approval' as event_type, story_id, "
                "approval_type || ' ' || status as summary "
                "FROM approvals "
                "ORDER BY event_time DESC LIMIT 50"
            )
            approval_events = [
                {
                    "event_time": str(r[0] or ""),
                    "event_type": str(r[1]),
                    "story_id": str(r[2] or ""),
                    "summary": str(r[3] or ""),
                }
                for r in await cursor.fetchall()
            ]
            all_events = task_events + approval_events
            all_events.sort(key=lambda e: e["event_time"], reverse=True)
            self._recent_events = all_events[:50]

            # 12. Story 级 task 计数（Story 6.4 Task 1.2 — 轻量轮询）
            cursor = await db.execute(
                "SELECT story_id, COUNT(*) as cnt FROM tasks GROUP BY story_id"
            )
            self._story_task_counts = {str(row[0]): int(row[1]) for row in await cursor.fetchall()}
        finally:
            await db.close()

        # 仅在成功加载后才更新基准时间，失败时保持旧值
        # 这样恢复后 seconds_ago 反映的是"距上次成功加载"的真实间隔
        self._last_refresh_time = now_mono

        self._update_dashboard(seconds_ago=seconds_ago)

    def _update_dashboard(self, *, seconds_ago: int = 0) -> None:
        """根据当前数据更新 DashboardScreen 和 ThreeQuestionHeader 显示。"""
        try:
            dashboard = self.query_one(DashboardScreen)
            dashboard.update_content(
                story_count=self.story_count,
                pending_approvals=self.pending_approvals,
                today_cost_usd=self.today_cost_usd,
                last_updated=self.last_updated,
                stories=self._stories,
                story_costs=self._story_costs,
                story_started_at=self._story_started_at,
                story_cl_rounds=self._story_cl_rounds,
                convergent_loop_max_rounds=self._convergent_loop_max_rounds,
                pending_approval_records=self._pending_approval_records,
                story_findings_summary=self._story_findings_summary,
                story_call_counts=self._story_call_counts,
                total_cost_usd=self._total_cost_usd,
                recent_events=self._recent_events,
                story_task_counts=self._story_task_counts,
            )
        except NoMatches:
            pass  # DashboardScreen 尚未挂载

        try:
            header = self.query_one(ThreeQuestionHeader)
            header.update_data(
                running_count=self.running_count,
                error_count=self.error_count,
                pending_approvals=self.pending_approvals,
                today_cost_usd=self.today_cost_usd,
                seconds_ago=seconds_ago,
            )
        except NoMatches:
            pass  # ThreeQuestionHeader 尚未挂载

    async def load_story_detail(self, story_id: str) -> dict[str, object]:
        """按需加载 Story 详情数据（不在 2s 轮询中执行）。

        返回 tasks、cost_logs、findings 明细和 findings_summary。
        使用短生命周期连接。
        """
        from ato.models.db import (
            get_connection,
            get_cost_logs_by_story,
            get_findings_by_story,
            get_story_findings_summary,
            get_tasks_by_story,
        )

        db = await get_connection(self._db_path)
        try:
            tasks = await get_tasks_by_story(db, story_id)
            cost_logs = await get_cost_logs_by_story(db, story_id)
            findings = await get_findings_by_story(db, story_id)
            all_summaries = await get_story_findings_summary(db)
            findings_summary = all_summaries.get(story_id, {})
        finally:
            await db.close()

        return {
            "tasks": tasks,
            "cost_logs": cost_logs,
            "findings": findings,
            "findings_summary": findings_summary,
        }

    async def refresh_data(self) -> None:
        """定时轮询回调——重新加载 SQLite 数据。

        每次使用短生命周期连接（打开 → 查询 → 关闭），
        不复用连接，最小化写锁持有时间。
        """
        try:
            await self._load_data()
        except Exception:
            logger.warning("refresh_data_failed", exc_info=True)

    def _resolve_orchestrator_pid(self) -> int | None:
        """每次写入时重新读取 PID 文件，应对 Orchestrator 重启或后启动。

        不导入 ``ato.core`` —— tui 模块通过 SQLite 与 core 解耦，
        PID 文件读取逻辑足够简单可内联实现。
        """
        import os

        pid_path = self._db_path.parent / "orchestrator.pid"
        if not pid_path.exists():
            return None
        try:
            pid = int(pid_path.read_text().strip())
        except (ValueError, OSError):
            return None
        try:
            os.kill(pid, 0)
            return pid
        except ProcessLookupError:
            return None
        except PermissionError:
            return pid  # 进程存在但无权发信号

    async def write_approval(
        self,
        *,
        approval_id: str,
        story_id: str,
        approval_type: str,
        decision: str,
    ) -> bool:
        """审批写入占位方法。

        写入 SQLite + 立即 commit + send nudge。
        TUI 只走 SQLite + nudge，不跨进程调用 TransitionQueue。

        仅更新 ``status='pending'`` 的记录，防止覆盖已被别处处理的审批。

        Returns:
            True 写入成功，False 审批已被处理（非 pending）。
        """
        from ato.models.db import get_connection
        from ato.nudge import send_external_nudge

        db = await get_connection(self._db_path)
        try:
            now_iso = datetime.now(tz=UTC).isoformat()
            cursor = await db.execute(
                "UPDATE approvals SET status = ?, decision = ?, decided_at = ? "
                "WHERE approval_id = ? AND status = 'pending'",
                (decision, decision, now_iso, approval_id),
            )
            await db.commit()
            if cursor.rowcount == 0:
                logger.warning(
                    "write_approval_skipped_not_pending",
                    approval_id=approval_id,
                )
                return False
        finally:
            await db.close()

        # Nudge best-effort — 每次重新读取 PID，应对 Orchestrator 重启
        current_pid = self._resolve_orchestrator_pid()
        if current_pid is not None:
            try:
                send_external_nudge(current_pid)
            except ProcessLookupError:
                logger.warning(
                    "nudge_skipped_process_not_found",
                    orchestrator_pid=current_pid,
                )
            except PermissionError:
                logger.warning(
                    "nudge_skipped_permission_error",
                    orchestrator_pid=current_pid,
                )
        return True

    async def submit_approval_decision(
        self,
        *,
        approval_id: str,
        status: str,
        decision: str,
        decision_reason: str | None = None,
    ) -> bool:
        """提交审批决策（分离 status/decision/decision_reason）。

        使用 ``update_approval_decision()`` 写入正确的 status 和 decision，
        与 CLI ``ato approve`` 语义对齐。

        Returns:
            True 写入成功，False 审批已被处理。
        """
        from ato.models.db import get_connection, update_approval_decision
        from ato.nudge import send_external_nudge

        db = await get_connection(self._db_path)
        try:
            now = datetime.now(tz=UTC)
            try:
                await update_approval_decision(
                    db,
                    approval_id,
                    status=status,
                    decision=decision,
                    decision_reason=decision_reason,
                    decided_at=now,
                )
            except ValueError:
                logger.warning(
                    "submit_approval_not_found",
                    approval_id=approval_id,
                )
                return False
            await db.commit()
        finally:
            await db.close()

        # Nudge best-effort
        current_pid = self._resolve_orchestrator_pid()
        if current_pid is not None:
            try:
                send_external_nudge(current_pid)
            except ProcessLookupError:
                logger.warning(
                    "nudge_skipped_process_not_found",
                    orchestrator_pid=current_pid,
                )
            except PermissionError:
                logger.warning(
                    "nudge_skipped_permission_error",
                    orchestrator_pid=current_pid,
                )
        return True
