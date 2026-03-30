"""claude_cli — Claude CLI 适配器。

通过 Claude CLI 调用 Claude，并返回结构化结果。
BMAD skills 在 OAuth 模式下自动加载。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime
from typing import Any

import structlog

from ato.adapters.base import BaseAdapter, ProcessStartCallback, cleanup_process, drain_stderr
from ato.models.schemas import (
    ClaudeOutput,
    CLIAdapterError,
    ErrorCategory,
    ProgressCallback,
    ProgressEvent,
)

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


def _classify_error(exit_code: int | None, stderr: str) -> tuple[ErrorCategory, bool]:
    """根据 exit code 和 stderr 内容分类错误。

    优先匹配 stderr 关键字，再匹配 exit code。

    Returns:
        (category, retryable) 元组。
    """
    stderr_lower = stderr.lower()
    # stderr 关键字优先
    if "auth" in stderr_lower or "credential" in stderr_lower:
        return ErrorCategory.AUTH_EXPIRED, True
    if "rate limit" in stderr_lower or "too many" in stderr_lower:
        return ErrorCategory.RATE_LIMIT, True
    if "json" in stderr_lower and ("parse" in stderr_lower or "decode" in stderr_lower):
        return ErrorCategory.PARSE_ERROR, False
    # exit code 分支（stderr 无明确关键字时）
    if exit_code == 401:
        return ErrorCategory.AUTH_EXPIRED, True
    if exit_code == 429:
        return ErrorCategory.RATE_LIMIT, True
    if exit_code == -15:  # SIGTERM
        return ErrorCategory.TIMEOUT, True
    return ErrorCategory.UNKNOWN, False


def _normalize_claude_event(raw: dict[str, Any]) -> ProgressEvent:
    """将 Claude stream-json 原始事件归一化为 ProgressEvent。"""
    now = datetime.now(tz=UTC)
    event_type = raw.get("type", "")

    if event_type == "system":
        session_id = str(raw.get("session_id", ""))[:8]
        return ProgressEvent(
            event_type="init",
            summary=f"会话初始化 (session={session_id})",
            cli_tool="claude",
            timestamp=now,
            raw=raw,
        )

    if event_type == "assistant":
        content = raw.get("message", {}).get("content", [])
        # 按优先级扫描：tool_use > text
        has_tool_use = False
        tool_name = ""
        has_text = False
        text_preview = ""
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "tool_use" and not has_tool_use:
                has_tool_use = True
                tool_name = item.get("name", "unknown")
            elif item.get("type") == "text" and not has_text:
                has_text = True
                text_preview = str(item.get("text", ""))[:100]

        if has_tool_use:
            return ProgressEvent(
                event_type="tool_use",
                summary=f"调用工具: {tool_name}",
                cli_tool="claude",
                timestamp=now,
                raw=raw,
            )
        if has_text:
            return ProgressEvent(
                event_type="text",
                summary=text_preview,
                cli_tool="claude",
                timestamp=now,
                raw=raw,
            )
        return ProgressEvent(
            event_type="other",
            summary="assistant",
            cli_tool="claude",
            timestamp=now,
            raw=raw,
        )

    if event_type == "user":
        return ProgressEvent(
            event_type="tool_result",
            summary="工具返回",
            cli_tool="claude",
            timestamp=now,
            raw=raw,
        )

    if event_type == "result":
        cost = raw.get("total_cost_usd", 0.0)
        return ProgressEvent(
            event_type="result",
            summary=f"完成 (cost=${cost:.4f})",
            cli_tool="claude",
            timestamp=now,
            raw=raw,
        )

    if event_type == "rate_limit_event":
        return ProgressEvent(
            event_type="other",
            summary="rate_limit_event",
            cli_tool="claude",
            timestamp=now,
            raw=raw,
        )

    return ProgressEvent(
        event_type="other",
        summary=str(event_type) if event_type else "unknown",
        cli_tool="claude",
        timestamp=now,
        raw=raw,
    )


def build_interactive_command(
    prompt: str,
    *,
    session_id: str | None = None,
) -> list[str]:
    """构建 interactive session 的 claude CLI 命令参数列表。

    Claude CLI 默认就是 interactive session。
    这里只传 prompt 作为首条用户消息，不使用 ``-p/--print``。
    支持 --resume 续接已有 session。

    Args:
        prompt: 发送给 CLI 的提示文本。
        session_id: 若提供则使用 --resume 续接。
    """
    cmd = ["claude", "--dangerously-skip-permissions"]
    if session_id:  # None 和 "" 都降级为 fresh session
        cmd.extend(["--resume", session_id])
    cmd.append(prompt)
    return cmd


class ClaudeAdapter(BaseAdapter):
    """Claude CLI 适配器。

    通过 ``asyncio.create_subprocess_exec`` 执行 ``claude -p`` 命令，
    解析 stream-json stdout 事件流，分类 stderr 错误。
    """

    def _build_command(
        self,
        prompt: str,
        options: dict[str, Any] | None = None,
    ) -> list[str]:
        """构建 claude CLI 命令参数列表。"""
        cmd = [
            "claude",
            "--dangerously-skip-permissions",
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
        ]
        if options:
            if model := options.get("model"):
                cmd.extend(["--model", str(model)])
            if effort := options.get("effort"):
                cmd.extend(["--effort", str(effort)])
            if max_turns := options.get("max_turns"):
                cmd.extend(["--max-turns", str(max_turns)])
            if json_schema := options.get("json_schema"):
                cmd.extend(["--json-schema", json.dumps(json_schema)])
            if resume := options.get("resume"):
                cmd.extend(["--resume", str(resume)])
        return cmd

    async def _consume_stream(
        self,
        stdout: asyncio.StreamReader,
        on_progress: ProgressCallback | None,
    ) -> dict[str, Any] | None:
        """逐行读取 stream-json stdout，收集 result 事件并透传 ProgressEvent。

        Returns:
            result 事件 dict，或 None（如果未收到 result 事件）。
            调用方根据 exit_code 决定如何处理 None。
        """
        result_data: dict[str, Any] | None = None
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
                logger.warning("claude_stream_json_parse_skip", line_preview=text[:100])
                continue
            if event.get("type") == "result":
                result_data = event
            if on_progress is not None:
                try:
                    await on_progress(_normalize_claude_event(event))
                except Exception:
                    logger.warning("progress_callback_error", exc_info=True)
        return result_data

    async def execute(
        self,
        prompt: str,
        options: dict[str, Any] | None = None,
        *,
        on_process_start: ProcessStartCallback | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> ClaudeOutput:
        """执行 Claude CLI 并返回结构化结果。"""
        cmd = self._build_command(prompt, options)
        cwd = (options or {}).get("cwd")
        timeout_seconds: int = (options or {}).get("timeout", 1800)

        logger.info("claude_adapter_execute", cmd_preview=cmd[:4], cwd=cwd)

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
                result_data = await self._consume_stream(
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
                            cli_tool="claude",
                            timestamp=datetime.now(tz=UTC),
                            raw={},
                        )
                    )
            await cleanup_process(proc)
            raise CLIAdapterError(
                f"Claude CLI timed out after {timeout_seconds}s",
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
                "claude_adapter_error",
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
                            cli_tool="claude",
                            timestamp=datetime.now(tz=UTC),
                            raw={},
                        )
                    )
            raise CLIAdapterError(
                f"Claude CLI exited with code {exit_code}",
                category=category,
                stderr=stderr,
                exit_code=exit_code,
                retryable=retryable,
            )

        # exit_code == 0 但未收到 result 事件 → parse error
        if result_data is None:
            if on_progress:
                with contextlib.suppress(Exception):
                    await on_progress(
                        ProgressEvent(
                            event_type="error",
                            summary="stream-json 未收到 result 事件",
                            cli_tool="claude",
                            timestamp=datetime.now(tz=UTC),
                            raw={},
                        )
                    )
            raise CLIAdapterError(
                "stream-json 未收到 result 事件",
                category=ErrorCategory.PARSE_ERROR,
                retryable=False,
            )

        result = ClaudeOutput.from_json(result_data, exit_code=exit_code)
        logger.info(
            "claude_adapter_success",
            cost_usd=result.cost_usd,
            duration_ms=result.duration_ms,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
        return result
