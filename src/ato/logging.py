"""logging — ATO 结构化日志配置。"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import structlog


def configure_logging(log_dir: str | None = None, debug: bool = False) -> None:
    """配置 ATO 标准日志。

    JSON 格式输出到 stderr；当 log_dir 非空时追加写入 <log_dir>/ato.log。
    stdlib logging 和 structlog 共享同一 processor 链，确保所有日志均为结构化 JSON。
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

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
        foreign_pre_chain=shared_processors,
    )

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(formatter)

    handlers: list[logging.Handler] = [stderr_handler]

    if log_dir is not None:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path / "ato.log", mode="a", encoding="utf-8")
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)

    root = logging.getLogger()
    root.handlers.clear()
    for handler in handlers:
        root.addHandler(handler)
    root.setLevel(level)
