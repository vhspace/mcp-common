"""Structured logging setup for MCP servers.

Supports channelized logs for ingestion pipelines:

* ``access`` — request lifecycle metadata (path, tool, status, duration, request_id).
* ``transcript`` — optional input/output payloads (off by default) with redaction and size limits.
* ``trace`` — errors with exceptions and optional stack traces (e.g. non-200 or hard failures).

Use :func:`setup_logging` as before; channel helpers add stable ``log_channel`` and related
fields. With ``json_output=True``, :class:`JSONFormatter` merges non-reserved LogRecord
attributes into the top-level JSON object for stable keys downstream.

The ``trace`` channel is **isolated** from the caller-facing stderr stream. Diagnostic
events (agent-remediation text, error fingerprints, tracebacks) are emitted by
:func:`log_trace_event` on a dedicated logger (:data:`TRACE_LOGGER_NAME`) that has
``propagate=False`` and, by default, only a :class:`logging.NullHandler`. They therefore
**never** reach the application's root/stderr ``StreamHandler`` (the one
:func:`setup_logging` installs) nor the :data:`logging.lastResort` fallback — they exist for
a separate triage agent / the failure-correlation pipeline (`vhspace/mcp-common#31
<https://github.com/vhspace/mcp-common/issues/31>`_). Route the channel to a durable sink
with :func:`configure_trace_channel` or ``setup_logging(trace_handler=...)``
(see `vhspace/mcp-common#117 <https://github.com/vhspace/mcp-common/issues/117>`_).
"""

from __future__ import annotations

import hashlib
import json
import logging
import logging.handlers
import os
import random
import re
import sys
import time
import traceback
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

from mcp_common.config import MCPSettings

_VALID_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
_ssl_warnings_suppressed = False

LOG_CHANNEL_APP = "app"
LOG_CHANNEL_ACCESS = "access"
LOG_CHANNEL_TRANSCRIPT = "transcript"
LOG_CHANNEL_TRACE = "trace"

#: Name of the dedicated diagnostic/trace logger. It is intentionally isolated
#: from the caller-facing stderr stream — see :func:`get_trace_logger`.
TRACE_LOGGER_NAME = "mcp_common.trace"

DEFAULT_NOISY_LOGGERS: tuple[str, ...] = ("urllib3", "httpx", "requests", "httpcore")

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
_ACCESS_EVENT_RESERVED_EXTRA_KEYS = frozenset(
    {"log_channel", "path", "tool", "status", "duration_ms", "request_id"}
)
_TRACE_EVENT_RESERVED_EXTRA_KEYS = frozenset(
    {"log_channel", "http_status", "request_id", "error_fingerprint", "source"}
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

    Field mapping:
        Datadog expects ``status`` for severity (we emit ``level``) and
        ``service`` for service name (we emit ``logger``).  Users should add
        Datadog pipeline remapping rules, or subclass this formatter to
        override field names.

    Aggregator compatibility:
        Datadog: auto-parses JSON from syslog bodies when ident is empty.
            Remap ``level`` → ``status`` and ``logger`` → ``service`` in a
            Datadog log pipeline. Raw field names work without remapping in
            Elastic, Splunk, and Graylog.
        RFC format: emits RFC 3164 (BSD syslog) via Python's SysLogHandler.
            Upgrade to RFC 5424 with ``rfc5424-logging-handler`` if needed.
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


def get_trace_logger() -> logging.Logger:
    """Return the dedicated diagnostic/trace logger (:data:`TRACE_LOGGER_NAME`).

    The trace channel is isolated from the caller-facing stderr stream:

    * ``propagate`` is forced to ``False`` so trace records never bubble up to
      the root logger's :class:`~logging.StreamHandler` (the one
      :func:`setup_logging` installs on stderr) or to any ancestor's handlers.
    * A :class:`logging.NullHandler` is attached by default so that, with no
      explicit sink configured, trace events are silently dropped **and** the
      :data:`logging.lastResort` stderr fallback never fires (that fallback only
      triggers when a record finds *zero* handlers in its propagation chain).

    :func:`log_trace_event` always emits here — regardless of the context logger
    a caller passes — so diagnostic artifacts (remediation text, fingerprints,
    tracebacks) are available to the failure-correlation pipeline
    (`vhspace/mcp-common#31 <https://github.com/vhspace/mcp-common/issues/31>`_)
    without ever reaching the calling agent's stderr. Route the channel to a
    durable sink with :func:`configure_trace_channel` or
    ``setup_logging(trace_handler=...)``.

    Idempotent and safe to call repeatedly; it never removes a sink an
    application has already attached.
    """
    logger = logging.getLogger(TRACE_LOGGER_NAME)
    logger.propagate = False
    if logger.level == logging.NOTSET:
        # Capture everything routed here; the sink/handler decides what to keep.
        logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())
    return logger


