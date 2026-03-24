"""CLI start/stop 命令测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from ato.cli import app

runner = CliRunner()


class TestStartCommand:
    def test_rejects_duplicate_start(self, tmp_path: Path) -> None:
        """Orchestrator 已运行时拒绝重复启动，exit code 1。"""
        db_path = tmp_path / ".ato" / "state.db"

        with patch("ato.core.is_orchestrator_running", return_value=True):
            result = runner.invoke(app, ["start", "--db-path", str(db_path)])

        assert result.exit_code == 1
        assert "已在运行中" in result.output


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
