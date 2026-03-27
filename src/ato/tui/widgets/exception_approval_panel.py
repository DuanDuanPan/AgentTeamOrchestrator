"""exception_approval_panel — 异常审批多选面板 Widget。

多行块渲染：类型图标 + 异常描述 + 影响范围 + 数字键选项列表。
数据由 DashboardScreen 通过 update_data() 推送，不自行创建 SQLite 连接。

Story 6.3b: 异常审批与多选交互。
"""

from __future__ import annotations

import contextlib
import json

from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget

from ato.approval_helpers import (
    format_option_labels,
    get_exception_context,
    get_exception_type_title,
    get_options_for_approval,
)
from ato.models.schemas import APPROVAL_TYPE_ICONS
from ato.tui.theme import RICH_COLORS, map_risk_to_color


class ExceptionApprovalPanel(Widget):
    """异常审批多选面板：多行块渲染。

    三要素：发生了什么 + 影响范围 + 你的选项。
    数据由 ``update_data()`` 批量推送，不自行创建 SQLite 连接。
    """

    # Reactive 属性驱动 UI 更新
    approval_id: reactive[str] = reactive("")
    story_id: reactive[str] = reactive("")
    approval_type: reactive[str] = reactive("")
    risk_level: reactive[str] = reactive("")
    expanded_context: reactive[bool] = reactive(False)

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._payload_dict: dict[str, object] = {}
        self._options: list[str] = []

    def update_data(
        self,
        *,
        approval_id: str,
        story_id: str,
        approval_type: str,
        risk_level: str | None = None,
        payload: str | None = None,
        options: list[str] | None = None,
        expanded_context: bool = False,
    ) -> None:
        """批量更新属性。"""
        self.approval_id = approval_id
        self.story_id = story_id
        self.approval_type = approval_type
        self.risk_level = risk_level or ""
        self.expanded_context = expanded_context

        # 解析 payload
        self._payload_dict = {}
        if payload:
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                self._payload_dict = json.loads(payload)

        # 选项列表
        if options is not None:
            self._options = options
        else:
            self._options = get_options_for_approval(approval_type, payload)

        # 根据 risk_level 设置 CSS class
        self.remove_class("exception-approval-high")
        self.remove_class("exception-approval-medium")
        if self.risk_level == "high":
            self.add_class("exception-approval-high")
        elif self.risk_level == "medium":
            self.add_class("exception-approval-medium")

    def render(self) -> Text:
        """多行块渲染。"""
        result = Text()
        icon = APPROVAL_TYPE_ICONS.get(self.approval_type, "?")
        title = get_exception_type_title(self.approval_type)
        risk_color = RICH_COLORS.get(
            map_risk_to_color(self.risk_level or None),
            RICH_COLORS["$muted"],
        )

        # 标题行：类型图标 + 异常类型描述 + story_id
        result.append(f"{icon} {title}", style=f"bold {RICH_COLORS['$error']}")
        result.append(f" [{self.risk_level or '-'}]", style=risk_color)
        result.append(f"  {self.story_id}\n", style=RICH_COLORS["$accent"])

        # 三要素
        what, impact = self._format_context(self.approval_type, self._payload_dict)

        # 发生了什么
        result.append("\n", style="")
        result.append(what, style=RICH_COLORS["$text"])
        result.append("\n", style="")

        # 影响范围
        if impact:
            result.append("\n", style="")
            result.append(impact, style=RICH_COLORS["$muted"])
            result.append("\n", style="")

        # 选项列表
        result.append("\n", style="")
        option_lines = self._format_options(self._options)
        for line in option_lines:
            result.append_text(line)
            result.append("\n", style="")

        # 底部提示
        result.append("\n", style="")
        digits = "/".join(str(i + 1) for i in range(len(self._options)))
        result.append(f"按 {digits} 选择", style=RICH_COLORS["$info"])
        result.append("，", style=RICH_COLORS["$muted"])
        result.append("[d] 查看更多上下文", style=RICH_COLORS["$muted"])

        # 展开态：更多上下文
        if self.expanded_context:
            result.append("\n\n", style="")
            expanded_text = self._get_expanded_context()
            if expanded_text:
                result.append("── 更多上下文 ──\n", style=RICH_COLORS["$muted"])
                result.append(expanded_text, style=RICH_COLORS["$muted"])

        return result

    def _format_context(
        self, approval_type: str, payload_dict: dict[str, object]
    ) -> tuple[str, str]:
        """格式化异常上下文：严格对齐 AC5 真实 payload 字段。"""
        return get_exception_context(approval_type, payload_dict)

    def _format_options(self, options: list[str]) -> list[Text]:
        """生成带数字键前缀的选项列表。"""
        labels = format_option_labels(self.approval_type, options)
        result: list[Text] = []
        for i, (key, label) in enumerate(zip(options, labels, strict=True), start=1):
            line = Text()
            line.append(f"[{i}] ", style=f"bold {RICH_COLORS['$info']}")
            line.append(f"{key.replace('_', ' ').title()}", style=RICH_COLORS["$text"])
            line.append(f" — {label}", style=RICH_COLORS["$muted"])
            result.append(line)
        return result

    def _get_expanded_context(self) -> str:
        """获取展开态的更多上下文信息。"""
        parts: list[str] = []
        pd = self._payload_dict

        # 优先展示真实 payload 字段
        if pd.get("stderr"):
            parts.append(f"stderr:\n{pd['stderr']}")
        if "worktree_path" in pd:
            parts.append(f"worktree_path: {pd['worktree_path']}")
        if pd.get("error_output"):
            parts.append(f"error_output:\n{pd['error_output']}")
        if "unresolved_findings" in pd:
            findings = pd["unresolved_findings"]
            if isinstance(findings, list):
                parts.append(f"unresolved_findings: {len(findings)} 条")
                for f in findings[:5]:  # 最多展示前 5 条
                    parts.append(f"  - {f}")
        if pd.get("raw_output_preview"):
            parts.append(f"raw_output_preview:\n{pd['raw_output_preview']}")
        if "round_summaries" in pd:
            summaries = pd["round_summaries"]
            if isinstance(summaries, list):
                parts.append(f"round_summaries: {len(summaries)} 轮")
                for s in summaries[-3:]:  # 最近 3 轮
                    parts.append(f"  - {s}")
        if pd.get("reason"):
            parts.append(f"reason: {pd['reason']}")
        if "task_id" in pd:
            parts.append(f"task_id: {pd['task_id']}")
        if pd.get("error"):
            parts.append(f"error: {pd['error']}")

        if not parts:
            # 展示原始 payload
            if pd:
                return json.dumps(pd, ensure_ascii=False, indent=2)
            return "无更多上下文信息"

        return "\n".join(parts)
