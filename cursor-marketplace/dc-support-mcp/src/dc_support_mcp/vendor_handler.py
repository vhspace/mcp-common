"""
Base vendor handler interface for support portal integrations.
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from .constants import AUTH_COOLDOWN

# Default number of most-recent comments ``get_ticket`` returns. Ticket
# threads are unbounded and dominate the token cost of a fetched ticket, so
# the comment list is capped to the newest ``DEFAULT_MAX_COMMENTS`` unless a
# caller asks for more (or opts out entirely via ``include_comments=False``).
DEFAULT_MAX_COMMENTS = 10


def bound_comments(
    comments: Sequence[Mapping[str, Any]],
    *,
    include_comments: bool = True,
    max_comments: int = DEFAULT_MAX_COMMENTS,
) -> tuple[list[dict[str, Any]], int, bool]:
    """Bound a ticket comment thread for token-efficient responses.

    Comment threads are stored oldest→newest (the order every backend here
    returns them — see the Atlassian ``activityStream`` and the Freshdesk
    ``conversations`` feed), so the *most recent* ``max_comments`` are the
    tail of the list.

    Returns ``(kept, total, truncated)`` where:
      * ``kept`` — the comments to surface (most-recent slice, original order
        preserved), or ``[]`` when ``include_comments`` is False.
      * ``total`` — the number of comments seen in this fetch (so a caller can
        tell it only got a window and re-fetch with a larger ``max_comments``).
      * ``truncated`` — True when any comments were dropped (by the cap or by
        ``include_comments=False``).

    ``max_comments`` < 0 means "no cap"; ``max_comments`` == 0 surfaces no
    bodies but still reports the real ``total``.
    """
    total = len(comments)
    if not include_comments:
        return [], total, total > 0
    if 0 <= max_comments < total:
        return [dict(c) for c in comments[total - max_comments :]], total, True
    return [dict(c) for c in comments], total, False


class VendorHandler(ABC):
    """Abstract base class for vendor support portal handlers."""

    last_error: str | None = None

    # ── List "more-signal" side-channel (issue #93) ───────────────────
    #
    # ``list_tickets`` truncates to the requested ``limit``; these
    # attributes let the CLI and MCP layers tell whether more results
    # exist beyond what was returned, and (when a backend cheaply exposes
    # it) the true total.  They mirror the existing ``last_error``
    # side-channel that both surfaces already read off the handler — see
    # ``_finalize_ticket_list``.  Reset at the start of every
    # ``list_tickets`` call so cached, reused handlers don't leak stale
    # values across MCP tool invocations.
    last_list_has_more: bool = False
    last_list_total: int | None = None

    # ── Session-timing helpers ────────────────────────────────────────
    #
    # These rely on attributes that concrete subclasses set in their
    # ``__init__`` — ``cookie_file`` (Path), ``_last_auth_attempt``
    # (datetime | None), and ``_last_auth_succeeded`` (bool).  They use
    # ``getattr`` so a subclass that doesn't manage cookies / cooldowns
    # still gets a safe default (``None`` / ``0``).

    def cookie_age_seconds(self) -> int | None:
        """Age of the on-disk cookie file in seconds, or None if missing.

        Source of truth is the cookie file's filesystem ``mtime`` rather
        than any in-pickle timestamp.  ``mtime`` is durable across
        process restarts (when in-memory state is gone) and is what the
        ``auth-status`` CLI has historically reported.
        """
        cookie_file = getattr(self, "cookie_file", None)
        if cookie_file is None:
            return None
        try:
            if not cookie_file.exists():
                return None
            mtime = datetime.fromtimestamp(cookie_file.stat().st_mtime)
        except (OSError, AttributeError, ValueError):
            return None
        return int((datetime.now() - mtime).total_seconds())

    def cooldown_remaining_seconds(self) -> int:
        """Seconds left until ``_last_auth_attempt`` clears cooldown.

        Returns ``0`` when no attempt has been recorded, when the most
        recent attempt succeeded (cooldown is only triggered by
        failures — see issue #65), or when the cooldown window has
        already elapsed.
        """
        last = getattr(self, "_last_auth_attempt", None)
        if not isinstance(last, datetime):
            return 0
        if getattr(self, "_last_auth_succeeded", False):
            return 0
        elapsed = (datetime.now() - last).total_seconds()
        return int(max(0, AUTH_COOLDOWN.total_seconds() - elapsed))

    # ── List finalization (fetch-one-extra more-signal) ───────────────

    def _finalize_ticket_list(
        self, fetched: Sequence[Mapping[str, Any]], limit: int
    ) -> list[dict[str, Any]]:
        """Truncate *fetched* to *limit* and record the more-signal.

        Two ways to know whether more results exist beyond what we return:

        * **Real server total** — when a fetcher set ``last_list_total``
          before calling this (e.g. the Atlassian ``allReqFilter`` model
          reports ``totalResults``), ``has_more`` is the authoritative
          ``returned < total``. This stays correct even when a page-walk
          stops early because the backend ignored our page cursor: we'd
          return only the first page but still know ``total`` exceeds it,
          so we never falsely claim a capped result is complete (#93/#94).
        * **Fetch-one-extra** — otherwise, callers over-fetch by one
          (request ``limit + 1`` rows); if more than ``limit`` came back,
          the surplus is dropped here and ``has_more`` is ``True``. This is
          backend-agnostic and needs no server-provided total (used by the
          IREN/Freshdesk path and the Atlassian HTML fallback).
        """
        truncated = [dict(row) for row in fetched[:limit]]
        total = self.last_list_total
        if total is not None:
            self.last_list_has_more = len(truncated) < total
        else:
            self.last_list_has_more = len(fetched) > limit
        return truncated

    def list_more_signal(self) -> dict[str, Any]:
        """Truncation more-signal from the most recent ``list_tickets`` call.

        Single source of truth for the CLI and MCP surfaces (issue #93) so
        the ``has_more`` / ``total`` side-channel is read in exactly one
        place rather than copy-pasted into each. ``getattr`` keeps it safe
        for a handler that has never run a list.

        Returns a dict with:
          * ``has_more``: ``True`` when more tickets exist beyond ``limit``.
          * ``total``: the real backend total when known, else ``None``.
        """
        return {
            "has_more": bool(getattr(self, "last_list_has_more", False)),
            "total": getattr(self, "last_list_total", None),
        }

    # ── Comment-thread bounding (token control) ───────────────────────

    def _apply_comment_bounds(
        self,
        ticket: dict[str, Any],
        *,
        include_comments: bool = True,
        max_comments: int = DEFAULT_MAX_COMMENTS,
    ) -> dict[str, Any]:
        """Cap *ticket*'s ``comments`` in place and attach a truncation signal.

        Mutates and returns *ticket*, adding two side-channel keys that mirror
        the ``has_more`` / ``total`` list signal:
          * ``comments_total`` — comments retrieved in this fetch.
          * ``comments_truncated`` — True when bodies were withheld (capped by
            ``max_comments`` or dropped by ``include_comments=False``).
        """
        kept, total, truncated = bound_comments(
            ticket.get("comments") or [],
            include_comments=include_comments,
            max_comments=max_comments,
        )
        ticket["comments"] = kept
        ticket["comments_total"] = total
        ticket["comments_truncated"] = truncated
        return ticket

    @abstractmethod
    def authenticate(self) -> bool:
        """
        Authenticate with the vendor portal.

        Returns:
            True if authentication successful, False otherwise
        """
        pass

    @abstractmethod
    def get_ticket(
        self,
        ticket_id: str,
        *,
        include_comments: bool = True,
        max_comments: int = DEFAULT_MAX_COMMENTS,
    ) -> dict[str, Any] | None:
        """
        Fetch a ticket by ID.

        Args:
            ticket_id: The ticket identifier
            include_comments: When False, omit comment bodies (still reports
                the count via ``comments_truncated`` where the backend bundles
                them with the ticket).
            max_comments: Cap the returned thread to this many most-recent
                comments (``< 0`` = no cap).

        Returns:
            Dictionary containing ticket information or None if not found
        """
        pass

    @abstractmethod
    def list_tickets(self, status: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        """
        List tickets with optional filtering.

        Args:
            status: Filter by status (e.g., "open", "closed")
            limit: Maximum number of tickets to return

        Returns:
            List of ticket dictionaries
        """
        pass

    def add_comment(
        self, ticket_id: str, comment: str, public: bool = True
    ) -> dict[str, Any] | None:
        """Add a comment to a ticket. Optional — not all vendors support this."""
        raise NotImplementedError(f"{type(self).__name__} does not support comments")

    def create_ticket(
        self,
        summary: str,
        description: str,
        cause: str = "",
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        """
        Create a new support ticket. Optional — not all vendors
        support this.

        Returns dict with created ticket info, or None on failure.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support ticket creation")

    def close(self) -> None:
        """Release any held resources (browser contexts, connections). Safe to call multiple times."""
        return  # Default no-op; override in handlers that hold resources

    def normalize_ticket(self, raw_ticket: dict[str, Any]) -> dict[str, Any]:
        """
        Normalize ticket data to common format.

        Args:
            raw_ticket: Vendor-specific ticket data

        Returns:
            Normalized ticket dictionary
        """
        return {
            "id": raw_ticket.get("id"),
            "summary": raw_ticket.get("summary"),
            "status": raw_ticket.get("status"),
            "priority": raw_ticket.get("priority"),
            "reporter": raw_ticket.get("reporter"),
            "assignee": raw_ticket.get("assignee"),
            "created": raw_ticket.get("created"),
            "updated": raw_ticket.get("updated"),
            "url": raw_ticket.get("url"),
            "comments": raw_ticket.get("comments", []),
        }
