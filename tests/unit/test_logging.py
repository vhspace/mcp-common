"""Tests for structured logging setup."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import mcp_common.logging as logging_mod
from mcp_common.logging import JSONFormatter, setup_logging, suppress_ssl_warnings

if TYPE_CHECKING:
    import pytest


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

    def test_suppress_ssl_true_by_default(self) -> None:
        logging_mod._ssl_warnings_suppressed = False
        try:
            setup_logging(name="test-setup-logging", suppress_ssl=True)
            assert logging_mod._ssl_warnings_suppressed is True
        finally:
            logging_mod._ssl_warnings_suppressed = False

    def test_suppress_ssl_false_skips(self) -> None:
        logging_mod._ssl_warnings_suppressed = False
        try:
            setup_logging(name="test-setup-logging", suppress_ssl=False)
            assert logging_mod._ssl_warnings_suppressed is False
        finally:
            logging_mod._ssl_warnings_suppressed = False


class TestSuppressSslWarnings:
    def setup_method(self) -> None:
        logging_mod._ssl_warnings_suppressed = False

    def teardown_method(self) -> None:
        logging_mod._ssl_warnings_suppressed = False

    def test_sets_flag(self) -> None:
        assert logging_mod._ssl_warnings_suppressed is False
        suppress_ssl_warnings()
        assert logging_mod._ssl_warnings_suppressed is True

    def test_idempotent(self) -> None:
        suppress_ssl_warnings()
        assert logging_mod._ssl_warnings_suppressed is True
        suppress_ssl_warnings()
        assert logging_mod._ssl_warnings_suppressed is True

    def test_no_op_when_urllib3_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import builtins

        real_import = builtins.__import__

        def _block_urllib3(name: str, *args: object, **kwargs: object) -> object:
            if name == "urllib3":
                raise ImportError("mocked")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _block_urllib3)
        suppress_ssl_warnings()
        assert logging_mod._ssl_warnings_suppressed is True
