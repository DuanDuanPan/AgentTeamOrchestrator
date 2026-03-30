"""test_progress_event — ProgressEvent 归一化测试。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ato.adapters.claude_cli import _normalize_claude_event
from ato.adapters.codex_cli import _normalize_codex_event
from ato.models.schemas import ProgressEvent

# ---------------------------------------------------------------------------
# ProgressEvent 模型测试
# ---------------------------------------------------------------------------


class TestProgressEventModel:
    def test_create_valid_event(self) -> None:
        event = ProgressEvent(
            event_type="init",
            summary="test",
            cli_tool="claude",
            timestamp=datetime.now(tz=UTC),
            raw={"type": "system"},
        )
        assert event.event_type == "init"
        assert event.summary == "test"
        assert event.cli_tool == "claude"

    def test_rejects_invalid_event_type(self) -> None:
        with pytest.raises(ValidationError):
            ProgressEvent(
                event_type="invalid_type",  # type: ignore[arg-type]
                summary="test",
                cli_tool="claude",
                timestamp=datetime.now(tz=UTC),
                raw={},
            )

    def test_rejects_invalid_cli_tool(self) -> None:
        with pytest.raises(ValidationError):
            ProgressEvent(
                event_type="init",
                summary="test",
                cli_tool="unknown",  # type: ignore[arg-type]
                timestamp=datetime.now(tz=UTC),
                raw={},
            )


# ---------------------------------------------------------------------------
# Claude 事件归一化测试
# ---------------------------------------------------------------------------


class TestNormalizeClaudeEvent:
    def test_system_event(self) -> None:
        raw = {"type": "system", "session_id": "sess-abc-123-def-456"}
        result = _normalize_claude_event(raw)
        assert result.event_type == "init"
        assert "sess-abc" in result.summary
        assert result.cli_tool == "claude"

    def test_assistant_text_only(self) -> None:
        raw = {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "Hello world"}]},
        }
        result = _normalize_claude_event(raw)
        assert result.event_type == "text"
        assert result.summary == "Hello world"

    def test_assistant_tool_use_only(self) -> None:
        raw = {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": "Read"}]},
        }
        result = _normalize_claude_event(raw)
        assert result.event_type == "tool_use"
        assert "Read" in result.summary

    def test_assistant_tool_use_takes_priority_over_text(self) -> None:
        """AC 5: tool_use 优先于 text。"""
        raw = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Let me read the file"},
                    {"type": "tool_use", "name": "Read"},
                ]
            },
        }
        result = _normalize_claude_event(raw)
        assert result.event_type == "tool_use"
        assert "Read" in result.summary

    def test_assistant_empty_content(self) -> None:
        raw = {"type": "assistant", "message": {"content": []}}
        result = _normalize_claude_event(raw)
        assert result.event_type == "other"
        assert result.summary == "assistant"

    def test_user_event(self) -> None:
        raw = {"type": "user"}
        result = _normalize_claude_event(raw)
        assert result.event_type == "tool_result"
        assert result.summary == "工具返回"

    def test_result_event(self) -> None:
        raw = {"type": "result", "total_cost_usd": 0.0125}
        result = _normalize_claude_event(raw)
        assert result.event_type == "result"
        assert "0.0125" in result.summary

    def test_rate_limit_event(self) -> None:
        raw = {"type": "rate_limit_event"}
        result = _normalize_claude_event(raw)
        assert result.event_type == "other"
        assert result.summary == "rate_limit_event"

    def test_unknown_type(self) -> None:
        raw = {"type": "something_new"}
        result = _normalize_claude_event(raw)
        assert result.event_type == "other"
        assert result.summary == "something_new"

    def test_text_truncated_at_100(self) -> None:
        long_text = "x" * 200
        raw = {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": long_text}]},
        }
        result = _normalize_claude_event(raw)
        assert len(result.summary) == 100


# ---------------------------------------------------------------------------
# Codex 事件归一化测试
# ---------------------------------------------------------------------------


class TestNormalizeCodexEvent:
    def test_thread_started(self) -> None:
        raw = {"type": "thread.started", "thread_id": "thread-abc-123-def"}
        result = _normalize_codex_event(raw)
        assert result.event_type == "init"
        assert "thread-abc-1" in result.summary
        assert result.cli_tool == "codex"

    def test_turn_started(self) -> None:
        raw = {"type": "turn.started"}
        result = _normalize_codex_event(raw)
        assert result.event_type == "other"
        assert result.summary == "新回合开始"

    def test_item_completed_agent_message(self) -> None:
        raw = {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "All good"},
        }
        result = _normalize_codex_event(raw)
        assert result.event_type == "text"
        assert result.summary == "All good"

    def test_item_completed_function_call(self) -> None:
        raw = {
            "type": "item.completed",
            "item": {"type": "function_call", "name": "read_file"},
        }
        result = _normalize_codex_event(raw)
        assert result.event_type == "tool_use"
        assert "read_file" in result.summary

    def test_item_completed_function_call_output(self) -> None:
        raw = {
            "type": "item.completed",
            "item": {"type": "function_call_output", "output": "data"},
        }
        result = _normalize_codex_event(raw)
        assert result.event_type == "tool_result"
        assert result.summary == "函数返回"

    def test_item_completed_command_execution(self) -> None:
        """AC 6: command_execution → tool_use with command content."""
        raw = {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "call": {"command": "pytest tests/ -v"},
            },
        }
        result = _normalize_codex_event(raw)
        assert result.event_type == "tool_use"
        assert "pytest tests/ -v" in result.summary

    def test_item_started(self) -> None:
        raw = {"type": "item.started", "item": {"type": "agent_message"}}
        result = _normalize_codex_event(raw)
        assert result.event_type == "other"
        assert result.summary == "item.started"

    def test_turn_completed(self) -> None:
        raw = {
            "type": "turn.completed",
            "usage": {"input_tokens": 500, "output_tokens": 200},
        }
        result = _normalize_codex_event(raw)
        assert result.event_type == "turn_end"
        assert "in=500" in result.summary
        assert "out=200" in result.summary

    def test_unknown_type(self) -> None:
        raw = {"type": "something_new"}
        result = _normalize_codex_event(raw)
        assert result.event_type == "other"
        assert result.summary == "something_new"
