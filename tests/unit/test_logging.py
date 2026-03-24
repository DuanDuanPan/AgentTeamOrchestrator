"""test_logging — 验证 configure_logging() 行为。"""

from __future__ import annotations

import io
import json
import logging
import re
import sys
from pathlib import Path

import structlog

from ato.logging import configure_logging

ISO_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


def _reset_logging() -> None:
    """重置 logging 和 structlog 状态，防止测试间干扰。"""
    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
        handler.close()
    structlog.reset_defaults()


class TestConfigureLogging:
    """configure_logging 测试套件。"""

    def setup_method(self) -> None:
        _reset_logging()

    def teardown_method(self) -> None:
        _reset_logging()

    def test_stderr_outputs_valid_json(self) -> None:
        """验证 stderr 实际输出的是可解析的 JSON。"""
        buf = io.StringIO()
        configure_logging()

        # 替换 stderr handler 的 stream 为可捕获的 buffer
        root = logging.getLogger()
        for h in root.handlers:
            if isinstance(h, logging.StreamHandler) and h.stream is sys.stderr:
                h.stream = buf
                break

        logger = structlog.get_logger()
        logger.info("test_event", key="value")

        output = buf.getvalue().strip()
        record = json.loads(output)
        assert record["event"] == "test_event"
        assert record["key"] == "value"
        assert record["level"] == "info"

    def test_json_contains_iso_timestamp(self, tmp_path: Path) -> None:
        """验证 JSON 输出包含 ISO 格式时间戳。"""
        log_dir = str(tmp_path / "logs")
        configure_logging(log_dir=log_dir)

        logger = structlog.get_logger()
        logger.info("ts_event", data="hello")

        log_file = tmp_path / "logs" / "ato.log"
        content = log_file.read_text().strip()
        last_line = content.split("\n")[-1]
        record = json.loads(last_line)

        assert "timestamp" in record
        assert ISO_TIMESTAMP_RE.match(record["timestamp"]), (
            f"timestamp 不是 ISO 格式: {record['timestamp']}"
        )
        assert record["level"] == "info"
        assert record["event"] == "ts_event"
        assert record["data"] == "hello"

    def test_stdlib_logging_outputs_json(self, tmp_path: Path) -> None:
        """验证 stdlib logging 也通过 structlog 链路输出 JSON。"""
        log_dir = str(tmp_path / "stdlib_logs")
        configure_logging(log_dir=log_dir)

        plain_logger = logging.getLogger("plain")
        plain_logger.info("plain_event")

        log_file = tmp_path / "stdlib_logs" / "ato.log"
        content = log_file.read_text().strip()
        last_line = content.split("\n")[-1]
        record = json.loads(last_line)

        assert record["event"] == "plain_event"
        assert record["level"] == "info"
        assert "timestamp" in record
        assert ISO_TIMESTAMP_RE.match(record["timestamp"])

    def test_log_dir_creates_directory_and_writes_file(self, tmp_path: Path) -> None:
        """验证传入 log_dir 时创建目录并写入 ato.log。"""
        log_dir = str(tmp_path / "custom_logs")
        configure_logging(log_dir=log_dir)

        logger = structlog.get_logger()
        logger.warning("disk_full", usage=95)

        log_file = tmp_path / "custom_logs" / "ato.log"
        assert log_file.exists()

        content = log_file.read_text()
        record = json.loads(content.strip().split("\n")[-1])
        assert record["event"] == "disk_full"
        assert record["usage"] == 95

    def test_debug_mode_enables_debug_level(self, tmp_path: Path) -> None:
        """验证 debug=True 时启用 DEBUG 级别。"""
        log_dir = str(tmp_path / "debug_logs")
        configure_logging(log_dir=log_dir, debug=True)

        logger = structlog.get_logger()
        logger.debug("debug_event", detail="verbose")

        log_file = tmp_path / "debug_logs" / "ato.log"
        assert log_file.exists()

        content = log_file.read_text()
        assert "debug_event" in content

    def test_default_level_is_info(self, tmp_path: Path) -> None:
        """验证默认级别为 INFO，DEBUG 消息不输出。"""
        log_dir = str(tmp_path / "info_logs")
        configure_logging(log_dir=log_dir, debug=False)

        logger = structlog.get_logger()
        logger.debug("should_not_appear")
        logger.info("should_appear")

        log_file = tmp_path / "info_logs" / "ato.log"
        content = log_file.read_text()
        assert "should_not_appear" not in content
        assert "should_appear" in content