def configure_trace_channel(
    handler: logging.Handler,
    *,
    level: int | str | None = None,
    formatter: logging.Formatter | None = None,
    replace: bool = True,
) -> logging.Logger:
    """Route the dedicated trace/diagnostic channel to a durable sink.

    Attaches ``handler`` to the trace logger (:data:`TRACE_LOGGER_NAME`), which
    has ``propagate=False``, so diagnostic records flow to the sink feeding the
    failure-correlation pipeline (`vhspace/mcp-common#31
    <https://github.com/vhspace/mcp-common/issues/31>`_) **without** ever
    reaching the caller-facing stderr handler.

    Args:
        handler: The sink to attach (e.g. a :class:`~logging.FileHandler`, a
            JSON/HTTP handler feeding the triage pipeline, or — in tests — a
            :class:`~logging.StreamHandler` over a buffer).
        level: Optional level for ``handler`` (``int`` or level name).
        formatter: Optional formatter for ``handler``. When omitted and the
            handler has no formatter, a :class:`JSONFormatter` is applied so the
            structured trace fields (``error_fingerprint``, ``source``, …) are
            preserved.
        replace: When ``True`` (default), drop any handlers already attached to
            the trace logger (including the inert default
            :class:`~logging.NullHandler`) so the channel has exactly this sink.
            When ``False``, append alongside the existing handlers.

    Returns:
        The configured trace logger.
    """
    logger = get_trace_logger()
    if level is not None:
        handler.setLevel(_coerce_level(level))
    if formatter is not None:
        handler.setFormatter(formatter)
    elif handler.formatter is None:
        handler.setFormatter(JSONFormatter())
    if replace:
        for existing in list(logger.handlers):
            logger.removeHandler(existing)
    logger.addHandler(handler)
    return logger


def _coerce_level(level: int | str) -> int:
    if isinstance(level, int):
        return level
    return getattr(logging, level.upper(), logging.NOTSET)


# Initialize the dedicated trace channel at import time so diagnostic events
# never reach the caller's stderr, even before ``setup_logging`` runs.
get_trace_logger()


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
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except ImportError:
        pass
    _ssl_warnings_suppressed = True


def suppress_noisy_loggers(
    level: int = logging.WARNING,
    names: Sequence[str] = DEFAULT_NOISY_LOGGERS,
) -> None:
    """Quiet noisy third-party loggers commonly used by MCP servers.

    By default, sets ``urllib3``, ``httpx``, ``requests``, and ``httpcore``
    to WARNING so request lifecycle chatter does not bury application logs
    at normal verbosity. Pass ``names`` to target a custom set or ``level``
    to use a different ceiling (e.g. ``logging.ERROR``).

    Safe to call multiple times — :meth:`logging.Logger.setLevel` is already
    idempotent.

    Args:
        level: ``logging`` level constant applied to each named logger.
            Defaults to :data:`logging.WARNING`.
        names: Iterable of logger names to quiet. Defaults to
            :data:`DEFAULT_NOISY_LOGGERS`.
    """
    for name in names:
        logging.getLogger(name).setLevel(level)


