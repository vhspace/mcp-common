"""Tests for structured logging setup."""

import json
import logging

from mcp_common.logging import JSONFormatter, setup_logging


class TestJSONFormatter:
    def test_formats_as_json(self) -> None:
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert data["level"] == "INFO"
        assert data["message"] == "hello"
        assert data["logger"] == "test"
        assert "timestamp" in data

    def test_includes_exception(self) -> None:
        formatter = JSONFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="",
            lineno=0,
            msg="fail",
            args=(),
            exc_info=exc_info,
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert "exception" in data
        assert "ValueError" in data["exception"]


class TestSetupLogging:
    def setup_method(self) -> None:
        for name in ("test-setup-logging", "test-json-logger", "test-bad-level"):
            logger = logging.getLogger(name)
            logger.handlers.clear()

    def test_returns_logger(self) -> None:
        logger = setup_logging(name="test-setup-logging")
        assert isinstance(logger, logging.Logger)
        assert logger.level == logging.INFO

    def test_json_output_uses_json_formatter(self) -> None:
        logger = setup_logging(name="test-json-logger", json_output=True)
        assert len(logger.handlers) == 1
        assert isinstance(logger.handlers[0].formatter, JSONFormatter)

    def test_custom_level(self) -> None:
        logger = setup_logging(name="test-setup-logging", level="DEBUG")
        assert logger.level == logging.DEBUG

    def test_invalid_level_falls_back_to_info(self) -> None:
        logger = setup_logging(name="test-bad-level", level="BOGUS")
        assert logger.level == logging.INFO

    def test_does_not_duplicate_handlers(self) -> None:
        logger = setup_logging(name="test-setup-logging")
        handler_count = len(logger.handlers)
        setup_logging(name="test-setup-logging")
        assert len(logger.handlers) == handler_count
