"""Shared HTTP authentication middleware for MCP servers."""

from __future__ import annotations

import hmac
import logging
from typing import Any

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_request
from fastmcp.server.middleware import Middleware, MiddlewareContext

logger = logging.getLogger(__name__)


class HttpAccessTokenAuth(Middleware):
    """Bearer token / X-API-Key authentication middleware for MCP HTTP transport.

    Validates ``Authorization: Bearer <token>`` or ``X-API-Key: <token>`` headers.
    The ``initialize`` method is allowed through without auth so the MCP handshake
    can complete before credentials are checked.
    """

    def __init__(self, token: str) -> None:
        self._token = token

    async def on_request(self, context: MiddlewareContext, call_next: Any) -> Any:
        if context.method == "initialize":
            return await call_next(context)

        try:
            request = get_http_request()
        except RuntimeError:
            return await call_next(context)

        auth_header = request.headers.get("authorization", "")
        api_key = request.headers.get("x-api-key", "")

        ok = False
        if api_key and hmac.compare_digest(api_key, self._token):
            ok = True
        elif auth_header.lower().startswith("bearer "):
            candidate = auth_header.split(" ", 1)[1].strip()
            if hmac.compare_digest(candidate, self._token):
                ok = True

        if not ok:
            raise ToolError(
                "Unauthorized: missing/invalid access token. "
                "Send 'Authorization: Bearer <token>' or 'X-API-Key: <token>'."
            )

        return await call_next(context)
