"""Progress-aware polling utilities for MCP tool implementations."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from fastmcp import Context

_PROGRESS_SEND_TIMEOUT = 5.0


@dataclass
class OperationStates:
    """Defines success/failure/in-progress states for a polled operation."""

    success: list[str] = field(default_factory=list)
    failure: list[str] = field(default_factory=list)
    in_progress: list[str] = field(default_factory=list)


@dataclass
class PollResult:
    """Result of a polling operation."""

    ok: bool
    final_state: str
    elapsed_s: float
    timed_out: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


async def _safe_report_progress(
    ctx: Context,
    progress: float,
    total: float,
    message: str,
    logger: logging.Logger | None = None,
) -> bool:
    """Send a progress notification, returning False if the channel is broken.

    Wraps ``ctx.report_progress`` with a short timeout and blanket exception
    handling so that a broken or back-pressured MCP transport never blocks the
    poll loop.
    """
    try:
        await asyncio.wait_for(
            ctx.report_progress(progress=progress, total=total, message=message),
            timeout=_PROGRESS_SEND_TIMEOUT,
        )
        return True
    except Exception:
        if logger:
            logger.debug("Progress notification failed; continuing without progress")
        return False


async def poll_with_progress(
    ctx: Context,
    check_fn: Callable[[], dict[str, Any] | Awaitable[dict[str, Any]]],
    state_key: str,
    states: OperationStates,
    *,
    timeout_s: float = 600,
    interval_s: float = 10,
    format_message: Callable[[dict[str, Any], float], str] | None = None,
    logger: logging.Logger | None = None,
    operation: str | None = None,
) -> PollResult:
    """Poll an operation with MCP progress notifications.

    Args:
        ctx: FastMCP Context for sending progress notifications.
        check_fn: Callable that returns current state dict. Can be sync or async.
        state_key: Key in the state dict that contains the status string.
        states: OperationStates defining success/failure/in-progress states.
        timeout_s: Maximum time to poll before giving up.
        interval_s: Seconds between polls.
        format_message: Optional function to format progress message from state dict
            and elapsed time.
        logger: Optional logger; when provided a timing event is emitted on completion.
        operation: Optional operation name for the timing event.

    Returns:
        PollResult with final state and timing info.
    """
    try:
        return await asyncio.wait_for(
            _poll_loop(
                ctx, check_fn, state_key, states,
                timeout_s=timeout_s, interval_s=interval_s,
                format_message=format_message, logger=logger, operation=operation,
            ),
            timeout=timeout_s + _PROGRESS_SEND_TIMEOUT,
        )
    except asyncio.TimeoutError:
        poll_result = PollResult(
            ok=False, final_state="unknown", elapsed_s=timeout_s, timed_out=True,
        )
        _emit_poll_timing(logger, poll_result, timeout_s, operation)
        return poll_result


async def _poll_loop(
    ctx: Context,
    check_fn: Callable[[], dict[str, Any] | Awaitable[dict[str, Any]]],
    state_key: str,
    states: OperationStates,
    *,
    timeout_s: float,
    interval_s: float,
    format_message: Callable[[dict[str, Any], float], str] | None,
    logger: logging.Logger | None,
    operation: str | None,
) -> PollResult:
    """Inner poll loop extracted for ``asyncio.wait_for`` wrapping."""
    loop = asyncio.get_event_loop()
    start = loop.time()
    current_state = "unknown"
    last_result: dict[str, Any] = {}

    while True:
        elapsed = loop.time() - start
        if elapsed >= timeout_s:
            break

        result = check_fn()
        if inspect.isawaitable(result):
            result = await result
        last_result = result

        current_state = str(last_result.get(state_key, "unknown"))

        if format_message:
            message = format_message(last_result, elapsed)
        else:
            message = f"{current_state} ({elapsed:.0f}s elapsed)"

        await _safe_report_progress(ctx, elapsed, timeout_s, message, logger)

        if current_state in states.success:
            poll_result = PollResult(
                ok=True, final_state=current_state, elapsed_s=elapsed, extra=last_result
            )
            _emit_poll_timing(logger, poll_result, timeout_s, operation)
            return poll_result

        if current_state in states.failure:
            poll_result = PollResult(
                ok=False, final_state=current_state, elapsed_s=elapsed, extra=last_result
            )
            _emit_poll_timing(logger, poll_result, timeout_s, operation)
            return poll_result

        await asyncio.sleep(interval_s)

    elapsed = loop.time() - start
    poll_result = PollResult(
        ok=False, final_state=current_state, elapsed_s=elapsed, timed_out=True, extra=last_result
    )
    _emit_poll_timing(logger, poll_result, timeout_s, operation)
    return poll_result


def _emit_poll_timing(
    logger: logging.Logger | None,
    result: PollResult,
    timeout_s: float,
    operation: str | None,
) -> None:
    """Emit a timing event for a completed poll cycle."""
    if logger is None:
        return
    from mcp_common.logging import log_timing_event

    log_timing_event(
        logger,
        message="poll completed",
        operation=operation or "poll_with_progress",
        expected_s=timeout_s,
        actual_s=result.elapsed_s,
        timed_out=result.timed_out,
        ok=result.ok,
        final_state=result.final_state,
    )
