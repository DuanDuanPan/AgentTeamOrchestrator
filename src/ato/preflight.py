"""preflight — 预检引擎。

三层前置检查引擎：系统环境 → 项目结构 → 编排前置 Artifact。
每层有 HALT 则跳过后续层。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from uuid import uuid4

import structlog
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ato.models.db import get_connection, init_db, insert_preflight_results
from ato.models.schemas import CheckResult

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

_CLI_VERSION_TIMEOUT: float = 10.0
_CLI_AUTH_TIMEOUT: float = 30.0


# ---------------------------------------------------------------------------
# subprocess 辅助
# ---------------------------------------------------------------------------


async def _run_subprocess(
    cmd: list[str],
    *,
    timeout: float,
) -> tuple[int, str, str]:
    """执行子进程并返回 (returncode, stdout, stderr)。

    超时时执行三阶段清理：terminate → wait(5s) → kill → wait。

    Raises:
        FileNotFoundError: 命令不存在。
        asyncio.TimeoutError: 超时。
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout,
        )
    except TimeoutError:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except TimeoutError:
            proc.kill()
            await proc.wait()
        raise
    stdout = stdout_bytes.decode(errors="replace")
    stderr = stderr_bytes.decode(errors="replace")
    return proc.returncode or 0, stdout, stderr


# ---------------------------------------------------------------------------
# Layer 1: 系统环境检查
# ---------------------------------------------------------------------------


def _check_python_version() -> CheckResult:
    """检查 Python 版本 ≥ 3.11。"""
    logger.debug("preflight_check_start", layer="system", check_item="python_version")
    major, minor = sys.version_info[:2]
    if (major, minor) >= (3, 11):
        msg = f"Python {sys.version.split()[0]}"
        logger.debug("preflight_check_pass", layer="system", check_item="python_version")
        return CheckResult(layer="system", check_item="python_version", status="PASS", message=msg)
    msg = f"Python {major}.{minor} < 3.11 — 请升级到 Python ≥ 3.11"
    logger.warning("preflight_check_halt", layer="system", check_item="python_version", message=msg)
    return CheckResult(layer="system", check_item="python_version", status="HALT", message=msg)


async def _check_cli_installed(cli_name: str, version_cmd: list[str]) -> CheckResult:
    """通用 CLI 安装检测。"""
    check_item = f"{cli_name}_installed"
    logger.debug("preflight_check_start", layer="system", check_item=check_item)
    try:
        returncode, stdout, stderr = await _run_subprocess(
            version_cmd, timeout=_CLI_VERSION_TIMEOUT
        )
    except FileNotFoundError:
        msg = f"{cli_name} 未安装 — 请安装 {cli_name} CLI"
        logger.warning("preflight_check_halt", layer="system", check_item=check_item, message=msg)
        return CheckResult(layer="system", check_item=check_item, status="HALT", message=msg)
    except TimeoutError:
        msg = f"{cli_name} --version 超时（{_CLI_VERSION_TIMEOUT}s）"
        logger.warning("preflight_check_halt", layer="system", check_item=check_item, message=msg)
        return CheckResult(layer="system", check_item=check_item, status="HALT", message=msg)

    if returncode != 0:
        msg = f"{cli_name} --version 返回非零退出码 {returncode}: {stderr.strip()}"
        logger.warning("preflight_check_halt", layer="system", check_item=check_item, message=msg)
        return CheckResult(layer="system", check_item=check_item, status="HALT", message=msg)

    version_line = stdout.strip().split("\n")[0]
    msg = f"{cli_name} 已安装: {version_line}"
    logger.debug("preflight_check_pass", layer="system", check_item=check_item)
    return CheckResult(layer="system", check_item=check_item, status="PASS", message=msg)


