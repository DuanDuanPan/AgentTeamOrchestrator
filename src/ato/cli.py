"""cli — CLI 入口点。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import time
from datetime import UTC, datetime
from pathlib import Path

import click.exceptions
import structlog
import typer
from rich.console import Console
from rich.text import Text

from ato.config import PhaseDefinition, build_phase_definitions, load_config
from ato.models.db import get_connection, get_story
from ato.models.schemas import CheckResult, ContextBriefing, StoryRecord
from ato.preflight import run_preflight

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

app = typer.Typer(name="ato", help="Agent Team Orchestrator")

# ---------------------------------------------------------------------------
# batch 子命令组
# ---------------------------------------------------------------------------

batch_app = typer.Typer(help="Batch 管理")
app.add_typer(batch_app, name="batch")

# ---------------------------------------------------------------------------
# 默认路径约定
# ---------------------------------------------------------------------------

_DEFAULT_DB_PATH = Path(".ato/state.db")
_DEFAULT_EPICS_PATH = Path("_bmad-output/planning-artifacts/epics.md")

# Status / Phase → 显示图标
_STATUS_ICONS: dict[str, str] = {
    "done": "✅",
    "blocked": "✖",
}
_PHASE_ICONS: dict[str, str] = {
    "queued": "⏳",
    "creating": "🔄",
}


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Agent Team Orchestrator — 多角色 AI 团队编排系统。"""
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


# ---------------------------------------------------------------------------
# ato init — Preflight 渲染与项目初始化
# ---------------------------------------------------------------------------

_console = Console()

_STATUS_MAP: dict[str, tuple[str, str]] = {
    "PASS": ("✔", "green"),
    "HALT": ("✖", "red bold"),
    "WARN": ("⚠", "yellow"),
    "INFO": ("ℹ", "dim"),
}

_LAYER_TITLES: dict[str, str] = {
    "system": "第一层：系统环境",
    "project": "第二层：项目结构",
    "artifact": "第三层：编排前置 Artifact",
}

_HINTS: dict[str, str] = {
    "python_version": "升级到 Python ≥ 3.11 后重新运行 `ato init`",
    "claude_installed": "安装 Claude CLI 后重新运行 `ato init`",
    "claude_auth": "执行 `claude auth` 完成登录",
    "codex_installed": "安装 Codex CLI 后重新运行 `ato init`",
    "codex_auth": "完成 Codex CLI 认证后重试",
    "git_installed": "安装 Git 后重试",
    "git_repo": "在目标目录执行 `git init`，或切换到已有仓库",
    "bmad_config": "补齐 `_bmad/bmm/config.yaml` 的必填字段",
    "bmad_skills": "运行 BMAD 安装流程以部署 skills 目录",
    "ato_yaml": "从 `ato.yaml.example` 复制并补全配置",
    "epic_files": "补齐 epics 文档，否则 `sprint-planning` / `create-story` 无法运行",
    "prd_files": "建议运行 `/bmad-create-prd`",
    "architecture_files": "建议运行 `/bmad-create-architecture`",
    "impl_directory": "修复目录权限，或检查 BMAD config 中 implementation_artifacts 路径",
}


def render_preflight_results(
    results: list[CheckResult],
    *,
    console: Console | None = None,
) -> None:
    """渲染 Preflight 检查结果到 console。"""
    con = console or _console

    con.print()
    con.print(Text("AgentTeamOrchestrator — Preflight Check", style="bold"))
    con.print()

    current_layer: str | None = None
    for r in results:
        if r.layer != current_layer:
            if current_layer is not None:
                con.print()
            current_layer = r.layer
            title = _LAYER_TITLES.get(r.layer, r.layer)
            con.print(Text(title, style="bold"))

        icon, style = _STATUS_MAP.get(r.status, ("?", ""))
        line = Text()
        line.append(f"  {icon} ", style=style)
        line.append(r.message)
        con.print(line, highlight=False)

        if r.status in ("WARN", "HALT"):
            hint = _HINTS.get(r.check_item)
            if hint:
                hint_line = Text()
                hint_line.append(f"    → {hint}", style="dim")
                con.print(hint_line, highlight=False)

    con.print()
    con.rule()
    _render_summary(results, con)


def _render_summary(results: list[CheckResult], console: Console) -> None:
    """渲染底部摘要。"""
    halt_count = sum(1 for r in results if r.status == "HALT")
    warn_count = sum(1 for r in results if r.status == "WARN")
    info_count = sum(1 for r in results if r.status == "INFO")

    if halt_count > 0:
        summary = Text(f"结果: ✖ 未就绪（{halt_count} 阻断）", style="red bold")
    elif warn_count > 0:
        summary = Text(f"结果: 就绪（{warn_count} 警告, {info_count} 信息）", style="yellow")
    else:
        summary = Text("结果: ✔ 就绪", style="green")

    console.print(summary)


@app.command("init")
def init_command(
    project_path: Path = typer.Argument(
        ".",
        help="目标项目路径",
        exists=True,
        file_okay=False,
        resolve_path=True,
    ),
    db_path: Path | None = typer.Option(
        None,
        "--db-path",
        help="SQLite 数据库路径（默认 <project>/.ato/state.db）",
    ),
) -> None:
    """初始化项目环境，执行 Preflight 检查。"""
    resolved_db = db_path or (project_path / ".ato" / "state.db")

    if resolved_db.exists():
        typer.confirm("已检测到现有数据库，是否重新初始化？", abort=True)

    try:
        asyncio.run(_init_async(project_path, resolved_db))
    except click.exceptions.Exit:
        raise
    except click.exceptions.Abort:
        raise
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


