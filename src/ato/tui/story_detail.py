"""story_detail — Story 详情视图。

第 2 层详情页，展示状态流可视化、Findings 摘要、执行产物、
成本明细、执行历史。支持 f/c/h/l 快捷键展开子视图（第 2.5 层）。
"""

from __future__ import annotations

import contextlib
import json
from typing import Any, ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import VerticalScroll
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Static

from ato.state_machine import CANONICAL_PHASES
from ato.tui.theme import RICH_COLORS
from ato.tui.widgets.convergent_loop_progress import ConvergentLoopProgress

# Phase order for flow visualization — aligned with state_machine.py
PHASE_ORDER: list[str] = ["queued", *CANONICAL_PHASES, "done"]


class StoryDetailView(Widget):
    """Story 详情视图（第 2 层）。

    通过 ``update_detail()`` 接收数据，渲染状态流、摘要和子视图。
    f/c/h/l 键展开子视图（第 2.5 层），ESC 返回上一层。
    """

    DEFAULT_CSS = ""

    BINDINGS: ClassVar[list[BindingType]] = [
        ("f", "toggle_findings", "Findings"),
        ("c", "toggle_costs", "成本"),
        ("h", "toggle_history", "历史"),
        ("l", "show_log_placeholder", "日志"),
        ("escape", "back", "返回"),
    ]

    class BackRequested(Message):
        """用户按 ESC 从详情概览请求返回主屏。"""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.can_focus = True
        # Data
        self._story_data: dict[str, object] = {}
        self._findings_summary: dict[str, int] = {}
        self._findings_detail: list[object] = []
        self._cost_logs: list[object] = []
        self._tasks: list[object] = []
        self._cl_round: int = 0
        self._cl_max_rounds: int = 3
        self._cost_usd: float = 0.0
        # Sub-view state: "findings" | "costs" | "history" | "log" | None
        self._expanded_view: str | None = None

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="story-detail-scroll"):
            yield Static(id="detail-phase-flow")
            yield Static(id="detail-summary")
            yield ConvergentLoopProgress(id="detail-cl-progress")
            yield Static(id="detail-expanded")

    def update_detail(
        self,
        *,
        story: dict[str, object],
        findings_summary: dict[str, int] | None = None,
        findings_detail: list[object] | None = None,
        cost_logs: list[object] | None = None,
        tasks: list[object] | None = None,
        cl_round: int = 0,
        cl_max_rounds: int = 3,
        cost_usd: float = 0.0,
    ) -> None:
        """接收并渲染 story 详情数据。"""
        self._story_data = story
        self._findings_summary = findings_summary or {}
        self._cl_round = cl_round
        self._cl_max_rounds = cl_max_rounds
        self._cost_usd = cost_usd
        if findings_detail is not None:
            self._findings_detail = findings_detail
        if cost_logs is not None:
            self._cost_logs = cost_logs
        if tasks is not None:
            self._tasks = tasks
        # 进入详情页时重置展开状态
        self._expanded_view = None
        self._render_all()

    def _render_all(self) -> None:
        """渲染所有区块。"""
        self._render_phase_flow()
        self._render_summary()
        self._render_cl_progress()
        self._render_expanded()

    # ------------------------------------------------------------------
    # 状态流可视化 (Task 2.2)
    # ------------------------------------------------------------------

    def _render_phase_flow(self) -> None:
        """渲染 phase flow：已完成 ✔ / 当前 ● / 未执行 ○。"""
        phase = str(self._story_data.get("current_phase", ""))
        result = Text()
        result.append("状态流: ", style=f"bold {RICH_COLORS['$accent']}")

        try:
            current_idx = PHASE_ORDER.index(phase)
        except ValueError:
            current_idx = -1

        for i, p in enumerate(PHASE_ORDER):
            if i > 0:
                result.append(" → ", style=RICH_COLORS["$muted"])
            if i < current_idx:
                result.append(f"✔{p}", style=RICH_COLORS["$muted"])
            elif i == current_idx:
                result.append(f"●{p}", style=f"bold {RICH_COLORS['$success']}")
            else:
                result.append(f"○{p}", style=RICH_COLORS["$muted"])

        self._update_child("#detail-phase-flow", result)

    # ------------------------------------------------------------------
    # 摘要区块 (Task 2.3–2.6)
    # ------------------------------------------------------------------

    def _render_summary(self) -> None:
        """渲染详情概览摘要。"""
        story = self._story_data
        sid = str(story.get("story_id", ""))
        title = str(story.get("title", ""))

        text = Text()
        text.append(f"\n{sid}", style=f"bold {RICH_COLORS['$accent']}")
        text.append(f"  {title}\n\n", style=RICH_COLORS["$text"])

        # Findings 摘要 (Task 2.3)
        self._append_findings_summary(text)

        # 执行产物 (Task 2.4)
        self._append_artifacts_summary(text)

        # 成本摘要 (Task 2.5)
        self._append_cost_summary(text)

        # 执行历史摘要 (Task 2.6)
        self._append_history_summary(text)

        # 快捷键提示 (Task 2.7)
        text.append("\n")
        text.append(
            "[f] Findings  [c] 成本  [h] 历史  [l] 日志  [ESC] 返回",
            style=RICH_COLORS["$muted"],
        )

        self._update_child("#detail-summary", text)

    def _append_findings_summary(self, text: Text) -> None:
        """追加 Findings 摘要行。"""
        fs = self._findings_summary
        b_open = fs.get("blocking_open", 0)
        b_closed = fs.get("blocking_closed", 0)
        s_open = fs.get("suggestion_open", 0)
        s_closed = fs.get("suggestion_closed", 0)
        total = b_open + b_closed + s_open + s_closed

        text.append("Findings: ", style=f"bold {RICH_COLORS['$text']}")
        if total == 0:
            text.append("无\n", style=RICH_COLORS["$muted"])
            return

        # blocking × open/closed
        parts: list[tuple[str, str]] = []
        if b_open > 0:
            parts.append((f"{b_open}B open", RICH_COLORS["$error"]))
        if b_closed > 0:
            parts.append((f"{b_closed}B closed", RICH_COLORS["$success"]))
        if s_open > 0:
            parts.append((f"{s_open}S open", RICH_COLORS["$warning"]))
        if s_closed > 0:
            parts.append((f"{s_closed}S closed", RICH_COLORS["$success"]))

        for i, (label, style) in enumerate(parts):
            if i > 0:
                text.append(" | ", style=RICH_COLORS["$muted"])
            text.append(label, style=style)

        text.append("\n")

    def _append_artifacts_summary(self, text: Text) -> None:
        """追加执行产物摘要行。"""
        artifacts = self._collect_artifacts()
        text.append("执行产物: ", style=f"bold {RICH_COLORS['$text']}")
        if artifacts:
            text.append(f"{len(artifacts)} 项", style=RICH_COLORS["$info"])
            # 最多展示前 3 个
            for a in artifacts[:3]:
                text.append(f"\n  · {a}", style=RICH_COLORS["$muted"])
            if len(artifacts) > 3:
                text.append(f"\n  …+{len(artifacts) - 3} 项", style=RICH_COLORS["$muted"])
        else:
            text.append("无", style=RICH_COLORS["$muted"])
        text.append("\n")

    def _append_cost_summary(self, text: Text) -> None:
        """追加成本摘要行。"""
        total = (
            sum(getattr(c, "cost_usd", 0.0) for c in self._cost_logs)
            if self._cost_logs
            else self._cost_usd
        )
        call_count = len(self._cost_logs)
        text.append("成本: ", style=f"bold {RICH_COLORS['$text']}")
        text.append(f"${total:.4f}", style=RICH_COLORS["$text"])
        if call_count > 0:
            text.append(f" ({call_count} 次调用)", style=RICH_COLORS["$muted"])
        text.append("\n")

    def _append_history_summary(self, text: Text) -> None:
        """追加执行历史摘要行。"""
        task_count = len(self._tasks)
        completed = sum(1 for t in self._tasks if getattr(t, "status", "") == "completed")
        running = sum(1 for t in self._tasks if getattr(t, "status", "") == "running")

        text.append("执行历史: ", style=f"bold {RICH_COLORS['$text']}")
        text.append(f"{task_count} tasks", style=RICH_COLORS["$info"])
        if completed > 0:
            text.append(f" ({completed} 完成", style=RICH_COLORS["$success"])
            if running > 0:
                text.append(f", {running} 运行中", style=RICH_COLORS["$warning"])
            text.append(")", style=RICH_COLORS["$success"])
        text.append("\n")

    # ------------------------------------------------------------------
    # ConvergentLoopProgress (Task 3 integration)
    # ------------------------------------------------------------------

    def _render_cl_progress(self) -> None:
        """更新 ConvergentLoopProgress 组件（仅对有 CL 数据的 story 显示）。"""
        with contextlib.suppress(Exception):
            cl_widget = self.query_one("#detail-cl-progress", ConvergentLoopProgress)
            cl_widget.update_progress(
                current_round=self._cl_round,
                max_rounds=self._cl_max_rounds,
                findings_summary=self._findings_summary,
            )

    # ------------------------------------------------------------------
    # 展开子视图（第 2.5 层）(Task 5)
    # ------------------------------------------------------------------

    def _render_expanded(self) -> None:
        """渲染展开子视图。"""
        if self._expanded_view is None:
            self._update_child("#detail-expanded", "")
            return
        if self._expanded_view == "findings":
            self._render_findings_detail()
        elif self._expanded_view == "costs":
            self._render_cost_detail()
        elif self._expanded_view == "history":
            self._render_history_detail()
        elif self._expanded_view == "log":
            pass  # already rendered in action_show_log_placeholder

    def _render_findings_detail(self) -> None:
        """展开 Findings 列表（每个 finding: severity + description + status + round_num）。"""
        text = Text()
        text.append("\n── Findings 详细列表 ──\n\n", style=f"bold {RICH_COLORS['$accent']}")
        if not self._findings_detail:
            text.append("  无 findings\n", style=RICH_COLORS["$muted"])
        else:
            for f in self._findings_detail:
                severity = getattr(f, "severity", "")
                desc = getattr(f, "description", "")
                status = getattr(f, "status", "")
                round_num = getattr(f, "round_num", 0)
                sev_color = "$error" if severity == "blocking" else "$warning"
                sev_style = RICH_COLORS[sev_color]
                stat_color = "$success" if status == "closed" else "$error"
                stat_style = RICH_COLORS[stat_color]
                text.append(f"  [{severity}]", style=sev_style)
                text.append(f" {desc}", style=RICH_COLORS["$text"])
                text.append(f" [{status}]", style=stat_style)
                text.append(f" R{round_num}\n", style=RICH_COLORS["$muted"])
        text.append("\n[ESC] 返回概览", style=RICH_COLORS["$muted"])
        self._update_child("#detail-expanded", text)

    def _render_cost_detail(self) -> None:
        """展开成本明细。"""
        text = Text()
        text.append("\n── 成本明细 ──\n\n", style=f"bold {RICH_COLORS['$accent']}")
        if not self._cost_logs:
            text.append("  无成本记录\n", style=RICH_COLORS["$muted"])
        else:
            for cl in self._cost_logs:
                phase = getattr(cl, "phase", "")
                role = getattr(cl, "role", "") or ""
                cli_tool = getattr(cl, "cli_tool", "")
                model = getattr(cl, "model", "") or ""
                input_t = getattr(cl, "input_tokens", 0)
                output_t = getattr(cl, "output_tokens", 0)
                cache_t = getattr(cl, "cache_read_input_tokens", 0)
                cost = getattr(cl, "cost_usd", 0.0)
                text.append(f"  {phase}", style=RICH_COLORS["$info"])
                text.append(f" | {role}", style=RICH_COLORS["$text"])
                text.append(f" | {cli_tool}", style=RICH_COLORS["$text"])
                if model:
                    text.append(f" | {model}", style=RICH_COLORS["$muted"])
                text.append(f" | in:{input_t} out:{output_t}", style=RICH_COLORS["$muted"])
                if cache_t > 0:
                    text.append(f" cache:{cache_t}", style=RICH_COLORS["$muted"])
                text.append(f" | ${cost:.4f}\n", style=RICH_COLORS["$text"])
        text.append("\n[ESC] 返回概览", style=RICH_COLORS["$muted"])
        self._update_child("#detail-expanded", text)

    def _render_history_detail(self) -> None:
        """展开执行历史时间轴。"""
        text = Text()
        text.append("\n── 执行历史 ──\n\n", style=f"bold {RICH_COLORS['$accent']}")
        if not self._tasks:
            text.append("  无执行记录\n", style=RICH_COLORS["$muted"])
        else:
            for t in self._tasks:
                started = getattr(t, "started_at", None)
                phase = getattr(t, "phase", "")
                role = getattr(t, "role", "")
                cli_tool = getattr(t, "cli_tool", "")
                status = getattr(t, "status", "")
                duration = getattr(t, "duration_ms", None)
                artifact = self._extract_artifact(t)

                started_str = str(started)[:19] if started else "-"
                duration_str = f"{duration}ms" if duration else "-"

                if status == "completed":
                    stat_style = RICH_COLORS["$success"]
                elif status == "failed":
                    stat_style = RICH_COLORS["$error"]
                else:
                    stat_style = RICH_COLORS["$warning"]

                text.append(f"  {started_str}", style=RICH_COLORS["$muted"])
                text.append(f" | {phase}", style=RICH_COLORS["$info"])
                text.append(f" | {role}", style=RICH_COLORS["$text"])
                text.append(f" | {cli_tool}", style=RICH_COLORS["$text"])
                text.append(f" | {status}", style=stat_style)
                if artifact:
                    text.append(f" | {artifact}", style=RICH_COLORS["$muted"])
                text.append(f" | {duration_str}\n", style=RICH_COLORS["$muted"])
        text.append("\n[ESC] 返回概览", style=RICH_COLORS["$muted"])
        self._update_child("#detail-expanded", text)

    # ------------------------------------------------------------------
    # 数据提取辅助
    # ------------------------------------------------------------------

    def _collect_artifacts(self) -> list[str]:
        """提取执行产物列表。

        context_briefing.artifacts_produced 优先，fallback expected_artifact。
        """
        artifacts: list[str] = []
        for task in self._tasks:
            cb_str = getattr(task, "context_briefing", None)
            if cb_str:
                try:
                    cb = json.loads(cb_str)
                    produced = cb.get("artifacts_produced", [])
                    if produced:
                        artifacts.extend(produced)
                        continue
                except (json.JSONDecodeError, TypeError, AttributeError):
                    pass
            ea = getattr(task, "expected_artifact", None)
            if ea:
                artifacts.append(str(ea))
        return artifacts

    def _extract_artifact(self, task: object) -> str:
        """单个 task 的产物展示：context_briefing.artifacts_produced > expected_artifact。"""
        cb_str = getattr(task, "context_briefing", None)
        if cb_str:
            try:
                cb = json.loads(cb_str)
                produced = cb.get("artifacts_produced", [])
                if produced:
                    return ", ".join(produced[:3])
            except (json.JSONDecodeError, TypeError, AttributeError):
                pass
        ea = getattr(task, "expected_artifact", None)
        return str(ea) if ea else ""

    # ------------------------------------------------------------------
    # 快捷键 actions (Task 2.7, Task 5)
    # ------------------------------------------------------------------

    def action_toggle_findings(self) -> None:
        """f 键：展开/折叠 Findings 列表。"""
        self._expanded_view = None if self._expanded_view == "findings" else "findings"
        self._render_expanded()

    def action_toggle_costs(self) -> None:
        """c 键：展开/折叠成本明细。"""
        self._expanded_view = None if self._expanded_view == "costs" else "costs"
        self._render_expanded()

    def action_toggle_history(self) -> None:
        """h 键：展开/折叠执行历史。"""
        self._expanded_view = None if self._expanded_view == "history" else "history"
        self._render_expanded()

    def action_show_log_placeholder(self) -> None:
        """l 键：显示日志 placeholder。"""
        text = Text()
        text.append("\n日志查看将在后续版本提供\n", style=RICH_COLORS["$warning"])
        text.append("\n[ESC] 返回概览", style=RICH_COLORS["$muted"])
        self._update_child("#detail-expanded", text)
        self._expanded_view = "log"

    def action_back(self) -> None:
        """ESC 键：从展开子视图返回详情概览，或从详情概览返回主屏。"""
        if self._expanded_view is not None:
            self._expanded_view = None
            self._render_expanded()
        else:
            self.post_message(self.BackRequested())

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _update_child(self, selector: str, content: str | Text) -> None:
        """安全更新子 Static 内容。"""
        with contextlib.suppress(Exception):
            self.query_one(selector, Static).update(content)
