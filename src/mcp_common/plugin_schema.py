"""
Universal MCP plugin schema.

Defines the platform-independent config that gets rendered into
Cursor, Claude Code, OpenCode, OpenHands, and other client-specific formats.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Author(BaseModel):
    name: str
    email: str | None = None


class MCPServer(BaseModel):
    """MCP server definition."""

    command: str = "uvx"
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


class CLITool(BaseModel):
    """Companion CLI tool that ships alongside the MCP server."""

    name: str
    entry_point: str
    description: str = ""


class Skill(BaseModel):
    """Agent skill definition."""

    name: str
    description: str
    path: str  # relative path to SKILL.md from repo root


class Rule(BaseModel):
    """Always-apply rule for agents."""

    name: str
    path: str  # relative path to RULE.md/.mdc from repo root


class Hook(BaseModel):
    """Lifecycle hook."""

    event: str = "SessionStart"
    script: str  # relative path to hook script from repo root
    async_: bool = Field(True, alias="async")

    model_config = {"populate_by_name": True}


class PluginConfig(BaseModel):
    """
    Universal MCP plugin config.

    One file to rule them all. Write this once as mcp-plugin.toml,
    then run `mcp-plugin-gen` to produce platform-specific configs.
    """

    name: str
    description: str
    version: str
    author: Author
    repository: str
    license: str = "Apache-2.0"
    keywords: list[str] = Field(default_factory=list)

    server: MCPServer
    cli: CLITool | None = None

    skills: list[Skill] = Field(default_factory=list)
    rules: list[Rule] = Field(default_factory=list)
    hooks: list[Hook] = Field(default_factory=list)

    env_file_discovery: list[str] = Field(
        default_factory=lambda: [
            "${WORKSPACE_ROOT}/.env",
            "/workspaces/together/.env",
            "~/.env",
        ],
        description="Paths to search for .env file (first match wins)",
    )
