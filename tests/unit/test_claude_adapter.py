"""test_claude_adapter — Claude CLI 适配器单元测试。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ato.adapters.base import cleanup_process
from ato.adapters.claude_cli import ClaudeAdapter, _classify_error, _normalize_claude_event
from ato.models.schemas import ClaudeOutput, CLIAdapterError, ErrorCategory, ProgressEvent

FIXTURES = Path(__file__).parent.parent / "fixtures"


# ---------------------------------------------------------------------------
# Fixture 加载
# ---------------------------------------------------------------------------


@pytest.fixture()
def success_fixture() -> dict[str, Any]:
    result: dict[str, Any] = json.loads((FIXTURES / "claude_output_success.json").read_text())
    return result


@pytest.fixture()
def structured_fixture() -> dict[str, Any]:
    result: dict[str, Any] = json.loads((FIXTURES / "claude_output_structured.json").read_text())
    return result


@pytest.fixture()
def stream_success_lines() -> list[bytes]:
    """Load claude_stream_success.jsonl as list of bytes lines."""
    text = (FIXTURES / "claude_stream_success.jsonl").read_text()
    return [line.encode() + b"\n" for line in text.strip().splitlines() if line.strip()]


@pytest.fixture()
def stream_tool_use_lines() -> list[bytes]:
    """Load claude_stream_tool_use.jsonl as list of bytes lines."""
    text = (FIXTURES / "claude_stream_tool_use.jsonl").read_text()
    return [line.encode() + b"\n" for line in text.strip().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# ClaudeOutput.from_json — Snapshot fixture 测试 (AC #5)
# ---------------------------------------------------------------------------


class TestClaudeOutputFromJson:
    def test_success_fixture_parsing(self, success_fixture: dict[str, Any]) -> None:
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

    def test_structured_fixture_parsing(self, structured_fixture: dict[str, Any]) -> None:
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
        assert cmd == [
            "claude", "--dangerously-skip-permissions",
            "-p", "hello world", "--output-format", "stream-json", "--verbose",
        ]

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
# Streaming mock helper
# ---------------------------------------------------------------------------


def _mock_stream_process(
    stdout_lines: list[bytes],
    stderr_data: bytes = b"",
    returncode: int = 0,
) -> MagicMock:
    """Create a mock process for streaming tests."""
    proc = MagicMock()
    proc.pid = 12345
    proc.returncode = returncode
    proc.terminate = MagicMock()
    proc.kill = MagicMock()
    proc.wait = AsyncMock(return_value=returncode)

    # Mock stdout as StreamReader — readline returns lines then empty bytes
    stdout = MagicMock()
    _lines = list(stdout_lines) + [b""]
    _idx = 0

    async def _readline() -> bytes:
        nonlocal _idx
        if _idx < len(_lines):
            line = _lines[_idx]
            _idx += 1
            return line
        return b""

    stdout.readline = _readline

    # Mock stderr as StreamReader — read returns data then empty
    stderr = MagicMock()
    _stderr_read = False

    async def _read(n: int = 4096) -> bytes:
        nonlocal _stderr_read
        if not _stderr_read:
            _stderr_read = True
            return stderr_data
        return b""

    stderr.read = _read

    proc.stdout = stdout
    proc.stderr = stderr
    return proc


# ---------------------------------------------------------------------------
# execute() — 流式 mock 测试
# ---------------------------------------------------------------------------


class TestClaudeAdapterStreaming:
    async def test_stream_success_with_progress(self, stream_success_lines: list[bytes]) -> None:
        """AC 1: on_progress 收到正确数量和类型的 ProgressEvent。"""
        proc = _mock_stream_process(stream_success_lines)
        events_received: list[ProgressEvent] = []

        async def on_progress(event: ProgressEvent) -> None:
            events_received.append(event)

        adapter = ClaudeAdapter()
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            result = await adapter.execute("test", on_progress=on_progress)

        assert result.status == "success"
        assert result.cost_usd == pytest.approx(0.0125)
        # 3 lines: system, assistant, result
        assert len(events_received) == 3
        assert events_received[0].event_type == "init"
        assert events_received[1].event_type == "text"
        assert events_received[2].event_type == "result"

    async def test_stream_success_without_progress(
        self, stream_success_lines: list[bytes]
    ) -> None:
        """AC 3: 不传 on_progress，ClaudeOutput 字段与改造前一致。"""
        proc = _mock_stream_process(stream_success_lines)
        adapter = ClaudeAdapter()
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            result = await adapter.execute("test")

        assert result.status == "success"
        assert result.cost_usd == pytest.approx(0.0125)
        assert result.input_tokens == 1024
        assert result.output_tokens == 256
        assert result.session_id == "sess-abc-123-def-456"

    async def test_stream_tool_use_events(self, stream_tool_use_lines: list[bytes]) -> None:
        """AC 5 verified via fixture: tool_use events correctly normalized."""
        proc = _mock_stream_process(stream_tool_use_lines)
        events_received: list[ProgressEvent] = []

        async def on_progress(event: ProgressEvent) -> None:
            events_received.append(event)

        adapter = ClaudeAdapter()
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            result = await adapter.execute("test", on_progress=on_progress)

        assert result.status == "success"
        # Find tool_use events
        tool_use_events = [e for e in events_received if e.event_type == "tool_use"]
        assert len(tool_use_events) >= 1

    async def test_stream_no_result_event(self) -> None:
        """stdout 无 result 事件 → PARSE_ERROR。"""
        lines = [b'{"type": "system", "session_id": "s1"}\n']
        proc = _mock_stream_process(lines)
        adapter = ClaudeAdapter()
        with (
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
            pytest.raises(CLIAdapterError) as exc_info,
        ):
            await adapter.execute("test")
        assert exc_info.value.category == ErrorCategory.PARSE_ERROR

    async def test_stream_parse_error_emits_error_event(self) -> None:
        """AC 13: parse error → on_progress 收到 error 事件。"""
        lines = [b'{"type": "system", "session_id": "s1"}\n']
        proc = _mock_stream_process(lines)
        events_received: list[ProgressEvent] = []

        async def on_progress(event: ProgressEvent) -> None:
            events_received.append(event)

        adapter = ClaudeAdapter()
        with (
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
            pytest.raises(CLIAdapterError) as exc_info,
        ):
            await adapter.execute("test", on_progress=on_progress)

        assert exc_info.value.category == ErrorCategory.PARSE_ERROR
        error_events = [e for e in events_received if e.event_type == "error"]
        assert len(error_events) == 1
        assert "未收到 result 事件" in error_events[0].summary

    async def test_stream_timeout_cleanup(self) -> None:
        """AC 9: 超时 → cleanup_process + stderr_task cancel + error event。"""
        proc = MagicMock()
        proc.pid = 99
        proc.returncode = None
        proc.terminate = MagicMock()
        proc.kill = MagicMock()
        proc.wait = AsyncMock()

        # stdout.readline blocks forever (simulates timeout)
        async def _hanging_readline() -> bytes:
            await asyncio.sleep(100)
            return b""

        proc.stdout = MagicMock()
        proc.stdout.readline = _hanging_readline

        stderr = MagicMock()
        stderr.read = AsyncMock(return_value=b"")
        proc.stderr = stderr

        events_received: list[ProgressEvent] = []

        async def on_progress(event: ProgressEvent) -> None:
            events_received.append(event)

        adapter = ClaudeAdapter()
        with (
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
            pytest.raises(CLIAdapterError) as exc_info,
        ):
            await adapter.execute("test", {"timeout": 0.1}, on_progress=on_progress)
        assert exc_info.value.category == ErrorCategory.TIMEOUT
        assert exc_info.value.retryable is True
        # Error event should have been emitted
        error_events = [e for e in events_received if e.event_type == "error"]
        assert len(error_events) == 1

    async def test_stream_error_emits_error_event(self) -> None:
        """AC 13: 非零退出码 → on_progress 收到 error 事件。"""
        # Send a result event so _consume_stream doesn't raise PARSE_ERROR
        result_line = json.dumps({"type": "result", "total_cost_usd": 0.0}).encode() + b"\n"
        proc = _mock_stream_process(
            [result_line],
            stderr_data=b"Error: auth token expired",
            returncode=1,
        )

        events_received: list[ProgressEvent] = []

        async def on_progress(event: ProgressEvent) -> None:
            events_received.append(event)

        adapter = ClaudeAdapter()
        with (
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
            pytest.raises(CLIAdapterError),
        ):
            await adapter.execute("test", on_progress=on_progress)
        error_events = [e for e in events_received if e.event_type == "error"]
        assert len(error_events) >= 1

    async def test_nonzero_exit_no_result_event_classifies_correctly(self) -> None:
        """F1 fix: 进程非零退出且无 result 事件 → 错误从 stderr/exit_code 正确分类。"""
        # 真实场景：进程崩溃，只输出了一个 system init 事件就退出
        init_line = json.dumps({"type": "system", "session_id": "s1"}).encode() + b"\n"
        proc = _mock_stream_process(
            [init_line],
            stderr_data=b"Error: auth token expired",
            returncode=1,
        )

        events_received: list[ProgressEvent] = []

        async def on_progress(event: ProgressEvent) -> None:
            events_received.append(event)

        adapter = ClaudeAdapter()
        with (
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
            pytest.raises(CLIAdapterError) as exc_info,
        ):
            await adapter.execute("test", on_progress=on_progress)
        # 应为 AUTH_EXPIRED 而非 PARSE_ERROR
        assert exc_info.value.category == ErrorCategory.AUTH_EXPIRED
        assert exc_info.value.retryable is True
        # 应发出 error 事件
        error_events = [e for e in events_received if e.event_type == "error"]
        assert len(error_events) >= 1

    async def test_on_process_start_callback(self, stream_success_lines: list[bytes]) -> None:
        proc = _mock_stream_process(stream_success_lines)
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
        await cleanup_process(proc, timeout=0)
        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()
