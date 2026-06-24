from __future__ import annotations

from typing import Any

from fastmcp.exceptions import ToolError
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult
from mcp_common.dual_mode import (
    READONLY_REFUSAL_MESSAGE,
    EnforceMode,
    classify_mutation,
    current_enforce_mode,
    is_blocked,
)
from mcp_common.dual_mode._registry import get_tools

from .agent_controller import AgentController


class InstrumentedFastMCP(FastMCP):
    """FastMCP with centralized tool-call interception.

    We keep interception here so:
    - we don't have to add parameters to every tool
    - we can read request `_meta` and attach response `_meta` uniformly
    - we can implement stats + hinting in one place
    - we apply the shared read-only backstop (``MCP_ENFORCE_READONLY``) here,
      since the classic ``mcp.server.fastmcp`` stack has no fastmcp 3.x
      middleware chain to dispatch ``ReadOnlyEnforcementMiddleware`` through
      (see ``_enforce_read_only`` / ``add_middleware``).
    """

    def __init__(
        self,
        *args: Any,
        agent_controller: AgentController | None = None,
        **kwargs: Any,
    ):
        super().__init__(*args, **kwargs)
        self._agent_controller = agent_controller or AgentController()
        self._dual_mode_middleware: list[Any] = []

    @property
    def agent_controller(self) -> AgentController:
        return self._agent_controller

    @property
    def middleware(self) -> list[Any]:
        """Registered dual-mode middleware (compatibility shim — see ``add_middleware``)."""
        return self._dual_mode_middleware

    def add_middleware(self, middleware: Any) -> None:
        """Compatibility shim for the mcp-common dual-mode read-only enforcement.

        The mcp-common dual-mode framework targets ``fastmcp`` 3.x, whose
        ``FastMCP`` exposes an ``add_middleware`` + ``middleware`` chain;
        ``@dual_mode_tool`` calls ``ensure_enforcement_installed(mcp)`` which
        relies on it. redfish-mcp is built on the classic
        ``mcp.server.fastmcp.FastMCP`` (this subclass centralizes tool-call
        interception in ``call_tool`` instead of a middleware chain), which has
        no such API. We record the registered middleware so
        ``ensure_enforcement_installed`` stays idempotent.

        The stored ``ReadOnlyEnforcementMiddleware`` is **not** dispatched on
        this transport (its ``on_call_tool`` never runs). Instead, ``call_tool``
        invokes :meth:`_enforce_read_only` directly, which mirrors the
        middleware's classify → ``is_blocked`` → refuse logic so behavior matches
        every fastmcp 3.x server. The per-tool ``read_only`` classification this
        relies on is recorded in the dual-mode registry by ``@dual_mode_tool``.
        """
        self._dual_mode_middleware.append(middleware)

    def _enforce_read_only(self, name: str) -> None:
        """Refuse a mutating tool call under ``MCP_ENFORCE_READONLY`` (no-op when unset).

        redfish-mcp's stand-in for mcp-common's
        ``ReadOnlyEnforcementMiddleware.on_call_tool``: because this subclass runs
        on the classic ``mcp.server.fastmcp`` stack (no fastmcp 3.x middleware
        dispatch — see :meth:`add_middleware`), the shared backstop is applied
        here, at the single ``call_tool`` interception point, before the tool body
        runs.

        It resolves the tool's mutation classification from the dual-mode registry
        (the ``read_only`` flag ``@dual_mode_tool`` recorded for the tool) using
        the framework's own :func:`classify_mutation` + :func:`is_blocked`, and
        on a block raises ``ToolError(READONLY_REFUSAL_MESSAGE)`` — exactly what
        the middleware raises. The classic low-level server surfaces a raised
        exception's message verbatim to the client as ``CallToolResult(isError=
        True, text=str(e))``, so the caller sees precisely the refusal string.

        Note: the classic FastMCP ``Tool`` has no ``tags`` attribute (the
        ``{"write"}`` tag convention is a fastmcp 3.x concept), so tags are not
        consulted here. redfish-mcp classifies every tool with an explicit
        ``read_only`` (derived from each tool's ``readOnlyHint``; see
        ``mcp_server._tool``), and :func:`classify_mutation` gives that explicit
        flag highest precedence, so the classification is complete without tags.
        A transparent no-op when the toggle is unset (``EnforceMode.OFF``), so
        default behavior is byte-identical to before.
        """
        mode = current_enforce_mode()
        if mode is EnforceMode.OFF:
            return
        # ``get_tools`` is typed against fastmcp 3.x ``FastMCP``; this subclass is
        # the classic ``mcp.server.fastmcp.FastMCP`` the dual-mode registry is keyed
        # by at runtime (the same duck-typed bridge ``@dual_mode_tool`` /
        # ``register`` already use when registering these tools).
        read_only = next(
            (meta.read_only for meta in get_tools(self) if meta.tool_name == name),  # type: ignore[arg-type]
            None,
        )
        if is_blocked(mode, classify_mutation(read_only, None)):
            raise ToolError(READONLY_REFUSAL_MESSAGE)

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> CallToolResult | Any:
        # Shared read-only backstop (MCP_ENFORCE_READONLY). Applied before context
        # acquisition and preflight elicitation so a blocked write never prompts
        # for credentials or touches the BMC. No-op when the toggle is unset.
        self._enforce_read_only(name)

        ctx = self.get_context()

        async def _exec(tool_name: str, tool_args: dict[str, Any], context) -> Any:
            # Match base FastMCP behavior: ToolManager does validation + conversion.
            return await self._tool_manager.call_tool(  # type: ignore[attr-defined]
                tool_name,
                tool_args,
                context=context,
                convert_result=True,
            )

        return await self._agent_controller.on_tool_call(
            tool_name=name,
            arguments=arguments,
            context=ctx,
            tool_executor=_exec,
        )
