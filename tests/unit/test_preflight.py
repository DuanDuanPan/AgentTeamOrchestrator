"""test_preflight — Preflight 三层检查引擎单元测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_proc(
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> AsyncMock:
    """创建一个模拟的 asyncio subprocess。"""
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(stdout.encode(), stderr.encode()))
    proc.returncode = returncode
    proc.terminate = MagicMock()
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


# ---------------------------------------------------------------------------
# Layer 1: check_system_environment
# ---------------------------------------------------------------------------


class TestLayer1:
    """Layer 1 — 系统环境检查单元测试。"""

    async def test_python_version_pass(self) -> None:
        """Python ≥ 3.11 返回 PASS。"""
        from ato.preflight import _check_python_version

        with patch("ato.preflight.sys") as mock_sys:
            mock_sys.version_info = (3, 11, 0, "final", 0)
            mock_sys.version = "3.11.0 (default)"
            result = _check_python_version()
        assert result.status == "PASS"
        assert result.layer == "system"
        assert result.check_item == "python_version"

    async def test_python_version_halt(self) -> None:
        """Python < 3.11 返回 HALT。"""
        from ato.preflight import _check_python_version

        with patch("ato.preflight.sys") as mock_sys:
            mock_sys.version_info = (3, 10, 2, "final", 0)
            mock_sys.version = "3.10.2 (default)"
            result = _check_python_version()
        assert result.status == "HALT"

    async def test_cli_installed_pass(self) -> None:
        """CLI 已安装返回 PASS。"""
        from ato.preflight import _check_cli_installed

        proc = _mock_proc(stdout="claude 1.0.0\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await _check_cli_installed("claude", ["claude", "--version"])
        assert result.status == "PASS"
        assert result.check_item == "claude_installed"

    async def test_cli_installed_not_found(self) -> None:
        """CLI 未安装（FileNotFoundError）返回 HALT。"""
        from ato.preflight import _check_cli_installed

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("No such file"),
        ):
            result = await _check_cli_installed("claude", ["claude", "--version"])
        assert result.status == "HALT"

    async def test_cli_installed_timeout(self) -> None:
        """CLI 版本检查超时返回 HALT。"""
        from ato.preflight import _check_cli_installed

        proc = _mock_proc()
        proc.communicate = AsyncMock(side_effect=TimeoutError())
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await _check_cli_installed("claude", ["claude", "--version"])
        assert result.status == "HALT"
        assert "timeout" in result.message.lower() or "超时" in result.message

    async def test_cli_installed_nonzero_exit(self) -> None:
        """CLI 版本检查返回非零退出码返回 HALT。"""
        from ato.preflight import _check_cli_installed

        proc = _mock_proc(returncode=1, stderr="error")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await _check_cli_installed("git", ["git", "--version"])
        assert result.status == "HALT"

    async def test_claude_auth_pass(self) -> None:
        """Claude 认证成功返回 PASS。"""
        from ato.preflight import _check_claude_auth

        proc = _mock_proc(stdout='{"result": "ok"}')
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await _check_claude_auth()
        assert result.status == "PASS"
        assert result.check_item == "claude_auth"

    async def test_claude_auth_fail(self) -> None:
        """Claude 认证失败返回 HALT。"""
        from ato.preflight import _check_claude_auth

        proc = _mock_proc(returncode=1, stderr="auth error")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await _check_claude_auth()
        assert result.status == "HALT"
        assert "claude auth" in result.message.lower() or "认证" in result.message

    async def test_claude_auth_timeout(self) -> None:
        """Claude 认证超时返回 HALT。"""
        from ato.preflight import _check_claude_auth

        proc = _mock_proc()
        proc.communicate = AsyncMock(side_effect=TimeoutError())
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await _check_claude_auth()
        assert result.status == "HALT"

    async def test_codex_auth_pass(self) -> None:
        """Codex 认证成功返回 PASS。"""
        from ato.preflight import _check_codex_auth

        proc = _mock_proc(stdout='{"result": "ok"}')
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await _check_codex_auth()
        assert result.status == "PASS"
        assert result.check_item == "codex_auth"

    async def test_codex_auth_fail(self) -> None:
        """Codex 认证失败返回 HALT。"""
        from ato.preflight import _check_codex_auth

        proc = _mock_proc(returncode=1, stderr="auth error")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await _check_codex_auth()
        assert result.status == "HALT"

    async def test_check_system_environment_all_pass(self) -> None:
        """所有系统检查通过的场景。"""
        from ato.preflight import check_system_environment

        proc = _mock_proc(stdout="version 1.0.0\n")
        with (
            patch("ato.preflight.sys") as mock_sys,
            patch("asyncio.create_subprocess_exec", return_value=proc),
        ):
            mock_sys.version_info = (3, 11, 0, "final", 0)
            mock_sys.version = "3.11.0"
            results = await check_system_environment()

        # python, claude install, claude auth, codex install, codex auth, git
        assert len(results) == 6
        assert all(r.status == "PASS" for r in results)
        # 验证顺序稳定
        items = [r.check_item for r in results]
        assert items == [
            "python_version",
            "claude_installed",
            "claude_auth",
            "codex_installed",
            "codex_auth",
            "git_installed",
        ]

    async def test_check_system_environment_skip_auth_when_cli_not_installed(self) -> None:
        """CLI 未安装时跳过对应 auth 检查。"""
        from ato.preflight import check_system_environment

        async def _mock_exec(*args: object, **kwargs: object) -> AsyncMock:
            cmd = args[0] if args else ""
            if cmd in ("claude", "codex"):
                raise FileNotFoundError("not found")
            return _mock_proc(stdout="git version 2.39.0\n")

        with (
            patch("ato.preflight.sys") as mock_sys,
            patch("asyncio.create_subprocess_exec", side_effect=_mock_exec),
        ):
            mock_sys.version_info = (3, 11, 0, "final", 0)
            mock_sys.version = "3.11.0"
            results = await check_system_environment()

        items = [r.check_item for r in results]
        # Claude and Codex install should be HALT, auth checks should be skipped
        assert "claude_installed" in items
        assert "claude_auth" not in items
        assert "codex_installed" in items
        assert "codex_auth" not in items
        assert "git_installed" in items

    async def test_check_system_environment_include_auth_false(self) -> None:
        """include_auth=False 时跳过认证检查。"""
        from ato.preflight import check_system_environment

        proc = _mock_proc(stdout="version 1.0.0\n")
        with (
            patch("ato.preflight.sys") as mock_sys,
            patch("asyncio.create_subprocess_exec", return_value=proc),
        ):
            mock_sys.version_info = (3, 11, 0, "final", 0)
            mock_sys.version = "3.11.0"
            results = await check_system_environment(include_auth=False)

        items = [r.check_item for r in results]
        assert "claude_auth" not in items
        assert "codex_auth" not in items
        # 安装检查仍在
        assert "claude_installed" in items
        assert "codex_installed" in items
        assert "git_installed" in items

    async def test_result_order_is_stable(self) -> None:
        """结果顺序与执行顺序一致：Python → Claude → Codex → Git。"""
        from ato.preflight import check_system_environment

        proc = _mock_proc(stdout="version 1.0.0\n")
        with (
            patch("ato.preflight.sys") as mock_sys,
            patch("asyncio.create_subprocess_exec", return_value=proc),
        ):
            mock_sys.version_info = (3, 12, 0, "final", 0)
            mock_sys.version = "3.12.0"
            results = await check_system_environment()

        items = [r.check_item for r in results]
        expected_order = [
            "python_version",
            "claude_installed",
            "claude_auth",
            "codex_installed",
            "codex_auth",
            "git_installed",
        ]
        assert items == expected_order


# ---------------------------------------------------------------------------
# Layer 2: check_project_structure
# ---------------------------------------------------------------------------


class TestLayer2:
    """Layer 2 — 项目结构检查单元测试。"""

    def _make_project(
        self,
        tmp_path: Path,
        *,
        git: bool = True,
        bmad_config: bool = True,
        bmad_config_content: str | None = None,
        skills_claude: bool = False,
        skills_codex: bool = False,
        skills_agents: bool = False,
        ato_yaml: bool = True,
    ) -> Path:
        """构建测试用项目目录。"""
        project = tmp_path / "project"
        project.mkdir()
        if git:
            (project / ".git").mkdir()
        if bmad_config:
            bmad_dir = project / "_bmad" / "bmm"
            bmad_dir.mkdir(parents=True)
            content = bmad_config_content or (
                "project_name: TestProject\n"
                'planning_artifacts: "{project-root}/planning"\n'
                'implementation_artifacts: "{project-root}/impl"\n'
            )
            (bmad_dir / "config.yaml").write_text(content)
        if skills_claude:
            (project / ".claude" / "skills").mkdir(parents=True)
        if skills_codex:
            (project / ".codex" / "skills").mkdir(parents=True)
        if skills_agents:
            (project / ".agents" / "skills").mkdir(parents=True)
        if ato_yaml:
            (project / "ato.yaml").write_text("project_name: test\n")
        return project

    async def test_full_project_all_pass(self, tmp_path: Path) -> None:
        """完整项目结构全部 PASS。"""
        from ato.preflight import check_project_structure

        project = self._make_project(tmp_path, skills_claude=True)
        # Mock git subprocess
        proc = _mock_proc(stdout=".git\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            results = await check_project_structure(project)
        statuses = {r.check_item: r.status for r in results}
        assert statuses["git_repo"] == "PASS"
        assert statuses["bmad_config"] == "PASS"
        assert statuses["bmad_skills"] == "PASS"
        assert statuses["ato_yaml"] == "PASS"

    async def test_missing_git_returns_halt(self, tmp_path: Path) -> None:
        """非 git 仓库返回 HALT。"""
        from ato.preflight import check_project_structure

        project = self._make_project(tmp_path, git=False)
        proc = _mock_proc(returncode=128, stderr="not a git repository")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            results = await check_project_structure(project)
        statuses = {r.check_item: r.status for r in results}
        assert statuses["git_repo"] == "HALT"

    async def test_missing_bmad_config_returns_halt(self, tmp_path: Path) -> None:
        """缺少 BMAD 配置返回 HALT。"""
        from ato.preflight import check_project_structure

        project = self._make_project(tmp_path, bmad_config=False, skills_claude=True)
        proc = _mock_proc(stdout=".git\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            results = await check_project_structure(project)
        statuses = {r.check_item: r.status for r in results}
        assert statuses["bmad_config"] == "HALT"

    async def test_invalid_bmad_config_returns_halt(self, tmp_path: Path) -> None:
        """BMAD 配置缺少必填字段返回 HALT。"""
        from ato.preflight import check_project_structure

        project = self._make_project(
            tmp_path,
            bmad_config_content="project_name: test\n",  # missing planning/implementation_artifacts
            skills_claude=True,
        )
        proc = _mock_proc(stdout=".git\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            results = await check_project_structure(project)
        statuses = {r.check_item: r.status for r in results}
        assert statuses["bmad_config"] == "HALT"

    async def test_empty_bmad_config_fields_returns_halt(self, tmp_path: Path) -> None:
        """BMAD 配置字段为空字符串返回 HALT。"""
        from ato.preflight import check_project_structure

        project = self._make_project(
            tmp_path,
            bmad_config_content=(
                "project_name: test\nplanning_artifacts: ''\nimplementation_artifacts: ''\n"
            ),
            skills_claude=True,
        )
        proc = _mock_proc(stdout=".git\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            results = await check_project_structure(project)
        statuses = {r.check_item: r.status for r in results}
        assert statuses["bmad_config"] == "HALT"

    async def test_no_skills_returns_warn(self, tmp_path: Path) -> None:
        """所有 skills 目录都不存在返回 WARN。"""
        from ato.preflight import check_project_structure

        project = self._make_project(tmp_path)
        proc = _mock_proc(stdout=".git\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            results = await check_project_structure(project)
        statuses = {r.check_item: r.status for r in results}
        assert statuses["bmad_skills"] == "WARN"

    async def test_only_claude_skills_pass(self, tmp_path: Path) -> None:
        """仅 .claude/skills/ 存在即 PASS。"""
        from ato.preflight import check_project_structure

        project = self._make_project(tmp_path, skills_claude=True)
        proc = _mock_proc(stdout=".git\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            results = await check_project_structure(project)
        statuses = {r.check_item: r.status for r in results}
        assert statuses["bmad_skills"] == "PASS"

    async def test_only_codex_skills_pass(self, tmp_path: Path) -> None:
        """仅 .codex/skills/ 存在即 PASS。"""
        from ato.preflight import check_project_structure

        project = self._make_project(tmp_path, skills_codex=True)
        proc = _mock_proc(stdout=".git\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            results = await check_project_structure(project)
        statuses = {r.check_item: r.status for r in results}
        assert statuses["bmad_skills"] == "PASS"

    async def test_only_agents_skills_pass(self, tmp_path: Path) -> None:
        """仅 .agents/skills/ 存在即 PASS。"""
        from ato.preflight import check_project_structure

        project = self._make_project(tmp_path, skills_agents=True)
        proc = _mock_proc(stdout=".git\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            results = await check_project_structure(project)
        statuses = {r.check_item: r.status for r in results}
        assert statuses["bmad_skills"] == "PASS"

    async def test_missing_ato_yaml_returns_info(self, tmp_path: Path) -> None:
        """缺少 ato.yaml 返回 INFO（init 时将自动生成）。"""
        from ato.preflight import check_project_structure

        project = self._make_project(tmp_path, ato_yaml=False, skills_claude=True)
        proc = _mock_proc(stdout=".git\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            results = await check_project_structure(project)
        statuses = {r.check_item: r.status for r in results}
        assert statuses["ato_yaml"] == "INFO"
        assert "ato.yaml.example" in results[-1].message  # 提示自动生成来源


# ---------------------------------------------------------------------------
# Layer 3: check_artifacts
# ---------------------------------------------------------------------------


class TestLayer3:
    """Layer 3 — 编排前置 Artifact 检查单元测试。"""

    def _make_artifacts_project(
        self,
        tmp_path: Path,
        *,
        epic_whole: bool = False,
        epic_sharded: bool = False,
        prd_whole: bool = False,
        prd_sharded: bool = False,
        arch_whole: bool = False,
        arch_sharded: bool = False,
        ux_whole: bool = False,
        ux_sharded: bool = False,
        project_context: bool = False,
        impl_dir: bool = True,
    ) -> Path:
        """构建测试用 artifact 项目。"""
        project = tmp_path / "project"
        project.mkdir()
        planning = project / "planning"
        planning.mkdir()

        # BMAD config
        bmad_dir = project / "_bmad" / "bmm"
        bmad_dir.mkdir(parents=True)
        (bmad_dir / "config.yaml").write_text(
            "project_name: test\n"
            'planning_artifacts: "{project-root}/planning"\n'
            'implementation_artifacts: "{project-root}/impl"\n'
        )

        if epic_whole:
            (planning / "epics.md").write_text("# Epics")
        if epic_sharded:
            epic_dir = planning / "epics"
            epic_dir.mkdir()
            (epic_dir / "epic-1.md").write_text("# Epic 1")
        if prd_whole:
            (planning / "prd.md").write_text("# PRD")
        if prd_sharded:
            prd_dir = planning / "prd-docs"
            prd_dir.mkdir()
            (prd_dir / "requirements.md").write_text("# Req")
        if arch_whole:
            (planning / "architecture.md").write_text("# Arch")
        if arch_sharded:
            arch_dir = planning / "architecture-docs"
            arch_dir.mkdir()
            (arch_dir / "decisions.md").write_text("# ADR")
        if ux_whole:
            (planning / "ux-design.md").write_text("# UX")
        if ux_sharded:
            ux_dir = planning / "ux-specs"
            ux_dir.mkdir()
            (ux_dir / "flows.md").write_text("# Flows")
        if project_context:
            (project / "project-context.md").write_text("# Context")
        if impl_dir:
            (project / "impl").mkdir()
        return project

    async def test_all_artifacts_present_whole(self, tmp_path: Path) -> None:
        """whole 模式所有 artifact 存在全部 PASS/INFO。"""
        from ato.preflight import check_artifacts

        project = self._make_artifacts_project(
            tmp_path,
            epic_whole=True,
            prd_whole=True,
            arch_whole=True,
            ux_whole=True,
            project_context=True,
        )
        results = await check_artifacts(project)
        statuses = {r.check_item: r.status for r in results}
        assert statuses["epic_files"] == "PASS"
        assert statuses["prd_files"] == "PASS"
        assert statuses["architecture_files"] == "PASS"
        assert statuses["ux_files"] == "PASS"
        assert statuses["project_context"] == "PASS"
        assert statuses["impl_directory"] == "PASS"

    async def test_all_artifacts_present_sharded(self, tmp_path: Path) -> None:
        """sharded 模式所有 artifact 存在全部 PASS。"""
        from ato.preflight import check_artifacts

        project = self._make_artifacts_project(
            tmp_path,
            epic_sharded=True,
            prd_sharded=True,
            arch_sharded=True,
            ux_sharded=True,
        )
        results = await check_artifacts(project)
        statuses = {r.check_item: r.status for r in results}
        assert statuses["epic_files"] == "PASS"
        assert statuses["prd_files"] == "PASS"
        assert statuses["architecture_files"] == "PASS"

    async def test_missing_epic_returns_halt(self, tmp_path: Path) -> None:
        """Epic 文件缺失返回 HALT。"""
        from ato.preflight import check_artifacts

        project = self._make_artifacts_project(tmp_path)
        results = await check_artifacts(project)
        statuses = {r.check_item: r.status for r in results}
        assert statuses["epic_files"] == "HALT"

    async def test_missing_prd_returns_warn(self, tmp_path: Path) -> None:
        """PRD 缺失返回 WARN。"""
        from ato.preflight import check_artifacts

        project = self._make_artifacts_project(tmp_path, epic_whole=True)
        results = await check_artifacts(project)
        statuses = {r.check_item: r.status for r in results}
        assert statuses["prd_files"] == "WARN"

    async def test_missing_architecture_returns_warn(self, tmp_path: Path) -> None:
        """架构文档缺失返回 WARN。"""
        from ato.preflight import check_artifacts

        project = self._make_artifacts_project(tmp_path, epic_whole=True)
        results = await check_artifacts(project)
        statuses = {r.check_item: r.status for r in results}
        assert statuses["architecture_files"] == "WARN"

    async def test_missing_ux_returns_info(self, tmp_path: Path) -> None:
        """UX 设计缺失返回 INFO。"""
        from ato.preflight import check_artifacts

        project = self._make_artifacts_project(tmp_path, epic_whole=True)
        results = await check_artifacts(project)
        statuses = {r.check_item: r.status for r in results}
        assert statuses["ux_files"] == "INFO"

    async def test_missing_project_context_returns_info(self, tmp_path: Path) -> None:
        """project-context.md 缺失返回 INFO。"""
        from ato.preflight import check_artifacts

        project = self._make_artifacts_project(tmp_path, epic_whole=True)
        results = await check_artifacts(project)
        statuses = {r.check_item: r.status for r in results}
        assert statuses["project_context"] == "INFO"

    async def test_impl_dir_auto_created(self, tmp_path: Path) -> None:
        """impl 目录不存在时自动创建。"""
        from ato.preflight import check_artifacts

        project = self._make_artifacts_project(tmp_path, epic_whole=True, impl_dir=False)
        assert not (project / "impl").exists()
        results = await check_artifacts(project)
        statuses = {r.check_item: r.status for r in results}
        assert statuses["impl_directory"] == "PASS"
        assert (project / "impl").is_dir()

    async def test_mixed_whole_and_sharded(self, tmp_path: Path) -> None:
        """whole 和 sharded 混合场景。"""
        from ato.preflight import check_artifacts

        project = self._make_artifacts_project(
            tmp_path,
            epic_whole=True,  # whole
            prd_sharded=True,  # sharded
            arch_whole=True,  # whole
        )
        results = await check_artifacts(project)
        statuses = {r.check_item: r.status for r in results}
        assert statuses["epic_files"] == "PASS"
        assert statuses["prd_files"] == "PASS"
        assert statuses["architecture_files"] == "PASS"

    async def test_impl_dir_readonly_returns_halt(self, tmp_path: Path) -> None:
        """impl 目录存在但只读返回 HALT。"""
        import os
        import sys

        from ato.preflight import check_artifacts

        if sys.platform == "win32":
            pytest.skip("chmod not reliable on Windows")

        project = self._make_artifacts_project(tmp_path, epic_whole=True, impl_dir=True)
        impl_dir = project / "impl"
        # 设为只读
        impl_dir.chmod(0o555)
        try:
            results = await check_artifacts(project)
            statuses = {r.check_item: r.status for r in results}
            # root 用户 os.access(W_OK) 总是返回 True，跳过断言
            if os.getuid() != 0:
                assert statuses["impl_directory"] == "HALT"
                impl_msg = next(r.message for r in results if r.check_item == "impl_directory")
                assert "不可写" in impl_msg
        finally:
            impl_dir.chmod(0o755)

    async def test_impl_dir_write_only_no_exec_returns_halt(self, tmp_path: Path) -> None:
        """impl 目录仅可写但无执行位（0200）返回 HALT — 无法在目录中创建文件。"""
        import os
        import sys

        from ato.preflight import check_artifacts

        if sys.platform == "win32":
            pytest.skip("chmod not reliable on Windows")

        project = self._make_artifacts_project(tmp_path, epic_whole=True, impl_dir=True)
        impl_dir = project / "impl"
        # 仅可写、不可执行 — os.access(W_OK) 会返回 True 但实际创建文件会失败
        impl_dir.chmod(0o200)
        try:
            results = await check_artifacts(project)
            statuses = {r.check_item: r.status for r in results}
            # root 用户 os.access 总是返回 True，跳过断言
            if os.getuid() != 0:
                assert statuses["impl_directory"] == "HALT"
                impl_msg = next(r.message for r in results if r.check_item == "impl_directory")
                assert "不可写" in impl_msg
        finally:
            impl_dir.chmod(0o755)

    async def test_relative_path_in_bmad_config(self, tmp_path: Path) -> None:
        """BMAD config 使用相对路径时，应相对于 project_path 解析。"""
        from ato.preflight import check_artifacts

        project = tmp_path / "project"
        project.mkdir()
        # BMAD config with relative paths (no {project-root})
        bmad_dir = project / "_bmad" / "bmm"
        bmad_dir.mkdir(parents=True)
        (bmad_dir / "config.yaml").write_text(
            "project_name: test\nplanning_artifacts: planning\nimplementation_artifacts: impl\n"
        )
        planning = project / "planning"
        planning.mkdir()
        (planning / "epics.md").write_text("# Epics")
        (project / "impl").mkdir()

        results = await check_artifacts(project)
        statuses = {r.check_item: r.status for r in results}
        # Epic should be found via project_path / "planning", not cwd / "planning"
        assert statuses["epic_files"] == "PASS"

    async def test_missing_bmad_config_still_works(self, tmp_path: Path) -> None:
        """BMAD config 不存在时使用默认路径。"""
        from ato.preflight import check_artifacts

        project = tmp_path / "project"
        project.mkdir()
        results = await check_artifacts(project)
        # Should still return results (using default paths), with HALT for missing epics
        statuses = {r.check_item: r.status for r in results}
        assert "epic_files" in statuses
