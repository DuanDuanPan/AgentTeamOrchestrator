"""test_claude_adapter — Claude CLI 适配器单元测试。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ato.adapters.base import cleanup_process
from ato.adapters.claude_cli import ClaudeAdapter, _classify_error
from ato.models.schemas import ClaudeOutput, CLIAdapterError, ErrorCategory

FIXTURES = Path(__file__).parent.parent / "fixtures"


# ---------------------------------------------------------------------------
# Fixture 加载
# ---------------------------------------------------------------------------


@pytest.fixture()
def success_fixture() -> dict:
    return json.loads((FIXTURES / "claude_output_success.json").read_text())


@pytest.fixture()
def structured_fixture() -> dict:
    return json.loads((FIXTURES / "claude_output_structured.json").read_text())


# ---------------------------------------------------------------------------
# ClaudeOutput.from_json — Snapshot fixture 测试 (AC #5)
# ---------------------------------------------------------------------------


class TestClaudeOutputFromJson:
    def test_success_fixture_parsing(self, success_fixture: dict) -> None:
        output = ClaudeOutput.from_json(success_fixture)
        assert output.status == "success"
        assert output.exit_code == 0
        assert output.text_result == "The implementation looks correct. All tests pass."
        assert output.cost_usd == pytest.approx(0.0125)
        assert output.input_tokens == 1024
        assert output.output_tokens == 256
        assert output.cache_read_input_tokens == 512
        assert output.session_id == "sess-abc-123-def-456"
        assert output.duration_ms == 3500
        assert output.structured_output is None
        assert output.model_usage is not None
        assert output.model_usage["model"] == "claude-opus-4-6"

    def test_structured_fixture_parsing(self, structured_fixture: dict) -> None:
        output = ClaudeOutput.from_json(structured_fixture)
        assert output.status == "success"
        assert output.structured_output is not None
        assert len(output.structured_output["findings"]) == 2
        assert output.cost_usd == pytest.approx(0.035)
        assert output.cache_read_input_tokens == 1024
        assert output.session_id == "sess-struct-789"

    def test_minimal_json_defaults(self) -> None:
        output = ClaudeOutput.from_json({})
        assert output.status == "success"
        assert output.text_result == ""
        assert output.cost_usd == 0.0
        assert output.input_tokens == 0
        assert output.output_tokens == 0
        assert output.cache_read_input_tokens == 0
        assert output.session_id is None

    def test_failure_exit_code(self) -> None:
        output = ClaudeOutput.from_json({"result": "error"}, exit_code=1)
        assert output.status == "failure"
        assert output.exit_code == 1


# ---------------------------------------------------------------------------
# 错误分类
# ---------------------------------------------------------------------------


class TestClassifyError:
    def test_auth_expired_from_stderr(self) -> None:
        cat, retryable = _classify_error(1, "Error: authentication token expired")
        assert cat == ErrorCategory.AUTH_EXPIRED
        assert retryable is True

    def test_rate_limit(self) -> None:
        cat, retryable = _classify_error(1, "Error: rate limit exceeded, too many requests")
        assert cat == ErrorCategory.RATE_LIMIT
        assert retryable is True

    def test_parse_error(self) -> None:
        cat, retryable = _classify_error(1, "Error: JSON decode failed")
        assert cat == ErrorCategory.PARSE_ERROR
        assert retryable is False

    def test_unknown_error(self) -> None:
        cat, retryable = _classify_error(1, "Something went wrong")
        assert cat == ErrorCategory.UNKNOWN
        assert retryable is False

    def test_credential_keyword(self) -> None:
        cat, _ = _classify_error(1, "Invalid credential")
        assert cat == ErrorCategory.AUTH_EXPIRED

    # Fix #3: exit code 分支测试
    def test_exit_code_401_auth(self) -> None:
        cat, retryable = _classify_error(401, "")
        assert cat == ErrorCategory.AUTH_EXPIRED
        assert retryable is True

    def test_exit_code_429_rate_limit(self) -> None:
        cat, retryable = _classify_error(429, "")
        assert cat == ErrorCategory.RATE_LIMIT
        assert retryable is True

    def test_exit_code_neg15_timeout(self) -> None:
        cat, retryable = _classify_error(-15, "")
        assert cat == ErrorCategory.TIMEOUT
        assert retryable is True

    def test_stderr_takes_priority_over_exit_code(self) -> None:
        """stderr 关键字优先于 exit code。"""
        cat, _ = _classify_error(429, "Error: authentication expired")
        assert cat == ErrorCategory.AUTH_EXPIRED


# ---------------------------------------------------------------------------
# 命令构建
# ---------------------------------------------------------------------------


class TestBuildCommand:
    def test_basic_command(self) -> None:
        adapter = ClaudeAdapter()
        cmd = adapter._build_command("hello world")
        assert cmd == ["claude", "-p", "hello world", "--output-format", "json"]

    def test_with_max_turns(self) -> None:
        adapter = ClaudeAdapter()
        cmd = adapter._build_command("prompt", {"max_turns": 5})
        assert "--max-turns" in cmd
        assert "5" in cmd

    def test_with_resume(self) -> None:
        adapter = ClaudeAdapter()
        cmd = adapter._build_command("prompt", {"resume": "sess-123"})
        assert "--resume" in cmd
        assert "sess-123" in cmd

    def test_with_json_schema(self) -> None:
        adapter = ClaudeAdapter()
        schema = {"type": "object", "properties": {"foo": {"type": "string"}}}
        cmd = adapter._build_command("prompt", {"json_schema": schema})
        assert "--json-schema" in cmd


# ---------------------------------------------------------------------------
# execute() — 成功 / 失败 / 超时 mock 测试
# ---------------------------------------------------------------------------


def _mock_process(stdout: bytes, stderr: bytes = b"", returncode: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.pid = 12345
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.terminate = MagicMock()
    proc.kill = MagicMock()
    proc.wait = AsyncMock(return_value=returncode)
    return proc


class TestClaudeAdapterExecute:
    async def test_success_execution(self, success_fixture: dict) -> None:
        proc = _mock_process(json.dumps(success_fixture).encode())
        adapter = ClaudeAdapter()
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            result = await adapter.execute("test prompt")
        assert result.status == "success"
        assert result.cost_usd == pytest.approx(0.0125)

    async def test_nonzero_exit_raises(self) -> None:
        proc = _mock_process(b"", b"Error: auth token expired", returncode=1)
        adapter = ClaudeAdapter()
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)), \
             pytest.raises(CLIAdapterError) as exc_info:
            await adapter.execute("test")
        assert exc_info.value.category == ErrorCategory.AUTH_EXPIRED
        assert exc_info.value.retryable is True

    async def test_invalid_json_raises_parse_error(self) -> None:
        proc = _mock_process(b"not valid json", returncode=0)
        adapter = ClaudeAdapter()
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)), \
             pytest.raises(CLIAdapterError) as exc_info:
            await adapter.execute("test")
        assert exc_info.value.category == ErrorCategory.PARSE_ERROR

    async def test_timeout_raises(self) -> None:
        proc = MagicMock()
        proc.pid = 99
        proc.returncode = None
        proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        proc.terminate = MagicMock()
        proc.kill = MagicMock()
        proc.wait = AsyncMock()
        adapter = ClaudeAdapter()
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)), \
             pytest.raises(CLIAdapterError) as exc_info:
            await adapter.execute("test", {"timeout": 1})
        assert exc_info.value.category == ErrorCategory.TIMEOUT
        assert exc_info.value.retryable is True

    async def test_on_process_start_callback(self, success_fixture: dict) -> None:
        proc = _mock_process(json.dumps(success_fixture).encode())
        callback = AsyncMock()
        adapter = ClaudeAdapter()
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            await adapter.execute("test", on_process_start=callback)
        callback.assert_awaited_once_with(proc)


# ---------------------------------------------------------------------------
# cleanup_process — 三阶段清理协议
# ---------------------------------------------------------------------------


class TestCleanupProcess:
    async def test_already_terminated(self) -> None:
        proc = MagicMock()
        proc.returncode = 0
        await cleanup_process(proc)
        proc.terminate.assert_not_called()

    async def test_terminate_succeeds(self) -> None:
        proc = MagicMock()
        proc.returncode = None
        proc.terminate = MagicMock()
        proc.wait = AsyncMock(return_value=0)
        await cleanup_process(proc, timeout=1)
        proc.terminate.assert_called_once()
        proc.kill.assert_not_called()

    async def test_terminate_then_kill(self) -> None:
        proc = MagicMock()
        proc.returncode = None
        proc.terminate = MagicMock()
        proc.kill = MagicMock()

        call_count = 0

        async def slow_wait() -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # 第一次 wait：模拟进程不响应 SIGTERM（挂起足够久让 wait_for 超时）
                await asyncio.sleep(10)
            # 第二次 wait（kill 后）：立即返回

        proc.wait = slow_wait
        await cleanup_process(proc, timeout=0.01)
        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()
        assert call_count == 2
