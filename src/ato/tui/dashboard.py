"""dashboard — 仪表盘视图。"""

from __future__ import annotations

from textual.widgets import Static


class DashboardScreen(Static):  # type: ignore[misc]
    """TUI 仪表盘占位组件。

    当前显示 stories 计数、approvals 计数、今日成本的文本摘要。
    后续 Story 6.2b 将替换为完整的三面板布局。
    """

    def __init__(self) -> None:
        super().__init__("加载中...")

    def update_content(
        self,
        *,
        story_count: int,
        pending_approvals: int,
        today_cost_usd: float,
        last_updated: str,
    ) -> None:
        """更新仪表盘显示内容。"""
        self.update(
            f"Stories: {story_count} | "
            f"待审批: {pending_approvals} | "
            f"今日成本: ${today_cost_usd:.2f} | "
            f"更新: {last_updated}"
        )