def setup_logging(
    *,
    level: str = "INFO",
    json_output: bool = False,
    name: str | None = None,
    suppress_ssl: bool = True,
    suppress_noisy: bool = True,
    system_log: bool = True,
    system_log_identifier: str | None = None,
    trace_handler: logging.Handler | None = None,
) -> logging.Logger:
    """Configure logging for an MCP server.

    The caller-facing channel is unchanged from previous releases: one stderr
    handler, optional JSON formatting, idempotent per logger name. Normal
    ``logger.info/warning/error`` on app loggers behaves exactly as before.

    The dedicated **trace/diagnostic** channel (:data:`TRACE_LOGGER_NAME`) is
    always isolated here (``propagate=False`` + default
    :class:`~logging.NullHandler`) so :func:`log_trace_event` records never reach
    this stderr handler. By default that channel has **no durable sink** —
    diagnostic events are dropped unless you opt in via ``trace_handler`` (or
    :func:`configure_trace_channel`). This is a deliberate behavior change in
    `vhspace/mcp-common#117 <https://github.com/vhspace/mcp-common/issues/117>`_:
    trace events no longer appear in the application's normal log stream.

    Args:
        level: Log level string (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        json_output: Use JSON formatting (recommended for containers). User
            ``extra`` fields are merged into each JSON line (see :class:`JSONFormatter`).
        name: Logger name. Defaults to root logger.
        suppress_ssl: Suppress urllib3 InsecureRequestWarning. Defaults to
            ``True`` because MCP servers commonly talk to internal services
            with self-signed certificates.
        suppress_noisy: Quiet noisy third-party loggers (urllib3, httpx,
            requests, httpcore) at WARNING. Defaults to ``True``. Skipped
            automatically when the effective level is DEBUG so verbose runs
            keep their detail. Pass ``False`` to leave those loggers
            untouched and rely on the global root configuration instead.
        system_log: Attach a SysLogHandler when a platform syslog socket is
            available.  Silently skipped when the socket does not exist or the
            connection fails.  Defaults to ``True``.
        system_log_identifier: Program identifier for syslog lines.  Defaults
            to the *name* argument.
        trace_handler: Optional durable sink for the trace/diagnostic channel.
            When provided it is attached to :data:`TRACE_LOGGER_NAME` (replacing
            the default :class:`~logging.NullHandler`) so failure diagnostics are
            persisted for the triage / failure-correlation pipeline
            (`vhspace/mcp-common#31 <https://github.com/vhspace/mcp-common/issues/31>`_).
            It must **not** be a stderr handler — keeping the trace channel off
            the caller's stderr is the whole point. ``None`` (default) leaves the
            channel sink-less (nothing is emitted anywhere). Additive: omitting
            it preserves the prior call signature.

    Platform notes:
        Linux (Ubuntu 22.04+): Routes to journald via /dev/log. Query with
            ``journalctl -t <identifier> --since "1 hour ago" -o json``.
        macOS (Tahoe+): Best-effort via /var/run/syslog. Apple's unified
            logging replaced traditional syslog in macOS 12; messages may
            not appear in ``log show``. Silent fallback to stderr-only.

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

    if suppress_noisy and normalized != "DEBUG":
        suppress_noisy_loggers()

    # The trace/diagnostic channel is independent of the app logger's handler
    # state: ensure it stays isolated (propagate=False + NullHandler) and, when a
    # durable sink is supplied, route it there — on every call, even the
    # idempotent re-entry below.
    get_trace_logger()
    if trace_handler is not None:
        configure_trace_channel(trace_handler)

    if logger.handlers:
        return logger

    if json_output:
        formatter: logging.Formatter = JSONFormatter()
    else:
        fmt = "%(asctime)s %(levelname)-8s %(name)s - %(message)s"
        formatter = logging.Formatter(fmt)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    if system_log:
        syslog_handler = _try_syslog_handler(system_log_identifier or name)
        if syslog_handler is not None:
            if json_output:
                syslog_handler.setFormatter(JSONFormatter())
                syslog_handler.ident = ""  # Clean JSON for aggregator auto-parsing
            else:
                syslog_handler.setFormatter(
                    logging.Formatter("%(name)s %(levelname)s - %(message)s")
                )
            logger.addHandler(syslog_handler)

    return logger


def _try_syslog_handler(
    identifier: str | None,
) -> logging.handlers.SysLogHandler | None:
    """Attempt to create a SysLogHandler for the current platform.

    Returns ``None`` when the platform socket does not exist or the connection
    fails — the caller should simply skip syslog in that case.

    Target platforms: macOS Tahoe (26) or later, Ubuntu 22.04+.  macOS 12+
    replaced traditional syslog with unified logging (``os_log``); the
    ``/var/run/syslog`` socket may not deliver messages on modern macOS.
    This is best-effort on macOS — silent fallback to stderr-only is expected.
    """
    if sys.platform == "linux":
        address = "/dev/log"
    elif sys.platform == "darwin":
        address = "/var/run/syslog"
    else:
        return None

    try:
        if not os.path.exists(address):
            return None
        h = logging.handlers.SysLogHandler(address=address)
        if identifier:
            h.ident = identifier + ": "
        return h
    except (OSError, ConnectionError):
        return None


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
    patterns = settings.compiled_log_redact_key_patterns()
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
        fingerprint = compute_http_error_fingerprint(http_status)
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


def _sanitize_and_truncate_payload(
    payload: Any | None,
    *,
    redact_substrings: frozenset[str],
    key_patterns: tuple[re.Pattern[str], ...],
    max_str_len: int,
    max_total_chars: int,
) -> Any | None:
    if payload is None:
        return None
    sanitized = sanitize_transcript_value(
        payload,
        redact_substrings=redact_substrings,
        key_patterns=key_patterns,
        max_str_len=max_str_len,
    )
    return _truncate_serialized(sanitized, max_total_chars=max_total_chars)


def _strip_reserved_extra(
    extra: dict[str, Any], *, reserved_keys: frozenset[str]
) -> dict[str, Any]:
    return {key: value for key, value in extra.items() if key not in reserved_keys}


def _emit_channel_event(
    logger: logging.Logger,
    message: str,
    *,
    channel: str,
    level: int = logging.INFO,
    reserved_keys: frozenset[str],
    fields: dict[str, Any],
    exc_info: Any = None,
    stack_info: bool = False,
    **extra: Any,
) -> None:
    payload = _strip_reserved_extra(extra, reserved_keys=reserved_keys)
    payload["log_channel"] = channel
    for k, v in fields.items():
        if v is not None:
            payload[k] = v
    logger.log(level, message, exc_info=exc_info, stack_info=stack_info, extra=payload)


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
    _emit_channel_event(
        logger,
        message,
        channel=LOG_CHANNEL_ACCESS,
        reserved_keys=_ACCESS_EVENT_RESERVED_EXTRA_KEYS,
        fields={
            "path": path,
            "tool": tool,
            "status": status,
            "duration_ms": duration_ms,
            "request_id": request_id,
        },
        **extra,
    )


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

    Does not use ``_emit_channel_event`` because transcript payloads require
    per-field redaction and size truncation before emission.
    """
    if not enabled:
        return

    rs = redact_substrings if redact_substrings is not None else _DEFAULT_REDACT_SUBSTRINGS
    sanitized_input = _sanitize_and_truncate_payload(
        input_payload,
        redact_substrings=rs,
        key_patterns=key_patterns,
        max_str_len=max_str_len,
        max_total_chars=max_total_chars,
    )
    sanitized_output = _sanitize_and_truncate_payload(
        output_payload,
        redact_substrings=rs,
        key_patterns=key_patterns,
        max_str_len=max_str_len,
        max_total_chars=max_total_chars,
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


def compute_http_error_fingerprint(status: int, path: str | None = None) -> str:
    """Stable fingerprint for HTTP failures grouped by status and endpoint."""
    raw = f"http|{status}|{path or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def log_trace_event(
    logger: logging.Logger | None,
    message: str,
    *,
    exc_info: bool | BaseException | None = True,
    capture_stack: bool = False,
    http_status: int | None = None,
    request_id: str | None = None,
    error_fingerprint: str | None = None,
    **extra: Any,
) -> None:
    """Emit a diagnostic event on the dedicated trace channel (``log_channel`` = ``trace``).

    The record (message, ``exc_info`` traceback, fingerprint, remediation text,
    and any ``extra`` fields) is emitted on the isolated trace logger
    (:data:`TRACE_LOGGER_NAME`) — **never** on ``logger``. Because that logger has
    ``propagate=False`` and, by default, only a :class:`~logging.NullHandler`, the
    diagnostic **never reaches the caller's stderr** (not the root
    ``StreamHandler`` :func:`setup_logging` installs, nor the
    :data:`logging.lastResort` fallback). Apps route the channel to a durable
    sink for the triage pipeline via :func:`configure_trace_channel` /
    ``setup_logging(trace_handler=...)``.

    Args:
        logger: The *context* logger. It is no longer used as the emission sink;
            only its name is preserved as the structured ``source`` field so the
            originating component is still identifiable in the trace record.
            ``None`` is accepted (``source`` is then omitted).
        message: Human-readable event message.
        exc_info: Exception (or ``True``/``False``) to attach. Safe to keep the
            full exception here — it is formatted by the trace channel's sink,
            not the caller's stderr.
        capture_stack: Capture the current stack into ``stack_info``.
        http_status: Optional HTTP status for HTTP-failure diagnostics.
        request_id: Optional request correlation id.
        error_fingerprint: Stable dedupe id (see :func:`compute_error_fingerprint`).
        **extra: Additional structured fields (e.g. ``tool_name``, ``project_repo``,
            ``version``, ``remediation``, ``traceback``).
    """
    source = getattr(logger, "name", None)
    _emit_channel_event(
        get_trace_logger(),
        message,
        channel=LOG_CHANNEL_TRACE,
        level=logging.ERROR,
        reserved_keys=_TRACE_EVENT_RESERVED_EXTRA_KEYS,
        fields={
            "http_status": http_status,
            "request_id": request_id,
            "error_fingerprint": error_fingerprint,
            "source": source,
        },
        exc_info=exc_info,
        stack_info=capture_stack,
        **extra,
    )


