"""claude_cli — Claude CLI 适配器。

通过 ``claude -p`` (OAuth 模式) 调用 Claude CLI 并返回结构化结果。
BMAD skills 在 OAuth 模式下自动加载。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog

from ato.adapters.base import BaseAdapter, ProcessStartCallback, cleanup_process
from ato.models.schemas import ClaudeOutput, CLIAdapterError, ErrorCategory

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


class ClaudeAdapter(BaseAdapter):
    """Claude CLI 适配器。

    通过 ``asyncio.create_subprocess_exec`` 执行 ``claude -p`` 命令，
    解析 JSON stdout，分类 stderr 错误。
    """

    def _build_command(
        self,
        prompt: str,
        options: dict[str, Any] | None = None,
    ) -> list[str]:
        """构建 claude CLI 命令参数列表。"""
        cmd = ["claude", "-p", prompt, "--output-format", "json"]
        if options:
            if max_turns := options.get("max_turns"):
                cmd.extend(["--max-turns", str(max_turns)])
            if json_schema := options.get("json_schema"):
                cmd.extend(["--json-schema", json.dumps(json_schema)])
            if resume := options.get("resume"):
                cmd.extend(["--resume", str(resume)])
        return cmd

    async def execute(
        self,
        prompt: str,
        options: dict[str, Any] | None = None,
        *,
        on_process_start: ProcessStartCallback | None = None,
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
        )
        try:
            if on_process_start is not None:
                await on_process_start(proc)

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout_seconds,
            )
        except TimeoutError as exc:
            await cleanup_process(proc)
            raise CLIAdapterError(
                f"Claude CLI timed out after {timeout_seconds}s",
                category=ErrorCategory.TIMEOUT,
                retryable=True,
            ) from exc
        except BaseException:
            await cleanup_process(proc)
            raise
        else:
            # communicate() 完成后 proc.returncode 已设置，无需额外清理
            pass

        exit_code = proc.returncode or 0
        stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

        if exit_code != 0:
            category, retryable = _classify_error(exit_code, stderr)
            logger.warning(
                "claude_adapter_error",
                exit_code=exit_code,
                category=category.value,
                stderr_preview=stderr[:200],
            )
            raise CLIAdapterError(
                f"Claude CLI exited with code {exit_code}",
                category=category,
                stderr=stderr,
                exit_code=exit_code,
                retryable=retryable,
            )

        # 解析 JSON stdout
        try:
            json_data = json.loads(stdout)
        except (json.JSONDecodeError, ValueError) as exc:
            raise CLIAdapterError(
                f"Failed to parse Claude CLI JSON output: {exc}",
                category=ErrorCategory.PARSE_ERROR,
                stderr=stderr,
                exit_code=exit_code,
                retryable=False,
            ) from exc

        result = ClaudeOutput.from_json(json_data, exit_code=exit_code)
        logger.info(
            "claude_adapter_success",
            cost_usd=result.cost_usd,
            duration_ms=result.duration_ms,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
        return result
