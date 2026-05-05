"""Standardized .env file loading for MCP servers and companion CLIs.

Every MCP repo has two entry points — the MCP server (usually via
``MCPSettings`` / pydantic-settings) and a companion CLI (raw ``os.environ``).
This module provides a single ``load_env`` function that both can call at
startup so they resolve the same credentials from the same ``.env`` files.

Precedence (later wins when *override* is True):
  1. ``.env`` next to the calling package  (repo-local)
  2. ``../.env`` one level up               (workspace root)
  3. Shell environment                      (always wins unless override=True)

Usage::

    # In CLI main():
    from mcp_common.env import load_env
    load_env()

    # In MCP server startup:
    from mcp_common.env import load_env
    load_env()
    settings = MySettings()   # pydantic-settings picks up the env vars we just loaded
"""

from __future__ import annotations

import logging
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

_loaded = False


def _find_env_files(search_paths: list[Path] | None = None) -> list[Path]:
    """Return ``.env`` file paths that exist, in load order (first = lowest priority).

    Default search when *search_paths* is ``None``:
      1. ``cwd / .env``
      2. ``cwd / .. / .env``  (one level up — workspace root convention)
    """
    if search_paths is not None:
        return [p for p in search_paths if p.is_file()]

    cwd = Path.cwd()
    candidates = [
        cwd / ".env",
        (cwd / "..").resolve() / ".env",
    ]
    return [p for p in candidates if p.is_file()]


def load_env(
    *,
    override: bool = True,
    search_paths: list[Path] | None = None,
    _force: bool = False,
) -> list[Path]:
    """Load ``.env`` files with standard MCP precedence.

    Call this once at startup — before constructing ``MCPSettings`` or reading
    ``os.environ`` for credentials.  Safe to call multiple times; subsequent
    calls are no-ops unless *_force* is ``True``.

    Args:
        override: When ``True`` (default), values from ``.env`` files overwrite
            existing environment variables.  This matches the dc-support-mcp
            convention where the ``.env`` file is the source of truth.
        search_paths: Explicit list of ``.env`` file paths to load (in order,
            later files win).  When ``None`` the default search is used:
            ``cwd/.env`` then ``cwd/../.env``.
        _force: Re-run loading even if ``load_env`` was already called.
            Intended for testing only.

    Returns:
        List of ``.env`` file paths that were actually loaded.
    """
    global _loaded
    if _loaded and not _force:
        return []

    env_files = _find_env_files(search_paths)

    loaded: list[Path] = []
    for path in env_files:
        load_dotenv(path, override=override)
        loaded.append(path)
        logger.debug("Loaded .env file: %s", path)

    _loaded = True

    if not loaded:
        logger.debug("No .env files found")

    return loaded


def reset_env_state() -> None:
    """Reset the module-level guard so ``load_env`` can fire again.

    Intended for testing only.
    """
    global _loaded
    _loaded = False


def env_search_paths() -> list[Path]:
    """Return the default .env search paths without loading anything.

    Useful for diagnostics (e.g. ``mcp-plugin-gen doctor``).
    """
    return _find_env_files()
