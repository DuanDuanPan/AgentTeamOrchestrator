"""recovery_summary — 恢复摘要 CLI 渲染器。

崩溃恢复完成后，将 RecoveryResult 渲染为人话版摘要输出到 stderr。
使用 rich 库（Console + Panel + Table），不使用 Textual。
"""

from __future__ import annotations

import json
from pathlib import Path

import aiosqlite
import structlog
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ato.models.db import get_connection, get_pending_approvals, get_story
from ato.models.schemas import ApprovalRecord, RecoveryResult

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


async def render_recovery_summary(
    result: RecoveryResult,
    db_path: Path,
    *,
    console: Console | None = None,
) -> None:
    """渲染恢复摘要到 stderr。

    Args:
        result: RecoveryEngine.run_recovery() 返回的恢复结果。
        db_path: SQLite 数据库路径，用于查询 needs_human 任务的详细信息。
        console: 可选 Console 实例（测试注入用）。默认写 stderr。
    """
    con = console or Console(stderr=True)

    parts: list[Text | Table | str] = []

    # 首行：数据完整性检查通过
    integrity_line = Text("✔ 数据完整性检查通过", style="green")
    parts.append(integrity_line)
    parts.append("")

    # 恢复模式标识
    total_tasks = len(result.classifications)
    if result.recovery_mode == "crash":
        mode_line = Text(f"检测到 {total_tasks} 个异常中断的任务，已自动分类处理")
    elif result.recovery_mode == "normal":
        mode_line = Text(f"检测到 {total_tasks} 个暂停的任务，正常恢复")
    else:
        mode_line = Text("无需恢复，系统状态正常")
    parts.append(mode_line)
    parts.append("")

    # 统计摘要
    if result.auto_recovered_count > 0:
        parts.append(Text(f"✔ {result.auto_recovered_count} 个任务自动恢复", style="green"))
    if result.dispatched_count > 0:
        parts.append(Text(f"🔄 {result.dispatched_count} 个任务已重新调度"))
    if result.needs_human_count > 0:
        parts.append(Text(f"◆ {result.needs_human_count} 个任务需要你决定", style="yellow"))

    # needs_human 决策列表
    if result.needs_human_count > 0:
        parts.append("")
        table, cmd_texts = await _build_needs_human_table(result, db_path)
        parts.append(table)
        # 命令单独渲染在表格外，保证每条命令是可复制的完整单行
        if cmd_texts:
            parts.append("")
            for cmd_text in cmd_texts:
                parts.append(cmd_text)
    else:
        if result.recovery_mode != "none":
            parts.append("")
            parts.append(Text("系统已恢复运行。"))

    # 用 Panel 包裹
    border_style = "yellow" if result.needs_human_count > 0 else "green"
    group = _build_renderable_group(parts)
    panel = Panel(group, title="恢复摘要", border_style=border_style)
    con.print(panel)

    logger.info(
        "recovery_summary_rendered",
        recovery_mode=result.recovery_mode,
        auto_recovered=result.auto_recovered_count,
        dispatched=result.dispatched_count,
        needs_human=result.needs_human_count,
    )


async def _build_needs_human_table(
    result: RecoveryResult,
    db_path: Path,
) -> tuple[Table, list[Text]]:
    """构建 needs_human 任务的决策表格和命令列表。

    Returns:
        (table, cmd_texts) — 表格仅含任务信息（4 列），
        命令作为独立 Text 列表返回，保证每条命令可完整复制。
    """
    table = Table(show_header=True, header_style="bold")
    table.add_column("Task", style="cyan")
    table.add_column("Story")
    table.add_column("Phase")
    table.add_column("Worktree")

    cmd_texts: list[Text] = []

    # 收集 needs_human 的 task 分类
    needs_human_tasks = [
        c for c in result.classifications if c.action == "needs_human"
    ]

    # 查询 DB 获取 worktree 和 approval 信息
    db = await get_connection(db_path)
    try:
        # 按 task_id 建立 crash_recovery approval 映射
        crash_approvals = await _get_crash_approval_map(db)

        for classification in needs_human_tasks:
            task_id = classification.task_id
            story_id = classification.story_id

            # 查询 story 的 worktree_path
            story = await get_story(db, story_id)
            worktree_path = story.worktree_path if story else "-"

            # 查询 task 的 phase
            from ato.models.db import get_tasks_by_story

            tasks = await get_tasks_by_story(db, story_id)
            phase = "-"
            for t in tasks:
                if t.task_id == task_id:
                    phase = t.phase
                    break

            table.add_row(
                task_id[:8],
                story_id,
                phase,
                worktree_path or "-",
            )

            # 构建该任务的命令列表（AC2: 三个选项）
            approval = crash_approvals.get(task_id)
            if approval is not None:
                short_id = approval.approval_id[:8]
                options_list = _extract_approval_options(approval)
                recommended = approval.recommended_action or "restart"
                header = Text(f"  {task_id[:8]}:", style="cyan")
                cmd_texts.append(header)
                for opt in options_list:
                    prefix = "→" if opt == recommended else " "
                    cmd_texts.append(
                        Text(
                            f"    {prefix} ato approve {short_id} "
                            f"--decision {opt}",
                            style="dim",
                        )
                    )
            else:
                logger.warning(
                    "crash_approval_missing_for_task",
                    task_id=task_id,
                    story_id=story_id,
                )
    finally:
        await db.close()

    return table, cmd_texts


def _extract_approval_options(approval: ApprovalRecord) -> list[str]:
    """从 approval payload 提取决策选项列表。

    优先从 payload.options 读取；缺失则 fallback 到 crash_recovery 默认选项。
    """
    from ato.models.schemas import APPROVAL_DEFAULT_VALID_OPTIONS

    if approval.payload:
        try:
            payload = json.loads(approval.payload)
            options = payload.get("options")
            if isinstance(options, list) and options:
                return [str(o) for o in options]
        except (json.JSONDecodeError, TypeError):
            pass
    fallback = ["restart", "resume", "abandon"]
    return list(APPROVAL_DEFAULT_VALID_OPTIONS.get("crash_recovery", fallback))


async def _get_crash_approval_map(
    db: aiosqlite.Connection,
) -> dict[str, ApprovalRecord]:
    """从 DB 查询 crash_recovery 类型的 pending approval，按 payload.task_id 建立映射。"""
    approvals = await get_pending_approvals(db)
    crash_approvals: dict[str, ApprovalRecord] = {}
    for approval in approvals:
        if approval.approval_type != "crash_recovery" or not approval.payload:
            continue
        try:
            payload = json.loads(approval.payload)
        except (json.JSONDecodeError, TypeError):
            continue
        task_id = payload.get("task_id")
        if isinstance(task_id, str):
            crash_approvals[task_id] = approval
    return crash_approvals


def _build_renderable_group(parts: list[Text | Table | str]) -> object:
    """将多个 renderable 部分组合为 rich Group。"""
    from rich.console import Group

    renderables = []
    for part in parts:
        if isinstance(part, str):
            renderables.append(Text(part))
        else:
            renderables.append(part)
    return Group(*renderables)
