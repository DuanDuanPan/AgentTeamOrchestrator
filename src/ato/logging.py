"""logging — ATO 结构化日志配置。"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Literal

import structlog

LogFormat = Literal["auto", "json", "console"]


def _resolve_stderr_log_format(log_format: LogFormat) -> Literal["json", "console"]:
    """根据配置和终端类型决定 stderr 渲染格式。"""
    if log_format == "auto":
        return "console" if sys.stderr.isatty() else "json"
    return log_format


def _short_id(value: object | None) -> str | None:
    """在控制台里缩短 UUID，降低视觉噪音。"""
    if value is None:
        return None
    text = str(value)
    return text[:8] if len(text) >= 8 and "-" in text else text


def _shape_console_event(
    _logger: Any,
    _method_name: str,
    event_dict: structlog.types.EventDict,
) -> structlog.types.EventDict:
    """将结构化事件整理为更适合人类阅读的控制台输出。"""
    event_name = str(event_dict.get("event", ""))
    timestamp = event_dict.get("timestamp")
    level = event_dict.get("level")
    component = event_dict.get("component")
    story_id = event_dict.get("story_id")
    task_id = _short_id(event_dict.get("task_id"))

    if event_name == "agent_progress":
        summary = str(event_dict.get("progress_summary", "")) or "agent_progress"
        phase = event_dict.get("phase")
        role = event_dict.get("role")
        progress_cli_tool = event_dict.get("progress_cli_tool") or event_dict.get("cli_tool")
        progress_type = event_dict.get("progress_type")

        shaped: structlog.types.EventDict = {
            "timestamp": timestamp,
            "level": level,
            "event": summary,
        }
        if phase or role:
            shaped["scope"] = "/".join(p for p in (phase, role) if p)
        if progress_cli_tool or progress_type:
            shaped["tool"] = ":".join(str(p) for p in (progress_cli_tool, progress_type) if p)
        if story_id:
            shaped["story"] = story_id
        if task_id:
            shaped["task"] = task_id
        if component:
            shaped["component"] = component
        return shaped

    shaped = {
        "timestamp": timestamp,
        "level": level,
        "event": event_name,
    }
    if component:
        shaped["component"] = component
    if story_id:
        shaped["story"] = story_id
    if task_id:
        shaped["task"] = task_id

    for key, value in event_dict.items():
        if key in shaped or key in {"timestamp", "level", "story_id", "task_id"}:
            continue
        shaped[key] = value
    return shaped


def _build_json_formatter(
    shared_processors: list[structlog.types.Processor],
) -> structlog.stdlib.ProcessorFormatter:
    """构建机器可读的 JSON formatter。"""
    return structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        foreign_pre_chain=shared_processors,
    )


def _build_console_formatter(
    shared_processors: list[structlog.types.Processor],
) -> structlog.stdlib.ProcessorFormatter:
    """构建面向终端的彩色 console formatter。"""
    return structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            _shape_console_event,
            structlog.dev.ConsoleRenderer(colors=True, sort_keys=False, pad_event_to=32),
        ],
        foreign_pre_chain=shared_processors,
    )


def configure_logging(
    log_dir: str | None = None,
    debug: bool = False,
    *,
    log_format: LogFormat = "auto",
) -> None:
    """配置 ATO 标准日志。

    stderr 在交互式终端默认使用彩色 console 输出；非交互场景保留 JSON。
    当 log_dir 非空时，始终追加写入机器可读的 <log_dir>/ato.log。
    """
    level = logging.DEBUG if debug else logging.INFO

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    stderr_format = _resolve_stderr_log_format(log_format)
    stderr_formatter = (
        _build_console_formatter(shared_processors)
        if stderr_format == "console"
        else _build_json_formatter(shared_processors)
    )
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(stderr_formatter)

    handlers: list[logging.Handler] = [stderr_handler]

    if log_dir is not None:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path / "ato.log", mode="a", encoding="utf-8")
        file_handler.setFormatter(_build_json_formatter(shared_processors))
        handlers.append(file_handler)

    root = logging.getLogger()
    root.handlers.clear()
    for handler in handlers:
        root.addHandler(handler)
    root.setLevel(level)
