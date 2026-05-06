"""Request router for the KVM daemon."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from redfish_mcp.kvm.exceptions import KVMError
from redfish_mcp.kvm.protocol import ErrorPayload, Request, Response

logger = logging.getLogger("redfish_mcp.kvm.router")

Handler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class Router:
    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}

    def register(self, method: str, handler: Handler) -> None:
        self._handlers[method] = handler

    async def dispatch(self, req: Request) -> Response:
        handler = self._handlers.get(req.method)
        if handler is None:
            return Response(
                id=req.id,
                error=ErrorPayload(
                    code="method_not_found", message=f"unknown method {req.method!r}"
                ),
            )
        try:
            result = await handler(req.params)
            return Response(id=req.id, result=result)
        except KVMError as exc:
            code = exc.reason or "kvm_error"
            return Response(
                id=req.id,
                error=ErrorPayload(code=code, message=str(exc), stage=exc.stage),
            )
        except Exception as exc:
            logger.exception("handler crash for method %s", req.method)
            return Response(
                id=req.id,
                error=ErrorPayload(code="internal", message=f"{type(exc).__name__}: {exc}"),
            )
