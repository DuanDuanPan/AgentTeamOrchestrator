"""CLI start/stop 命令测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

from ato.cli import _derive_project_root, _resolve_config_path, app

runner = CliRunner()


class TestStartCommand:
    def test_rejects_duplicate_start(self, tmp_path: Path) -> None:
        """Orchestrator 已运行时拒绝重复启动，exit code 1。"""
        db_path = tmp_path / ".ato" / "state.db"

        with patch("ato.core.is_orchestrator_running", return_value=True):
            result = runner.invoke(app, ["start", "--db-path", str(db_path)])

        assert result.exit_code == 1
        assert "已在运行中" in result.output


class TestDeriveProjectRoot:
    """8.5 AC5: 从 db_path 推导项目根目录。"""

    def test_standard_layout(self, tmp_path: Path) -> None:
        """标准 .ato/state.db 布局推导到祖父目录。"""
        project = tmp_path / "myproject"
        project.mkdir()
        ato_dir = project / ".ato"
        ato_dir.mkdir()
        db_path = ato_dir / "state.db"

        assert _derive_project_root(db_path) == project

    def test_custom_db_same_dir_with_ato_yaml(self, tmp_path: Path) -> None:
        """自���义 db 同级目录有 ato.yaml 时推导到 db 所在目录。"""
        custom_dir = tmp_path / "custom"
        custom_dir.mkdir()
        (custom_dir / "ato.yaml").write_text("roles: {}\n")
        db_path = custom_dir / "my.db"

        assert _derive_project_root(db_path) == custom_dir

    def test_fallback_to_cwd(self, tmp_path: Path) -> None:
        """无标准布局且无 ato.yaml 时回退到 cwd。"""
        db_path = tmp_path / "random" / "my.db"
        root = _derive_project_root(db_path)
        assert root == Path.cwd()


class TestResolveConfigPath:
    """8.5 AC5: 配置发现优先级。"""

    def test_explicit_config_takes_priority(self, tmp_path: Path) -> None:
        """显式 --config 路径直接返回，不检查存在性。"""
        explicit = tmp_path / "custom" / "ato.yaml"
        db_path = tmp_path / ".ato" / "state.db"
        assert _resolve_config_path(explicit, db_path) == explicit

    def test_grandparent_discovery(self, tmp_path: Path) -> None:
        """标准布局从 db 祖父目录发现 ato.yaml。"""
        project = tmp_path / "proj"
        project.mkdir()
        (project / "ato.yaml").write_text("roles: {}\n")
        ato_dir = project / ".ato"
        ato_dir.mkdir()
        db_path = ato_dir / "state.db"

        assert _resolve_config_path(None, db_path) == project / "ato.yaml"

    def test_parent_discovery(self, tmp_path: Path) -> None:
        """db 同级目录有 ato.yaml 时发现。"""
        custom_dir = tmp_path / "custom"
        custom_dir.mkdir()
        (custom_dir / "ato.yaml").write_text("roles: {}\n")
        db_path = custom_dir / "state.db"

        assert _resolve_config_path(None, db_path) == custom_dir / "ato.yaml"

    def test_custom_db_prefers_same_dir_over_grandparent(self, tmp_path: Path) -> None:
        """custom db: 同级目录 ato.yaml 优先于祖父目录的 ato.yaml。"""
        parent_proj = tmp_path / "parent"
        parent_proj.mkdir()
        (parent_proj / "ato.yaml").write_text("# parent config\n")

        subproj = parent_proj / "subproj"
        subproj.mkdir()
        (subproj / "ato.yaml").write_text("# subproj config\n")

        db_path = subproj / "custom.db"
        result = _resolve_config_path(None, db_path)
        assert result == subproj / "ato.yaml"

    def test_standard_layout_ignores_stray_ato_dir_config(self, tmp_path: Path) -> None:
        """标准 .ato/state.db 布局：即使 .ato/ 下有 ato.yaml 也应选项目根的。"""
        project = tmp_path / "project"
        project.mkdir()
        (project / "ato.yaml").write_text("# correct project config\n")

        ato_dir = project / ".ato"
        ato_dir.mkdir()
        (ato_dir / "ato.yaml").write_text("# stray config — should NOT be picked\n")

        db_path = ato_dir / "state.db"
        result = _resolve_config_path(None, db_path)
        assert result == project / "ato.yaml"

    def test_returns_none_when_not_found(self, tmp_path: Path, monkeypatch: object) -> None:
        """所有候选都不存在时返回 None。"""
        db_path = tmp_path / "nowhere" / ".ato" / "state.db"
        # CWD 下也不能有 ato.yaml — chdir 到一个已知空目录
        empty_cwd = tmp_path / "empty_cwd"
        empty_cwd.mkdir()
        monkeypatch.chdir(empty_cwd)  # type: ignore[union-attr]

        result = _resolve_config_path(None, db_path)
        assert result is None


class TestStartDbPathProjectRoot:
    """8.5 AC5: start --db-path 指向其他项目时，从该项目根做 preflight/config。"""

    def test_start_uses_db_derived_project_root_for_preflight_and_config(
        self, tmp_path: Path
    ) -> None:
        """start 使用从 db_path 推导的项目根做 preflight 和配置加载，而非 cwd。"""
        project = tmp_path / "myproject"
        project.mkdir()
        ato_dir = project / ".ato"
        ato_dir.mkdir()
        db_path = ato_dir / "state.db"
        (project / "ato.yaml").write_text("roles:\n  dev:\n    cli: claude\n")

        mock_preflight = AsyncMock(return_value=[])

        with (
            patch("ato.core.is_orchestrator_running", return_value=False),
            patch("ato.logging.configure_logging"),
            patch("ato.preflight.run_preflight", mock_preflight),
            patch("ato.config.load_config") as mock_load,
            patch("ato.core.Orchestrator") as mock_orch,
        ):
            mock_load.return_value = MagicMock()
            mock_orch.return_value.run = AsyncMock()
            runner.invoke(app, ["start", "--db-path", str(db_path)])

        # preflight 应该以 project 作为第一个参数（而非 cwd）
        mock_preflight.assert_called_once()
        assert mock_preflight.call_args[0][0] == project

        # load_config 应该使用 project 下的 ato.yaml（而非 cwd）
        mock_load.assert_called_once()
        assert mock_load.call_args[0][0] == project / "ato.yaml"


class TestStopCommand:
    def test_no_pid_file_friendly_message(self, tmp_path: Path) -> None:
        """PID 文件不存在时输出友好提示。"""
        pid_path = tmp_path / "nonexistent.pid"
        result = runner.invoke(app, ["stop", "--pid-file", str(pid_path)])

        assert result.exit_code == 0
        assert "未在运行" in result.output

    def test_stale_pid_cleanup(self, tmp_path: Path) -> None:
        """进程不存活时清理 PID 文件并提示。"""
        pid_path = tmp_path / "orchestrator.pid"
        pid_path.write_text("9999999")

        with patch("ato.cli.os.kill", side_effect=ProcessLookupError):
            result = runner.invoke(app, ["stop", "--pid-file", str(pid_path)])

        assert result.exit_code == 0
        assert "已不存在" in result.output

    def test_stop_cleans_pid_after_default_sigterm_exit(self, tmp_path: Path) -> None:
        """进程因默认 SIGTERM（handler 未注册）退出时，stop 也清理 PID 文件。

        模拟：第一次 kill(pid, 0) 成功（进程存活），发送 SIGTERM 成功，
        然后 poll 时 kill(pid, 0) 抛 ProcessLookupError（进程已退出）。
        """
        pid_path = tmp_path / "orchestrator.pid"
        pid_path.write_text("42")

        call_count = 0

        def mock_kill(pid: int, sig: int) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # 第一次 kill(pid, 0) — 进程存活
                return
            if call_count == 2:
                # SIGTERM 发送成功
                return
            # 后续 poll kill(pid, 0) — 进程已退出
            raise ProcessLookupError

        with patch("ato.cli.os.kill", side_effect=mock_kill):
            result = runner.invoke(app, ["stop", "--pid-file", str(pid_path)])

        assert result.exit_code == 0
        assert "已停止" in result.output
        # PID 文件必须被清理
        assert not pid_path.exists()
