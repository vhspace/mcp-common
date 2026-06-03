"""Type definitions for the dc-support-mcp server."""

from typing import NotRequired, TypedDict


class CommentData(TypedDict):
    """Structure for ticket comments."""

    author: str
    date: str
    comment: str
    type: str


class TicketData(TypedDict):
    """Structure for ticket information."""

    id: str
    summary: str
    status: str
    reporter: str
    assignee: str
    created: str
    url: str
    comments: list[CommentData]
    description: NotRequired[str]


class SimplifiedTicketData(TypedDict):
    """Simplified ticket structure for list views."""

    id: str
    summary: str
    status: str
    created: str
    assignee: str
    url: str


class CookieData(TypedDict):
    """Structure for cookie storage."""

    name: str
    value: str
    domain: str | None
    path: str | None
