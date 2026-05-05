"""Standardized .env file loading for MCP servers and companion CLIs.

Every MCP repo has two entry points — the MCP server (usually via
``MCPSettings`` / pydantic-settings) and a companion CLI (raw ``os.environ``).
This module provides a single ``load_env`` function that both can call at
startup so they resolve the same credentials from the same ``.env`` files.

Precedence (with default ``override=False``):
  1. Existing shell/container env vars always win (safest for production).
  2. ``../.env`` one level up (workspace root) — loaded first, lowest priority.
  3. ``.env`` in *search_from* directory (repo-local) — loaded second.

When ``override=True``, ``.env`` values *overwrite* existing env vars.  Use
this only when the ``.env`` file must be the authoritative source of truth
(e.g. local dev matching MCP server behavior).

Usage::

    # In CLI main():
    from mcp_common.env import load_env
    load_env()

    # File-relative search (useful when cwd != repo root):
    load_env(search_from=Path(__file__).parent)

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


def _find_env_files(
    search_paths: list[Path] | None = None,
    search_from: Path | None = None,
    env_file: str = ".env",
) -> list[Path]:
    """Return ``.env`` file paths that exist, in load order (first = lowest priority).

    Default search when *search_paths* is ``None``:
      1. ``search_from / .. / env_file``  (one level up — workspace root convention)
      2. ``search_from / env_file``        (repo-local, higher priority)

    *search_from* defaults to ``Path.cwd()`` when not provided.
    """
    if search_paths is not None:
        return [p for p in search_paths if p.is_file()]

    base = search_from if search_from is not None else Path.cwd()
    candidates = [
        (base / "..").resolve() / env_file,
        base / env_file,
    ]
    return [p for p in candidates if p.is_file()]


def load_env(
    *,
    override: bool = False,
    search_paths: list[Path | str] | None = None,
    search_from: Path | str | None = None,
    env_file: str = ".env",
    _force: bool = False,
) -> list[Path]:
    """Load ``.env`` files with standard MCP precedence.

    Call this once at startup — before constructing ``MCPSettings`` or reading
    ``os.environ`` for credentials.  Safe to call multiple times; subsequent
    calls are no-ops unless *_force* is ``True``.

    Args:
        override: If True, .env values overwrite existing env vars.
            If False (default), existing env vars take precedence over .env
            values.  The default is False because production deployments
            (K8s, Docker) intentionally set env vars that should not be
            clobbered by a checked-in .env file.
        search_paths: Explicit list of ``.env`` file paths to load (in order,
            later files win).  When ``None`` the default search is used.
        search_from: Base directory for the default .env search.  Defaults to
            ``Path.cwd()``.  Downstream repos that want file-relative lookup
            can pass ``Path(__file__).parent`` here.
        env_file: Filename to search for (default ``.env``).
        _force: Re-run loading even if ``load_env`` was already called.
            Intended for testing only.

    Returns:
        List of ``.env`` file paths that were actually loaded.
    """
    global _loaded
    if _loaded and not _force:
        return []

    resolved_search_paths: list[Path] | None = None
    if search_paths is not None:
        resolved_search_paths = [Path(p) for p in search_paths]

    resolved_from: Path | None = None
    if search_from is not None:
        resolved_from = Path(search_from)

    env_files = _find_env_files(resolved_search_paths, resolved_from, env_file)

    loaded: list[Path] = []
    for path in env_files:
        load_dotenv(path, override=override)
        loaded.append(path)
        logger.debug("Loaded .env file: %s (override=%s)", path, override)

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


def env_search_paths(search_from: Path | str | None = None) -> list[Path]:
    """Return the default .env search paths without loading anything.

    Useful for diagnostics (e.g. ``mcp-plugin-gen doctor``).
    """
    resolved = Path(search_from) if search_from is not None else None
    return _find_env_files(search_from=resolved)
