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

    @property
    def agent_controller(self) -> AgentController:
        return self._agent_controller

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
