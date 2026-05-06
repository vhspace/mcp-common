"""Smoke tests for mcp-common logging integration.

Validates that the setup_logging wiring used by dc-support-mcp's
mcp_server.py and cli.py works as expected with the mcp-common v0.8.0
logging stack.
"""

from __future__ import annotations

import io
import json
import logging
import time

import pytest
from mcp_common.logging import JSONFormatter, setup_logging, timed_operation


@pytest.mark.unit
def test_setup_logging_returns_logger() -> None:
    """setup_logging(name=...) must return a configured Logger."""
    logger = setup_logging(
        name="dc-support-mcp-smoke",
        level="INFO",
        json_output=False,
        system_log=True,
    )
    assert isinstance(logger, logging.Logger)


@pytest.mark.unit
def test_json_formatter_emits_required_fields() -> None:
    """JSONFormatter output must contain timestamp, level, logger, message."""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JSONFormatter())

    test_logger = logging.getLogger("dc-support.smoke.json")
    test_logger.handlers.clear()
    test_logger.addHandler(handler)
    test_logger.setLevel(logging.INFO)
    test_logger.propagate = False

    test_logger.info("hello")
    handler.flush()

    record = json.loads(buf.getvalue().strip())
    assert record["level"] == "INFO"
    assert record["logger"] == "dc-support.smoke.json"
    assert record["message"] == "hello"
    assert "timestamp" in record


@pytest.mark.unit
def test_timed_operation_emits_timing_fields() -> None:
    """timed_operation must emit ok and actual_s in the JSON event."""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JSONFormatter())

    test_logger = logging.getLogger("dc-support.smoke.timing")
    test_logger.handlers.clear()
    test_logger.addHandler(handler)
    test_logger.setLevel(logging.INFO)
    test_logger.propagate = False

    with timed_operation(test_logger, "smoke-op", expected_s=1.0):
        time.sleep(0.01)
    handler.flush()

    lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    assert lines, "timed_operation produced no log output"
    event = json.loads(lines[-1])
    assert event["operation"] == "smoke-op"
    assert event["ok"] is True
    assert isinstance(event["actual_s"], (int, float))


@pytest.mark.unit
def test_timed_operation_marks_failure_on_exception() -> None:
    """timed_operation must set ok=False when the wrapped block raises."""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JSONFormatter())

    test_logger = logging.getLogger("dc-support.smoke.timing.fail")
    test_logger.handlers.clear()
    test_logger.addHandler(handler)
    test_logger.setLevel(logging.INFO)
    test_logger.propagate = False

    with pytest.raises(RuntimeError, match="boom"):
        with timed_operation(test_logger, "smoke-op-fail"):
            raise RuntimeError("boom")
    handler.flush()

    lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    assert lines, "timed_operation produced no log output on failure"
    event = json.loads(lines[-1])
    assert event["operation"] == "smoke-op-fail"
    assert event["ok"] is False
