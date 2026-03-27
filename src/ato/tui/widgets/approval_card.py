"""approval_card — 审批卡片 Widget。

折叠态单行渲染：类型图标 + story ID + 一句话摘要 + 推荐操作 + 风险指示。
数据由 DashboardScreen 通过 update_data() 推送，不自行创建 SQLite 连接。
"""

from __future__ import annotations

from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget

from ato.approval_helpers import format_approval_summary, is_binary_approval
from ato.models.schemas import APPROVAL_TYPE_ICONS
from ato.tui.theme import RICH_COLORS, map_risk_to_color


class ApprovalCard(Widget):
    """审批卡片：折叠态单行渲染。

    格式：{类型图标} {story_id}  {摘要}  [{推荐}] [{风险}]

    数据由 ``update_data()`` 批量推送，不自行创建 SQLite 连接。
    """

    # Reactive 属性驱动 UI 更新
    approval_id: reactive[str] = reactive("")
    story_id: reactive[str] = reactive("")
    approval_type: reactive[str] = reactive("")
    summary: reactive[str] = reactive("")
    recommended_action: reactive[str] = reactive("")
    risk_level: reactive[str] = reactive("")

    def update_data(
        self,
        *,
        approval_id: str,
        story_id: str,
        approval_type: str,
        payload: str | None = None,
        recommended_action: str | None = None,
        risk_level: str | None = None,
    ) -> None:
        """批量更新 reactive 属性。"""
        self.approval_id = approval_id
        self.story_id = story_id
        self.approval_type = approval_type
        self.summary = format_approval_summary(approval_type, payload)
        self.recommended_action = recommended_action or ""
        self.risk_level = risk_level or ""

        # 异常审批样式增强 (Story 6.3b AC4)
        is_exception = not is_binary_approval(approval_type, payload)
        self.remove_class("approval-exception-row")
        self.remove_class("approval-exception-high")
        if is_exception:
            self.add_class("approval-exception-row")
            if risk_level == "high":
                self.add_class("approval-exception-high")

    def render(self) -> Text:
        """折叠态单行渲染。"""
        icon = APPROVAL_TYPE_ICONS.get(self.approval_type, "?")
        risk_color_var = map_risk_to_color(self.risk_level or None)
        risk_color = RICH_COLORS.get(risk_color_var, RICH_COLORS["$muted"])

        # 异常审批使用 $error 色图标 (AC4)
        is_exception = self.has_class("approval-exception-row")
        icon_color = RICH_COLORS["$error"] if is_exception else RICH_COLORS["$warning"]

        result = Text()
        # 类型图标
        result.append(f"{icon} ", style=icon_color)
        # story ID
        result.append(f"{self.story_id}  ", style=RICH_COLORS["$accent"])
        # 摘要
        result.append(f"{self.summary}  ", style=RICH_COLORS["$text"])
        # 推荐操作
        if self.recommended_action:
            result.append(f"[{self.recommended_action}]", style=RICH_COLORS["$muted"])
            result.append(" ", style="")
        # 风险指示
        risk_label = self.risk_level if self.risk_level else "-"
        result.append(f"[{risk_label}]", style=risk_color)

        return result
