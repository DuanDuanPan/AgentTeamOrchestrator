"""test_cli_init — CLI init 命令测试与渲染输出测试。"""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import AsyncMock, patch

from rich.console import Console
from typer.testing import CliRunner

from ato.cli import app, render_preflight_results
from ato.models.schemas import CheckResult

runner = CliRunner()


# ---------------------------------------------------------------------------
# 辅助: 构建 CheckResult
# ---------------------------------------------------------------------------


def _cr(
    layer: str = "system",
    check_item: str = "python_version",
    status: str = "PASS",
    message: str = "Python 3.12.1",
) -> CheckResult:
    return CheckResult(layer=layer, check_item=check_item, status=status, message=message)  # type: ignore[arg-type]


def _all_pass_results() -> list[CheckResult]:
    return [
        _cr("system", "python_version", "PASS", "Python 3.12.1"),
        _cr("system", "claude_installed", "PASS", "claude 已安装: v4.6.2"),
        _cr("system", "claude_auth", "PASS", "Claude CLI 认证有效"),
        _cr("system", "git_installed", "PASS", "git 已安装: git version 2.44.0"),
        _cr("project", "git_repo", "PASS", "Git 仓库已确认"),
        _cr("project", "bmad_config", "PASS", "BMAD 配置有效"),
        _cr("project", "bmad_skills", "PASS", "BMAD Skills 已部署: .claude/skills"),
        _cr("project", "ato_yaml", "PASS", "ato.yaml 已找到"),
        _cr("artifact", "epic_files", "PASS", "Epic 文件 已找到（2 个文件）"),
        _cr("artifact", "prd_files", "PASS", "PRD 文件 已找到（1 个文件）"),
        _cr("artifact", "impl_directory", "PASS", "implementation_artifacts 目录已就绪"),
    ]


def _halt_results() -> list[CheckResult]:
    return [
        _cr("system", "python_version", "PASS", "Python 3.12.1"),
        _cr("system", "claude_installed", "HALT", "claude 未安装 — 请安装 claude CLI"),
    ]


def _warn_results() -> list[CheckResult]:
    return [
        _cr("system", "python_version", "PASS", "Python 3.12.1"),
        _cr("system", "git_installed", "PASS", "git 已安装: git version 2.44.0"),
        _cr("project", "git_repo", "PASS", "Git 仓库已确认"),
        _cr("project", "bmad_skills", "WARN", "未找到 BMAD Skills 目录"),
        _cr("project", "ato_yaml", "PASS", "ato.yaml 已找到"),
        _cr("artifact", "epic_files", "PASS", "Epic 文件 已找到（2 个文件）"),
        _cr("artifact", "prd_files", "WARN", "PRD 文件 未找到"),
        _cr("artifact", "impl_directory", "PASS", "implementation_artifacts 目录已就绪"),
    ]


# ---------------------------------------------------------------------------
# Task 3: CLI 命令测试
# ---------------------------------------------------------------------------


