"""Sync polling helper for CLI commands waiting on a terminal state.

Sync companion to :func:`mcpanvil.progress.poll_with_progress`. Use
:func:`poll_until` from CLI subcommands (AWX jobs, MAAS commissioning,
UFM probes, etc.) where an async MCP ``Context`` is not available.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

__all__ = ["PollTimeout", "poll_until"]

T = TypeVar("T")


class PollTimeout(Exception):  # noqa: N818  # intentional name (mirrors asyncio.TimeoutError style)
    """Raised by :func:`poll_until` when polling exceeds ``timeout_s``.

    Attributes:
        elapsed_s: Wall-clock seconds spent polling before the timeout.
        last_value: Most recent value returned by ``fetch`` (or ``None``
            if ``fetch`` never completed once before the timeout).
    """

    def __init__(self, *, elapsed_s: float, last_value: object) -> None:
        super().__init__(f"poll_until timed out after {elapsed_s:.1f}s (last_value={last_value!r})")
        self.elapsed_s = elapsed_s
        self.last_value = last_value


def poll_until(
    fetch: Callable[[], T],
    is_terminal: Callable[[T], bool],
    *,
    timeout_s: float = 600.0,
    interval_s: float = 2.0,
    on_tick: Callable[[float, T], None] | None = None,
) -> T:
    """Poll ``fetch()`` until ``is_terminal(value)`` returns ``True``.

    Sync companion to :func:`mcpanvil.progress.poll_with_progress` for
    CLI commands (AWX jobs, MAAS commissioning, UFM probes, etc.) where
    an async MCP ``Context`` is unavailable.

    Each iteration: call ``fetch()`` once, check ``is_terminal`` on the
    result; if not terminal, call ``on_tick(elapsed_s, value)`` (when
    given), check the timeout, then sleep ``interval_s`` before the
    next iteration. Uses :func:`time.monotonic` so elapsed tracking is
    clock-skew-safe.

    Args:
        fetch: Callable invoked each iteration to get the current value.
            Should return quickly; this helper does NOT enforce a per-call
            timeout.
        is_terminal: Predicate called with each fresh ``fetch`` value.
            Return ``True`` to stop polling and return that value.
        timeout_s: Maximum wall-clock seconds before raising
            :class:`PollTimeout`. Defaults to 10 minutes.
        interval_s: Seconds to sleep between non-terminal iterations.
            Defaults to 2 seconds.
        on_tick: Optional callback invoked after each non-terminal fetch
            with ``(elapsed_s, value)`` — handy for progress output.

    Returns:
        The first ``fetch`` value for which ``is_terminal`` returned True.

    Raises:
        PollTimeout: If ``timeout_s`` elapses before ``is_terminal``
            returns ``True``. The exception carries ``elapsed_s`` and
            ``last_value`` attributes for context.
    """
    start = time.monotonic()
    while True:
        value = fetch()
        elapsed = time.monotonic() - start
        if is_terminal(value):
            return value
        if on_tick is not None:
            on_tick(elapsed, value)
        if elapsed >= timeout_s:
            raise PollTimeout(elapsed_s=elapsed, last_value=value)
        time.sleep(interval_s)
