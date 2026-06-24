from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from .agent_controller import AgentController


class InstrumentedFastMCP(FastMCP):
    """FastMCP with centralized tool-call interception.

    We keep interception here so:
    - we don't have to add parameters to every tool
    - we can read request `_meta` and attach response `_meta` uniformly
    - we can implement stats + hinting in one place
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
        no such API. We record registered middleware so
        ``ensure_enforcement_installed`` stays idempotent. The optional
        ``MCP_ENFORCE_READONLY`` middleware is stored but not auto-invoked on
        this transport; per-tool ``read_only`` classification is still recorded
        in the dual-mode registry, and tools keep their own ``allow_write``
        gates.
        """
        self._dual_mode_middleware.append(middleware)

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> CallToolResult | Any:
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
