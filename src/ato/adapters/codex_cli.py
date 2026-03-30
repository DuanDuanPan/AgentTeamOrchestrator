"""codex_cli — Codex CLI 适配器与价格表。

通过 ``codex exec`` (非交互模式) 调用 Codex CLI 并返回结构化结果。
sandbox 和 model 参数仅在调用方显式传入时追加到命令行，
未指定时由 Codex CLI 自身决定默认行为。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from ato.adapters.base import (
    BaseAdapter,
    ProcessStartCallback,
    cleanup_process,
    drain_stderr,
)
from ato.models.schemas import (
    CLIAdapterError,
    CodexOutput,
    ErrorCategory,
    ProgressCallback,
    ProgressEvent,
)

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Codex 成本价格表 (ADR-24)
# ---------------------------------------------------------------------------

CODEX_PRICE_TABLE: dict[str, dict[str, float]] = {
    "codex-mini-latest": {
        "input_per_1m": 1.50,
        "cached_input_per_1m": 0.375,
        "output_per_1m": 6.00,
    },
}


def calculate_cost(
    model: str | None,
    input_tokens: int,
    output_tokens: int,
    *,
    cached_input_tokens: int = 0,
) -> float:
    """根据价格表计算 Codex 调用成本。

    model 为 None 或未知模型时返回 0.0 并 structlog 警告。
    """
    if model is None:
        logger.warning("codex_model_none_cost_fallback", model=model)
        return 0.0
    prices = CODEX_PRICE_TABLE.get(model)
    if prices is None:
        logger.warning("codex_unknown_model_price", model=model)
        return 0.0
    uncached_input_tokens = max(input_tokens - cached_input_tokens, 0)
    return (
        uncached_input_tokens * prices["input_per_1m"] / 1_000_000
        + cached_input_tokens * prices["cached_input_per_1m"] / 1_000_000
        + output_tokens * prices["output_per_1m"] / 1_000_000
    )


# ---------------------------------------------------------------------------
# JSONL 解析辅助函数
# ---------------------------------------------------------------------------


def _parse_jsonl(stdout: str) -> list[dict[str, Any]]:
    """逐行解析 JSONL 事件流。

    - 空行跳过
    - 非 JSON 行 structlog 警告后跳过
    - 返回成功解析的事件列表
    """
    events: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            events.append(json.loads(stripped))
        except (json.JSONDecodeError, ValueError):
            logger.warning("codex_jsonl_parse_skip", line_preview=stripped[:100])
    return events


def _aggregate_usage(events: list[dict[str, Any]]) -> tuple[int, int, int]:
    """从 turn.completed 事件聚合 token 用量。

    Returns:
        (input_tokens, cached_input_tokens, output_tokens)
    """
    input_tokens = 0
    cached_input_tokens = 0
    output_tokens = 0
    for ev in events:
        if ev.get("type") == "turn.completed":
            usage = ev.get("usage", {})
            input_tokens += usage.get("input_tokens", 0)
            cached_input_tokens += usage.get("cached_input_tokens", 0)
            output_tokens += usage.get("output_tokens", 0)
    return input_tokens, cached_input_tokens, output_tokens


def _extract_text_result(events: list[dict[str, Any]]) -> str:
    """从最后一个 item.completed (type=agent_message) 提取文本结果。

    优先使用当前 CLI 的 ``item.text``，兼容旧版 ``item.content[].text``。
    """
    text = ""
    for ev in events:
        if ev.get("type") != "item.completed":
            continue
        item = ev.get("item", {})
        # 只提取 agent_message 类型的 item
        if item.get("type") != "agent_message":
            continue
        # 当前 CLI 格式：item.text
        if "text" in item:
            text = item["text"]
        # 旧版格式：item.content[].text
        elif "content" in item and isinstance(item["content"], list):
            parts = [c.get("text", "") for c in item["content"] if isinstance(c, dict)]
            if parts:
                text = "".join(parts)
    return text


def _parse_output_file(content: str) -> tuple[dict[str, Any] | None, str]:
    """解析 -o 输出文件内容。

    JSON 解析成功时返回 (structured_output, text)；失败时返回 (None, raw_text)。
    """
    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            return parsed, content
        return None, content
    except (json.JSONDecodeError, ValueError):
        return None, content


def _classify_error(exit_code: int | None, stderr: str) -> tuple[ErrorCategory, bool]:
    """根据 exit code 和 stderr 内容分类错误。

    与 ClaudeAdapter 使用相同的关键字匹配策略。
    """
    stderr_lower = stderr.lower()
    if "auth" in stderr_lower or "credential" in stderr_lower:
        return ErrorCategory.AUTH_EXPIRED, True
    if "rate limit" in stderr_lower or "too many" in stderr_lower:
        return ErrorCategory.RATE_LIMIT, True
    if "json" in stderr_lower and ("parse" in stderr_lower or "decode" in stderr_lower):
        return ErrorCategory.PARSE_ERROR, False
    if exit_code == 401:
        return ErrorCategory.AUTH_EXPIRED, True
    if exit_code == 429:
        return ErrorCategory.RATE_LIMIT, True
    if exit_code == -15:  # SIGTERM
        return ErrorCategory.TIMEOUT, True
    return ErrorCategory.UNKNOWN, False


def _normalize_codex_event(raw: dict[str, Any]) -> ProgressEvent:
    """将 Codex JSONL 原始事件归一化为 ProgressEvent。"""
    now = datetime.now(tz=UTC)
    event_type = raw.get("type", "")

    if event_type == "thread.started":
        thread_id = str(raw.get("thread_id", ""))[:12]
        return ProgressEvent(
            event_type="init",
            summary=f"会话初始化 (thread={thread_id})",
            cli_tool="codex",
            timestamp=now,
            raw=raw,
        )

    if event_type == "turn.started":
        return ProgressEvent(
            event_type="other",
            summary="新回合开始",
            cli_tool="codex",
            timestamp=now,
            raw=raw,
        )

    if event_type == "item.completed":
        item = raw.get("item", {})
        item_type = item.get("type", "")

        if item_type == "agent_message":
            text = item.get("text", "")
            if not text and isinstance(item.get("content"), list):
                parts = [c.get("text", "") for c in item["content"] if isinstance(c, dict)]
                text = "".join(parts)
            return ProgressEvent(
                event_type="text",
                summary=text[:100],
                cli_tool="codex",
                timestamp=now,
                raw=raw,
            )

        if item_type == "function_call":
            return ProgressEvent(
                event_type="tool_use",
                summary=f"调用函数: {item.get('name', 'unknown')}",
                cli_tool="codex",
                timestamp=now,
                raw=raw,
            )

        if item_type == "function_call_output":
            return ProgressEvent(
                event_type="tool_result",
                summary="函数返回",
                cli_tool="codex",
                timestamp=now,
                raw=raw,
            )

        if item_type == "command_execution":
            call = item.get("call", {})
            cmd = str(call.get("command", ""))[:60]
            return ProgressEvent(
                event_type="tool_use",
                summary=f"执行命令: {cmd}",
                cli_tool="codex",
                timestamp=now,
                raw=raw,
            )

        return ProgressEvent(
            event_type="other",
            summary=f"item.completed ({item_type})",
            cli_tool="codex",
            timestamp=now,
            raw=raw,
        )

    if event_type == "item.started":
        return ProgressEvent(
            event_type="other",
            summary="item.started",
            cli_tool="codex",
            timestamp=now,
            raw=raw,
        )

    if event_type == "turn.completed":
        usage = raw.get("usage", {})
        in_t = usage.get("input_tokens", 0)
        out_t = usage.get("output_tokens", 0)
        return ProgressEvent(
            event_type="turn_end",
            summary=f"回合结束 (in={in_t} out={out_t})",
            cli_tool="codex",
            timestamp=now,
            raw=raw,
        )

    return ProgressEvent(
        event_type="other",
        summary=str(event_type) if event_type else "unknown",
        cli_tool="codex",
        timestamp=now,
        raw=raw,
    )


# ---------------------------------------------------------------------------
# CodexAdapter
# ---------------------------------------------------------------------------


class CodexAdapter(BaseAdapter):
    """Codex CLI 适配器。

    通过 ``asyncio.create_subprocess_exec`` 执行 ``codex exec`` 命令，
    解析 JSONL stdout 事件流，分类错误。
    """

    def _build_command(
        self,
        prompt: str,
        options: dict[str, Any] | None = None,
    ) -> list[str]:
        """构建 codex exec 命令参数列表。"""
        cmd = ["codex", "--dangerously-bypass-approvals-and-sandbox", "exec", prompt, "--json"]

        if options:
            if sandbox := options.get("sandbox"):
                cmd.extend(["--sandbox", str(sandbox)])
            if model := options.get("model"):
                cmd.extend(["--model", str(model)])
            if reasoning_effort := options.get("reasoning_effort"):
                cmd.extend(["-c", f"model_reasoning_effort={reasoning_effort}"])
            if reasoning_summary_format := options.get("reasoning_summary_format"):
                cmd.extend(["-c", f"model_reasoning_summary_format={reasoning_summary_format}"])
            if output_schema := options.get("output_schema"):
                cmd.extend(["--output-schema", str(output_schema)])
            if output_file := options.get("output_file"):
                cmd.extend(["-o", str(output_file)])
            if options.get("ephemeral"):
                cmd.append("--ephemeral")
        return cmd

    async def _consume_stream(
        self,
        stdout: asyncio.StreamReader,
        on_progress: ProgressCallback | None,
    ) -> list[dict[str, Any]]:
        """逐行读取 JSONL stdout，收集事件列表并透传 ProgressEvent。"""
        events: list[dict[str, Any]] = []
        while True:
            line = await stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                event = json.loads(text)
            except json.JSONDecodeError:
                logger.warning("codex_jsonl_parse_skip", line_preview=text[:100])
                continue
            events.append(event)
            if on_progress is not None:
                try:
                    await on_progress(_normalize_codex_event(event))
                except Exception:
                    logger.warning("progress_callback_error", exc_info=True)
        return events

    async def execute(
        self,
        prompt: str,
        options: dict[str, Any] | None = None,
        *,
        on_process_start: ProcessStartCallback | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> CodexOutput:
        """执行 Codex CLI 并返回结构化结果。"""
        opts = options or {}
        cwd = opts.get("cwd")
        timeout_seconds: int = opts.get("timeout", 1800)
        model_name: str | None = opts.get("model")

        # 如果需要结构化输出但未指定 output_file，使用临时文件
        temp_dir: tempfile.TemporaryDirectory[str] | None = None
        output_file_path: Path | None = None
        managed_output_file = False
        output_file_mtime_ns_before: int | None = None
        output_file_size_before: int | None = None

        if opts.get("output_schema") and not opts.get("output_file"):
            temp_dir = tempfile.TemporaryDirectory()
            output_file_path = Path(temp_dir.name) / "codex_output.json"
            opts = {**opts, "output_file": str(output_file_path)}
            managed_output_file = True
        elif opts.get("output_file"):
            output_file_path = Path(str(opts["output_file"]))

        if output_file_path is not None and output_file_path.exists():
            stat = output_file_path.stat()
            output_file_mtime_ns_before = stat.st_mtime_ns
            output_file_size_before = stat.st_size

        cmd = self._build_command(prompt, opts)

        logger.info("codex_adapter_execute", cmd_preview=cmd[:5], cwd=cwd)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                limit=16 * 1024 * 1024,  # 16MB — MCP 工具可能返回超大 JSON
            )
            stderr_task = asyncio.create_task(drain_stderr(proc.stderr))  # type: ignore[arg-type]
            try:
                if on_process_start is not None:
                    await on_process_start(proc)

                # timeout 覆盖整个子进程生命周期（stdout + stderr + wait），
                # 而非仅 _consume_stream，防止进程关闭 stdout 后僵死
                async with asyncio.timeout(timeout_seconds):
                    events = await self._consume_stream(
                        proc.stdout,  # type: ignore[arg-type]
                        on_progress,
                    )
                    stderr = await stderr_task
                    await proc.wait()
            except TimeoutError as exc:
                if on_progress:
                    with contextlib.suppress(Exception):
                        await on_progress(
                            ProgressEvent(
                                event_type="error",
                                summary=f"超时 ({timeout_seconds}s)",
                                cli_tool="codex",
                                timestamp=datetime.now(tz=UTC),
                                raw={},
                            )
                        )
                await cleanup_process(proc)
                raise CLIAdapterError(
                    f"Codex CLI timed out after {timeout_seconds}s",
                    category=ErrorCategory.TIMEOUT,
                    retryable=True,
                ) from exc
            except BaseException:
                await cleanup_process(proc)
                raise
            finally:
                if not stderr_task.done():
                    stderr_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await stderr_task

            exit_code = proc.returncode or 0

            if exit_code != 0:
                stderr = stderr_task.result() if stderr_task.done() else ""
                category, retryable = _classify_error(exit_code, stderr)
                logger.warning(
                    "codex_adapter_error",
                    exit_code=exit_code,
                    category=category.value,
                    stderr_preview=stderr[:200],
                )
                if on_progress:
                    with contextlib.suppress(Exception):
                        await on_progress(
                            ProgressEvent(
                                event_type="error",
                                summary=f"退出码 {exit_code}: {category.value}",
                                cli_tool="codex",
                                timestamp=datetime.now(tz=UTC),
                                raw={},
                            )
                        )
                raise CLIAdapterError(
                    f"Codex CLI exited with code {exit_code}",
                    category=category,
                    stderr=stderr,
                    exit_code=exit_code,
                    retryable=retryable,
                )

            # 校验：stdout 为空或无有效事件 → parse_error
            if not events:
                if on_progress:
                    with contextlib.suppress(Exception):
                        await on_progress(
                            ProgressEvent(
                                event_type="error",
                                summary="无有效 JSONL 事件",
                                cli_tool="codex",
                                timestamp=datetime.now(tz=UTC),
                                raw={},
                            )
                        )
                raise CLIAdapterError(
                    "Codex CLI stdout contained no valid JSONL events",
                    category=ErrorCategory.PARSE_ERROR,
                    stderr=stderr,
                    exit_code=exit_code,
                    retryable=False,
                )

            # 校验：有事件但缺少关键 turn.completed → 数据不完整
            has_turn = any(e.get("type") == "turn.completed" for e in events)
            if not has_turn:
                if on_progress:
                    with contextlib.suppress(Exception):
                        await on_progress(
                            ProgressEvent(
                                event_type="error",
                                summary="缺少 turn.completed 事件",
                                cli_tool="codex",
                                timestamp=datetime.now(tz=UTC),
                                raw={},
                            )
                        )
                raise CLIAdapterError(
                    "Codex CLI JSONL missing turn.completed event",
                    category=ErrorCategory.PARSE_ERROR,
                    stderr=stderr,
                    exit_code=exit_code,
                    retryable=False,
                )

            # 读取 -o 输出文件
            output_file_content: str | None = None
            if output_file_path is not None and output_file_path.exists():
                stat = output_file_path.stat()
                output_file_was_written = (
                    output_file_mtime_ns_before is None
                    or output_file_size_before is None
                    or stat.st_mtime_ns != output_file_mtime_ns_before
                    or stat.st_size != output_file_size_before
                )
                if output_file_was_written:
                    output_file_content = output_file_path.read_text(encoding="utf-8")

            # 校验：必须至少有一个文本结果来源（JSONL agent_message 或 -o 文件内容）
            has_agent_msg = any(
                e.get("type") == "item.completed"
                and e.get("item", {}).get("type") == "agent_message"
                for e in events
            )
            if not has_agent_msg and not output_file_content:
                if on_progress:
                    with contextlib.suppress(Exception):
                        await on_progress(
                            ProgressEvent(
                                event_type="error",
                                summary="无文本结果",
                                cli_tool="codex",
                                timestamp=datetime.now(tz=UTC),
                                raw={},
                            )
                        )
                raise CLIAdapterError(
                    "Codex CLI produced no text result"
                    " (no agent_message in JSONL and no output file content)",
                    category=ErrorCategory.PARSE_ERROR,
                    stderr=stderr,
                    exit_code=exit_code,
                    retryable=False,
                )

            # 聚合 usage 并计算成本
            input_tokens, cached_input_tokens, output_tokens = _aggregate_usage(events)
            cost = calculate_cost(
                model_name,
                input_tokens,
                output_tokens,
                cached_input_tokens=cached_input_tokens,
            )

            result = CodexOutput.from_events(
                events,
                exit_code=exit_code,
                output_file_content=output_file_content,
                model_name=model_name,
                cost_usd=cost,
            )

            logger.info(
                "codex_adapter_success",
                cost_usd=result.cost_usd,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                model_name=model_name,
            )
            return result
        finally:
            if managed_output_file and temp_dir is not None:
                temp_dir.cleanup()