async def _check_claude_auth() -> CheckResult:
    """检查 Claude CLI 认证有效性。"""
    check_item = "claude_auth"
    logger.debug("preflight_check_start", layer="system", check_item=check_item)
    cmd = [
        "claude",
        "--dangerously-skip-permissions",
        "-p",
        "ping",
        "--max-turns",
        "1",
        "--output-format",
        "json",
        "--no-session-persistence",
    ]
    try:
        returncode, _stdout, stderr = await _run_subprocess(cmd, timeout=_CLI_AUTH_TIMEOUT)
    except FileNotFoundError:
        msg = "Claude CLI 未找到 — 无法执行认证检查"
        logger.warning("preflight_check_halt", layer="system", check_item=check_item, message=msg)
        return CheckResult(layer="system", check_item=check_item, status="HALT", message=msg)
    except TimeoutError:
        msg = f"Claude 认证测试超时（{_CLI_AUTH_TIMEOUT}s）"
        logger.warning("preflight_check_halt", layer="system", check_item=check_item, message=msg)
        return CheckResult(layer="system", check_item=check_item, status="HALT", message=msg)

    if returncode != 0:
        msg = f"Claude 认证失败 — 请执行 `claude auth` 登录: {stderr.strip()}"
        logger.warning("preflight_check_halt", layer="system", check_item=check_item, message=msg)
        return CheckResult(layer="system", check_item=check_item, status="HALT", message=msg)

    logger.debug("preflight_check_pass", layer="system", check_item=check_item)
    return CheckResult(
        layer="system", check_item=check_item, status="PASS", message="Claude CLI 认证有效"
    )


async def _check_codex_auth() -> CheckResult:
    """检查 Codex CLI 认证有效性。"""
    check_item = "codex_auth"
    logger.debug("preflight_check_start", layer="system", check_item=check_item)
    cmd = [
        "codex",
        "exec",
        "ping",
        "--json",
        "--skip-git-repo-check",
        "--ephemeral",
        "-s",
        "read-only",
    ]
    try:
        returncode, _stdout, stderr = await _run_subprocess(cmd, timeout=_CLI_AUTH_TIMEOUT)
    except FileNotFoundError:
        msg = "Codex CLI 未找到 — 无法执行认证检查"
        logger.warning("preflight_check_halt", layer="system", check_item=check_item, message=msg)
        return CheckResult(layer="system", check_item=check_item, status="HALT", message=msg)
    except TimeoutError:
        msg = f"Codex 认证测试超时（{_CLI_AUTH_TIMEOUT}s）"
        logger.warning("preflight_check_halt", layer="system", check_item=check_item, message=msg)
        return CheckResult(layer="system", check_item=check_item, status="HALT", message=msg)

    if returncode != 0:
        msg = f"Codex 认证失败 — 请确认 Codex CLI 认证: {stderr.strip()}"
        logger.warning("preflight_check_halt", layer="system", check_item=check_item, message=msg)
        return CheckResult(layer="system", check_item=check_item, status="HALT", message=msg)

    logger.debug("preflight_check_pass", layer="system", check_item=check_item)
    return CheckResult(
        layer="system", check_item=check_item, status="PASS", message="Codex CLI 认证有效"
    )


async def check_system_environment(
    *,
    include_auth: bool = True,
) -> list[CheckResult]:
    """Layer 1: 系统环境检查。

    按固定顺序检测：Python → Claude install → Claude auth → Codex install → Codex auth → Git。
    若 CLI 安装失败则跳过对应的 auth 检查。
    ``include_auth=False`` 时跳过所有 auth 检查。
    """
    results: list[CheckResult] = []

    # 1. Python version
    results.append(_check_python_version())

    # 2. Claude CLI installed
    claude_result = await _check_cli_installed("claude", ["claude", "--version"])
    results.append(claude_result)

    # 3. Claude CLI auth (only if installed and include_auth)
    if include_auth and claude_result.status == "PASS":
        results.append(await _check_claude_auth())

    # 4. Codex CLI installed
    codex_result = await _check_cli_installed("codex", ["codex", "--version"])
    results.append(codex_result)

    # 5. Codex CLI auth (only if installed and include_auth)
    if include_auth and codex_result.status == "PASS":
        results.append(await _check_codex_auth())

    # 6. Git installed
    results.append(await _check_cli_installed("git", ["git", "--version"]))

    return results


# ---------------------------------------------------------------------------
# Layer 2: 项目结构检查
# ---------------------------------------------------------------------------


class _BMadConfigCheck(BaseModel):
    """BMAD config.yaml 必填字段验证（宽松模式，忽略额外字段）。"""

    model_config = ConfigDict(extra="ignore")

    project_name: str
    planning_artifacts: str = Field(min_length=1)
    implementation_artifacts: str = Field(min_length=1)