async def _init_async(project_path: Path, db_path: Path) -> None:
    """执行 Preflight 检查并渲染结果。"""
    results = await run_preflight(project_path, db_path, include_auth=True)

    render_preflight_results(results)

    has_halt = any(r.status == "HALT" for r in results)
    if has_halt:
        typer.echo("环境检查未通过，请根据上方提示修复后重新运行 `ato init`", err=True)
        raise typer.Exit(code=2)

    typer.prompt(
        "按 Enter 继续初始化，或 Ctrl-C 取消",
        default="",
        show_default=False,
    )

    typer.echo("✔ 系统已初始化")
    typer.echo("运行 `ato start` 开始编排")
    typer.echo("运行 `ato tui` 打开仪表盘")


# ---------------------------------------------------------------------------
# ato batch select
# ---------------------------------------------------------------------------


@batch_app.command("select")
def batch_select(
    epics_file: Path | None = typer.Option(None, "--epics-file", help="Epics 文件路径"),
    db_path: Path | None = typer.Option(None, "--db-path", help="SQLite 数据库路径"),
    max_stories: int = typer.Option(5, "--max-stories", help="推荐 batch 最大 story 数"),
    story_ids: str | None = typer.Option(
        None,
        "--story-ids",
        help="直接指定 story keys（逗号分隔），跳过推荐",
    ),
) -> None:
    """选择要执行的 story batch。"""
    resolved_db = db_path or _DEFAULT_DB_PATH
    resolved_epics = epics_file or _DEFAULT_EPICS_PATH

    # 检查 DB
    if not resolved_db.exists():
        typer.echo(
            f"错误：数据库不存在: {resolved_db}。请先运行 `ato init`。",
            err=True,
        )
        raise typer.Exit(code=1)

    # 检查 epics
    if not resolved_epics.exists():
        typer.echo(f"错误：Epics 文件不存在: {resolved_epics}", err=True)
        raise typer.Exit(code=1)

    try:
        asyncio.run(_batch_select_async(resolved_db, resolved_epics, max_stories, story_ids))
    except click.exceptions.Exit:
        raise
    except Exception as exc:
        typer.echo(f"错误：{exc}", err=True)
        raise typer.Exit(code=1) from exc


async def _batch_select_async(
    db_path: Path,
    epics_path: Path,
    max_stories: int,
    story_ids: str | None,
) -> None:
    from ato.batch import (
        BatchProposal,
        EpicInfo,
        LocalBatchRecommender,
        build_canonical_key_map,
        confirm_batch,
        load_epics,
    )
    from ato.models.db import get_active_batch, get_connection, get_story

    # 构建 canonical key 映射（从 sprint-status.yaml）
    sprint_status = epics_path.parent.parent / "implementation-artifacts" / "sprint-status.yaml"
    key_map: dict[str, str] = {}
    if sprint_status.exists():
        key_map = build_canonical_key_map(sprint_status)

    db = await get_connection(db_path)
    try:
        # 检查是否已有 active batch
        existing = await get_active_batch(db)
        if existing is not None:
            typer.echo(
                f"已存在 active batch ({existing.batch_id[:8]}...)。"
                "请先完成或取消当前 batch 后再创建新 batch。",
                err=True,
            )
            raise typer.Exit(code=1)

        # 解析 epics（使用 canonical key map）
        epics_info = load_epics(epics_path, canonical_key_map=key_map)
        if not epics_info:
            typer.echo("错误：未从 epics 文件中解析出任何 story。", err=True)
            raise typer.Exit(code=1)

        # 获取已有 story 状态
        existing_stories: dict[str, StoryRecord] = {}
        for info in epics_info:
            story = await get_story(db, info.story_key)
            if story is not None:
                existing_stories[info.story_key] = story

        selected_indices: list[int] | None = None

        if story_ids is not None:
            # 非交互模式：直接指定 story keys（保留用户输入顺序）
            # 支持 canonical key 和 short_key 匹配
            input_keys = [k.strip() for k in story_ids.split(",") if k.strip()]
            # 建立双向查找表
            by_canonical = {i.story_key: i for i in epics_info}
            by_short = {i.short_key: i for i in epics_info}
            selected_infos: list[EpicInfo] = []
            unmatched: list[str] = []
            for key in input_keys:
                found = by_canonical.get(key)
                if found is None:
                    found = by_short.get(key)
                if found is None:
                    unmatched.append(key)
                elif found not in selected_infos:
                    selected_infos.append(found)
            if unmatched:
                typer.echo(
                    f"错误：以下 story keys 不在 epics 中: {unmatched}",
                    err=True,
                )
                raise typer.Exit(code=1)
            proposal = BatchProposal(stories=selected_infos, reason="用户直接指定")
        else:
            # 推荐模式
            recommender = LocalBatchRecommender()
            proposal = recommender.recommend(epics_info, existing_stories, max_stories)

            if not proposal.stories:
                typer.echo("没有可推荐的 stories（所有 stories 已完成或依赖未满足）。")
                return

            # 展示推荐
            typer.echo("推荐 batch 方案:")
            typer.echo("")
            for idx, info in enumerate(proposal.stories):
                status_str = ""
                story = existing_stories.get(info.story_key)
                if story is not None:
                    status_str = f" [{story.status}]"
                typer.echo(f"  {idx + 1}. {info.story_key} — {info.title}{status_str}")
            typer.echo("")

            # 交互选择（支持按编号选择子集）
            selection = typer.prompt(
                "输入要选择的编号（逗号分隔，如 1,3,5），或 Enter 全选",
                default="",
            ).strip()

            if selection:
                try:
                    raw_indices = [int(s.strip()) - 1 for s in selection.split(",")]
                except ValueError:
                    typer.echo("错误：无效输入，请输入编号", err=True)
                    raise typer.Exit(code=1) from None
                # 去重（保留首次出现顺序）+ 范围校验
                seen: set[int] = set()
                selected_indices = []
                for idx in raw_indices:
                    if idx < 0 or idx >= len(proposal.stories):
                        typer.echo(
                            f"错误：编号 {idx + 1} 超出范围 (1-{len(proposal.stories)})",
                            err=True,
                        )
                        raise typer.Exit(code=1)
                    if idx not in seen:
                        seen.add(idx)
                        selected_indices.append(idx)

        # 确认 batch（返回实际写入数量，排除不可回退 stories）
        batch, actual_count = await confirm_batch(db, proposal, selected_indices=selected_indices)
        typer.echo(f"✅ Batch 已创建 ({batch.batch_id[:8]}...)，包含 {actual_count} 个 stories。")

    finally:
        await db.close()


