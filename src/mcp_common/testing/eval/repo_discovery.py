"""Discover vhspace MCP repos from mcp-plugin.toml files in the workspace.

Uses the existing marketplace_builder.discover_plugins() infrastructure
to walk the workspace and find all MCP plugin repos.  Each mcp-plugin.toml
contains a ``repository`` field with the GitHub URL.

This replaces hardcoded repo paths throughout the eval pipeline.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RepoInfo:
    """Resolved metadata for an MCP plugin repository."""

    name: str  # e.g. "netbox-mcp"
    github_url: str  # e.g. "https://github.com/vhspace/netbox-mcp"
    github_repo: str  # e.g. "vhspace/netbox-mcp"
    local_path: Path  # e.g. /workspaces/together/netbox-mcp


def _extract_github_repo(url: str) -> str:
    """Extract ``owner/repo`` from a GitHub URL."""
    match = re.search(r"github\.com/([^/]+/[^/]+?)(?:\.git)?$", url)
    return match.group(1) if match else url


def discover_repos(workspace: Path) -> dict[str, RepoInfo]:
    """Discover all MCP plugin repos under *workspace*.

    Returns a dict mapping plugin name to :class:`RepoInfo`.
    Hidden directories (``.worktrees``, ``.mergefix``, etc.) are filtered out.
    """
    from mcp_common.marketplace_builder import discover_plugins

    try:
        plugins = discover_plugins(workspace)
    except FileNotFoundError:
        _log.warning("Workspace path does not exist: %s", workspace)
        return {}

    repos: dict[str, RepoInfo] = {}
    for repo_path, cfg in plugins:
        if any(part.startswith(".") for part in repo_path.relative_to(workspace).parts):
            continue
        repos[cfg.name] = RepoInfo(
            name=cfg.name,
            github_url=cfg.repository,
            github_repo=_extract_github_repo(cfg.repository),
            local_path=repo_path,
        )
    return repos


def resolve_server_to_repo(
    server_name: str,
    workspace: Path,
    *,
    _cache: dict[Path, dict[str, RepoInfo]] | None = None,
) -> RepoInfo | None:
    """Resolve an eval failure's server name to its :class:`RepoInfo`.

    The *server_name* from :class:`EvalFailure` might be ``"netbox-mcp"``,
    ``"netbox_mcp"``, or just ``"netbox"`` — we normalise and match.

    An optional *_cache* dict avoids re-walking the filesystem when this
    function is called in a loop for multiple failures.
    """
    if _cache is not None and workspace in _cache:
        repos = _cache[workspace]
    else:
        repos = discover_repos(workspace)
        if _cache is not None:
            _cache[workspace] = repos

    if server_name in repos:
        return repos[server_name]

    normalized = server_name.replace("_", "-").lower()
    if normalized in repos:
        return repos[normalized]

    for name, info in repos.items():
        if name.startswith(normalized) or normalized.startswith(name.split("-")[0]):
            return info

    _log.warning("Could not resolve server '%s' to a repo", server_name)
    return None