async def _check_git_repo(project_path: Path) -> CheckResult:
    """检查目标路径是否为 git 仓库。"""
    check_item = "git_repo"
    logger.debug("preflight_check_start", layer="project", check_item=check_item)
    try:
        returncode, _stdout, _stderr = await _run_subprocess(
            ["git", "-C", str(project_path), "rev-parse", "--git-dir"],
            timeout=_CLI_VERSION_TIMEOUT,
        )
    except FileNotFoundError:
        msg = "Git 未安装 — 无法检测 git 仓库"
        logger.warning("preflight_check_halt", layer="project", check_item=check_item, message=msg)
        return CheckResult(layer="project", check_item=check_item, status="HALT", message=msg)
    except TimeoutError:
        msg = "Git 仓库检测超时"
        logger.warning("preflight_check_halt", layer="project", check_item=check_item, message=msg)
        return CheckResult(layer="project", check_item=check_item, status="HALT", message=msg)

    if returncode != 0:
        msg = f"{project_path} 不是 git 仓库"
        logger.warning("preflight_check_halt", layer="project", check_item=check_item, message=msg)
        return CheckResult(layer="project", check_item=check_item, status="HALT", message=msg)

    logger.debug("preflight_check_pass", layer="project", check_item=check_item)
    return CheckResult(
        layer="project", check_item=check_item, status="PASS", message="Git 仓库已确认"
    )


def _check_bmad_config(project_path: Path) -> CheckResult:
    """检查 BMAD 配置文件。"""
    check_item = "bmad_config"
    logger.debug("preflight_check_start", layer="project", check_item=check_item)
    config_path = project_path / "_bmad" / "bmm" / "config.yaml"
    if not config_path.is_file():
        msg = f"BMAD 配置文件不存在: {config_path}"
        logger.warning("preflight_check_halt", layer="project", check_item=check_item, message=msg)
        return CheckResult(layer="project", check_item=check_item, status="HALT", message=msg)

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            msg = f"BMAD 配置文件格式错误: {config_path}"
            logger.warning(
                "preflight_check_halt", layer="project", check_item=check_item, message=msg
            )
            return CheckResult(layer="project", check_item=check_item, status="HALT", message=msg)
        _BMadConfigCheck.model_validate(raw)
    except (yaml.YAMLError, ValidationError) as exc:
        msg = f"BMAD 配置验证失败: {exc}"
        logger.warning("preflight_check_halt", layer="project", check_item=check_item, message=msg)
        return CheckResult(layer="project", check_item=check_item, status="HALT", message=msg)

    logger.debug("preflight_check_pass", layer="project", check_item=check_item)
    return CheckResult(
        layer="project", check_item=check_item, status="PASS", message="BMAD 配置有效"
    )


def _check_bmad_skills(project_path: Path) -> CheckResult:
    """检查 BMAD skills 目录。"""
    check_item = "bmad_skills"
    logger.debug("preflight_check_start", layer="project", check_item=check_item)
    skills_dirs = [
        project_path / ".claude" / "skills",
        project_path / ".codex" / "skills",
        project_path / ".agents" / "skills",
    ]
    found = [d for d in skills_dirs if d.is_dir()]
    if found:
        logger.debug("preflight_check_pass", layer="project", check_item=check_item)
        return CheckResult(
            layer="project",
            check_item=check_item,
            status="PASS",
            message=f"BMAD Skills 已部署: "
            f"{', '.join(str(d.relative_to(project_path)) for d in found)}",
        )
    msg = "未找到 BMAD Skills 目录（.claude/skills/、.codex/skills/ 或 .agents/skills/）"
    logger.warning("preflight_check_warn", layer="project", check_item=check_item, message=msg)
    return CheckResult(layer="project", check_item=check_item, status="WARN", message=msg)


def _check_ato_yaml(project_path: Path) -> CheckResult:
    """检查 ato.yaml 配置文件。"""
    check_item = "ato_yaml"
    logger.debug("preflight_check_start", layer="project", check_item=check_item)
    ato_path = project_path / "ato.yaml"
    if ato_path.is_file():
        logger.debug("preflight_check_pass", layer="project", check_item=check_item)
        return CheckResult(
            layer="project", check_item=check_item, status="PASS", message="ato.yaml 已找到"
        )
    msg = "ato.yaml 不存在，init 时将自动从 ato.yaml.example 生成"
    logger.info("preflight_check_info", layer="project", check_item=check_item, message=msg)
    return CheckResult(layer="project", check_item=check_item, status="INFO", message=msg)