# ---------------------------------------------------------------------------
# ato batch status
# ---------------------------------------------------------------------------


@batch_app.command("status")
def batch_status(
    db_path: Path | None = typer.Option(None, "--db-path", help="SQLite 数据库路径"),
    output_json: bool = typer.Option(False, "--json", help="JSON 格式输出到 stdout"),
) -> None:
    """查看当前 batch 进度。"""
    resolved_db = db_path or _DEFAULT_DB_PATH

    # 检查 DB
    if not resolved_db.exists():
        typer.echo(
            f"错误：数据库不存在: {resolved_db}。请先运行 `ato init`。",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        asyncio.run(_batch_status_async(resolved_db, output_json))
    except click.exceptions.Exit:
        raise
    except Exception as exc:
        typer.echo(f"错误：{exc}", err=True)
        raise typer.Exit(code=1) from exc


_EMPTY_MSG = "尚无 story。运行 `ato batch select` 选择第一个 batch"


async def _batch_status_async(db_path: Path, output_json: bool) -> None:
    from ato.models.db import (
        get_active_batch,
        get_batch_progress,
        get_batch_stories,
        get_connection,
    )

    db = await get_connection(db_path)
    try:
        batch = await get_active_batch(db)

        if batch is None:
            # 空状态引导 (AC3 / UX-DR13)
            if output_json:
                typer.echo(json.dumps({"batch": None, "message": _EMPTY_MSG}))
            else:
                typer.echo(_EMPTY_MSG)
            return

        # 获取进度和 stories
        progress = await get_batch_progress(db, batch.batch_id)
        stories = await get_batch_stories(db, batch.batch_id)

        if output_json:
            # AC4: JSON 输出
            data = {
                "batch_id": batch.batch_id,
                "status": batch.status,
                "created_at": batch.created_at.isoformat(),
                "progress": {
                    "done": progress.done,
                    "active": progress.active,
                    "pending": progress.pending,
                    "failed": progress.failed,
                    "total": progress.total,
                },
                "stories": [
                    {
                        "story_id": story.story_id,
                        "title": story.title,
                        "status": story.status,
                        "current_phase": story.current_phase,
                        "sequence_no": link.sequence_no,
                    }
                    for link, story in stories
                ],
            }
            typer.echo(json.dumps(data, ensure_ascii=False))
        else:
            # 人类可读输出
            created_str = batch.created_at.strftime("%Y-%m-%d")
            bid = batch.batch_id[:8]
            typer.echo(f"Batch ({bid}...) ({created_str} 创建)  状态: {batch.status}")
            typer.echo("")

            # 进度条
            if progress.total > 0:
                filled = int(progress.done / progress.total * 10)
                bar = "█" * filled + "░" * (10 - filled)
                typer.echo(f"  已完成  {bar}  {progress.done}/{progress.total}")
            typer.echo("")

            # Story 列表
            for _link, story in stories:
                icon = _STATUS_ICONS.get(story.status, "")
                if not icon:
                    icon = _PHASE_ICONS.get(story.current_phase, "🔄")
                phase_display = story.current_phase
                typer.echo(f"  {icon} {story.story_id:<40} {phase_display}")

    finally:
        await db.close()


# ---------------------------------------------------------------------------
# ato start
# ---------------------------------------------------------------------------

_STOP_POLL_INTERVAL = 0.3
_STOP_TIMEOUT = 15.0


@app.command("start")
def start_cmd(
    db_path: Path | None = typer.Option(None, "--db-path", help="SQLite 数据库路径"),
    config_path: Path | None = typer.Option(None, "--config", help="ato.yaml 配置文件路径"),
) -> None:
    """启动 Orchestrator 事件循环。"""
    from ato.core import is_orchestrator_running

    resolved_db = db_path or _DEFAULT_DB_PATH
    pid_path = resolved_db.parent / "orchestrator.pid"

    # 重复启动防护
    if is_orchestrator_running(pid_path):
        typer.echo("错误：Orchestrator 已在运行中。", err=True)
        raise typer.Exit(code=1)

    # 初始化日志
    from ato.logging import configure_logging

    configure_logging(log_dir=str(resolved_db.parent / "logs"))

    # Preflight 快速检查
    from ato.preflight import run_preflight

    try:
        preflight_results = asyncio.run(run_preflight(Path.cwd(), resolved_db, include_auth=False))
    except click.exceptions.Exit:
        raise
    except Exception as exc:
        typer.echo(f"错误：Preflight 检查失败: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    if any(r.status == "HALT" for r in preflight_results):
        typer.echo("错误：Preflight 检查存在 HALT 项，无法启动。", err=True)
        raise typer.Exit(code=2)

    # 加载配置
    from ato.config import load_config

    resolved_config = config_path or Path("ato.yaml")
    try:
        settings = load_config(resolved_config)
    except Exception as exc:
        typer.echo(f"错误：配置加载失败: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    # 启动 Orchestrator
    from ato.core import Orchestrator

    orchestrator = Orchestrator(settings=settings, db_path=resolved_db)
    asyncio.run(orchestrator.run())


# ---------------------------------------------------------------------------
# ato stop
# ---------------------------------------------------------------------------


@app.command("stop")
def stop_cmd(
    pid_file: Path | None = typer.Option(None, "--pid-file", help="PID 文件路径"),
) -> None:
    """优雅停止 Orchestrator。"""
    from ato.core import read_pid_file, remove_pid_file

    pid_path = pid_file or Path(".ato/orchestrator.pid")
    pid = read_pid_file(pid_path)

    if pid is None:
        typer.echo("Orchestrator 未在运行（无 PID 文件）。")
        return

    # 检查进程是否存活
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        typer.echo("Orchestrator 进程已不存在，清理 PID 文件。")
        remove_pid_file(pid_path)
        return
    except PermissionError:
        pass  # 进程存在但无权检查——继续尝试发 SIGTERM

    # 发送 SIGTERM
    try:
        os.kill(pid, signal.SIGTERM)
        typer.echo(f"已向 Orchestrator (PID {pid}) 发送停止信号。")
    except ProcessLookupError:
        typer.echo("Orchestrator 进程已不存在，清理 PID 文件。")
        remove_pid_file(pid_path)
        return
    except PermissionError as exc:
        typer.echo("错误：无权向 Orchestrator 进程发送信号。", err=True)
        raise typer.Exit(code=1) from exc

    # 轮询等待进程退出
    deadline = time.monotonic() + _STOP_TIMEOUT
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            # 进程已退出——可能是默认 SIGTERM 行为（handler 尚未注册），
            # 此时 Orchestrator 不会自行清理 PID 文件，由 stop 负责。
            remove_pid_file(pid_path)
            typer.echo("Orchestrator 已停止。")
            return
        except PermissionError:
            pass
        time.sleep(_STOP_POLL_INTERVAL)

    # 超时——升级为 SIGKILL
    typer.echo("等待超时，尝试强制终止 (SIGKILL)...", err=True)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        remove_pid_file(pid_path)
        typer.echo("Orchestrator 已停止。")
        return
    except PermissionError as exc:
        typer.echo("错误：无权强制终止 Orchestrator 进程。", err=True)
        raise typer.Exit(code=1) from exc

    # 等待 SIGKILL 生效
    time.sleep(1.0)
    try:
        os.kill(pid, 0)
        typer.echo("错误：Orchestrator 进程仍未退出。", err=True)
        raise typer.Exit(code=1)
    except ProcessLookupError:
        typer.echo("Orchestrator 已强制停止。")
        remove_pid_file(pid_path)


# ---------------------------------------------------------------------------
# ato plan — 阶段序列预览
# ---------------------------------------------------------------------------

# 阶段类型 → rich 颜色样式
_PHASE_TYPE_STYLES: dict[str, str] = {
    "structured_job": "cyan",
    "convergent_loop": "magenta",
    "interactive_session": "green",
}
_SYSTEM_PHASE_STYLE = "dim"  # queued, done


def render_plan(
    story: StoryRecord,
    phase_defs: list[PhaseDefinition],
    *,
    console: Console | None = None,
) -> None:
    """渲染 story 阶段序列预览到 console。"""
    from ato.state_machine import CANONICAL_PHASES, PHASE_TO_STATUS

    con = console or _console

    full_sequence = ["queued", *CANONICAL_PHASES, "done"]

    # 构建 phase_info 映射（name → (phase_type, role)）
    phase_info: dict[str, tuple[str, str]] = {}
    for pd in phase_defs:
        phase_info[pd.name] = (pd.phase_type, pd.role)

    # 标题
    con.print()
    con.print(Text("AgentTeamOrchestrator — Story Plan", style="bold"))
    con.print()

    current_phase = story.current_phase
    is_blocked = current_phase == "blocked"
    is_done = current_phase == "done"

    # Story 信息
    status_str = PHASE_TO_STATUS.get(current_phase, current_phase)
    story_line = Text()
    story_line.append(f"Story: {story.story_id} — {story.title}")
    con.print(story_line, highlight=False)

    if is_blocked:
        con.print(
            Text("⚠ 当前状态: blocked（MVP 不显示 blocked 前进度）", style="yellow"),
            highlight=False,
        )
    else:
        phase_line = Text()
        phase_line.append(f"当前阶段: {current_phase} ({status_str})")
        con.print(phase_line, highlight=False)

    con.print()

    # 确定当前阶段在序列中的位置
    if is_done:
        current_idx = len(full_sequence)  # 所有阶段都是 completed
    elif is_blocked:
        current_idx = -1  # 不做进度推断
    elif current_phase in full_sequence:
        current_idx = full_sequence.index(current_phase)
    else:
        current_idx = -1  # 未知阶段，全部按 future 渲染

    # 逐行渲染
    for i, phase_name in enumerate(full_sequence):
        is_system_phase = phase_name in ("queued", "done")

        # 确定进度状态
        if current_idx < 0:
            # blocked 或未知：全部按未激活阶段渲染（保留 phase-type 颜色）
            icon = "○"
            if is_system_phase:
                style = _SYSTEM_PHASE_STYLE
            else:
                pt = phase_info.get(phase_name, (None, None))[0] if phase_info else None
                style = _PHASE_TYPE_STYLES.get(pt, "") if pt else ""
            suffix = ""
        elif i < current_idx:
            icon = "✔"
            style = "green"
            suffix = ""
        elif i == current_idx:
            icon = "▶"
            pt = phase_info.get(phase_name, (None, None))[0] if phase_info else None
            color = _PHASE_TYPE_STYLES.get(pt, "") if pt else ""
            style = f"bold {color}".strip() if color else "bold"
            suffix = " ← 当前"
        else:
            icon = "○"
            if is_system_phase:
                style = _SYSTEM_PHASE_STYLE
            else:
                pt = phase_info.get(phase_name, (None, None))[0] if phase_info else None
                style = _PHASE_TYPE_STYLES.get(pt, "") if pt else ""
            suffix = ""

        # 构建行文本
        line = Text()
        line.append(f"  {icon} ", style=style)
        line.append(f"{phase_name:<16}", style=style)

        # 类型/角色信息（仅在有配置时显示）
        if phase_info and phase_name in phase_info:
            pt, role = phase_info[phase_name]
            line.append(f"({pt} | {role})", style=style)

        if suffix:
            line.append(suffix, style=style)

        con.print(line, highlight=False)

    con.print()


@app.command("plan")
def plan_command(
    story_id: str = typer.Argument(..., help="Story ID"),
    db_path: Path | None = typer.Option(None, "--db-path", help="SQLite 数据库路径"),
    config_path: Path | None = typer.Option(None, "--config", help="ato.yaml 配置文件路径"),
) -> None:
    """预览 story 将经历的完整阶段序列。"""
    resolved_db = db_path or _DEFAULT_DB_PATH

    if not resolved_db.exists():
        typer.echo(
            f"错误：数据库不存在: {resolved_db}。请先运行 `ato init`。",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        asyncio.run(_plan_async(story_id, resolved_db, config_path))
    except click.exceptions.Exit:
        raise
    except click.exceptions.Abort:
        raise
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


async def _plan_async(
    story_id: str,
    db_path: Path,
    config_path: Path | None,
) -> None:
    """执行 plan 异步逻辑。"""
    db = await get_connection(db_path)
    try:
        story = await get_story(db, story_id)
    finally:
        await db.close()

    if story is None:
        typer.echo(f"Story not found: {story_id}", err=True)
        raise typer.Exit(code=1)

    # 配置加载（可选降级）
    phase_definitions: list[PhaseDefinition] = []
    resolved_config = config_path or Path("ato.yaml")
    try:
        settings = load_config(resolved_config)
        phase_definitions = build_phase_definitions(settings)
    except Exception as exc:
        logger.warning(
            "plan_config_load_failed",
            config_path=str(resolved_config),
            error=str(exc),
        )
        typer.echo("⚠ 配置加载失败，仅显示阶段序列", err=True)

    render_plan(story, phase_definitions)


# ---------------------------------------------------------------------------
# ato tui — TUI 指挥台
# ---------------------------------------------------------------------------


def _resolve_tui_config(
    explicit: Path | None,
    db_path: Path,
) -> Path | None:
    """自动发现 TUI 使用的 ato.yaml。

    搜索链（首个存在的胜出）：
    1. 显式 ``--config`` 路径（直接返回，不检查存在性——由调用方校验）
    2. db_path 同级目录（支持 ``./custom.db`` + 同级 ``ato.yaml``）
    3. db_path 祖父目录（标准 ``.ato/state.db`` 布局）
    4. CWD 下 ``ato.yaml``

    全部找不到时返回 ``None``，由调用方使用默认值。
    """
    if explicit is not None:
        return explicit
    candidates = [
        db_path.parent / "ato.yaml",
        db_path.parent.parent / "ato.yaml",
        Path("ato.yaml"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


@app.command("tui")
def tui_cmd(
    db_path: Path | None = typer.Option(None, "--db-path", help="SQLite 数据库路径"),
    config_path: Path | None = typer.Option(None, "--config", help="ato.yaml 配置文件路径"),
) -> None:
    """启动 TUI 指挥台，连接运行中的 Orchestrator。"""
    resolved_db = db_path or _DEFAULT_DB_PATH

    if not resolved_db.exists():
        typer.echo("数据库未找到，请先运行 ato init", err=True)
        raise typer.Exit(code=1)

    # 检测 Orchestrator 运行状态（复用 core.py 的 PID 读取约定）
    from ato.core import read_pid_file

    pid_path = resolved_db.parent / "orchestrator.pid"
    pid = read_pid_file(pid_path)
    orchestrator_pid: int | None = None

    if pid is None:
        typer.echo("⚠️ Orchestrator 未运行，写入已记录，需等待下次启动后处理")
    else:
        try:
            os.kill(pid, 0)
            orchestrator_pid = pid
        except ProcessLookupError:
            typer.echo("⚠️ Orchestrator 未运行（stale PID），写入已记录，需等待下次启动后处理")
        except PermissionError:
            orchestrator_pid = pid  # 进程存在但无权发信号

    # 加载配置以获取 convergent_loop.max_rounds（不 hardcode）
    # 显式 --config 失败 → 报错退出（与 start 一致）
    # 自动发现失败 → 降级使用默认值（TUI 只需 max_rounds，仍可启动）
    cl_max_rounds = 3  # 默认值
    if config_path is not None:
        # 用户显式指定：失败即退出
        try:
            settings = load_config(config_path)
            cl_max_rounds = settings.convergent_loop.max_rounds
        except Exception as exc:
            typer.echo(f"错误：配置加载失败: {exc}", err=True)
            raise typer.Exit(code=2) from exc
    else:
        # 自动发现：失败可降级
        resolved_config = _resolve_tui_config(None, resolved_db)
        if resolved_config is not None:
            try:
                settings = load_config(resolved_config)
                cl_max_rounds = settings.convergent_loop.max_rounds
            except Exception:
                logger.warning("tui_config_load_failed", exc_info=True)

    from ato.tui.app import ATOApp

    ATOApp(
        db_path=resolved_db,
        orchestrator_pid=orchestrator_pid,
        convergent_loop_max_rounds=cl_max_rounds,
    ).run()


# ---------------------------------------------------------------------------
# ato submit — Interactive Session 完成提交
# ---------------------------------------------------------------------------


def _send_nudge_safe(pid_path: Path) -> None:
    """安全发送 nudge，Orchestrator 未运行时仅输出提示。"""
    from ato.core import read_pid_file
    from ato.nudge import send_external_nudge

    pid = read_pid_file(pid_path)
    if pid is None:
        typer.echo("Orchestrator 未运行，跳过 nudge（下次启动时自动检测）。")
        return
    try:
        send_external_nudge(pid)
    except ProcessLookupError:
        typer.echo("Orchestrator 进程不存在，跳过 nudge。")
    except PermissionError:
        typer.echo("无权向 Orchestrator 发送 nudge 信号。", err=True)


async def _check_new_commits(worktree_path: str, base_commit: str, db_path: Path) -> bool:
    """检测 worktree 中是否有新 commit。"""
    from ato.worktree_mgr import WorktreeManager

    mgr = WorktreeManager(
        project_root=Path(worktree_path).parent.parent,
        db_path=db_path,
    )
    return await mgr.has_new_commits(
        worktree_path=Path(worktree_path),
        since_rev=base_commit,
    )


async def _extract_changed_files(worktree_path: str, base_commit: str) -> list[str]:
    """从 worktree git diff 提取变更文件列表。"""
    import asyncio as _asyncio

    from ato.adapters.base import cleanup_process

    proc = await _asyncio.create_subprocess_exec(
        "git",
        "diff",
        "--name-only",
        base_commit,
        "HEAD",
        stdout=_asyncio.subprocess.PIPE,
        stderr=_asyncio.subprocess.PIPE,
        cwd=worktree_path,
    )
    try:
        stdout_bytes, _ = await _asyncio.wait_for(proc.communicate(), timeout=30)
    except TimeoutError:
        await cleanup_process(proc)
        return []
    finally:
        await cleanup_process(proc)

    if proc.returncode != 0:
        return []
    stdout = stdout_bytes.decode() if stdout_bytes else ""
    return [f.strip() for f in stdout.splitlines() if f.strip()]


@app.command("submit")
def submit_cmd(
    story_id: str = typer.Argument(..., help="Story ID"),
    db_path: Path | None = typer.Option(None, "--db-path", help="SQLite 数据库路径"),
    briefing_file: Path | None = typer.Option(
        None,
        "--briefing-file",
        help="Context Briefing JSON 文件路径",
    ),
    config_path: Path | None = typer.Option(None, "--config", help="ato.yaml 配置文件路径"),
) -> None:
    """提交 Interactive Session 完成，标记任务完成并通知 Orchestrator。"""
    resolved_db = db_path or _DEFAULT_DB_PATH
    if not resolved_db.exists():
        typer.echo(
            f"错误：数据库不存在: {resolved_db}。请先运行 `ato init`。",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        asyncio.run(_submit_async(story_id, resolved_db, briefing_file, config_path))
    except click.exceptions.Exit:
        raise
    except Exception as exc:
        typer.echo(f"错误：{exc}", err=True)
        raise typer.Exit(code=1) from exc


async def _submit_async(
    story_id: str,
    db_path: Path,
    briefing_file: Path | None,
    config_path: Path | None,
) -> None:
    """submit 命令的异步实现。"""
    from ato.config import build_phase_definitions, load_config
    from ato.models.db import (
        get_connection,
        get_story,
        get_tasks_by_story,
        update_task_status,
    )

    ato_dir = db_path.parent

    # 1. 验证 story 存在
    db = await get_connection(db_path)
    try:
        story = await get_story(db, story_id)
    finally:
        await db.close()

    if story is None:
        typer.echo(f"Story not found: {story_id}", err=True)
        raise typer.Exit(code=1)

    # 2. 加载配置，确定 interactive phases
    resolved_config = config_path or Path("ato.yaml")
    if not resolved_config.exists():
        # 尝试 ato.yaml.example
        resolved_config = Path("ato.yaml.example")
    if not resolved_config.exists():
        typer.echo("错误：找不到 ato.yaml 配置文件。", err=True)
        raise typer.Exit(code=1)

    settings = load_config(resolved_config)
    interactive_phases = {
        pd.name
        for pd in build_phase_definitions(settings)
        if pd.phase_type == "interactive_session"
    }

    # 3. 验证 story 在 interactive phase
    if story.current_phase not in interactive_phases:
        typer.echo(
            f"Story '{story_id}' 不在 interactive session 阶段 "
            f"（当前: {story.current_phase}，允许: {interactive_phases}）",
            err=True,
        )
        raise typer.Exit(code=1)

    # 4. 读取 sidecar 元数据
    sidecar_path = ato_dir / "sessions" / f"{story_id}.json"
    if not sidecar_path.exists():
        typer.echo(
            f"错误：Session 元数据不存在: {sidecar_path}",
            err=True,
        )
        raise typer.Exit(code=1)

    sidecar_data = json.loads(sidecar_path.read_text())
    base_commit = sidecar_data.get("base_commit")
    if not base_commit:
        typer.echo("错误：Session 元数据缺少 base_commit。", err=True)
        raise typer.Exit(code=1)

    # 5. 验证有新 commit
    worktree_path = story.worktree_path
    if not worktree_path:
        typer.echo("错误：Story 没有关联的 worktree 路径。", err=True)
        raise typer.Exit(code=1)

    has_commits = await _check_new_commits(worktree_path, base_commit, db_path)
    if not has_commits:
        typer.echo(
            f"No commits found in worktree since {base_commit[:8]}",
            err=True,
        )
        raise typer.Exit(code=1)

    # 6. 构造 ContextBriefing
    now = datetime.now(tz=UTC)
    if briefing_file is not None:
        briefing_data = json.loads(briefing_file.read_text())
        # JSON 中 datetime 为字符串，需要先转换
        if isinstance(briefing_data.get("created_at"), str):
            briefing_data["created_at"] = datetime.fromisoformat(briefing_data["created_at"])
        briefing = ContextBriefing.model_validate(briefing_data)
        # 校验 briefing 属于当前 story 和 phase
        if briefing.story_id != story_id:
            typer.echo(
                f"错误：briefing 的 story_id ({briefing.story_id}) "
                f"与当前 story ({story_id}) 不匹配。",
                err=True,
            )
            raise typer.Exit(code=1)
        if briefing.phase != story.current_phase:
            typer.echo(
                f"错误：briefing 的 phase ({briefing.phase}) "
                f"与当前 phase ({story.current_phase}) 不匹配。",
                err=True,
            )
            raise typer.Exit(code=1)
        if briefing.task_type != story.current_phase:
            typer.echo(
                f"错误：briefing 的 task_type ({briefing.task_type}) "
                f"与当前 phase ({story.current_phase}) 不匹配。",
                err=True,
            )
            raise typer.Exit(code=1)
    else:
        # 交互式输入——最小默认值
        agent_notes = typer.prompt("输入工作备注（可选，直接回车跳过）", default="")
        key_decisions_str = typer.prompt("输入关键决策（逗号分隔，可选）", default="")
        key_decisions = (
            [d.strip() for d in key_decisions_str.split(",") if d.strip()]
            if key_decisions_str
            else []
        )
        # 从 worktree git diff 自动提取变更文件
        artifacts = await _extract_changed_files(worktree_path, base_commit)
        briefing = ContextBriefing(
            story_id=story_id,
            phase=story.current_phase,
            task_type=story.current_phase,
            artifacts_produced=artifacts,
            key_decisions=key_decisions,
            agent_notes=agent_notes,
            created_at=now,
        )

    # 7. 更新 task 状态
    # 用 sidecar 中的 pid 精确匹配 task，避免多个 running task 时猜错
    sidecar_pid = sidecar_data.get("pid")
    db = await get_connection(db_path)
    try:
        tasks = await get_tasks_by_story(db, story_id)
        # 收集当前 phase 的 running task 候选
        candidates = [t for t in tasks if t.status == "running" and t.phase == story.current_phase]
        running_task = None
        if sidecar_pid is not None:
            for t in candidates:
                if t.pid == sidecar_pid:
                    running_task = t
                    break
        # PID 不匹配时：仅在唯一候选时 fallback，多候选则报错
        if running_task is None:
            if len(candidates) == 1:
                running_task = candidates[0]
            elif len(candidates) > 1:
                pids = [str(t.pid) for t in candidates]
                typer.echo(
                    f"错误：存在 {len(candidates)} 个 running interactive task "
                    f"(PIDs: {', '.join(pids)})，但 sidecar PID ({sidecar_pid}) "
                    f"未匹配到任何一个。请检查 session 元数据。",
                    err=True,
                )
                raise typer.Exit(code=1)

        if running_task is None:
            typer.echo("错误：未找到运行中的 interactive task。", err=True)
            raise typer.Exit(code=1)

        await update_task_status(
            db,
            running_task.task_id,
            "completed",
            context_briefing=briefing.model_dump_json(),
            completed_at=now,
        )
    finally:
        await db.close()

    # 8. 发送 nudge
    pid_path = ato_dir / "orchestrator.pid"
    _send_nudge_safe(pid_path)

    typer.echo(f"✅ Story '{story_id}' interactive session 已完成。")


# ---------------------------------------------------------------------------
# ato approvals — 审批队列查看
# ---------------------------------------------------------------------------

# 审批类型图标映射
_APPROVAL_TYPE_ICONS: dict[str, str] = {
    "merge_authorization": "🔀",
    "session_timeout": "⏱",
    "crash_recovery": "↩",
    "blocking_abnormal": "⚠",
    "budget_exceeded": "💰",
    "regression_failure": "✖",
    "convergent_loop_escalation": "🔄",
    "batch_confirmation": "📦",
    "timeout": "⏳",
    "precommit_failure": "🔧",
    "needs_human_review": "👁",
}


def _approval_summary(approval_type: str, payload: str | None) -> str:
    """从 approval_type + payload 生成确定性摘要。"""
    payload_dict: dict[str, object] = {}
    if payload:
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            payload_dict = json.loads(payload)

    templates: dict[str, str] = {
        "merge_authorization": "Merge 授权请求",
        "session_timeout": "Interactive session 超时",
        "crash_recovery": "崩溃恢复决策",
        "blocking_abnormal": "Blocking 异常数量超阈值",
        "budget_exceeded": "预算超限",
        "regression_failure": "回归测试失败",
        "convergent_loop_escalation": "Convergent Loop 需人工介入",
        "batch_confirmation": "Batch 确认",
        "timeout": "任务超时",
        "precommit_failure": "Pre-commit 检查失败",
        "needs_human_review": "需要人工审阅",
    }
    summary = templates.get(approval_type, approval_type)

    # 附加关键 payload 信息
    if approval_type == "session_timeout" and "elapsed_seconds" in payload_dict:
        elapsed = payload_dict["elapsed_seconds"]
        summary += f" ({elapsed}s)"
    elif approval_type == "blocking_abnormal" and "blocking_count" in payload_dict:
        count = payload_dict["blocking_count"]
        threshold = payload_dict.get("threshold", "?")
        summary += f" ({count}/{threshold})"
    elif approval_type == "crash_recovery" and "phase" in payload_dict:
        summary += f" (phase: {payload_dict['phase']})"

    return summary


@app.command("approvals")
def approvals_cmd(
    db_path: Path | None = typer.Option(None, "--db-path", help="SQLite 数据库路径"),
    output_json: bool = typer.Option(False, "--json", help="JSON 格式输出"),
) -> None:
    """查看审批队列。"""
    resolved_db = db_path or _DEFAULT_DB_PATH
    if not resolved_db.exists():
        typer.echo(
            f"错误：数据库不存在: {resolved_db}。请先运行 `ato init`。",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        asyncio.run(_approvals_async(resolved_db, output_json))
    except click.exceptions.Exit:
        raise
    except Exception as exc:
        typer.echo(f"错误：{exc}", err=True)
        raise typer.Exit(code=1) from exc


async def _approvals_async(db_path: Path, output_json: bool) -> None:
    """approvals 命令的异步实现。"""
    from ato.models.db import get_connection, get_pending_approvals

    db = await get_connection(db_path)
    try:
        pending = await get_pending_approvals(db)
    finally:
        await db.close()

    if not pending:
        if output_json:
            typer.echo(json.dumps({"approvals": [], "message": "无待处理审批"}))
        else:
            typer.echo("✔ 无待处理审批")
        return

    if output_json:
        data = {
            "approvals": [
                {
                    "approval_id": a.approval_id,
                    "story_id": a.story_id,
                    "approval_type": a.approval_type,
                    "summary": _approval_summary(a.approval_type, a.payload),
                    "recommended_action": a.recommended_action,
                    "risk_level": a.risk_level,
                    "created_at": a.created_at.isoformat(),
                }
                for a in pending
            ]
        }
        typer.echo(json.dumps(data, ensure_ascii=False))
        return

    from rich.table import Table

    table = Table(title="待处理审批")
    table.add_column("类型", width=4)
    table.add_column("ID", width=8)
    table.add_column("Story", width=20)
    table.add_column("摘要", min_width=20)
    table.add_column("推荐", width=12)
    table.add_column("风险", width=6)
    table.add_column("创建时间", width=16)

    for a in pending:
        icon = _APPROVAL_TYPE_ICONS.get(a.approval_type, "?")
        short_id = a.approval_id[:8]
        summary = _approval_summary(a.approval_type, a.payload)
        recommended = a.recommended_action or "-"
        risk = a.risk_level or "-"
        created = a.created_at.strftime("%m-%d %H:%M")
        table.add_row(icon, short_id, a.story_id, summary, recommended, risk, created)

    _console.print(table)


# ---------------------------------------------------------------------------
# ato approve — 审批决策提交
# ---------------------------------------------------------------------------


# 二元审批关键字
_BINARY_DECISIONS = {"approve", "reject"}

# 无 payload.options 时的默认合法选项（按 approval_type）
_DEFAULT_VALID_OPTIONS: dict[str, list[str]] = {
    "merge_authorization": ["approve", "reject"],
    "session_timeout": ["restart", "resume", "abandon"],
    "crash_recovery": ["restart", "resume", "abandon"],
    "blocking_abnormal": ["confirm_fix", "human_review"],
    "budget_exceeded": ["increase_budget", "reject"],
    "regression_failure": ["fix_forward", "reject"],
    "timeout": ["continue_waiting", "abandon"],
    "convergent_loop_escalation": ["retry", "skip", "escalate"],
    "batch_confirmation": ["confirm", "reject"],
    "precommit_failure": ["retry", "skip"],
    "needs_human_review": ["retry", "skip", "escalate"],
}


def _extract_valid_options(approval: object) -> list[str]:
    """从 approval 的 payload.options 提取合法决策选项。

    优先使用 payload 中定义的 options（创建时可自定义），
    fallback 到 _DEFAULT_VALID_OPTIONS。
    """
    # approval 是 ApprovalRecord，但为避免导入循环用 duck typing
    payload_str = getattr(approval, "payload", None)
    approval_type = getattr(approval, "approval_type", "")

    if payload_str:
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            payload = json.loads(payload_str)
            options = payload.get("options")
            if isinstance(options, list) and all(isinstance(o, str) for o in options):
                return options

    return _DEFAULT_VALID_OPTIONS.get(approval_type, [])


@app.command("approve")
def approve_cmd(
    approval_id: str = typer.Argument(..., help="Approval ID（前缀 ≥4 字符）"),
    decision: str = typer.Option(..., "--decision", "-d", help="决策选项"),
    reason: str | None = typer.Option(None, "--reason", "-r", help="决策理由（可选）"),
    db_path: Path | None = typer.Option(None, "--db-path", help="SQLite 数据库路径"),
) -> None:
    """提交审批决策。"""
    resolved_db = db_path or _DEFAULT_DB_PATH
    if not resolved_db.exists():
        typer.echo(
            f"错误：数据库不存在: {resolved_db}。请先运行 `ato init`。",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        asyncio.run(_approve_async(resolved_db, approval_id, decision, reason))
    except click.exceptions.Exit:
        raise
    except Exception as exc:
        typer.echo(f"错误：{exc}", err=True)
        raise typer.Exit(code=1) from exc


async def _approve_async(
    db_path: Path,
    approval_id_prefix: str,
    decision: str,
    reason: str | None,
) -> None:
    """approve 命令的异步实现。"""
    from ato.models.db import (
        get_approval_by_id,
        get_connection,
        update_approval_decision,
    )

    db = await get_connection(db_path)
    try:
        # 查询 approval
        try:
            approval = await get_approval_by_id(db, approval_id_prefix)
        except ValueError as exc:
            typer.echo(f"查询失败: {exc}", err=True)
            raise typer.Exit(code=1) from exc

        # 验证 pending
        if approval.status != "pending":
            typer.echo(
                f"此审批已处理 (status={approval.status})。\n"
                "选项: 运行 `ato approvals` 查看待处理审批。",
                err=True,
            )
            raise typer.Exit(code=1)

        # 校验 decision 合法性
        valid_options = _extract_valid_options(approval)
        if valid_options and decision not in valid_options:
            typer.echo(
                f"无效的决策选项: '{decision}'。\n该审批的合法选项: {', '.join(valid_options)}",
                err=True,
            )
            raise typer.Exit(code=1)

        # 解析 status 写入规则
        now = datetime.now(tz=UTC)
        if decision in _BINARY_DECISIONS:
            write_status = "approved" if decision == "approve" else "rejected"
        else:
            # 多选审批统一写 approved
            write_status = "approved"

        await update_approval_decision(
            db,
            approval.approval_id,
            status=write_status,
            decision=decision,
            decision_reason=reason,
            decided_at=now,
        )
        await db.commit()
    finally:
        await db.close()

    # 发送 nudge
    pid_path = db_path.parent / "orchestrator.pid"
    _send_nudge_safe(pid_path)

    # 确认输出
    icon = _APPROVAL_TYPE_ICONS.get(approval.approval_type, "?")
    _console.print(
        f"{icon} 审批已提交: {approval.approval_id[:8]}... → {decision} ({write_status})"
    )
