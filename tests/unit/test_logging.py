"""Tests for structured logging setup."""

from __future__ import annotations

import io
import json
import logging
import re

import pytest

import mcp_common.logging as logging_mod
from mcp_common.logging import (
    LOG_CHANNEL_ACCESS,
    LOG_CHANNEL_APP,
    LOG_CHANNEL_TRACE,
    LOG_CHANNEL_TRANSCRIPT,
    JSONFormatter,
    compute_error_fingerprint,
    format_exception_for_trace,
    log_access_event,
    log_trace_event,
    log_transcript_event,
    redact_config_from_settings,
    sanitize_transcript_value,
    setup_logging,
    suppress_ssl_warnings,
    transcript_should_log,
)


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
        assert data["log_channel"] == LOG_CHANNEL_APP
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
        assert data["log_channel"] == LOG_CHANNEL_APP

    def test_merges_extra_fields(self) -> None:
        formatter = JSONFormatter()
        record = logging.makeLogRecord(
            {
                "name": "test",
                "level": logging.INFO,
                "pathname": "",
                "lineno": 0,
                "msg": "x",
                "request_id": "abc-123",
                "tool": "my_tool",
            }
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert data["request_id"] == "abc-123"
        assert data["tool"] == "my_tool"

    def test_respects_log_channel_extra(self) -> None:
        formatter = JSONFormatter()
        record = logging.makeLogRecord(
            {
                "name": "test",
                "level": logging.INFO,
                "pathname": "",
                "lineno": 0,
                "msg": "x",
                "log_channel": LOG_CHANNEL_ACCESS,
            }
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert data["log_channel"] == LOG_CHANNEL_ACCESS


class TestSanitizeAndTruncate:
    def test_redacts_key_substrings(self) -> None:
        out = sanitize_transcript_value({"user": "u", "api_token": "secret"})
        assert out["user"] == "u"
        assert out["api_token"] == "[REDACTED]"

    def test_redacts_by_custom_substrings(self) -> None:
        subs = frozenset({"customsecret"})
        out = sanitize_transcript_value(
            {"customsecret_field": "x", "ok": 1},
            redact_substrings=subs,
        )
        assert out["ok"] == 1
        assert out["customsecret_field"] == "[REDACTED]"

    def test_redacts_by_key_pattern(self) -> None:
        patterns = (re.compile(r".*_SECRET$", re.I),)
        out = sanitize_transcript_value(
            {"MY_SECRET": "hidden", "plain": "v"},
            key_patterns=patterns,
        )
        assert out["MY_SECRET"] == "[REDACTED]"
        assert out["plain"] == "v"

    def test_truncates_long_strings_with_ellipsis(self) -> None:
        s = "a" * 100
        out = sanitize_transcript_value(s, max_str_len=20)
        assert isinstance(out, str)
        assert out.endswith("…")
        assert len(out) == 20


class TestTranscriptEvent:
    def test_truncation_marker_in_payload(self) -> None:
        buf = io.StringIO()
        h = logging.StreamHandler(buf)
        h.setFormatter(JSONFormatter())
        log = logging.getLogger("test-transcript-trunc")
        log.handlers.clear()
        log.setLevel(logging.INFO)
        log.addHandler(h)
        huge = {"x": "y" * 50000}
        log_transcript_event(
            log,
            enabled=True,
            input_payload=huge,
            max_str_len=4096,
            max_total_chars=200,
        )
        line = buf.getvalue().strip()
        data = json.loads(line)
        assert data["log_channel"] == LOG_CHANNEL_TRANSCRIPT
        inp = data["input_payload"]
        assert inp["_log_truncated"] is True
        assert inp["_original_chars"] > 200
        assert "preview" in inp


class TestTraceAndFingerprint:
    def test_fingerprint_stable_for_same_exception(self) -> None:
        try:
            raise ValueError("same")
        except ValueError as e:
            fp1 = compute_error_fingerprint(e)
            fp2 = compute_error_fingerprint(e)
        assert fp1 == fp2
        assert len(fp1) == 16

    def test_trace_log_includes_fingerprint(self) -> None:
        buf = io.StringIO()
        h = logging.StreamHandler(buf)
        h.setFormatter(JSONFormatter())
        log = logging.getLogger("test-trace-fp")
        log.handlers.clear()
        log.setLevel(logging.ERROR)
        log.addHandler(h)
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            log_trace_event(
                log,
                "failed",
                exc_info=True,
                error_fingerprint="manual-fp",
            )
        line = buf.getvalue().strip()
        data = json.loads(line)
        assert data["log_channel"] == LOG_CHANNEL_TRACE
        assert data["error_fingerprint"] == "manual-fp"
        assert "exception" in data

    def test_trace_event_extra_cannot_override_log_channel(self) -> None:
        buf = io.StringIO()
        h = logging.StreamHandler(buf)
        h.setFormatter(JSONFormatter())
        log = logging.getLogger("test-trace-channel-lock")
        log.handlers.clear()
        log.setLevel(logging.ERROR)
        log.addHandler(h)
        log_trace_event(log, "trace", exc_info=False, log_channel="not-trace")
        data = json.loads(buf.getvalue().strip())
        assert data["log_channel"] == LOG_CHANNEL_TRACE

    def test_format_exception_for_trace(self) -> None:
        try:
            raise KeyError("nope")
        except KeyError as e:
            text = format_exception_for_trace(e)
        assert "KeyError" in text
        assert "nope" in text


class TestAccessEvent:
    def test_access_event_json_fields(self) -> None:
        buf = io.StringIO()
        h = logging.StreamHandler(buf)
        h.setFormatter(JSONFormatter())
        log = logging.getLogger("test-access-json")
        log.handlers.clear()
        log.setLevel(logging.INFO)
        log.addHandler(h)
        log_access_event(
            log,
            path="/mcp",
            tool=None,
            status=200,
            duration_ms=12.5,
            request_id="rid-1",
            method="POST",
        )
        data = json.loads(buf.getvalue().strip())
        assert data["log_channel"] == LOG_CHANNEL_ACCESS
        assert data["path"] == "/mcp"
        assert data["status"] == 200
        assert data["duration_ms"] == 12.5
        assert data["request_id"] == "rid-1"
        assert data["method"] == "POST"

    def test_access_event_extra_cannot_override_log_channel(self) -> None:
        buf = io.StringIO()
        h = logging.StreamHandler(buf)
        h.setFormatter(JSONFormatter())
        log = logging.getLogger("test-access-channel-lock")
        log.handlers.clear()
        log.setLevel(logging.INFO)
        log.addHandler(h)
        log_access_event(log, log_channel="not-access", path="/health")
        data = json.loads(buf.getvalue().strip())
        assert data["log_channel"] == LOG_CHANNEL_ACCESS


class TestTranscriptSampling:
    def test_transcript_should_log_respects_flags(self) -> None:
        from mcp_common.config import MCPSettings

        off = MCPSettings(log_transcript=False, log_transcript_sample_rate=1.0)
        assert transcript_should_log(off) is False
        on = MCPSettings(log_transcript=True, log_transcript_sample_rate=1.0)
        assert transcript_should_log(on) is True

    def test_transcript_sample_rate_zero_never_logs(self) -> None:
        from mcp_common.config import MCPSettings

        s = MCPSettings(log_transcript=True, log_transcript_sample_rate=0.0)
        assert transcript_should_log(s) is False

    def test_transcript_sample_rate_respects_random(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from mcp_common.config import MCPSettings

        s = MCPSettings(log_transcript=True, log_transcript_sample_rate=0.5)
        monkeypatch.setattr(logging_mod.random, "random", lambda: 0.1)
        assert transcript_should_log(s) is True
        monkeypatch.setattr(logging_mod.random, "random", lambda: 0.9)
        assert transcript_should_log(s) is False

    def test_redact_config_uses_compiled_patterns_from_settings(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from mcp_common.config import MCPSettings

        settings = MCPSettings(log_redact_key_patterns=[r"^TOKEN_.*$"])
        subs1, patterns1 = redact_config_from_settings(settings)

        def _boom(_pattern: str) -> re.Pattern[str]:
            raise AssertionError("unexpected re.compile call")

        monkeypatch.setattr(logging_mod.re, "compile", _boom)
        subs2, patterns2 = redact_config_from_settings(settings)

        assert patterns1 == patterns2
        assert patterns2[0].search("TOKEN_VALUE")
        assert "token" in subs1
        assert "token" in subs2


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
