"""Decorators for the dc-support-mcp server."""

import re
import sys
import time
from collections.abc import Callable
from functools import wraps
from typing import Any, ParamSpec, TypeVar

from .constants import TICKET_ID_PATTERN

P = ParamSpec("P")
T = TypeVar("T")


def verbose_log(
    enter_message: str, exit_message: str | None = None
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """
    Decorator to log method entry/exit when verbose=True.

    Args:
        enter_message: Message to log when entering the method
        exit_message: Optional message to log when exiting (defaults to enter_message with "completed")
    """

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @wraps(func)
        def wrapper(self: Any, *args: P.args, **kwargs: P.kwargs) -> T:
            if hasattr(self, "verbose") and self.verbose:
                sys.stderr.write(f"→ {enter_message}\n")

            start = time.monotonic()
            result = func(self, *args, **kwargs)
            elapsed = time.monotonic() - start

            if hasattr(self, "verbose") and self.verbose:
                exit_msg = exit_message or f"{enter_message} completed"
                sys.stderr.write(f"✓ {exit_msg} ({elapsed:.1f}s)\n")

            return result

        return wrapper  # type: ignore[return-value]

    return decorator


def validate_ticket_id(func: Callable[P, T]) -> Callable[P, T]:
    """
    Decorator to validate ticket ID format before processing.

    Expects the first argument after self to be ticket_id.
    """

    @wraps(func)
    def wrapper(self: Any, ticket_id: str, *args: P.args, **kwargs: P.kwargs) -> T:
        if not re.match(TICKET_ID_PATTERN, ticket_id):
            raise ValueError(f"Invalid ticket ID format: {ticket_id}. Expected format: SUPP-####")
        return func(self, ticket_id, *args, **kwargs)  # type: ignore[arg-type]

    return wrapper  # type: ignore[return-value]
