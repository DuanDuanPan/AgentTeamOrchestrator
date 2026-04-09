"""test_codex_adapter — Codex CLI 适配器单元测试。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ato.adapters.codex_cli import (
    CodexAdapter,
    _aggregate_usage,
    _classify_error,
    _extract_text_result,
    _parse_jsonl,
    _parse_output_file,
    calculate_cost,
)
from ato.models.schemas import CLIAdapterError, CodexOutput, ErrorCategory, ProgressEvent

FIXTURES = Path(__file__).parent.parent / "fixtures"


# ---------------------------------------------------------------------------
# Fixture 加载
# ---------------------------------------------------------------------------


@pytest.fixture()
def success_events() -> list[dict[str, Any]]:
    lines = (FIXTURES / "codex_events_success.jsonl").read_text().strip().splitlines()
    return [json.loads(line) for line in lines]


@pytest.fixture()
def success_output() -> dict[str, Any]:
    result: dict[str, Any] = json.loads((FIXTURES / "codex_output_success.json").read_text())
    return result


@pytest.fixture()
def error_events() -> list[dict[str, Any]]:
    lines = (FIXTURES / "codex_events_error.jsonl").read_text().strip().splitlines()
    return [json.loads(line) for line in lines]


@pytest.fixture()
def success_events_raw() -> str:
    return (FIXTURES / "codex_events_success.jsonl").read_text()


@pytest.fixture()
def success_output_raw() -> str:
    return (FIXTURES / "codex_output_success.json").read_text()


@pytest.fixture()
def stream_success_lines() -> list[bytes]:
    text = (FIXTURES / "codex_stream_success.jsonl").read_text()
    return [line.encode() + b"\n" for line in text.strip().splitlines() if line.strip()]


@pytest.fixture()
def stream_tool_use_lines() -> list[bytes]:
    text = (FIXTURES / "codex_stream_tool_use.jsonl").read_text()
    return [line.encode() + b"\n" for line in text.strip().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# TestParseJsonl — JSONL 逐行解析 + 容错
# ---------------------------------------------------------------------------


class TestParseJsonl:
    def test_parse_valid_jsonl(self, success_events_raw: str) -> None:
        events = _parse_jsonl(success_events_raw)
        assert len(events) == 4
        assert events[0]["type"] == "thread.started"
        assert events[-1]["type"] == "turn.completed"

    def test_skip_empty_lines(self) -> None:
        raw = '{"type":"turn.started"}\n\n\n{"type":"turn.completed","usage":{}}\n'
        events = _parse_jsonl(raw)
        assert len(events) == 2

    def test_skip_non_json_lines(self) -> None:
        raw = 'not json\n{"type":"turn.started"}\nmore garbage\n'
        events = _parse_jsonl(raw)
        assert len(events) == 1
        assert events[0]["type"] == "turn.started"


# ---------------------------------------------------------------------------
# TestAggregateUsage — token 聚合
# ---------------------------------------------------------------------------


class TestAggregateUsage:
    def test_single_turn(self, success_events: list[dict[str, Any]]) -> None:
        inp, cached, out = _aggregate_usage(success_events)
        assert inp == 26024
        assert cached == 10624
        assert out == 29

    def test_multiple_turns(self) -> None:
        usage1 = {"input_tokens": 100, "cached_input_tokens": 30, "output_tokens": 10}
        usage2 = {"input_tokens": 200, "cached_input_tokens": 50, "output_tokens": 20}
        events = [
            {"type": "turn.completed", "usage": usage1},
            {"type": "turn.completed", "usage": usage2},
        ]
        inp, cached, out = _aggregate_usage(events)
        assert inp == 300
        assert cached == 80
        assert out == 30

    def test_no_turn_completed(self) -> None:
        events = [{"type": "thread.started", "thread_id": "t1"}]
        inp, cached, out = _aggregate_usage(events)
        assert (inp, cached, out) == (0, 0, 0)


# ---------------------------------------------------------------------------
# TestExtractTextResult — 文本结果提取 + 旧版兼容
# ---------------------------------------------------------------------------


class TestExtractTextResult:
    def test_current_format_item_text(self, success_events: list[dict[str, Any]]) -> None:
        text = _extract_text_result(success_events)
        assert "findings" in text
        parsed = json.loads(text)
        assert len(parsed["findings"]) == 1

    def test_legacy_format_item_content(self) -> None:
        events = [
            {
                "type": "item.completed",
                "item": {
                    "id": "item_0",
                    "type": "agent_message",
                    "content": [
                        {"type": "text", "text": "part1"},
                        {"type": "text", "text": "part2"},
                    ],
                },
            },
        ]
        text = _extract_text_result(events)
        assert text == "part1part2"

    def test_last_item_wins(self) -> None:
        item0 = {"id": "item_0", "type": "agent_message", "text": "first"}
        item1 = {"id": "item_1", "type": "agent_message", "text": "second"}
        events = [
            {"type": "item.completed", "item": item0},
            {"type": "item.completed", "item": item1},
        ]
        text = _extract_text_result(events)
        assert text == "second"

    def test_no_items_returns_empty(self) -> None:
        events = [{"type": "turn.completed", "usage": {}}]
        text = _extract_text_result(events)
        assert text == ""

    def test_tool_result_does_not_override_agent_message(self) -> None:
        """R3: item.type != agent_message 应被跳过。"""
        events = [
            {
                "type": "item.completed",
                "item": {"id": "i0", "type": "agent_message", "text": "findings"},
            },
            {
                "type": "item.completed",
                "item": {"id": "i1", "type": "tool_result", "text": "tool output"},
            },
        ]
        text = _extract_text_result(events)
        assert text == "findings"


# ---------------------------------------------------------------------------
# TestParseOutputFile — JSON / 文本 fallback
# ---------------------------------------------------------------------------


class TestParseOutputFile:
    def test_valid_json(self, success_output_raw: str) -> None:
        structured, text = _parse_output_file(success_output_raw)
        assert structured is not None
        assert len(structured["findings"]) == 2
        assert text == success_output_raw

    def test_non_json_fallback(self) -> None:
        structured, text = _parse_output_file("just plain text")
        assert structured is None
        assert text == "just plain text"

    def test_json_array_not_dict(self) -> None:
        structured, text = _parse_output_file("[1, 2, 3]")
        assert structured is None
        assert text == "[1, 2, 3]"


# ---------------------------------------------------------------------------
# TestCodexOutputFromEvents — fixture 解析 → CodexOutput (AC #4)
# ---------------------------------------------------------------------------


class TestCodexOutputFromEvents:
    def test_success_fixture(
        self, success_events: list[dict[str, Any]], success_output_raw: str
    ) -> None:
        cost = calculate_cost("codex-mini-latest", 26024, 29, cached_input_tokens=10624)
        output = CodexOutput.from_events(
            success_events,
            exit_code=0,
            output_file_content=success_output_raw,
            model_name="codex-mini-latest",
            cost_usd=cost,
        )
        assert output.status == "success"
        assert output.exit_code == 0
        assert output.input_tokens == 26024
        assert output.output_tokens == 29
        assert output.cache_read_input_tokens == 10624
        assert output.session_id == "thread-codex-abc-123"
        assert output.model_name == "codex-mini-latest"
        assert output.structured_output is not None
        assert len(output.structured_output["findings"]) == 2
        assert output.cost_usd == pytest.approx(cost)

    def test_without_output_file(self, success_events: list[dict[str, Any]]) -> None:
        output = CodexOutput.from_events(
            success_events, exit_code=0, model_name="codex-mini-latest", cost_usd=0.01
        )
        assert output.structured_output is None
        assert "findings" in output.text_result

    def test_failure_exit_code(self, success_events: list[dict[str, Any]]) -> None:
        output = CodexOutput.from_events(
            success_events,
            exit_code=1,
            model_name="codex-mini-latest",
            cost_usd=0.0,
        )
        assert output.status == "failure"
        assert output.exit_code == 1

    def test_plain_text_output_file_fallback(self) -> None:
        """R1: -o 为纯文本时 text_result 应回填，不丢失。"""
        usage = {"input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 5}
        events = [{"type": "turn.completed", "usage": usage}]
        output = CodexOutput.from_events(
            events,
            exit_code=0,
            output_file_content="plain review text",
            model_name="codex-mini-latest",
            cost_usd=0.0,
        )
        assert output.structured_output is None
        assert output.text_result == "plain review text"

    def test_empty_events(self) -> None:
        output = CodexOutput.from_events(
            [],
            exit_code=0,
            model_name="codex-mini-latest",
            cost_usd=0.0,
        )
        assert output.text_result == ""
        assert output.input_tokens == 0
        assert output.output_tokens == 0
        assert output.session_id is None


# ---------------------------------------------------------------------------
# TestClassifyError — 错误分类
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


# ---------------------------------------------------------------------------
# TestBuildCommand — 命令构建
# ---------------------------------------------------------------------------


class TestBuildCommand:
    def test_basic_command_no_default_sandbox(self) -> None:
        """无 options 时不应默认追加 --sandbox。"""
        adapter = CodexAdapter()
        cmd = adapter._build_command("review this code")
        assert cmd[:5] == [
            "codex",
            "--dangerously-bypass-approvals-and-sandbox",
            "exec",
            "review this code",
            "--json",
        ]
        assert "--sandbox" not in cmd

    def test_no_options_no_sandbox_flag(self) -> None:
        """未传入 sandbox 选项时命令不包含 --sandbox。"""
        adapter = CodexAdapter()
        cmd = adapter._build_command("prompt")
        assert "--sandbox" not in cmd

    def test_explicit_sandbox_passed(self) -> None:
        """显式传入 sandbox 时仍追加 --sandbox。"""
        adapter = CodexAdapter()
        cmd = adapter._build_command("prompt", {"sandbox": "workspace-write"})
        idx = cmd.index("--sandbox")
        assert cmd[idx + 1] == "workspace-write"

    def test_explicit_sandbox_read_only(self) -> None:
        """显式传入 read-only sandbox 时追加 --sandbox read-only。"""
        adapter = CodexAdapter()
        cmd = adapter._build_command("prompt", {"sandbox": "read-only"})
        idx = cmd.index("--sandbox")
        assert cmd[idx + 1] == "read-only"

    def test_with_output_schema(self) -> None:
        adapter = CodexAdapter()
        cmd = adapter._build_command("prompt", {"output_schema": "/tmp/schema.json"})
        assert "--output-schema" in cmd
        assert "/tmp/schema.json" in cmd

    def test_with_output_file(self) -> None:
        adapter = CodexAdapter()
        cmd = adapter._build_command("prompt", {"output_file": "/tmp/out.json"})
        assert "-o" in cmd
        assert "/tmp/out.json" in cmd

    def test_with_ephemeral(self) -> None:
        adapter = CodexAdapter()
        cmd = adapter._build_command("prompt", {"ephemeral": True})
        assert "--ephemeral" in cmd

    def test_model_passed_to_command(self) -> None:
        """R2-1: --model 应透传到 codex exec 命令。"""
        adapter = CodexAdapter()
        cmd = adapter._build_command("prompt", {"model": "codex-pro"})
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "codex-pro"

    def test_no_model_option_no_flag(self) -> None:
        """无 model 参数时不加 --model。"""
        adapter = CodexAdapter()
        cmd = adapter._build_command("prompt")
        assert "--model" not in cmd


# ---------------------------------------------------------------------------
# TestCalculateCost — 成本计算
# ---------------------------------------------------------------------------


class TestCalculateCost:
    def test_known_model(self) -> None:
        cost = calculate_cost("codex-mini-latest", 1_000_000, 1_000_000, cached_input_tokens=0)
        expected = 1.50 + 6.00
        assert cost == pytest.approx(expected)

    def test_with_cache(self) -> None:
        cost = calculate_cost("codex-mini-latest", 1_000_000, 0, cached_input_tokens=500_000)
        # uncached = 500_000, cached = 500_000
        expected = 500_000 * 1.50 / 1_000_000 + 500_000 * 0.375 / 1_000_000
        assert cost == pytest.approx(expected)

    def test_unknown_model_returns_zero(self) -> None:
        cost = calculate_cost("unknown-model", 1000, 500)
        assert cost == 0.0

    def test_none_model_returns_zero(self) -> None:
        """model=None 时返回 0.0（安全降级）。"""
        cost = calculate_cost(None, 1_000_000, 1_000_000)
        assert cost == 0.0

    def test_cached_exceeds_input(self) -> None:
        """cached > input 时 uncached 为 0。"""
        cost = calculate_cost("codex-mini-latest", 100, 0, cached_input_tokens=200)
        # uncached = max(100 - 200, 0) = 0
        expected = 200 * 0.375 / 1_000_000
        assert cost == pytest.approx(expected)


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
    proc.pid = 54321
    proc.returncode = returncode
    proc.terminate = MagicMock()
    proc.kill = MagicMock()
    proc.wait = AsyncMock(return_value=returncode)

    stdout = MagicMock()
    _lines = [*list(stdout_lines), b""]
    _idx = 0

    async def _readline() -> bytes:
        nonlocal _idx
        if _idx < len(_lines):
            line = _lines[_idx]
            _idx += 1
            return line
        return b""

    stdout.readline = _readline

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


def _raw_to_lines(raw: str) -> list[bytes]:
    """Convert raw JSONL string to list of bytes lines for streaming mock."""
    return [line.encode() + b"\n" for line in raw.strip().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# TestCodexAdapterExecute — execute() streaming mock 测试
# ---------------------------------------------------------------------------


class TestCodexAdapterExecute:
    async def test_success_execution_no_model_default(self, success_events_raw: str) -> None:
        """无 model 选项时 model_name 为 None，cost_usd 为 0.0。"""
        proc = _mock_stream_process(_raw_to_lines(success_events_raw))
        adapter = CodexAdapter()
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            result = await adapter.execute("review this code")
        assert result.status == "success"
        assert result.input_tokens == 26024
        assert result.output_tokens == 29
        assert result.cache_read_input_tokens == 10624
        assert result.model_name is None
        assert result.cost_usd == 0.0

    async def test_success_execution_with_explicit_model(self, success_events_raw: str) -> None:
        """显式传 model 时 model_name 正确、cost_usd 为正值。"""
        proc = _mock_stream_process(_raw_to_lines(success_events_raw))
        adapter = CodexAdapter()
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            result = await adapter.execute("review this code", {"model": "codex-mini-latest"})
        assert result.status == "success"
        assert result.model_name == "codex-mini-latest"
        assert result.cost_usd > 0

    async def test_all_garbage_stdout_raises_parse_error(self) -> None:
        """R1-2: exit=0 但 JSONL 全部解析失败应报 parse_error。"""
        proc = _mock_stream_process([b"not-json\n", b"garbage\n"])
        adapter = CodexAdapter()
        with (
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
            pytest.raises(CLIAdapterError) as exc_info,
        ):
            await adapter.execute("test")
        assert exc_info.value.category == ErrorCategory.PARSE_ERROR

    async def test_empty_stdout_raises_parse_error(self) -> None:
        """R3-1: exit=0 + stdout 为空应报 parse_error，不能静默成功。"""
        proc = _mock_stream_process([])
        adapter = CodexAdapter()
        with (
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
            pytest.raises(CLIAdapterError) as exc_info,
        ):
            await adapter.execute("test")
        assert exc_info.value.category == ErrorCategory.PARSE_ERROR
        assert exc_info.value.retryable is False

    async def test_nonzero_exit_raises(self) -> None:
        proc = _mock_stream_process([], stderr_data=b"Error: auth token expired", returncode=1)
        adapter = CodexAdapter()
        with (
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
            pytest.raises(CLIAdapterError) as exc_info,
        ):
            await adapter.execute("test")
        assert exc_info.value.category == ErrorCategory.AUTH_EXPIRED
        assert exc_info.value.retryable is True

    async def test_timeout_raises(self) -> None:
        proc = MagicMock()
        proc.pid = 99
        proc.returncode = None
        proc.terminate = MagicMock()
        proc.kill = MagicMock()
        proc.wait = AsyncMock()

        async def _hanging_readline() -> bytes:
            await asyncio.sleep(100)
            return b""

        proc.stdout = MagicMock()
        proc.stdout.readline = _hanging_readline
        stderr = MagicMock()
        stderr.read = AsyncMock(return_value=b"")
        proc.stderr = stderr

        adapter = CodexAdapter()
        with (
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
            pytest.raises(CLIAdapterError) as exc_info,
        ):
            await adapter.execute("test", {"timeout": 0.1})
        assert exc_info.value.category == ErrorCategory.TIMEOUT
        assert exc_info.value.retryable is True

    async def test_on_process_start_callback(self, success_events_raw: str) -> None:
        proc = _mock_stream_process(_raw_to_lines(success_events_raw))
        callback = AsyncMock()
        adapter = CodexAdapter()
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            await adapter.execute("test", on_process_start=callback)
        callback.assert_awaited_once_with(proc)

    async def test_partial_jsonl_missing_turn_completed(self) -> None:
        """R2-3: 有事件但缺 turn.completed 应报 parse_error。"""
        lines = [
            b'{"type":"thread.started","thread_id":"t1"}\n',
            b"not-json\n",
        ]
        proc = _mock_stream_process(lines)
        adapter = CodexAdapter()
        with (
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
            pytest.raises(CLIAdapterError) as exc_info,
        ):
            await adapter.execute("test")
        assert exc_info.value.category == ErrorCategory.PARSE_ERROR

    async def test_missing_agent_message_raises_parse_error(self) -> None:
        """R4-1: 有 turn.completed 但无 agent_message 应报 parse_error。"""
        lines = [
            b'{"type":"thread.started","thread_id":"t1"}\n',
            b'{"type":"turn.completed","usage":{"input_tokens":100,"cached_input_tokens":0,"output_tokens":10}}\n',
        ]
        proc = _mock_stream_process(lines)
        adapter = CodexAdapter()
        with (
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
            pytest.raises(CLIAdapterError) as exc_info,
        ):
            await adapter.execute("test")
        assert exc_info.value.category == ErrorCategory.PARSE_ERROR

    async def test_cwd_passed_to_subprocess(self, success_events_raw: str) -> None:
        proc = _mock_stream_process(_raw_to_lines(success_events_raw))
        adapter = CodexAdapter()
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)) as mock_exec:
            await adapter.execute("test", {"cwd": "/some/repo"})
        _, kwargs = mock_exec.call_args
        assert kwargs["cwd"] == "/some/repo"

    async def test_model_passed_to_subprocess(self) -> None:
        """R2-1: model 选项应出现在实际 exec 命令中。"""
        raw = (FIXTURES / "codex_events_success.jsonl").read_text()
        proc = _mock_stream_process(_raw_to_lines(raw))
        adapter = CodexAdapter()
        with patch(
            "asyncio.create_subprocess_exec",
            AsyncMock(return_value=proc),
        ) as mock_exec:
            await adapter.execute("test", {"model": "codex-pro"})
        args = mock_exec.call_args[0]
        assert "--model" in args
        assert "codex-pro" in args


# ---------------------------------------------------------------------------
# TestCodexAdapterStreaming — 流式进度回调测试
# ---------------------------------------------------------------------------


class TestCodexAdapterOutputFile:
    """output_file (-o) 相关测试——使用 streaming mock。"""

    async def test_output_file_missing_raises_parse_error(self) -> None:
        """指定 output_file 但文件不存在时报 parse_error。"""
        lines = [
            b'{"type":"thread.started","thread_id":"t1"}\n',
            b'{"type":"turn.completed","usage":{"input_tokens":100,"cached_input_tokens":0,"output_tokens":10}}\n',
        ]
        proc = _mock_stream_process(lines)
        adapter = CodexAdapter()
        with (
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
            pytest.raises(CLIAdapterError) as exc_info,
        ):
            await adapter.execute("test", {"output_file": "/tmp/does-not-exist-codex.json"})
        assert exc_info.value.category == ErrorCategory.PARSE_ERROR

    async def test_output_file_present_no_agent_msg_succeeds(self, tmp_path: Path) -> None:
        """有 -o 文件内容时即使无 agent_message 也成功。"""
        lines = [
            b'{"type":"thread.started","thread_id":"t1"}\n',
            b'{"type":"turn.completed","usage":{"input_tokens":100,"cached_input_tokens":0,"output_tokens":10}}\n',
        ]
        out = tmp_path / "out.json"
        # 文件在 execute 前不存在，readline 消费完后才出现（模拟 Codex 行为）
        proc = _mock_stream_process(lines)

        original_readline = proc.stdout.readline
        _call_count = 0

        async def _readline_then_write() -> bytes:
            nonlocal _call_count
            result: bytes = await original_readline()
            _call_count += 1
            # 在最后一行（空 bytes）返回前写入输出文件
            if result == b"" and not out.exists():
                out.write_text('{"findings": []}', encoding="utf-8")
            return result

        proc.stdout.readline = _readline_then_write

        adapter = CodexAdapter()
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            result = await adapter.execute("test", {"output_file": str(out)})
        assert result.status == "success"
        assert result.structured_output == {"findings": []}

    async def test_stale_output_file_not_accepted(self, tmp_path: Path) -> None:
        """旧的 output_file 内容不能被当作本次执行结果。"""
        lines = [
            b'{"type":"thread.started","thread_id":"t1"}\n',
            b'{"type":"turn.completed","usage":{"input_tokens":100,"cached_input_tokens":0,"output_tokens":10}}\n',
        ]
        out = tmp_path / "stale.json"
        out.write_text('{"findings": [{"message": "stale"}]}', encoding="utf-8")
        proc = _mock_stream_process(lines)
        adapter = CodexAdapter()
        with (
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
            pytest.raises(CLIAdapterError) as exc_info,
        ):
            await adapter.execute("test", {"output_file": str(out)})
        assert exc_info.value.category == ErrorCategory.PARSE_ERROR


class TestCodexAdapterStreaming:
    async def test_stream_success_with_progress(self, stream_success_lines: list[bytes]) -> None:
        """AC 2: on_progress 收到正确数量和类型的 ProgressEvent。"""
        proc = _mock_stream_process(stream_success_lines)
        events_received: list[ProgressEvent] = []

        async def on_progress(event: ProgressEvent) -> None:
            events_received.append(event)

        adapter = CodexAdapter()
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            result = await adapter.execute("test", on_progress=on_progress)

        assert result.status == "success"
        # 5 lines: thread.started, turn.started, item.started, item.completed, turn.completed
        assert len(events_received) == 5
        assert events_received[0].event_type == "init"
        assert events_received[-1].event_type == "turn_end"

    async def test_stream_success_without_progress(self, stream_success_lines: list[bytes]) -> None:
        """AC 4: 不传 on_progress，CodexOutput 字段与改造前一致。"""
        proc = _mock_stream_process(stream_success_lines)
        adapter = CodexAdapter()
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            result = await adapter.execute("test")

        assert result.status == "success"
        assert result.input_tokens == 500
        assert result.output_tokens == 200

    async def test_stream_tool_use_events(self, stream_tool_use_lines: list[bytes]) -> None:
        """AC 6: command_execution events correctly normalized."""
        proc = _mock_stream_process(stream_tool_use_lines)
        events_received: list[ProgressEvent] = []

        async def on_progress(event: ProgressEvent) -> None:
            events_received.append(event)

        adapter = CodexAdapter()
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            result = await adapter.execute("test", on_progress=on_progress)

        assert result.status == "success"
        tool_use_events = [e for e in events_received if e.event_type == "tool_use"]
        assert len(tool_use_events) >= 2  # function_call + command_execution
        # command_execution should include command text
        cmd_events = [e for e in tool_use_events if "执行命令" in e.summary]
        assert len(cmd_events) == 1
        assert "pytest tests/" in cmd_events[0].summary

    async def test_parse_error_emits_error_event(self) -> None:
        """AC 13: parse error → on_progress 收到 error 事件。"""
        proc = _mock_stream_process([])
        events_received: list[ProgressEvent] = []

        async def on_progress(event: ProgressEvent) -> None:
            events_received.append(event)

        adapter = CodexAdapter()
        with (
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
            pytest.raises(CLIAdapterError) as exc_info,
        ):
            await adapter.execute("test", on_progress=on_progress)

        assert exc_info.value.category == ErrorCategory.PARSE_ERROR
        error_events = [e for e in events_received if e.event_type == "error"]
        assert len(error_events) == 1
        assert error_events[0].summary == "无有效 JSONL 事件"

    async def test_error_event_on_nonzero_exit(self) -> None:
        """AC 13: 非零退出码 → on_progress 收到 error 事件。"""
        proc = _mock_stream_process([], stderr_data=b"Error: auth expired", returncode=1)
        events_received: list[ProgressEvent] = []

        async def on_progress(event: ProgressEvent) -> None:
            events_received.append(event)

        adapter = CodexAdapter()
        with (
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
            pytest.raises(CLIAdapterError),
        ):
            await adapter.execute("test", on_progress=on_progress)
        error_events = [e for e in events_received if e.event_type == "error"]
        assert len(error_events) >= 1