class TestInitNormalFlow:
    """3.2 正常流程：全 PASS，退出码 0。"""

    def test_exit_code_0(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "proj"
        project_dir.mkdir()

        mock_preflight = AsyncMock(return_value=_all_pass_results())
        with patch("ato.cli.run_preflight", mock_preflight):
            result = runner.invoke(app, ["init", str(project_dir)], input="\n")

        assert result.exit_code == 0
        assert "系统已初始化" in result.output

    def test_calls_run_preflight_with_correct_args(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "proj"
        project_dir.mkdir()

        mock_preflight = AsyncMock(return_value=_all_pass_results())
        with patch("ato.cli.run_preflight", mock_preflight):
            runner.invoke(app, ["init", str(project_dir)], input="\n")

        mock_preflight.assert_called_once()
        call_args = mock_preflight.call_args
        assert call_args[0][0] == project_dir
        assert call_args[1]["include_auth"] is True


class TestInitHaltFlow:
    """3.3 HALT 流程：退出码 2。"""

    def test_exit_code_2_on_halt(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "proj"
        project_dir.mkdir()

        mock_preflight = AsyncMock(return_value=_halt_results())
        with patch("ato.cli.run_preflight", mock_preflight):
            result = runner.invoke(app, ["init", str(project_dir)])

        assert result.exit_code == 2

    def test_no_confirmation_prompt_on_halt(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "proj"
        project_dir.mkdir()

        mock_preflight = AsyncMock(return_value=_halt_results())
        with patch("ato.cli.run_preflight", mock_preflight):
            result = runner.invoke(app, ["init", str(project_dir)])

        assert "按 Enter 继续" not in result.output


class TestInitWarnFlow:
    """3.4 WARN 流程：退出码 0，摘要包含"警告"。"""

    def test_exit_code_0_with_warn(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "proj"
        project_dir.mkdir()

        mock_preflight = AsyncMock(return_value=_warn_results())
        with patch("ato.cli.run_preflight", mock_preflight):
            result = runner.invoke(app, ["init", str(project_dir)], input="\n")

        assert result.exit_code == 0

    def test_summary_contains_warn(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "proj"
        project_dir.mkdir()

        mock_preflight = AsyncMock(return_value=_warn_results())
        with patch("ato.cli.run_preflight", mock_preflight):
            result = runner.invoke(app, ["init", str(project_dir)], input="\n")

        assert "警告" in result.output


class TestInitReinitDetection:
    """3.5 重新初始化检测：已有 db 时提示确认。"""

    def test_reinit_prompt_shown(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        db_dir = project_dir / ".ato"
        db_dir.mkdir()
        db_file = db_dir / "state.db"
        db_file.write_text("placeholder")

        mock_preflight = AsyncMock(return_value=_all_pass_results())
        with patch("ato.cli.run_preflight", mock_preflight):
            result = runner.invoke(app, ["init", str(project_dir)], input="y\n\n")

        assert "重新初始化" in result.output

    def test_reinit_confirm_proceeds(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        db_dir = project_dir / ".ato"
        db_dir.mkdir()
        db_file = db_dir / "state.db"
        db_file.write_text("placeholder")

        mock_preflight = AsyncMock(return_value=_all_pass_results())
        with patch("ato.cli.run_preflight", mock_preflight):
            result = runner.invoke(app, ["init", str(project_dir)], input="y\n\n")

        assert result.exit_code == 0
        mock_preflight.assert_called_once()


class TestInitReinitReject:
    """3.6 重新初始化拒绝：Click abort 行为保留。"""

    def test_reinit_reject_aborts(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        db_dir = project_dir / ".ato"
        db_dir.mkdir()
        db_file = db_dir / "state.db"
        db_file.write_text("placeholder")

        mock_preflight = AsyncMock(return_value=_all_pass_results())
        with patch("ato.cli.run_preflight", mock_preflight):
            result = runner.invoke(app, ["init", str(project_dir)], input="n\n")

        assert result.exit_code != 0
        mock_preflight.assert_not_called()


class TestInitCustomDbPath:
    """3.7 --db-path 自定义路径参数。"""

    def test_custom_db_path(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        custom_db = tmp_path / "custom" / "my.db"

        mock_preflight = AsyncMock(return_value=_all_pass_results())
        with patch("ato.cli.run_preflight", mock_preflight):
            result = runner.invoke(
                app,
                ["init", str(project_dir), "--db-path", str(custom_db)],
                input="\n",
            )

        assert result.exit_code == 0
        call_args = mock_preflight.call_args
        assert call_args[0][1] == custom_db


class TestInitDefaultProjectPath:
    """3.8 默认 project_path 为当前目录。"""

    def test_default_project_path(self, tmp_path: Path, monkeypatch: object) -> None:
        import os

        monkeypatch.setattr(os, "getcwd", lambda: str(tmp_path))  # type: ignore[attr-defined]

        mock_preflight = AsyncMock(return_value=_all_pass_results())
        with patch("ato.cli.run_preflight", mock_preflight):
            result = runner.invoke(app, ["init"], input="\n")

        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Task 4: 渲染输出测试
# ---------------------------------------------------------------------------


def _capture_render(results: list[CheckResult]) -> str:
    """使用 StringIO console 捕获渲染输出。"""
    buf = io.StringIO()
    test_console = Console(file=buf, force_terminal=True, width=120)
    render_preflight_results(results, console=test_console)
    return buf.getvalue()


class TestRenderTitle:
    """4.4 验证标题显示。"""

    def test_title_displayed(self) -> None:
        output = _capture_render(_all_pass_results())
        assert "AgentTeamOrchestrator" in output
        assert "Preflight Check" in output


class TestRenderLayerTitles:
    """4.3 验证层标题。"""

    def test_layer_titles(self) -> None:
        output = _capture_render(_all_pass_results())
        assert "第一层" in output
        assert "第二层" in output
        assert "第三层" in output

    def test_partial_layers(self) -> None:
        output = _capture_render(_halt_results())
        assert "第一层" in output
        assert "第二层" not in output


class TestRenderOnlyActualItems:
    """4.5 验证只渲染输入 results 中实际存在的检查项。"""

    def test_no_synthetic_items(self) -> None:
        results = [
            _cr("system", "python_version", "PASS", "Python 3.12.1"),
        ]
        output = _capture_render(results)
        assert "Python 3.12.1" in output
        assert "第二层" not in output
        assert "第三层" not in output


class TestRenderHints:
    """4.6 验证 WARN/HALT 显示建议行，INFO/PASS 不显示。"""

    def test_halt_shows_hint(self) -> None:
        results = [
            _cr("system", "claude_installed", "HALT", "claude 未安装"),
        ]
        output = _capture_render(results)
        assert "→" in output
        assert "安装 Claude CLI" in output

    def test_warn_shows_hint(self) -> None:
        results = [
            _cr("project", "bmad_skills", "WARN", "未找到 BMAD Skills"),
        ]
        output = _capture_render(results)
        assert "→" in output
        assert "BMAD 安装流程" in output

    def test_pass_no_hint(self) -> None:
        results = [
            _cr("system", "python_version", "PASS", "Python 3.12.1"),
        ]
        output = _capture_render(results)
        assert "→" not in output

    def test_info_no_hint(self) -> None:
        results = [
            _cr("artifact", "ux_files", "INFO", "UX 设计文件 未找到"),
        ]
        output = _capture_render(results)
        assert "→" not in output


class TestRenderStatusIcons:
    """4.7 验证四种状态图标。"""

    def test_pass_icon(self) -> None:
        output = _capture_render([_cr(status="PASS")])
        assert "✔" in output

    def test_halt_icon(self) -> None:
        output = _capture_render([_cr("system", "claude_installed", "HALT", "未安装")])
        assert "✖" in output

    def test_warn_icon(self) -> None:
        output = _capture_render([_cr("project", "bmad_skills", "WARN", "未找到")])
        assert "⚠" in output

    def test_info_icon(self) -> None:
        output = _capture_render([_cr("artifact", "ux_files", "INFO", "跳过")])
        assert "ℹ" in output


class TestRenderSummary:
    """4.8 验证摘要文本。"""

    def test_all_pass_summary(self) -> None:
        output = _capture_render(_all_pass_results())
        assert "就绪" in output

    def test_warn_summary(self) -> None:
        output = _capture_render(_warn_results())
        assert "警告" in output

    def test_halt_summary(self) -> None:
        output = _capture_render(_halt_results())
        assert "未就绪" in output
        assert "阻断" in output

    def test_warn_summary_always_includes_info_count(self) -> None:
        """Fix 3: WARN 摘要始终包含"N 警告, M 信息"，即使 info_count == 0。"""
        results = [
            _cr("system", "python_version", "PASS", "Python 3.12.1"),
            _cr("project", "bmad_skills", "WARN", "未找到"),
        ]
        output = _capture_render(results)
        assert "1 警告" in output
        assert "0 信息" in output


# ---------------------------------------------------------------------------
# Review fix: HALT stderr 输出 & python_version hint
# ---------------------------------------------------------------------------


class TestInitHaltStderr:
    """Fix 1: HALT 时错误信息输出到 stderr。"""

    def test_halt_writes_to_stderr(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "proj"
        project_dir.mkdir()

        mock_preflight = AsyncMock(return_value=_halt_results())
        with patch("ato.cli.run_preflight", mock_preflight):
            result = runner.invoke(app, ["init", str(project_dir)])

        assert result.exit_code == 2
        # CliRunner mixes stdout/stderr; verify the stderr message appears in output
        assert "环境检查未通过" in result.output


class TestRenderPythonVersionHint:
    """Fix 2: python_version HALT 时显示修复指引。"""

    def test_python_version_halt_has_hint(self) -> None:
        results = [
            _cr("system", "python_version", "HALT", "Python 3.10 < 3.11"),
        ]
        output = _capture_render(results)
        assert "→" in output
        assert "Python" in output or "3.11" in output
