"""MCP server for agent memory backed by Graphiti temporal knowledge graph."""

import argparse
import logging
from contextlib import asynccontextmanager
from datetime import datetime

from mcp.server.fastmcp import FastMCP  # type: ignore[import-untyped]
from mcp_common.agent_remediation import mcp_remediation_wrapper
from mcp_common.logging import suppress_ssl_warnings

from .backend import MemoryBackend
from .config import Settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = Settings()
backend = MemoryBackend(settings)


@asynccontextmanager
async def lifespan(server):
    await backend.initialize()
    logger.info("Memory MCP server started")
    yield
    await backend.close()
    logger.info("Memory MCP server stopped")


def _build_mcp(host: str = "0.0.0.0", port: int = 8000) -> FastMCP:
    """Build and return a configured FastMCP instance with all tools registered."""
    server = FastMCP(
        "agent-memory",
        instructions=(
            "Long-term memory for AI agents with temporal knowledge graph, "
            "decay, and rule promotion"
        ),
        host=host,
        port=port,
        lifespan=lifespan,
    )

    @server.tool()
    @mcp_remediation_wrapper(project_repo="vhspace/agent-memory")
    async def memory_search(
        query: str,
        group_id: str | None = None,
        max_results: int = 10,
        center_node_uuid: str | None = None,
    ) -> list[dict]:
        """Search for facts and relationships in agent memory.

        Always search before starting tasks to leverage prior knowledge.
        """
        return await backend.search_facts(
            query=query,
            group_ids=[group_id] if group_id else None,
            max_facts=max_results,
            center_node_uuid=center_node_uuid,
        )

    @server.tool()
    @mcp_remediation_wrapper(project_repo="vhspace/agent-memory")
    async def memory_search_nodes(
        query: str,
        group_id: str | None = None,
        max_results: int = 10,
    ) -> list[dict]:
        """Search for entity nodes in agent memory."""
        return await backend.search_nodes(
            query=query,
            group_ids=[group_id] if group_id else None,
            max_nodes=max_results,
        )

    @server.tool()
    @mcp_remediation_wrapper(project_repo="vhspace/agent-memory")
    async def memory_add(
        name: str,
        body: str,
        source: str = "text",
        source_description: str = "",
        group_id: str | None = None,
        reference_time: str | None = None,
    ) -> dict:
        """Add a new memory (episode) to the knowledge graph.

        Use after resolving incidents, learning something new, or capturing
        operational knowledge worth retaining.
        """
        ref_time = datetime.fromisoformat(reference_time) if reference_time else None
        return await backend.add_episode(
            name=name,
            body=body,
            source=source,
            source_description=source_description,
            group_id=group_id,
            reference_time=ref_time,
        )

    @server.tool()
    @mcp_remediation_wrapper(project_repo="vhspace/agent-memory")
    async def memory_episodes(
        group_id: str | None = None,
        last_n: int = 10,
    ) -> list[dict]:
        """List recent memory episodes."""
        return await backend.get_episodes(group_id=group_id, last_n=last_n)

    @server.tool()
    @mcp_remediation_wrapper(project_repo="vhspace/agent-memory")
    async def memory_list_groups() -> list[dict]:
        """List all memory groups (namespaces) with episode counts and last activity.

        Use to discover available group namespaces before filtering searches or
        episodes by group.
        """
        return await backend.get_groups()

    @server.tool()
    @mcp_remediation_wrapper(project_repo="vhspace/agent-memory")
    async def memory_status() -> dict:
        """Check memory system health and connectivity."""
        return await backend.get_status()

    return server


mcp = _build_mcp()


# ------------------------------------------------------------------
# Entrypoint
# ------------------------------------------------------------------


def main():
    suppress_ssl_warnings()
    parser = argparse.ArgumentParser(description="Agent Memory MCP Server")
    parser.add_argument("--transport", choices=["sse", "stdio", "streamable-http"], default="sse")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    server = _build_mcp(host=args.host, port=args.port)
    server.run(transport=args.transport)


if __name__ == "__main__":
    main()
