"""cli — CLI 入口点。"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import time
from pathlib import Path

import click.exceptions
import structlog
import typer
from rich.console import Console
from rich.text import Text

from ato.models.schemas import CheckResult, StoryRecord
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
        summary = Text(
            f"结果: 就绪（{warn_count} 警告, {info_count} 信息）", style="yellow"
        )
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
        preflight_results = asyncio.run(
            run_preflight(Path.cwd(), resolved_db, include_auth=False)
        )
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
