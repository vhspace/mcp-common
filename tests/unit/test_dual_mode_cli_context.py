"""Tests for ``mcp_common.dual_mode.CliContext`` — the CLI-side Context shim."""

from __future__ import annotations

import io
import logging
import warnings
from typing import Any

import anyio
import pytest

from mcp_common.dual_mode import CliContext


def _run_async(coro: Any) -> Any:
    """Drive a coroutine to completion with ``anyio`` so the test stays sync."""
    return anyio.run(lambda: coro)


class _CapturingHandler(logging.Handler):
    """In-memory log handler that records records at any level."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def capture() -> tuple[_CapturingHandler, logging.Logger]:
    """Logger + capture handler scoped to each test."""
    handler = _CapturingHandler()
    logger = logging.getLogger(f"cli_context_test_{id(handler)}")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    logger.propagate = False
    yield handler, logger
    logger.removeHandler(handler)


class TestLogLevels:
    @pytest.mark.parametrize(
        "method,level",
        [
            ("info", logging.INFO),
            ("warning", logging.WARNING),
            ("error", logging.ERROR),
            ("debug", logging.DEBUG),
        ],
    )
    def test_level_method_logs_at_matching_level(
        self,
        capture: tuple[_CapturingHandler, logging.Logger],
        method: str,
        level: int,
    ) -> None:
        handler, logger = capture
        ctx = CliContext(logger=logger)

        async def _call() -> None:
            await getattr(ctx, method)(f"hello via {method}")

        _run_async(_call())
        assert len(handler.records) == 1
        assert handler.records[0].levelno == level
        assert handler.records[0].getMessage() == f"hello via {method}"

    def test_log_method_uses_named_level(
        self, capture: tuple[_CapturingHandler, logging.Logger]
    ) -> None:
        handler, logger = capture
        ctx = CliContext(logger=logger)

        async def _call() -> None:
            await ctx.log("alert", level="warning")
            await ctx.log("critical", level="critical")

        _run_async(_call())
        assert len(handler.records) == 2
        assert handler.records[0].levelno == logging.WARNING
        assert handler.records[1].levelno == logging.CRITICAL

    def test_log_unknown_level_falls_back_to_info(
        self, capture: tuple[_CapturingHandler, logging.Logger]
    ) -> None:
        handler, logger = capture
        ctx = CliContext(logger=logger)

        async def _call() -> None:
            await ctx.log("plain message", level="bogus")

        _run_async(_call())
        assert handler.records[0].levelno == logging.INFO

    def test_logger_name_override(self, capture: tuple[_CapturingHandler, logging.Logger]) -> None:
        handler, logger = capture
        # Wire ``handler`` onto the named target logger so the override path captures.
        target = logging.getLogger("dual_mode_alt_logger")
        target.setLevel(logging.DEBUG)
        target.addHandler(handler)
        target.propagate = False

        ctx = CliContext(logger=logger)

        async def _call() -> None:
            await ctx.info("scoped", logger_name="dual_mode_alt_logger")

        _run_async(_call())
        try:
            assert handler.records, "Expected log record from override logger"
            assert handler.records[0].name == "dual_mode_alt_logger"
        finally:
            target.removeHandler(handler)


class TestReportProgress:
    def test_progress_with_total_shows_percent(self) -> None:
        buf = io.StringIO()
        ctx = CliContext(stream=buf)

        async def _call() -> None:
            await ctx.report_progress(progress=25, total=100, message="quarter")

        _run_async(_call())
        out = buf.getvalue().strip()
        assert "%" in out
        assert "25" in out
        assert "quarter" in out

    def test_progress_without_total_shows_raw(self) -> None:
        buf = io.StringIO()
        ctx = CliContext(stream=buf)

        async def _call() -> None:
            await ctx.report_progress(progress=7)

        _run_async(_call())
        out = buf.getvalue().strip()
        assert "7" in out

    def test_progress_zero_total_does_not_divide_by_zero(self) -> None:
        buf = io.StringIO()
        ctx = CliContext(stream=buf)

        async def _call() -> None:
            await ctx.report_progress(progress=5, total=0, message="stalled")

        _run_async(_call())
        out = buf.getvalue().strip()
        assert "5" in out
        assert "0" in out
        assert "stalled" in out

    def test_progress_message_omitted_when_none(self) -> None:
        buf = io.StringIO()
        ctx = CliContext(stream=buf)

        async def _call() -> None:
            await ctx.report_progress(progress=50, total=100)

        _run_async(_call())
        # Just verify no spurious "None" appears in output.
        assert "None" not in buf.getvalue()

    def test_progress_write_failure_does_not_raise(
        self, capture: tuple[_CapturingHandler, logging.Logger]
    ) -> None:
        _, logger = capture

        class _BrokenStream:
            def write(self, s: str) -> int:
                raise RuntimeError("stream broken")

            def flush(self) -> None:
                raise RuntimeError("stream broken")

        # CliContext writes via ``print``; passing a broken file forces
        # the write to raise; the shim catches and logs at DEBUG.
        ctx = CliContext(logger=logger, stream=_BrokenStream())

        async def _call() -> None:
            await ctx.report_progress(progress=10, total=20, message="halfway")

        _run_async(_call())
        # Should not have raised.


class TestAsyncSignatures:
    def test_methods_are_coroutine_functions(self) -> None:
        import inspect

        for method_name in ("info", "warning", "error", "debug", "log", "report_progress"):
            assert inspect.iscoroutinefunction(getattr(CliContext, method_name))


class TestContextDriftDetection:
    def test_drift_warning_lists_unshimmed_methods(self) -> None:
        from mcp_common.dual_mode.cli_context import _detect_context_drift

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _detect_context_drift()

        drift_warnings = [w for w in caught if "CliContext" in str(w.message)]
        # FastMCP's Context has many more async methods than we shim;
        # the warning should fire at import (or here on demand) listing them.
        assert drift_warnings, "Expected at least one drift warning"
        msg = str(drift_warnings[0].message)
        assert "sample" in msg  # one of the known unshimmed methods