async def check_project_structure(project_path: Path) -> list[CheckResult]:
    """Layer 2: 项目结构检查。"""
    results: list[CheckResult] = []

    results.append(await _check_git_repo(project_path))
    results.append(_check_bmad_config(project_path))
    results.append(_check_bmad_skills(project_path))
    results.append(_check_ato_yaml(project_path))

    return results


# ---------------------------------------------------------------------------
# Layer 3: 编排前置 Artifact 检查
# ---------------------------------------------------------------------------


def _load_bmad_paths(project_path: Path) -> tuple[Path, Path]:
    """从 BMAD config 加载 planning/implementation artifacts 路径。

    返回 (planning_path, impl_path)。若配置不存在则使用默认路径。
    """
    config_path = project_path / "_bmad" / "bmm" / "config.yaml"
    planning_default = project_path / "_bmad-output" / "planning-artifacts"
    impl_default = project_path / "_bmad-output" / "implementation-artifacts"

    if not config_path.is_file():
        return planning_default, impl_default

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return planning_default, impl_default

        planning_str = raw.get("planning_artifacts", "")
        impl_str = raw.get("implementation_artifacts", "")

        # 替换 {project-root} 占位符
        project_root = str(project_path)
        planning_str = planning_str.replace("{project-root}", project_root)
        impl_str = impl_str.replace("{project-root}", project_root)

        # 相对路径相对于 project_path 解析（而非 cwd）
        planning = Path(planning_str) if planning_str else planning_default
        impl = Path(impl_str) if impl_str else impl_default
        if not planning.is_absolute():
            planning = project_path / planning
        if not impl.is_absolute():
            impl = project_path / impl
        return planning, impl
    except yaml.YAMLError:
        return planning_default, impl_default


def _check_artifact_glob(
    planning_path: Path,
    check_item: str,
    whole_pattern: str,
    sharded_pattern: str,
    missing_status: str,
    artifact_label: str,
) -> CheckResult:
    """通用 artifact glob 检测。

    同时搜索 whole 和 sharded 模式，任一匹配即可。
    """
    logger.debug("preflight_check_start", layer="artifact", check_item=check_item)

    # whole 模式
    whole_matches = list(planning_path.glob(whole_pattern))
    # sharded 模式
    sharded_matches = list(planning_path.glob(sharded_pattern))

    all_matches = whole_matches + sharded_matches
    if all_matches:
        count = len(all_matches)
        logger.debug("preflight_check_pass", layer="artifact", check_item=check_item)
        return CheckResult(
            layer="artifact",
            check_item=check_item,
            status="PASS",
            message=f"{artifact_label} 已找到（{count} 个文件）",
        )

    msg = f"{artifact_label} 未找到"
    if missing_status == "HALT":
        logger.warning("preflight_check_halt", layer="artifact", check_item=check_item, message=msg)
    else:
        logger.debug(
            "preflight_check_result",
            layer="artifact",
            check_item=check_item,
            status=missing_status,
        )
    return CheckResult(
        layer="artifact",
        check_item=check_item,
        status=missing_status,  # type: ignore[arg-type]
        message=msg,
    )


