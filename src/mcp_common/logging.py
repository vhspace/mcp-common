"""Structured logging setup for MCP servers.

Supports channelized logs for ingestion pipelines:

* ``access`` — request lifecycle metadata (path, tool, status, duration, request_id).
* ``transcript`` — optional input/output payloads (off by default) with redaction and size limits.
* ``trace`` — errors with exceptions and optional stack traces (e.g. non-200 or hard failures).

Use :func:`setup_logging` as before; channel helpers add stable ``log_channel`` and related
fields. With ``json_output=True``, :class:`JSONFormatter` merges non-reserved LogRecord
attributes into the top-level JSON object for stable keys downstream.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import sys
import traceback
from collections.abc import Mapping
from typing import Any

from mcp_common.config import MCPSettings

_VALID_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
_ssl_warnings_suppressed = False

LOG_CHANNEL_APP = "app"
LOG_CHANNEL_ACCESS = "access"
LOG_CHANNEL_TRANSCRIPT = "transcript"
LOG_CHANNEL_TRACE = "trace"

_DEFAULT_REDACT_SUBSTRINGS: frozenset[str] = frozenset(
    {
        "password",
        "secret",
        "token",
        "authorization",
        "api_key",
        "apikey",
        "credential",
        "cookie",
        "bearer",
    }
)


def _logrecord_reserved_keys() -> frozenset[str]:
    sample = logging.LogRecord(
        name="",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="",
        args=(),
        exc_info=None,
    )
    return frozenset(sample.__dict__.keys())


_LOGRECORD_RESERVED = _logrecord_reserved_keys()


class JSONFormatter(logging.Formatter):
    """JSON log formatter for container/production environments.

    Emits ``timestamp``, ``level``, ``logger``, ``message``, optional ``exception``,
    optional ``stack_info``, and user fields from ``logger.info(..., extra={})`` that
    are not reserved :class:`logging.LogRecord` attributes.

    If ``log_channel`` is absent, it defaults to :data:`LOG_CHANNEL_APP`.
    """

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "log_channel": getattr(record, "log_channel", LOG_CHANNEL_APP),
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
        stack_info = getattr(record, "stack_info", None)
        if stack_info:
            log_entry["stack_info"] = stack_info

        for key, value in record.__dict__.items():
            if key in _LOGRECORD_RESERVED:
                continue
            if key in {"log_channel", "message"}:
                continue
            log_entry[key] = value

        return json.dumps(log_entry, default=str)


def suppress_ssl_warnings() -> None:
    """Suppress urllib3 ``InsecureRequestWarning`` globally.

    Call at startup when SSL verification is intentionally disabled (e.g.
    internal BMCs, self-signed certs).  Many MCP servers talk to internal
    services with ``verify=False`` and these warnings clutter output and
    get swallowed by agents.

    Safe to call multiple times — only the first call has any effect.
    No-op if urllib3 is not installed.
    """
    global _ssl_warnings_suppressed
    if _ssl_warnings_suppressed:
        return
    try:
        import urllib3  # type: ignore[import-not-found]

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except ImportError:
        pass
    _ssl_warnings_suppressed = True


def setup_logging(
    *,
    level: str = "INFO",
    json_output: bool = False,
    name: str | None = None,
    suppress_ssl: bool = True,
) -> logging.Logger:
    """Configure logging for an MCP server.

    Behavior is unchanged from previous releases: one stderr handler, optional JSON
    formatting, idempotent per logger name. Channel helpers work with the returned
    logger or any child logger.

    Args:
        level: Log level string (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        json_output: Use JSON formatting (recommended for containers). User
            ``extra`` fields are merged into each JSON line (see :class:`JSONFormatter`).
        name: Logger name. Defaults to root logger.
        suppress_ssl: Suppress urllib3 InsecureRequestWarning. Defaults to
            ``True`` because MCP servers commonly talk to internal services
            with self-signed certificates.

    Returns:
        Configured logger instance.
    """
    if suppress_ssl:
        suppress_ssl_warnings()

    logger = logging.getLogger(name)

    normalized = level.upper()
    if normalized not in _VALID_LEVELS:
        normalized = "INFO"
    logger.setLevel(getattr(logging, normalized))

    if logger.handlers:
        return logger

    handler = logging.StreamHandler(sys.stderr)
    if json_output:
        handler.setFormatter(JSONFormatter())
    else:
        fmt = "%(asctime)s %(levelname)-8s %(name)s - %(message)s"
        handler.setFormatter(logging.Formatter(fmt))

    logger.addHandler(handler)
    return logger


def _key_matches_redact(key: str, substrings: frozenset[str]) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(s in normalized for s in substrings)


def _key_matches_patterns(key: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(p.search(key) for p in patterns)


def redact_config_from_settings(
    settings: MCPSettings,
) -> tuple[frozenset[str], tuple[re.Pattern[str], ...]]:
    """Build redaction substrings and compiled key patterns from :class:`~mcp_common.config.MCPSettings`."""
    extra = frozenset(s.lower().replace("-", "_") for s in settings.log_redact_key_substrings)
    subs = _DEFAULT_REDACT_SUBSTRINGS | extra
    patterns = tuple(re.compile(p) for p in settings.log_redact_key_patterns)
    return subs, patterns


def transcript_should_log(settings: MCPSettings) -> bool:
    """Return whether a transcript line should be emitted (honors ``log_transcript`` and sampling)."""
    if not settings.log_transcript:
        return False
    return random.random() < settings.log_transcript_sample_rate


def mcp_log_access(
    logger: logging.Logger,
    settings: MCPSettings,
    message: str = "request completed",
    **fields: Any,
) -> None:
    """Stdio-friendly access log: no-ops when ``settings.log_access`` is false."""
    if not settings.log_access:
        return
    log_access_event(logger, message, **fields)


def mcp_log_transcript(
    logger: logging.Logger,
    settings: MCPSettings,
    message: str = "transcript",
    *,
    phase: str | None = None,
    input_payload: Any | None = None,
    output_payload: Any | None = None,
    request_id: str | None = None,
    tool: str | None = None,
) -> None:
    """Stdio-friendly transcript log; respects transcript enable flag and sampling."""
    if not transcript_should_log(settings):
        return
    subs, patterns = redact_config_from_settings(settings)
    log_transcript_event(
        logger,
        message,
        enabled=True,
        phase=phase,
        input_payload=input_payload,
        output_payload=output_payload,
        request_id=request_id,
        tool=tool,
        redact_substrings=subs,
        key_patterns=patterns,
        max_str_len=settings.log_transcript_max_str_len,
        max_total_chars=settings.log_transcript_max_total_chars,
    )


def mcp_log_trace(
    logger: logging.Logger,
    settings: MCPSettings,
    message: str,
    *,
    exc: BaseException | None = None,
    http_status: int | None = None,
    request_id: str | None = None,
    **extra: Any,
) -> None:
    """Stdio-friendly trace log; no-ops when ``settings.log_trace_on_error`` is false."""
    if not settings.log_trace_on_error:
        return
    fingerprint: str | None = None
    if exc is not None:
        fingerprint = compute_error_fingerprint(exc)
    elif http_status is not None:
        fingerprint = compute_http_error_fingerprint(http_status, request_id)
    log_trace_event(
        logger,
        message,
        exc_info=exc if exc is not None else False,
        capture_stack=settings.log_trace_include_stack,
        http_status=http_status,
        request_id=request_id,
        error_fingerprint=fingerprint,
        **extra,
    )


def sanitize_transcript_value(
    value: Any,
    *,
    redact_substrings: frozenset[str] = _DEFAULT_REDACT_SUBSTRINGS,
    key_patterns: tuple[re.Pattern[str], ...] = (),
    max_str_len: int = 2048,
    _depth: int = 0,
    _max_depth: int = 24,
) -> Any:
    """Redact and truncate a single value (recursive for dict/list/tuple).

    Dict keys matching redact substrings or patterns get values replaced with
    ``"[REDACTED]"``. Strings longer than ``max_str_len`` are truncated with an ellipsis suffix.
    """
    if _depth > _max_depth:
        return "[DEPTH_LIMIT]"

    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for k, v in value.items():
            sk = str(k)
            if _key_matches_redact(sk, redact_substrings) or (
                key_patterns and _key_matches_patterns(sk, key_patterns)
            ):
                out[sk] = "[REDACTED]"
            else:
                out[sk] = sanitize_transcript_value(
                    v,
                    redact_substrings=redact_substrings,
                    key_patterns=key_patterns,
                    max_str_len=max_str_len,
                    _depth=_depth + 1,
                    _max_depth=_max_depth,
                )
        return out

    if isinstance(value, (list, tuple)):
        seq = [
            sanitize_transcript_value(
                item,
                redact_substrings=redact_substrings,
                key_patterns=key_patterns,
                max_str_len=max_str_len,
                _depth=_depth + 1,
                _max_depth=_max_depth,
            )
            for item in value
        ]
        return seq if isinstance(value, list) else tuple(seq)

    if isinstance(value, str):
        if len(value) > max_str_len:
            return value[: max_str_len - 1] + "…"
        return value

    if isinstance(value, (int, float, bool)) or value is None:
        return value

    text = str(value)
    if len(text) > max_str_len:
        return text[: max_str_len - 1] + "…"
    return text


def _truncate_serialized(
    sanitized: Any,
    *,
    max_total_chars: int,
) -> Any:
    """If JSON length exceeds ``max_total_chars``, replace with a preview payload."""
    try:
        raw = json.dumps(sanitized, default=str)
    except (TypeError, ValueError):
        raw = str(sanitized)
    if len(raw) <= max_total_chars:
        return sanitized
    preview_len = max(0, max_total_chars - 80)
    preview = raw[:preview_len] + "…"
    return {
        "_log_truncated": True,
        "_original_chars": len(raw),
        "preview": preview,
    }


def log_access_event(
    logger: logging.Logger,
    message: str = "request completed",
    *,
    enabled: bool = True,
    path: str | None = None,
    tool: str | None = None,
    status: int | None = None,
    duration_ms: float | None = None,
    request_id: str | None = None,
    **extra: Any,
) -> None:
    """Emit an access / request log line (``log_channel`` = ``access``)."""
    if not enabled:
        return
    payload: dict[str, Any] = {
        "log_channel": LOG_CHANNEL_ACCESS,
        **extra,
    }
    if path is not None:
        payload["path"] = path
    if tool is not None:
        payload["tool"] = tool
    if status is not None:
        payload["status"] = status
    if duration_ms is not None:
        payload["duration_ms"] = duration_ms
    if request_id is not None:
        payload["request_id"] = request_id

    logger.info(message, extra=payload)


def log_transcript_event(
    logger: logging.Logger,
    message: str = "transcript",
    *,
    enabled: bool = False,
    phase: str | None = None,
    input_payload: Any | None = None,
    output_payload: Any | None = None,
    request_id: str | None = None,
    tool: str | None = None,
    redact_substrings: frozenset[str] | None = None,
    key_patterns: tuple[re.Pattern[str], ...] = (),
    max_str_len: int = 2048,
    max_total_chars: int = 65536,
) -> None:
    """Emit a transcript log (``log_channel`` = ``transcript``).

    **Disabled by default** — when ``enabled`` is ``False``, this is a no-op.
    """
    if not enabled:
        return

    rs = redact_substrings if redact_substrings is not None else _DEFAULT_REDACT_SUBSTRINGS
    sanitized_input = (
        None
        if input_payload is None
        else _truncate_serialized(
            sanitize_transcript_value(
                input_payload,
                redact_substrings=rs,
                key_patterns=key_patterns,
                max_str_len=max_str_len,
            ),
            max_total_chars=max_total_chars,
        )
    )
    sanitized_output = (
        None
        if output_payload is None
        else _truncate_serialized(
            sanitize_transcript_value(
                output_payload,
                redact_substrings=rs,
                key_patterns=key_patterns,
                max_str_len=max_str_len,
            ),
            max_total_chars=max_total_chars,
        )
    )

    extra: dict[str, Any] = {
        "log_channel": LOG_CHANNEL_TRANSCRIPT,
        "input_payload": sanitized_input,
        "output_payload": sanitized_output,
    }
    if phase is not None:
        extra["phase"] = phase
    if request_id is not None:
        extra["request_id"] = request_id
    if tool is not None:
        extra["tool"] = tool

    logger.info(message, extra=extra)


def compute_error_fingerprint(exc: BaseException) -> str:
    """Stable short fingerprint for an exception (for deduping / correlation)."""
    tb = exc.__traceback__
    frame = None
    if tb is not None:
        frames = traceback.extract_tb(tb)
        if frames:
            frame = frames[-1]
    parts = [type(exc).__name__, str(exc)[:200]]
    if frame is not None:
        parts.append(f"{frame.filename}:{frame.lineno}")
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def compute_http_error_fingerprint(status: int, request_id: str | None) -> str:
    """Fingerprint for HTTP failures without a Python exception."""
    rid = request_id or ""
    raw = f"http|{status}|{rid}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def log_trace_event(
    logger: logging.Logger,
    message: str,
    *,
    exc_info: bool | BaseException | None = True,
    capture_stack: bool = False,
    http_status: int | None = None,
    request_id: str | None = None,
    error_fingerprint: str | None = None,
    **extra: Any,
) -> None:
    """Emit a trace log (``log_channel`` = ``trace``) for failures and diagnostics."""
    payload: dict[str, Any] = {
        "log_channel": LOG_CHANNEL_TRACE,
        **extra,
    }
    if http_status is not None:
        payload["http_status"] = http_status
    if request_id is not None:
        payload["request_id"] = request_id
    if error_fingerprint is not None:
        payload["error_fingerprint"] = error_fingerprint

    logger.error(
        message,
        exc_info=exc_info,
        stack_info=capture_stack,
        extra=payload,
    )


def format_exception_for_trace(exc: BaseException) -> str:
    """Format an exception as a single string (for non-logging callers)."""
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
