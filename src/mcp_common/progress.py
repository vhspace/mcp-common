"""Progress-aware polling utilities for MCP tool implementations."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from fastmcp import Context


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


async def poll_with_progress(
    ctx: Context,
    check_fn: Callable[[], dict[str, Any] | Awaitable[dict[str, Any]]],
    state_key: str,
    states: OperationStates,
    *,
    timeout_s: float = 600,
    interval_s: float = 10,
    format_message: Callable[[dict[str, Any], float], str] | None = None,
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

    Returns:
        PollResult with final state and timing info.
    """
    elapsed = 0.0
    current_state = "unknown"
    last_result: dict[str, Any] = {}

    while elapsed < timeout_s:
        result = check_fn()
        if inspect.isawaitable(result):
            result = await result
        last_result = result  # type: ignore[assignment]

        current_state = str(last_result.get(state_key, "unknown"))

        if format_message:
            message = format_message(last_result, elapsed)
        else:
            message = f"{current_state} ({elapsed:.0f}s elapsed)"

        await ctx.report_progress(
            progress=elapsed,
            total=timeout_s,
            message=message,
        )

        if current_state in states.success:
            return PollResult(
                ok=True, final_state=current_state, elapsed_s=elapsed, extra=last_result
            )

        if current_state in states.failure:
            return PollResult(
                ok=False, final_state=current_state, elapsed_s=elapsed, extra=last_result
            )

        await asyncio.sleep(interval_s)
        elapsed += interval_s

    return PollResult(
        ok=False, final_state=current_state, elapsed_s=elapsed, timed_out=True, extra=last_result
    )