async def check_artifacts(project_path: Path) -> list[CheckResult]:
    """Layer 3: 编排前置 Artifact 检查。"""
    results: list[CheckResult] = []

    planning_path, impl_path = _load_bmad_paths(project_path)

    # Epic 文件（必须）
    results.append(
        _check_artifact_glob(
            planning_path,
            "epic_files",
            "*epic*.md",
            "*epic*/*.md",
            "HALT",
            "Epic 文件",
        )
    )

    # PRD（推荐）
    results.append(
        _check_artifact_glob(
            planning_path,
            "prd_files",
            "*prd*.md",
            "*prd*/*.md",
            "WARN",
            "PRD 文件",
        )
    )

    # 架构文档（推荐）
    results.append(
        _check_artifact_glob(
            planning_path,
            "architecture_files",
            "*architecture*.md",
            "*architecture*/*.md",
            "WARN",
            "架构文档",
        )
    )

    # UX 设计（可选）
    results.append(
        _check_artifact_glob(
            planning_path,
            "ux_files",
            "*ux*.md",
            "*ux*/*.md",
            "INFO",
            "UX 设计文件",
        )
    )

    # project-context.md（可选）
    check_item = "project_context"
    logger.debug("preflight_check_start", layer="artifact", check_item=check_item)
    pc_matches = list(project_path.glob("**/project-context.md"))
    if pc_matches:
        results.append(
            CheckResult(
                layer="artifact",
                check_item=check_item,
                status="PASS",
                message=f"project-context.md 已找到: {pc_matches[0].relative_to(project_path)}",
            )
        )
    else:
        results.append(
            CheckResult(
                layer="artifact",
                check_item=check_item,
                status="INFO",
                message="project-context.md 未找到",
            )
        )

    # implementation_artifacts 目录（必须可写）
    check_item = "impl_directory"
    logger.debug("preflight_check_start", layer="artifact", check_item=check_item)
    try:
        impl_path.mkdir(parents=True, exist_ok=True)
        # 验证目录实际可写 — mkdir(exist_ok=True) 对已存在的只读目录不会报错
        if not os.access(impl_path, os.W_OK | os.X_OK):
            msg = f"implementation_artifacts 目录不可写: {impl_path}"
            logger.warning(
                "preflight_check_halt",
                layer="artifact",
                check_item=check_item,
                message=msg,
            )
            results.append(
                CheckResult(
                    layer="artifact",
                    check_item=check_item,
                    status="HALT",
                    message=msg,
                )
            )
        else:
            results.append(
                CheckResult(
                    layer="artifact",
                    check_item=check_item,
                    status="PASS",
                    message=f"implementation_artifacts 目录已就绪: {impl_path}",
                )
            )
    except OSError as exc:
        msg = f"implementation_artifacts 目录创建失败: {exc}"
        logger.warning(
            "preflight_check_halt",
            layer="artifact",
            check_item=check_item,
            message=msg,
        )
        results.append(
            CheckResult(layer="artifact", check_item=check_item, status="HALT", message=msg)
        )

    return results


# ---------------------------------------------------------------------------
# 编排函数
# ---------------------------------------------------------------------------


def _has_halt(results: list[CheckResult]) -> bool:
    """检查结果列表中是否含有 HALT。"""
    return any(r.status == "HALT" for r in results)


async def run_preflight(
    project_path: Path,
    db_path: Path,
    *,
    include_auth: bool = True,
) -> list[CheckResult]:
    """执行完整 preflight 三层检查。

    顺序执行：Layer 1 → Layer 2 → Layer 3。
    每层有 HALT 则跳过后续层。
    检查完成后持久化结果到 SQLite。

    Args:
        project_path: 目标项目根目录。
        db_path: SQLite 数据库路径。
        include_auth: ``True``（ato init）执行 CLI 认证测试；
            ``False``（ato start）跳过认证测试。

    Returns:
        完整的 ``list[CheckResult]``（供 CLI 渲染消费）。
    """
    all_results: list[CheckResult] = []
    run_id = uuid4().hex

    # Layer 1: 系统环境
    logger.info("preflight_layer_start", layer="system")
    layer1 = await check_system_environment(include_auth=include_auth)
    all_results.extend(layer1)

    if _has_halt(layer1):
        logger.warning(
            "preflight_layer_halt", layer="system", message="Layer 1 有 HALT，跳过后续层"
        )
    else:
        # Layer 2: 项目结构
        logger.info("preflight_layer_start", layer="project")
        layer2 = await check_project_structure(project_path)
        all_results.extend(layer2)

        if _has_halt(layer2):
            logger.warning(
                "preflight_layer_halt", layer="project", message="Layer 2 有 HALT，跳过 Layer 3"
            )
        else:
            # Layer 3: 编排前置 Artifact
            logger.info("preflight_layer_start", layer="artifact")
            layer3 = await check_artifacts(project_path)
            all_results.extend(layer3)

    # 持久化阶段：先完成所有检查，再操作 SQLite
    logger.info("preflight_persist_start", run_id=run_id, result_count=len(all_results))
    await init_db(db_path)
    db = await get_connection(db_path)
    try:
        await insert_preflight_results(db, run_id, all_results)
    finally:
        await db.close()
    logger.info("preflight_persist_done", run_id=run_id)

    return all_results