def format_exception_for_trace(exc: BaseException) -> str:
    """Format an exception as a single string (for non-logging callers)."""
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


# ---------------------------------------------------------------------------
# Timing telemetry
# ---------------------------------------------------------------------------

_TIMING_EVENT_RESERVED_EXTRA_KEYS = frozenset(
    {"log_channel", "operation", "expected_s", "actual_s", "timed_out", "ok"}
)


def log_timing_event(
    logger: logging.Logger,
    message: str = "operation completed",
    *,
    operation: str | None = None,
    expected_s: float | None = None,
    actual_s: float | None = None,
    timed_out: bool = False,
    ok: bool = True,
    **extra: Any,
) -> None:
    """Emit an access-channel timing event with structured fields.

    Designed for measuring operation durations (polling, API calls, etc.).
    All timing fields are optional and forwarded as ``extra`` on the log record.
    """
    _emit_channel_event(
        logger,
        message,
        channel=LOG_CHANNEL_ACCESS,
        reserved_keys=_TIMING_EVENT_RESERVED_EXTRA_KEYS,
        fields={
            "operation": operation,
            "expected_s": expected_s,
            "actual_s": actual_s,
            "timed_out": timed_out,
            "ok": ok,
        },
        **extra,
    )


@contextmanager
def timed_operation(
    logger: logging.Logger,
    operation: str,
    *,
    expected_s: float | None = None,
) -> Generator[None, None, None]:
    """Context manager that measures wall-clock duration and emits a timing event."""
    start = time.monotonic()
    ok = True
    try:
        yield
    except Exception:
        ok = False
        raise
    finally:
        actual_s = time.monotonic() - start
        log_timing_event(
            logger,
            operation=operation,
            expected_s=expected_s,
            actual_s=actual_s,
            ok=ok,
        )
