"""ato tui CLI 命令单元测试。"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from ato.cli import app as cli_app

runner = CliRunner()


def test_tui_db_not_found(tmp_path: Path) -> None:
    """数据库不存在时退出码 1，输出错误提示。"""
    db_path = tmp_path / ".ato" / "state.db"
    result = runner.invoke(cli_app, ["tui", "--db-path", str(db_path)])
    assert result.exit_code == 1
    assert "数据库未找到" in result.output


def test_tui_starts_with_valid_db(tmp_path: Path) -> None:
    """数据库存在时启动 ATOApp。"""
    db_path = tmp_path / ".ato" / "state.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.touch()

    with patch("ato.tui.app.ATOApp") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        runner.invoke(cli_app, ["tui", "--db-path", str(db_path)])
        mock_app_cls.assert_called_once()
        mock_app.run.assert_called_once()


def test_tui_orchestrator_not_running_warning(tmp_path: Path) -> None:
    """Orchestrator 未运行时打印警告但仍启动 TUI。"""
    db_path = tmp_path / ".ato" / "state.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.touch()
    # 无 PID 文件 → Orchestrator 未运行

    with patch("ato.tui.app.ATOApp") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        result = runner.invoke(cli_app, ["tui", "--db-path", str(db_path)])
        assert "Orchestrator 未运行" in result.output
        mock_app.run.assert_called_once()


def test_tui_orchestrator_running_passes_pid(tmp_path: Path) -> None:
    """Orchestrator 运行时传递 PID 给 ATOApp。"""
    db_path = tmp_path / ".ato" / "state.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.touch()
    pid_path = tmp_path / ".ato" / "orchestrator.pid"
    pid_path.write_text(str(os.getpid()))  # 使用当前进程 PID（保证存活）

    with patch("ato.tui.app.ATOApp") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        runner.invoke(cli_app, ["tui", "--db-path", str(db_path)])
        mock_app_cls.assert_called_once()
        call_kwargs = mock_app_cls.call_args[1]
        assert call_kwargs["orchestrator_pid"] == os.getpid()


def test_tui_stale_pid_warning(tmp_path: Path) -> None:
    """Stale PID 文件时打印警告但仍启动 TUI。"""
    db_path = tmp_path / ".ato" / "state.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.touch()
    pid_path = tmp_path / ".ato" / "orchestrator.pid"
    pid_path.write_text("999999")  # 不存在的 PID

    with patch("ato.tui.app.ATOApp") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        result = runner.invoke(cli_app, ["tui", "--db-path", str(db_path)])
        assert "stale PID" in result.output or "Orchestrator 未运行" in result.output
        mock_app.run.assert_called_once()
        call_kwargs = mock_app_cls.call_args[1]
        assert call_kwargs["orchestrator_pid"] is None


def test_tui_default_db_path_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """默认 db_path (.ato/state.db) 不存在时退出码 1。"""
    monkeypatch.chdir(tmp_path)  # 确保 CWD 无 .ato/state.db
    result = runner.invoke(cli_app, ["tui"], catch_exceptions=False)
    assert result.exit_code == 1
    assert "数据库未找到" in result.output


def _make_phase(
    name: str, role: str, ptype: str, success: str, failure: str | None = None
) -> dict[str, str | None]:
    d: dict[str, str | None] = {
        "name": name,
        "role": role,
        "type": ptype,
        "next_on_success": success,
    }
    if failure:
        d["next_on_failure"] = failure
    return d


def _write_ato_yaml(directory: Path, *, max_rounds: int) -> Path:
    """辅助：在 directory 下写入一份合法 ato.yaml，返回文件路径。"""
    import yaml

    sj = "structured_job"
    config = {
        "roles": {"dev": {"cli": "claude", "model": "opus"}},
        "phases": [
            _make_phase("creating", "dev", sj, "validating"),
            _make_phase("validating", "dev", sj, "dev_ready", "creating"),
            _make_phase("dev_ready", "dev", sj, "developing"),
            _make_phase("developing", "dev", sj, "reviewing"),
            _make_phase("reviewing", "dev", sj, "qa_testing", "fixing"),
            _make_phase("fixing", "dev", sj, "reviewing"),
            _make_phase("qa_testing", "dev", sj, "uat", "fixing"),
            _make_phase("uat", "dev", "interactive_session", "merging"),
            _make_phase("merging", "dev", sj, "regression"),
            _make_phase("regression", "dev", sj, "done"),
        ],
        "convergent_loop": {"max_rounds": max_rounds},
    }
    yaml_path = directory / "ato.yaml"
    yaml_path.write_text(yaml.dump(config))
    return yaml_path


def test_tui_config_loaded_from_db_parent(tmp_path: Path) -> None:
    """标准布局：.ato/state.db 的项目根有 ato.yaml，应正确加载。"""
    project_root = tmp_path / "target_project"
    db_path = project_root / ".ato" / "state.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.touch()
    _write_ato_yaml(project_root, max_rounds=7)

    with patch("ato.tui.app.ATOApp") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        runner.invoke(cli_app, ["tui", "--db-path", str(db_path)])
        call_kwargs = mock_app_cls.call_args[1]
        assert call_kwargs["convergent_loop_max_rounds"] == 7


def test_tui_config_explicit_flag(tmp_path: Path) -> None:
    """显式 --config 优先于所有自动发现。"""
    db_path = tmp_path / ".ato" / "state.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.touch()
    (tmp_path / "elsewhere").mkdir(parents=True, exist_ok=True)
    cfg = _write_ato_yaml(tmp_path / "elsewhere", max_rounds=11)

    with patch("ato.tui.app.ATOApp") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        runner.invoke(
            cli_app,
            ["tui", "--db-path", str(db_path), "--config", str(cfg)],
        )
        call_kwargs = mock_app_cls.call_args[1]
        assert call_kwargs["convergent_loop_max_rounds"] == 11


def test_tui_config_explicit_missing_exits_with_error(tmp_path: Path) -> None:
    """显式 --config 指向不存在的文件时应退出码非 0，而非静默降级。"""
    db_path = tmp_path / ".ato" / "state.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.touch()

    result = runner.invoke(
        cli_app,
        ["tui", "--db-path", str(db_path), "--config", str(tmp_path / "missing.yaml")],
    )
    assert result.exit_code != 0, (
        "显式 --config 指向不存在文件时应报错退出，不应静默启动 TUI"
    )


def test_tui_config_auto_discover_failure_still_starts(tmp_path: Path) -> None:
    """自动发现全部失败时 TUI 仍以默认值启动（不报错）。"""
    db_path = tmp_path / "isolated" / ".ato" / "state.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.touch()
    # 无 ato.yaml 在任何搜索位置

    with patch("ato.tui.app.ATOApp") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        result = runner.invoke(cli_app, ["tui", "--db-path", str(db_path)])
        assert result.exit_code == 0 or result.exit_code is None
        call_kwargs = mock_app_cls.call_args[1]
        assert call_kwargs["convergent_loop_max_rounds"] == 3


def test_tui_config_custom_db_path_same_dir(tmp_path: Path) -> None:
    """自定义 db 路径：同目录有 ato.yaml 时应被发现。

    复现场景：项目目录有 custom-state.db + ato.yaml(max_rounds=11)，
    执行 ato tui --db-path ./custom-state.db 应收到 11 而非默认 3。
    """
    db_path = tmp_path / "custom-state.db"
    db_path.touch()
    _write_ato_yaml(tmp_path, max_rounds=11)

    with patch("ato.tui.app.ATOApp") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        runner.invoke(cli_app, ["tui", "--db-path", str(db_path)])
        call_kwargs = mock_app_cls.call_args[1]
        assert call_kwargs["convergent_loop_max_rounds"] == 11
