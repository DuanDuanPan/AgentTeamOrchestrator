"""cli — CLI 入口点。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
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
from ato.models.schemas import (
    APPROVAL_DEFAULT_VALID_OPTIONS as _DEFAULT_VALID_OPTIONS,
)
from ato.models.schemas import APPROVAL_TYPE_ICONS, CheckResult, ContextBriefing, StoryRecord
from ato.preflight import run_preflight

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

app = typer.Typer(name="ato", help="Agent Team Orchestrator")

# ---------------------------------------------------------------------------
# 退出码常量
# ---------------------------------------------------------------------------

EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_ENV_ERROR = 2

# ---------------------------------------------------------------------------
# 统一错误格式
# ---------------------------------------------------------------------------


def _format_cli_error(what: str, options: str | list[str]) -> str:
    """生成统一 CLI 错误消息。

    格式：
      发生了什么：<描述>
      你的选项：<恢复操作>
    """
    opts = " / ".join(options) if isinstance(options, list) else options
    return f"发生了什么：{what}\n你的选项：{opts}"


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


def _derive_project_root(db_path: Path) -> Path:
    """从 db_path 推导项目根目录。

    标准布局 ``<project>/.ato/state.db`` → 祖父目录即项目根。
    自定义 db（同级目录有 ``ato.yaml``）→ db 所在目录。
    回退到当前工作目录。
    """
    # 标准布局: <project>/.ato/state.db
    grandparent = db_path.parent.parent
    if db_path.parent.name == ".ato" and grandparent.is_dir():
        return grandparent

    # 自定义 db: db 同级目录有 ato.yaml
    if (db_path.parent / "ato.yaml").is_file():
        return db_path.parent

    return Path.cwd()


def _resolve_config_path(
    explicit: Path | None,
    db_path: Path,
) -> Path | None:
    """自动发现 ato.yaml 配置文件。

    搜索链（首个存在的胜出）：
    1. 显式 ``--config`` 路径（直接返回，不检查存在性——由调用方校验）
    2. ``_derive_project_root(db_path)`` 推导出的项目根下的 ``ato.yaml``
    3. CWD 下 ``ato.yaml``（仅当推导结果为 CWD 以外目录时才尝试）

    全部找不到时返回 ``None``。
    """
    if explicit is not None:
        return explicit
    project_root = _derive_project_root(db_path)
    project_config = project_root / "ato.yaml"
    if project_config.exists():
        return project_config
    # 仅当 project_root 不是 cwd 时，再尝试 cwd 回退
    cwd_config = Path("ato.yaml")
    if project_root != Path.cwd() and cwd_config.exists():
        return cwd_config
    return None


# Status / Phase → 显示图标
_STATUS_ICONS: dict[str, str] = {
    "done": "✅",
    "blocked": "✖",
}
_PHASE_ICONS: dict[str, str] = {
    "queued": "⏳",
    "planning": "📋",
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
    "ato_yaml": "`ato init` 将自动从 `ato.yaml.example` 生成；若失败请检查 example 是否存在",
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
        typer.echo(_format_cli_error(str(exc), "检查错误信息并重试"), err=True)
        raise typer.Exit(code=EXIT_ERROR) from exc


def _ensure_ato_yaml(project_path: Path) -> None:
    """确保 project_path 下存在 ato.yaml。

    若已存在则跳过；否则从 ato.yaml.example 复制。
    example 也不存在时以非零退出码终止。
    """
    ato_yaml = project_path / "ato.yaml"
    if ato_yaml.is_file():
        typer.echo("ℹ 使用已有配置文件: ato.yaml")
        return

    example = project_path / "ato.yaml.example"
    if not example.is_file():
        typer.echo(
            _format_cli_error(
                "ato.yaml.example 不存在，无法自动生成配置",
                "在项目根目录放置 ato.yaml.example 后重新运行 `ato init`",
            ),
            err=True,
        )
        raise typer.Exit(code=EXIT_ERROR)

    shutil.copy2(example, ato_yaml)
    typer.echo("✔ 已从 ato.yaml.example 生成 ato.yaml，可按需调整后运行 `ato start`")


async def _init_async(project_path: Path, db_path: Path) -> None:
    """执行 Preflight 检查并渲染结果。"""
    results = await run_preflight(project_path, db_path, include_auth=True)

    render_preflight_results(results)

    has_halt = any(r.status == "HALT" for r in results)
    if has_halt:
        typer.echo(
            _format_cli_error("环境检查未通过", "根据上方提示修复后重新运行 `ato init`"),
            err=True,
        )
        raise typer.Exit(code=EXIT_ENV_ERROR)

    typer.prompt(
        "按 Enter 继续初始化，或 Ctrl-C 取消",
        default="",
        show_default=False,
    )

    # 配置自动生成：preflight 已落库，在最终成功提示之前生成 ato.yaml
    _ensure_ato_yaml(project_path)

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
            _format_cli_error(f"数据库不存在: {resolved_db}", "运行 `ato init` 初始化项目"),
            err=True,
        )
        raise typer.Exit(code=EXIT_ENV_ERROR)

    # 检查 epics
    if not resolved_epics.exists():
        typer.echo(
            _format_cli_error(
                f"Epics 文件不存在: {resolved_epics}",
                "确认 epics 文件路径或使用 --epics-file 指定",
            ),
            err=True,
        )
        raise typer.Exit(code=EXIT_ENV_ERROR)

    try:
        asyncio.run(_batch_select_async(resolved_db, resolved_epics, max_stories, story_ids))
    except click.exceptions.Exit:
        raise
    except Exception as exc:
        typer.echo(_format_cli_error(str(exc), "检查错误信息并重试"), err=True)
        raise typer.Exit(code=EXIT_ERROR) from exc


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
                _format_cli_error(
                    f"已存在 active batch ({existing.batch_id[:8]}...)",
                    "先完成或取消当前 batch 后再创建新 batch",
                ),
                err=True,
            )
            raise typer.Exit(code=EXIT_ERROR)

        # 解析 epics（使用 canonical key map）
        epics_info = load_epics(epics_path, canonical_key_map=key_map)
        if not epics_info:
            typer.echo(
                _format_cli_error("未从 epics 文件中解析出任何 story", "检查 epics 文件格式"),
                err=True,
            )
            raise typer.Exit(code=EXIT_ERROR)

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
                    _format_cli_error(
                        f"以下 story keys 不在 epics 中: {unmatched}",
                        "检查 story key 拼写或运行 `ato batch select` 查看可用 stories",
                    ),
                    err=True,
                )
                raise typer.Exit(code=EXIT_ERROR)
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
                    typer.echo(
                        _format_cli_error("无效输入", "输入数字编号（如 1,3,5）"),
                        err=True,
                    )
                    raise typer.Exit(code=EXIT_ERROR) from None
                # 去重（保留首次出现顺序）+ 范围校验
                seen: set[int] = set()
                selected_indices = []
                for idx in raw_indices:
                    if idx < 0 or idx >= len(proposal.stories):
                        typer.echo(
                            _format_cli_error(
                                f"编号 {idx + 1} 超出范围 (1-{len(proposal.stories)})",
                                "输入有效编号",
                            ),
                            err=True,
                        )
                        raise typer.Exit(code=EXIT_ERROR)
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
            _format_cli_error(f"数据库不存在: {resolved_db}", "运行 `ato init` 初始化项目"),
            err=True,
        )
        raise typer.Exit(code=EXIT_ENV_ERROR)

    try:
        asyncio.run(_batch_status_async(resolved_db, output_json))
    except click.exceptions.Exit:
        raise
    except Exception as exc:
        typer.echo(_format_cli_error(str(exc), "检查错误信息并重试"), err=True)
        raise typer.Exit(code=EXIT_ERROR) from exc


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
        typer.echo(
            _format_cli_error("Orchestrator 已在运行中", "运行 `ato stop` 停止后重试"),
            err=True,
        )
        raise typer.Exit(code=EXIT_ERROR)

    # 初始化日志
    from ato.logging import configure_logging

    configure_logging(log_dir=str(resolved_db.parent / "logs"))

    # 从 db_path 推���项目根（而非硬编码 cwd）
    project_root = _derive_project_root(resolved_db)

    # Preflight 快速检查
    from ato.preflight import run_preflight

    try:
        preflight_results = asyncio.run(
            run_preflight(project_root, resolved_db, include_auth=False)
        )
    except click.exceptions.Exit:
        raise
    except Exception as exc:
        typer.echo(
            _format_cli_error(f"Preflight 检查失败: {exc}", "运行 `ato init` 检查环境"),
            err=True,
        )
        raise typer.Exit(code=EXIT_ENV_ERROR) from exc

    if any(r.status == "HALT" for r in preflight_results):
        typer.echo(
            _format_cli_error(
                "Preflight 检查存在 HALT 项，无法启动",
                "运行 `ato init` 查看详细检查结果并修复",
            ),
            err=True,
        )
        raise typer.Exit(code=EXIT_ENV_ERROR)

    # 加载配置：显式 --config 优先，其次从 db 推导的项目根发现 ato.yaml
    from ato.config import load_config

    resolved_config = _resolve_config_path(config_path, resolved_db)
    if resolved_config is None:
        typer.echo(
            _format_cli_error(
                "未找到 ato.yaml 配置文件",
                "运行 `ato init` 生成配置，或使用 `--config` 指定路径",
            ),
            err=True,
        )
        raise typer.Exit(code=EXIT_ENV_ERROR)
    try:
        settings = load_config(resolved_config)
    except Exception as exc:
        typer.echo(
            _format_cli_error(f"配置加载失败: {exc}", "检查 ato.yaml 配置文件"),
            err=True,
        )
        raise typer.Exit(code=EXIT_ENV_ERROR) from exc

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
        typer.echo(
            _format_cli_error("无权向 Orchestrator 进程发送信号", "使用 sudo 或以正确用户运行"),
            err=True,
        )
        raise typer.Exit(code=EXIT_ERROR) from exc

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
        typer.echo(
            _format_cli_error("无权强制终止 Orchestrator 进程", "使用 sudo 或以正确用户运行"),
            err=True,
        )
        raise typer.Exit(code=EXIT_ERROR) from exc

    # 等待 SIGKILL 生效
    time.sleep(1.0)
    try:
        os.kill(pid, 0)
        typer.echo(
            _format_cli_error("Orchestrator 进程仍未退出", "手动检查进程状态 (kill -9)"),
            err=True,
        )
        raise typer.Exit(code=EXIT_ERROR)
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
            _format_cli_error(f"数据库不存在: {resolved_db}", "运行 `ato init` 初始化项目"),
            err=True,
        )
        raise typer.Exit(code=EXIT_ENV_ERROR)

    try:
        asyncio.run(_plan_async(story_id, resolved_db, config_path))
    except click.exceptions.Exit:
        raise
    except click.exceptions.Abort:
        raise
    except Exception as exc:
        typer.echo(_format_cli_error(str(exc), "检查错误信息并重试"), err=True)
        raise typer.Exit(code=EXIT_ERROR) from exc


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
        typer.echo(
            _format_cli_error(
                f"Story 不存在: {story_id}",
                "运行 `ato batch status` 查看可用 stories",
            ),
            err=True,
        )
        raise typer.Exit(code=EXIT_ERROR)

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
    """自动发现 TUI 使用的 ato.yaml（委托给共享实现）。"""
    return _resolve_config_path(explicit, db_path)


@app.command("tui")
def tui_cmd(
    db_path: Path | None = typer.Option(None, "--db-path", help="SQLite 数据库路径"),
    config_path: Path | None = typer.Option(None, "--config", help="ato.yaml 配置文件路径"),
) -> None:
    """启动 TUI 指挥台，连接运行中的 Orchestrator。"""
    resolved_db = db_path or _DEFAULT_DB_PATH

    if not resolved_db.exists():
        typer.echo(
            _format_cli_error("数据库未找到", "运行 `ato init` 初始化项目"),
            err=True,
        )
        raise typer.Exit(code=EXIT_ENV_ERROR)

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
            typer.echo(
                _format_cli_error(f"配置加载失败: {exc}", "检查 ato.yaml 配置文件"),
                err=True,
            )
            raise typer.Exit(code=EXIT_ENV_ERROR) from exc
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
            _format_cli_error(f"数据库不存在: {resolved_db}", "运行 `ato init` 初始化项目"),
            err=True,
        )
        raise typer.Exit(code=EXIT_ENV_ERROR)

    try:
        asyncio.run(_submit_async(story_id, resolved_db, briefing_file, config_path))
    except click.exceptions.Exit:
        raise
    except Exception as exc:
        typer.echo(_format_cli_error(str(exc), "检查错误信息并重试"), err=True)
        raise typer.Exit(code=EXIT_ERROR) from exc


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
        typer.echo(
            _format_cli_error(
                f"Story 不存在: {story_id}",
                "运行 `ato batch status` 查看可用 stories",
            ),
            err=True,
        )
        raise typer.Exit(code=EXIT_ERROR)

    # 2. 加载配置，确定 interactive phases
    resolved_config = config_path or Path("ato.yaml")
    if not resolved_config.exists():
        # 尝试 ato.yaml.example
        resolved_config = Path("ato.yaml.example")
    if not resolved_config.exists():
        typer.echo(
            _format_cli_error("找不到 ato.yaml 配置文件", "创建 ato.yaml 或使用 --config 指定路径"),
            err=True,
        )
        raise typer.Exit(code=EXIT_ENV_ERROR)

    settings = load_config(resolved_config)
    interactive_phases = {
        pd.name
        for pd in build_phase_definitions(settings)
        if pd.phase_type == "interactive_session"
    }

    # 3. 验证 story 在 interactive phase
    if story.current_phase not in interactive_phases:
        typer.echo(
            _format_cli_error(
                f"Story '{story_id}' 不在 interactive session 阶段（当前: {story.current_phase}）",
                f"等待 story 进入 interactive 阶段后再提交（允许: {interactive_phases}）",
            ),
            err=True,
        )
        raise typer.Exit(code=EXIT_ERROR)

    # 4. 读取 sidecar 元数据
    sidecar_path = ato_dir / "sessions" / f"{story_id}.json"
    if not sidecar_path.exists():
        typer.echo(
            _format_cli_error(
                f"Session 元数据不存在: {sidecar_path}",
                "确认 interactive session 已启动",
            ),
            err=True,
        )
        raise typer.Exit(code=EXIT_ERROR)

    sidecar_data = json.loads(sidecar_path.read_text())
    base_commit = sidecar_data.get("base_commit")
    if not base_commit:
        typer.echo(
            _format_cli_error("Session 元数据缺少 base_commit", "检查 session 元数据文件"),
            err=True,
        )
        raise typer.Exit(code=EXIT_ERROR)

    # 5. 验证有新 commit
    worktree_path = story.worktree_path
    if not worktree_path:
        typer.echo(
            _format_cli_error("Story 没有关联的 worktree 路径", "确认 story 已分配 worktree"),
            err=True,
        )
        raise typer.Exit(code=EXIT_ERROR)

    has_commits = await _check_new_commits(worktree_path, base_commit, db_path)
    if not has_commits:
        typer.echo(
            _format_cli_error(
                f"Worktree 中无新 commit (since {base_commit[:8]})",
                "在 worktree 中完成工作并 commit 后重试",
            ),
            err=True,
        )
        raise typer.Exit(code=EXIT_ERROR)

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
                _format_cli_error(
                    f"briefing 的 story_id ({briefing.story_id}) 与当前 story ({story_id}) 不匹配",
                    "检查 --briefing-file 内容",
                ),
                err=True,
            )
            raise typer.Exit(code=EXIT_ERROR)
        if briefing.phase != story.current_phase:
            typer.echo(
                _format_cli_error(
                    f"briefing phase ({briefing.phase})"
                    f" 与当前 phase ({story.current_phase}) 不匹配",
                    "检查 --briefing-file 内容",
                ),
                err=True,
            )
            raise typer.Exit(code=EXIT_ERROR)
        if briefing.task_type != story.current_phase:
            typer.echo(
                _format_cli_error(
                    f"briefing task_type ({briefing.task_type})"
                    f" 与当前 phase ({story.current_phase}) 不匹配",
                    "检查 --briefing-file 内容",
                ),
                err=True,
            )
            raise typer.Exit(code=EXIT_ERROR)
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
                    _format_cli_error(
                        f"存在 {len(candidates)} 个 running interactive task "
                        f"(PIDs: {', '.join(pids)})，但 sidecar PID ({sidecar_pid}) 未匹配",
                        "检查 session 元数据",
                    ),
                    err=True,
                )
                raise typer.Exit(code=EXIT_ERROR)

        if running_task is None:
            typer.echo(
                _format_cli_error(
                    "未找到运行中的 interactive task",
                    "确认 interactive session 已启动",
                ),
                err=True,
            )
            raise typer.Exit(code=EXIT_ERROR)

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

_APPROVAL_TYPE_ICONS = APPROVAL_TYPE_ICONS


def _approval_summary(approval_type: str, payload: str | None) -> str:
    """从 approval_type + payload 生成确定性摘要。

    委托到 approval_helpers.format_approval_summary() 共享实现。
    """
    from ato.approval_helpers import format_approval_summary

    return format_approval_summary(approval_type, payload)


@app.command("approvals")
def approvals_cmd(
    db_path: Path | None = typer.Option(None, "--db-path", help="SQLite 数据库路径"),
    output_json: bool = typer.Option(False, "--json", help="JSON 格式输出"),
) -> None:
    """查看审批队列。"""
    resolved_db = db_path or _DEFAULT_DB_PATH
    if not resolved_db.exists():
        typer.echo(
            _format_cli_error(f"数据库不存在: {resolved_db}", "运行 `ato init` 初始化项目"),
            err=True,
        )
        raise typer.Exit(code=EXIT_ENV_ERROR)

    try:
        asyncio.run(_approvals_async(resolved_db, output_json))
    except click.exceptions.Exit:
        raise
    except Exception as exc:
        typer.echo(_format_cli_error(str(exc), "检查错误信息并重试"), err=True)
        raise typer.Exit(code=EXIT_ERROR) from exc


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
                    "payload": json.loads(a.payload) if a.payload else None,
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
# ato approval-detail — 审批详情查看（三要素展示）
# ---------------------------------------------------------------------------

# 异常 approval 类型集合（使用 Rich Panel 三要素展示）
_EXCEPTION_APPROVAL_TYPES = {
    "regression_failure",
    "blocking_abnormal",
    "budget_exceeded",
    "timeout",
    "precommit_failure",
    "rebase_conflict",
}


def _extract_impact(approval_type: str, payload_dict: dict[str, object]) -> str:
    """从 approval_type + payload 提取影响范围描述。"""
    if approval_type == "regression_failure":
        # merge queue 已冻结——所有后续 merge 被阻塞
        # blocked_count 由 _handle_regression_failure 写入（waiting entries 数量）
        blocked_count = payload_dict.get("blocked_count")
        if isinstance(blocked_count, (int, float)) and int(blocked_count) > 0:
            return f"merge queue 已冻结，后续 {int(blocked_count)} 个 merge 被阻塞"
        return "merge queue 已冻结，所有后续 merge 被阻塞"
    if approval_type == "blocking_abnormal":
        count = payload_dict.get("blocking_count", "?")
        threshold = payload_dict.get("threshold", "?")
        return f"blocking 数 {count} 超阈值 {threshold}"
    if approval_type == "budget_exceeded":
        spent = payload_dict.get("spent_usd", "?")
        budget = payload_dict.get("budget_usd", "?")
        return f"已消费 ${spent}，预算 ${budget}"
    if approval_type == "timeout":
        elapsed = payload_dict.get("elapsed_seconds", "?")
        return f"任务运行 {elapsed}s 未完成"
    if approval_type == "precommit_failure":
        return "代码质量检查未通过，需修复后重试"
    if approval_type == "rebase_conflict":
        return "分支冲突需人工解决"
    return "请查看 payload 详情"


def _render_exception_approval(approval: object) -> None:
    """Rich 格式化异常审批三要素展示。"""
    from rich.panel import Panel

    payload_str = getattr(approval, "payload", None)
    approval_type = getattr(approval, "approval_type", "")
    risk_level = getattr(approval, "risk_level", None)
    recommended = getattr(approval, "recommended_action", None)
    approval_id = getattr(approval, "approval_id", "")

    payload_dict: dict[str, object] = {}
    if payload_str:
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            payload_dict = json.loads(payload_str)

    options = payload_dict.get("options")
    if not isinstance(options, list) or not all(isinstance(o, str) for o in options):
        options = _DEFAULT_VALID_OPTIONS.get(approval_type, [])

    if not recommended:
        from ato.models.schemas import APPROVAL_RECOMMENDED_ACTIONS

        recommended = APPROVAL_RECOMMENDED_ACTIONS.get(approval_type, "")

    # 构建内容
    content = Text()
    content.append("发生了什么\n", style="bold")
    content.append(f"  {_approval_summary(approval_type, payload_str)}\n\n")

    content.append("影响范围\n", style="bold")
    content.append(f"  {_extract_impact(approval_type, payload_dict)}\n\n")

    # regression_failure: 显示测试失败摘要（AC3 — 操作者需要看到失败详情来决策）
    test_summary = payload_dict.get("test_output_summary")
    if isinstance(test_summary, str) and test_summary.strip():
        content.append("失败摘要\n", style="bold")
        # 截断显示，避免 Panel 过长
        truncated = test_summary.strip()[:300]
        content.append(f"  {truncated}\n\n")

    content.append("你的选项\n", style="bold")
    for i, opt in enumerate(options, 1):
        marker = "★ " if opt == recommended else "  "
        content.append(f"  {marker}[{i}] {opt}\n")

    # Panel 边框颜色
    border = "red" if risk_level == "high" else "yellow" if risk_level == "medium" else "default"
    icon = _APPROVAL_TYPE_ICONS.get(approval_type, "?")

    panel = Panel(content, title=f"{icon} {approval_type}", border_style=border)
    _console.print(panel)

    # 快捷命令提示
    if recommended and recommended in options:
        short_id = approval_id[:8]
        _console.print(
            Text(f"  💡 ato approve {short_id} --decision {recommended}", style="dim"),
        )


def _render_simple_approval(approval: object) -> None:
    """非异常类型审批的简化展示。"""
    approval_type = getattr(approval, "approval_type", "")
    approval_id = getattr(approval, "approval_id", "")
    story_id = getattr(approval, "story_id", "")
    status = getattr(approval, "status", "")
    recommended = getattr(approval, "recommended_action", "")
    risk_level = getattr(approval, "risk_level", "")
    payload_str = getattr(approval, "payload", None)
    created_at = getattr(approval, "created_at", "")

    icon = _APPROVAL_TYPE_ICONS.get(approval_type, "?")
    summary = _approval_summary(approval_type, payload_str)

    _console.print(f"{icon} [{approval_id[:8]}] {approval_type}")
    _console.print(f"  Story: {story_id}")
    _console.print(f"  摘要: {summary}")
    _console.print(f"  状态: {status} | 风险: {risk_level or '-'} | 推荐: {recommended or '-'}")
    _console.print(f"  创建: {created_at}")

    if recommended:
        options = _extract_valid_options(approval)
        if recommended in options:
            _console.print(
                Text(f"  💡 ato approve {approval_id[:8]} --decision {recommended}", style="dim"),
            )


@app.command("approval-detail")
def approval_detail_cmd(
    approval_id: str = typer.Argument(..., help="Approval ID（前缀 ≥4 字符）"),
    db_path: Path | None = typer.Option(None, "--db-path", help="SQLite 数据库路径"),
) -> None:
    """查看审批详情（三要素展示）。"""
    resolved_db = db_path or _DEFAULT_DB_PATH
    if not resolved_db.exists():
        typer.echo(
            _format_cli_error(f"数据库不存在: {resolved_db}", "运行 `ato init` 初始化项目"),
            err=True,
        )
        raise typer.Exit(code=EXIT_ENV_ERROR)

    try:
        asyncio.run(_approval_detail_async(resolved_db, approval_id))
    except click.exceptions.Exit:
        raise
    except Exception as exc:
        typer.echo(_format_cli_error(str(exc), "检查错误信息并重试"), err=True)
        raise typer.Exit(code=EXIT_ERROR) from exc


async def _approval_detail_async(db_path: Path, approval_id_prefix: str) -> None:
    """approval-detail 命令的异步实现。"""
    from ato.models.db import get_approval_by_id, get_connection

    db = await get_connection(db_path)
    try:
        try:
            approval = await get_approval_by_id(db, approval_id_prefix)
        except ValueError as exc:
            typer.echo(
                _format_cli_error(str(exc), "运行 `ato approvals` 查看待处理审批"),
                err=True,
            )
            raise typer.Exit(code=EXIT_ERROR) from exc
    finally:
        await db.close()

    # 异常类型使用 Rich 三要素展示，其他类型简化展示
    if approval.approval_type in _EXCEPTION_APPROVAL_TYPES:
        _render_exception_approval(approval)
    else:
        _render_simple_approval(approval)


# ---------------------------------------------------------------------------
# ato approve — 审批决策提交
# ---------------------------------------------------------------------------


# 二元审批关键字
_BINARY_DECISIONS = {"approve", "reject"}


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


@app.command("findings")
def findings_cmd(
    story_id: str = typer.Argument(..., help="Story ID"),
    db_path: Path | None = typer.Option(None, "--db-path", help="SQLite 数据库路径"),
    output_json: bool = typer.Option(False, "--json", help="JSON 格式输出"),
) -> None:
    """查看 story 的 finding 跨轮次状态摘要（first_seen_round + current_status）。"""
    resolved_db = db_path or _DEFAULT_DB_PATH
    if not resolved_db.exists():
        typer.echo(
            f"数据库不存在: {resolved_db}\n请先执行 'ato init' 初始化项目。",
            err=True,
        )
        raise typer.Exit(code=1)
    asyncio.run(_findings_impl(resolved_db, story_id, output_json))


async def _findings_impl(db_path: Path, story_id: str, output_json: bool) -> None:
    """findings 命令的异步实现。"""
    from ato.models.db import get_connection, get_finding_trajectory

    db = await get_connection(db_path)
    try:
        trajectory = await get_finding_trajectory(db, story_id)
    finally:
        await db.close()

    if not trajectory:
        if output_json:
            typer.echo(json.dumps({"story_id": story_id, "findings": []}))
        else:
            typer.echo(f"Story {story_id} 无 findings 记录")
        return

    if output_json:
        typer.echo(json.dumps({"story_id": story_id, "findings": trajectory}, ensure_ascii=False))
        return

    from rich.table import Table

    table = Table(title=f"Finding Trajectory — {story_id}")
    table.add_column("ID", width=8)
    table.add_column("Severity", width=10)
    table.add_column("File", width=30)
    table.add_column("Rule", width=12)
    table.add_column("First Seen", width=10)
    table.add_column("Status", width=12)
    table.add_column("Description", width=40)

    for f in trajectory:
        table.add_row(
            f["finding_id"][:8],
            f["severity"],
            f["file_path"],
            f["rule_id"],
            str(f["first_seen_round"]),
            f["current_status"],
            f["description"][:40],
        )

    from rich.console import Console

    Console().print(table)


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
            _format_cli_error(f"数据库不存在: {resolved_db}", "运行 `ato init` 初始化项目"),
            err=True,
        )
        raise typer.Exit(code=EXIT_ENV_ERROR)

    try:
        asyncio.run(_approve_async(resolved_db, approval_id, decision, reason))
    except click.exceptions.Exit:
        raise
    except Exception as exc:
        typer.echo(_format_cli_error(str(exc), "检查错误信息并重试"), err=True)
        raise typer.Exit(code=EXIT_ERROR) from exc


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
            typer.echo(
                _format_cli_error(str(exc), "运行 `ato approvals` 查看待处理审批"),
                err=True,
            )
            raise typer.Exit(code=EXIT_ERROR) from exc

        # 验证 pending
        if approval.status != "pending":
            typer.echo(
                _format_cli_error(
                    f"此审批已处理 (status={approval.status})",
                    "运行 `ato approvals` 查看待处理审批",
                ),
                err=True,
            )
            raise typer.Exit(code=EXIT_ERROR)

        # 校验 decision 合法性
        valid_options = _extract_valid_options(approval)
        if valid_options and decision not in valid_options:
            typer.echo(
                _format_cli_error(
                    f"无效的决策选项: '{decision}'",
                    [f"--decision {o}" for o in valid_options],
                ),
                err=True,
            )
            raise typer.Exit(code=EXIT_ERROR)

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


# ---------------------------------------------------------------------------
# ato uat — UAT 结果提交
# ---------------------------------------------------------------------------


@app.command("uat")
def uat_cmd(
    story_id: str = typer.Argument(..., help="Story ID"),
    result: str = typer.Option(..., "--result", help="pass 或 fail"),
    reason: str = typer.Option("", "--reason", help="失败原因描述"),
    db_path: Path | None = typer.Option(None, "--db-path", help="SQLite 数据库路径"),
) -> None:
    """提交 UAT 测试结果。"""
    resolved_db = db_path or _DEFAULT_DB_PATH
    if not resolved_db.exists():
        typer.echo(
            _format_cli_error(f"数据库不存在: {resolved_db}", "运行 `ato init` 初始化项目"),
            err=True,
        )
        raise typer.Exit(code=EXIT_ENV_ERROR)

    if result not in ("pass", "fail"):
        typer.echo(
            _format_cli_error(
                f"--result 必须是 'pass' 或 'fail'，收到: '{result}'",
                "--result pass / --result fail",
            ),
            err=True,
        )
        raise typer.Exit(code=EXIT_ERROR)

    if result == "fail" and not reason:
        typer.echo(
            _format_cli_error("UAT 失败时必须提供原因", "添加 --reason '描述失败原因'"),
            err=True,
        )
        raise typer.Exit(code=EXIT_ERROR)

    try:
        asyncio.run(_uat_async(story_id, result, reason, resolved_db))
    except click.exceptions.Exit:
        raise
    except Exception as exc:
        typer.echo(_format_cli_error(str(exc), "检查错误信息并重试"), err=True)
        raise typer.Exit(code=EXIT_ERROR) from exc


async def _uat_async(
    story_id: str,
    result: str,
    reason: str,
    db_path: Path,
) -> None:
    """uat 命令的异步实现。"""
    from ato.models.db import (
        get_connection,
        get_story,
        get_tasks_by_story,
        update_task_status,
    )

    ato_dir = db_path.parent
    now = datetime.now(tz=UTC)

    # 1. 验证 story 存在
    db = await get_connection(db_path)
    try:
        story = await get_story(db, story_id)
    finally:
        await db.close()

    if story is None:
        typer.echo(
            _format_cli_error(
                f"Story 不存在: {story_id}",
                "运行 `ato batch status` 查看可用 stories",
            ),
            err=True,
        )
        raise typer.Exit(code=EXIT_ERROR)

    # 2. 验证 story 在 uat 阶段
    if story.current_phase != "uat":
        typer.echo(
            _format_cli_error(
                f"Story '{story_id}' 不在 UAT 阶段（当前: {story.current_phase}）",
                "等待 story 进入 UAT 阶段后重试",
            ),
            err=True,
        )
        raise typer.Exit(code=EXIT_ERROR)

    # 3. 构造 UAT 结果 payload
    uat_payload = {
        "uat_result": result,
        "reason": reason,
        "submitted_at": now.isoformat(),
    }

    if result == "pass":
        # pass 路径：标记 task 为 completed，Orchestrator 检测完成后触发 uat_pass
        db = await get_connection(db_path)
        try:
            tasks = await get_tasks_by_story(db, story_id)
            running_task = None
            for t in tasks:
                if t.status == "running" and t.phase == "uat":
                    running_task = t
                    break

            if running_task is None:
                typer.echo(
                    _format_cli_error(
                        "未找到运行中的 UAT task",
                        "确认 Orchestrator 已启动且 story 在 UAT 阶段",
                    ),
                    err=True,
                )
                raise typer.Exit(code=EXIT_ERROR)

            await update_task_status(
                db,
                running_task.task_id,
                "completed",
                context_briefing=json.dumps(uat_payload, ensure_ascii=False),
                completed_at=now,
            )
        finally:
            await db.close()

        pid_path = ato_dir / "orchestrator.pid"
        _send_nudge_safe(pid_path)
        typer.echo(f"✅ Story '{story_id}' UAT 通过，进入 merge 阶段。")

    else:
        # fail 路径：标记 task 为 failed + uat_fail_requested，
        # 由 Orchestrator 在 _poll_cycle 中检测并通过自己的 TQ 执行转换。
        # 不在 CLI 进程中创建 TransitionQueue，避免状态机缓存分叉。
        db = await get_connection(db_path)
        try:
            tasks = await get_tasks_by_story(db, story_id)
            running_task = None
            for t in tasks:
                if t.status == "running" and t.phase == "uat":
                    running_task = t
                    break

            if running_task is None:
                typer.echo(
                    _format_cli_error(
                        "未找到运行中的 UAT task",
                        "确认 Orchestrator 已启动且 story 在 UAT 阶段",
                    ),
                    err=True,
                )
                raise typer.Exit(code=EXIT_ERROR)

            await update_task_status(
                db,
                running_task.task_id,
                "failed",
                context_briefing=json.dumps(uat_payload, ensure_ascii=False),
                error_message=f"uat_fail: {reason}",
                expected_artifact="uat_fail_requested",
                completed_at=now,
            )
        finally:
            await db.close()

        pid_path = ato_dir / "orchestrator.pid"
        _send_nudge_safe(pid_path)
        typer.echo(
            f"✅ Story '{story_id}' UAT 未通过，退回 fix 阶段重新进入质量门控。原因: {reason}"
        )


# ---------------------------------------------------------------------------
# ato history — 执行历史查看 (Story 5.2)
# ---------------------------------------------------------------------------


@app.command("history")
def history_command(
    story_id: str = typer.Argument(help="要查看历史的 Story ID"),
    db_path: Path = typer.Option(
        None,
        "--db-path",
        help="SQLite 数据库路径（默认 .ato/state.db）",
    ),
) -> None:
    """查看某个 Story 的完整执行历史时间轴。"""
    resolved_db = db_path or _DEFAULT_DB_PATH
    if not resolved_db.exists():
        typer.echo(
            _format_cli_error(
                "数据库文件不存在",
                "运行 `ato init` 初始化项目",
            ),
            err=True,
        )
        raise typer.Exit(code=EXIT_ENV_ERROR)

    try:
        asyncio.run(_history_async(story_id, resolved_db))
    except click.exceptions.Exit:
        raise
    except Exception as exc:
        logger.error("history_command_failed", error=str(exc))
        typer.echo(
            _format_cli_error(str(exc), "检查数据库状态或联系管理员"),
            err=True,
        )
        raise typer.Exit(code=EXIT_ERROR) from exc


async def _history_async(story_id: str, db_path: Path) -> None:
    """ato history 的异步实现。"""
    from datetime import UTC, datetime

    from rich.table import Table

    from ato.models.db import get_connection, get_story, get_tasks_by_story

    db = await get_connection(db_path)
    try:
        story = await get_story(db, story_id)
        if story is None:
            typer.echo(
                _format_cli_error(
                    f"Story 不存在: {story_id}",
                    "运行 `ato batch status` 查看可用 stories",
                ),
                err=True,
            )
            raise typer.Exit(code=EXIT_ERROR)

        tasks = await get_tasks_by_story(db, story_id)
    finally:
        await db.close()

    con = Console()

    if not tasks:
        con.print(f"Story {story_id} 暂无执行记录。")
        return

    con.print()
    con.print(Text(f"Story {story_id} 执行历史", style="bold"))
    con.rule()

    table = Table(show_header=True, header_style="bold")
    table.add_column("时间")
    table.add_column("Phase")
    table.add_column("Role")
    table.add_column("Tool")
    table.add_column("状态")
    table.add_column("Artifact")
    table.add_column("耗时")
    table.add_column("成本")

    now = datetime.now(tz=UTC)
    total_duration_ms = 0
    total_cost = 0.0
    task_count = 0

    for task in tasks:
        task_count += 1

        # 时间格式化
        time_str = _format_task_time(task.started_at, now)

        # 状态颜色图标
        status_display = _format_task_status(task.status)

        # artifact 提取
        artifact = _extract_artifact(task)

        # 耗时
        duration_str = "-"
        if task.duration_ms is not None:
            total_duration_ms += task.duration_ms
            duration_str = _format_duration(task.duration_ms)

        # 成本
        cost_str = "-"
        if task.cost_usd is not None:
            total_cost += task.cost_usd
            cost_str = f"${task.cost_usd:.2f}"

        table.add_row(
            time_str,
            task.phase,
            task.role,
            task.cli_tool,
            status_display,
            artifact,
            duration_str,
            cost_str,
        )

    # 汇总行
    table.add_section()
    table.add_row(
        "汇总",
        f"{task_count} 个任务",
        "",
        "",
        "",
        "",
        _format_duration(total_duration_ms) if total_duration_ms > 0 else "-",
        f"${total_cost:.2f}" if total_cost > 0 else "-",
    )

    con.print(table)


def _format_task_time(started_at: datetime | None, now: datetime) -> str:
    """格式化任务时间：同日 HH:MM:SS，跨日 MM-DD HH:MM。"""
    if started_at is None:
        return "-"
    if started_at.date() == now.date():
        return started_at.strftime("%H:%M:%S")
    return started_at.strftime("%m-%d %H:%M")


def _format_task_status(status: str) -> str:
    """任务状态带颜色图标。"""
    status_map = {
        "completed": "[green]✔[/green]",
        "failed": "[red]✖[/red]",
        "running": "[cyan]●[/cyan]",
        "pending": "○",
        "paused": "⏸",
    }
    return status_map.get(status, status)


def _extract_artifact(task: object) -> str:
    """从 task 提取 artifact 展示字符串。

    优先读取 context_briefing.artifacts_produced，fallback 到 expected_artifact。
    """
    # 使用 getattr 以支持 TaskRecord 和测试 mock
    context_briefing = getattr(task, "context_briefing", None)
    if context_briefing:
        try:
            data = json.loads(context_briefing)
            artifacts = data.get("artifacts_produced", [])
            if artifacts:
                return ", ".join(artifacts)
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass

    expected = getattr(task, "expected_artifact", None)
    return expected or "-"


def _format_duration(ms: int) -> str:
    """将毫秒格式化为人可读的耗时字符串。"""
    if ms < 1000:
        return f"{ms}ms"
    seconds = ms // 1000
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    remaining_seconds = seconds % 60
    return f"{minutes}m{remaining_seconds:02d}s"


# ---------------------------------------------------------------------------
# ato cost report — 成本报告 (Story 5.2)
# ---------------------------------------------------------------------------

cost_app = typer.Typer(help="成本管理")
app.add_typer(cost_app, name="cost")


@cost_app.command("report")
def cost_report_command(
    story: str | None = typer.Option(
        None,
        "--story",
        help="按 Story 过滤，查看该 Story 的详细成本明细",
    ),
    db_path: Path = typer.Option(
        None,
        "--db-path",
        help="SQLite 数据库路径（默认 .ato/state.db）",
    ),
) -> None:
    """生成成本报告：总览或按 Story 详情。"""
    resolved_db = db_path or _DEFAULT_DB_PATH
    if not resolved_db.exists():
        typer.echo(
            _format_cli_error(
                "数据库文件不存在",
                "运行 `ato init` 初始化项目",
            ),
            err=True,
        )
        raise typer.Exit(code=EXIT_ENV_ERROR)

    try:
        asyncio.run(_cost_report_async(story, resolved_db))
    except click.exceptions.Exit:
        raise
    except Exception as exc:
        logger.error("cost_report_failed", error=str(exc))
        typer.echo(
            _format_cli_error(str(exc), "检查数据库状态或联系管理员"),
            err=True,
        )
        raise typer.Exit(code=EXIT_ERROR) from exc


async def _cost_report_async(story_id: str | None, db_path: Path) -> None:
    """ato cost report 的异步实现。"""
    from ato.models.db import get_connection

    db = await get_connection(db_path)
    try:
        if story_id is not None:
            # Story 详情模式
            await _render_cost_story_detail(db, story_id)
        else:
            # 总览模式
            await _render_cost_overview(db)
    finally:
        await db.close()


async def _render_cost_overview(db: object) -> None:
    """渲染成本报告总览：时间范围汇总 + 按 Story 明细。"""
    from datetime import UTC, datetime, timedelta

    import aiosqlite
    from rich.table import Table

    from ato.models.db import get_cost_by_period, get_cost_by_story, get_cost_summary

    assert isinstance(db, aiosqlite.Connection)

    now = datetime.now(tz=UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # 本周起始（周一）
    week_start = today_start - timedelta(days=today_start.weekday())

    today_summary = await get_cost_by_period(db, today_start)
    week_summary = await get_cost_by_period(db, week_start)
    total_summary = await get_cost_summary(db)

    con = Console()

    # 检查是否有数据
    if total_summary["call_count"] == 0:
        con.print("暂无成本数据。运行 story 后将自动记录。")
        return

    con.print()
    con.print(Text("成本报告", style="bold"))
    con.rule()
    con.print()

    # 表 1：时间范围汇总
    con.print(Text("时间范围汇总", style="bold"))
    period_table = Table(show_header=True, header_style="bold")
    period_table.add_column("时间范围")
    period_table.add_column("总成本", justify="right")
    period_table.add_column("输入 Tokens", justify="right")
    period_table.add_column("输出 Tokens", justify="right")
    period_table.add_column("调用次数", justify="right")

    for label, summary in [
        ("今日", today_summary),
        ("本周", week_summary),
        ("全部", total_summary),
    ]:
        period_table.add_row(
            label,
            f"${summary['total_cost_usd']:.2f}",
            str(summary["total_input_tokens"]),
            str(summary["total_output_tokens"]),
            str(summary["call_count"]),
        )

    con.print(period_table)
    con.print()

    # 表 2：按 Story 明细
    story_costs = await get_cost_by_story(db)
    if story_costs:
        con.print(Text("按 Story 明细", style="bold"))
        story_table = Table(show_header=True, header_style="bold")
        story_table.add_column("Story")
        story_table.add_column("总成本", justify="right")
        story_table.add_column("调用次数", justify="right")

        for entry in story_costs:
            story_table.add_row(
                str(entry["story_id"]),
                f"${entry['total_cost_usd']:.2f}",
                str(entry["call_count"]),
            )

        con.print(story_table)


async def _render_cost_story_detail(db: object, story_id: str) -> None:
    """渲染单个 Story 的详细成本明细。"""
    import aiosqlite
    from rich.table import Table

    from ato.models.db import get_cost_logs_by_story

    assert isinstance(db, aiosqlite.Connection)

    records = await get_cost_logs_by_story(db, story_id)
    con = Console()

    if not records:
        con.print(f"Story '{story_id}' 暂无成本数据。运行 story 后将自动记录。")
        return

    con.print()
    con.print(Text(f"Story {story_id} 成本明细", style="bold"))
    con.rule()

    # 检测是否有 cache_read_input_tokens 数据
    has_cache_tokens = any(r.cache_read_input_tokens > 0 for r in records)

    table = Table(show_header=True, header_style="bold")
    table.add_column("时间")
    table.add_column("Phase")
    table.add_column("Role")
    table.add_column("Tool")
    table.add_column("Model")
    table.add_column("Input Tokens", justify="right")
    table.add_column("Output Tokens", justify="right")
    if has_cache_tokens:
        table.add_column("Cache Read", justify="right")
    table.add_column("Cost USD", justify="right")

    total_cost = 0.0
    total_input = 0
    total_output = 0
    total_cache_read = 0

    for record in records:
        total_cost += record.cost_usd
        total_input += record.input_tokens
        total_output += record.output_tokens
        total_cache_read += record.cache_read_input_tokens

        time_str = record.created_at.strftime("%m-%d %H:%M")

        row: list[str] = [
            time_str,
            record.phase,
            record.role or "-",
            record.cli_tool,
            record.model or "-",
            str(record.input_tokens),
            str(record.output_tokens),
        ]
        if has_cache_tokens:
            row.append(str(record.cache_read_input_tokens))
        row.append(f"${record.cost_usd:.2f}")
        table.add_row(*row)

    # 汇总行
    summary_row: list[str] = [
        "汇总",
        f"{len(records)} 条记录",
        "",
        "",
        "",
        str(total_input),
        str(total_output),
    ]
    if has_cache_tokens:
        summary_row.append(str(total_cache_read))
    summary_row.append(f"${total_cost:.2f}")
    table.add_section()
    table.add_row(*summary_row)

    con.print(table)
