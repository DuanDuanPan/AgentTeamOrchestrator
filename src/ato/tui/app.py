"""app — TUI 应用入口。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import structlog
from textual.app import App, ComposeResult
from textual.binding import BindingType
from textual.css.query import NoMatches
from textual.reactive import reactive
from textual.widgets import Footer, Header

from ato.tui.dashboard import DashboardScreen

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


class ATOApp(App[None]):  # type: ignore[misc]
    """ATO TUI 主应用。

    作为独立前台进程运行，通过 SQLite 与后台 Orchestrator 通信。
    不在 ``__init__`` 中读 SQLite——数据加载在 ``on_mount()``。
    """

    CSS_PATH = "app.tcss"
    TITLE = "Agent Team Orchestrator"
    BINDINGS: ClassVar[list[BindingType]] = [("q", "quit", "退出")]

    # Reactive 属性驱动 UI 更新
    story_count: reactive[int] = reactive(0)
    pending_approvals: reactive[int] = reactive(0)
    today_cost_usd: reactive[float] = reactive(0.0)
    last_updated: reactive[str] = reactive("")

    def __init__(self, *, db_path: Path, orchestrator_pid: int | None = None) -> None:
        super().__init__()
        self._db_path = db_path
        self._orchestrator_pid = orchestrator_pid

    def compose(self) -> ComposeResult:
        """最小布局：Header + DashboardScreen + Footer。"""
        yield Header()
        yield DashboardScreen()
        yield Footer()

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

        db = await get_connection(self._db_path)
        try:
            # 1. Story 状态统计
            cursor = await db.execute("SELECT COUNT(*) FROM stories")
            row = await cursor.fetchone()
            self.story_count = int(row[0]) if row else 0

            # 2. Pending approvals 计数
            cursor = await db.execute("SELECT COUNT(*) FROM approvals WHERE status = 'pending'")
            row = await cursor.fetchone()
            self.pending_approvals = int(row[0]) if row else 0

            # 3. 今日成本汇总
            cursor = await db.execute(
                "SELECT COALESCE(SUM(cost_usd), 0.0) FROM cost_log "
                "WHERE date(created_at) = date('now')"
            )
            row = await cursor.fetchone()
            self.today_cost_usd = float(row[0]) if row else 0.0

            # 4. 最后更新时间
            self.last_updated = datetime.now(tz=UTC).strftime("%H:%M:%S")
        finally:
            await db.close()

        self._update_dashboard()

    def _update_dashboard(self) -> None:
        """根据当前数据更新 DashboardScreen 显示。"""
        try:
            dashboard = self.query_one(DashboardScreen)
            dashboard.update_content(
                story_count=self.story_count,
                pending_approvals=self.pending_approvals,
                today_cost_usd=self.today_cost_usd,
                last_updated=self.last_updated,
            )
        except NoMatches:
            pass  # DashboardScreen 尚未挂载

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
