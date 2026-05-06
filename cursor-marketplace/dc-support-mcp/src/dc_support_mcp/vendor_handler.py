"""
Base vendor handler interface for support portal integrations.
"""

from abc import ABC, abstractmethod
from typing import Any


class VendorHandler(ABC):
    """Abstract base class for vendor support portal handlers."""

    last_error: str | None = None

    @abstractmethod
    def authenticate(self) -> bool:
        """
        Authenticate with the vendor portal.

        Returns:
            True if authentication successful, False otherwise
        """
        pass

    @abstractmethod
    def get_ticket(self, ticket_id: str) -> dict[str, Any] | None:
        """
        Fetch a ticket by ID.

        Args:
            ticket_id: The ticket identifier

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
