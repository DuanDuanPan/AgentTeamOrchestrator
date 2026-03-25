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
