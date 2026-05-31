"""Smoke tests for structured logging via mcp-common."""

from __future__ import annotations

import logging

from mcp_common import setup_logging


class TestSetupLogging:
    def test_returns_logger(self) -> None:
        log = setup_logging(level="DEBUG", json_output=False, name="gpu_diag_mcp.test")
        assert isinstance(log, logging.Logger)
        assert log.name == "gpu_diag_mcp.test"
        assert log.level == logging.DEBUG

    def test_json_output(self) -> None:
        log = setup_logging(level="INFO", json_output=True, name="gpu_diag_mcp.test_json")
        assert log.level == logging.INFO
        assert any(
            hasattr(h.formatter, "format") and "JSONFormatter" in type(h.formatter).__name__
            for h in log.handlers
        )

    def test_idempotent(self) -> None:
        log1 = setup_logging(level="INFO", name="gpu_diag_mcp.test_idem")
        handler_count = len(log1.handlers)
        log2 = setup_logging(level="INFO", name="gpu_diag_mcp.test_idem")
        assert log1 is log2
        assert len(log2.handlers) == handler_count

    def test_server_module_logger(self) -> None:
        from gpu_diag_mcp.server import log

        assert isinstance(log, logging.Logger)
        assert log.name == "gpu_diag_mcp"
