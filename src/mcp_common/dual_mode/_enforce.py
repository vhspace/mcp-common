"""Enforced read-only ("eval") mode for the dual-mode dispatch layer.

This is the server-side hard guarantee behind ``vhspace/mcp-common#148``: when
enabled, read-only tools/commands run normally but any **create / update /
destroy** (mutating) tool/command does **not** execute — the caller receives a
terse, non-tainting refusal (exactly :data:`READONLY_REFUSAL_MESSAGE`, nothing
else) so an eval's tool-selection results are never biased by a verbose
"writes are disabled because this is an eval" hint.

Because every dual-mode MCP funnels its tool calls through the same FastMCP
server and its CLI through :func:`mcp_common.dual_mode.build_cli_from_mcp`, the
guard lives here and is inherited for free:

* **MCP side** — :class:`ReadOnlyEnforcementMiddleware` is a FastMCP
  middleware auto-installed on the server the first time any
  ``@dual_mode_tool`` is registered against it
  (:func:`ensure_enforcement_installed`). It intercepts **every** ``tools/call``
  on that server — including plain ``@mcp.tool`` tools that never went through
  ``@dual_mode_tool`` (e.g. netbox-mcp's ``{"write"}``-tagged
  ``netbox_update_device``) — so a ``{"write"}``-tagged write is auto-blocked
  with no per-server change. A blocked call raises
  ``fastmcp.exceptions.ToolError(READONLY_REFUSAL_MESSAGE)``; FastMCP surfaces a
  ``ToolError`` message verbatim to the client (the calling agent sees exactly
  the refusal string, not a masked "internal error").
* **CLI side** — :func:`mcp_common.dual_mode.builder` consults
  :func:`current_enforce_mode` + :func:`classify_mutation` + :func:`is_blocked`
  inside each synthesized command and, when blocked, prints the same one-liner
  to stderr and exits non-zero **without** executing the tool function.

Toggle (``MCP_ENFORCE_READONLY``)
---------------------------------
Read from the environment **at dispatch time** (so tests can toggle it and the
repo's ``.env`` loading — :func:`mcp_common.env.load_env` — is honored), and
**disabled by default** so an unset variable is byte-identical to today:

* unset / ``""`` / ``0`` / ``false`` / ``no`` / ``off`` / ``none`` /
  ``disabled`` → :attr:`EnforceMode.OFF` — everything runs.
* ``strict`` → :attr:`EnforceMode.STRICT` — block anything **not** explicitly
  ``read_only=True`` (mutating *and* unclassified tools are refused).
* any other value (``1`` / ``true`` / ``yes`` / ``on`` / ``enabled`` …) →
  :attr:`EnforceMode.ENABLED` — the common case: block only tools classified
  as **mutating** (``{"write"}`` tag or ``read_only=False``); unclassified and
  read-only tools run.

Mutation classification
------------------------
A tool's :class:`MutationClass` is derived (identically for MCP and CLI) from
its explicit ``read_only`` flag and its tags:

* ``read_only=True`` → :attr:`MutationClass.READ_ONLY` (never blocked).
* ``read_only=False`` → :attr:`MutationClass.MUTATING`.
* otherwise ``"write" in tags`` → :attr:`MutationClass.MUTATING`
  (the existing ``tags={"write"}`` convention).
* otherwise → :attr:`MutationClass.UNCLASSIFIED` (allowed in ``ENABLED`` mode,
  blocked in ``STRICT`` mode).

Relationship to ``read_only_tools`` (#131)
-------------------------------------------
``read_only_tools`` (mcp-common#131) **trims** the exposed tool surface so the
model under test never *sees* write tools — a harness-side measure that relies
on the runner remembering to trim. Enforce mode is the **server-side backstop**:
even if a write tool is exposed or invoked directly (e.g. a ``bash`` tool runs
``netbox-cli`` in a ``cli`` / ``combined`` eval where an Inspect allow-list
cannot intercept it), the mutation still does not execute. Use both: trim to
shrink the surface, enforce mode to guarantee no write runs.
"""

from __future__ import annotations

import os
from enum import Enum
from typing import TYPE_CHECKING, Any

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware

from mcp_common.dual_mode._registry import get_tools

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

    from fastmcp import FastMCP
    from fastmcp.server.middleware import MiddlewareContext
    from fastmcp.tools.tool import ToolResult

__all__ = [
    "ENFORCE_READONLY_ENV_VAR",
    "READONLY_REFUSAL_MESSAGE",
    "EnforceMode",
    "MutationClass",
    "ReadOnlyEnforcementMiddleware",
    "classify_mutation",
    "current_enforce_mode",
    "ensure_enforcement_installed",
    "is_blocked",
]

ENFORCE_READONLY_ENV_VAR = "MCP_ENFORCE_READONLY"
"""Environment variable that toggles enforced read-only mode (read at dispatch time)."""

READONLY_REFUSAL_MESSAGE = "This operation is not enabled."
"""The exact, terse, non-tainting refusal returned/printed for a blocked call.

Deliberately generic: no mention of "eval", "read-only", or any reason, so a
model under test cannot infer it is being evaluated or that writes are gated.
"""

WRITE_TAG = "write"
"""Tag that marks a tool as mutating under the existing ``tags={"write"}`` convention."""

_OFF_VALUES = frozenset({"", "0", "false", "no", "off", "none", "disabled"})
_STRICT_VALUES = frozenset({"strict"})


class EnforceMode(Enum):
    """Resolved state of the ``MCP_ENFORCE_READONLY`` toggle."""

    OFF = "off"
    ENABLED = "enabled"
    STRICT = "strict"


class MutationClass(Enum):
    """Whether a tool/command mutates state, for enforce-mode gating."""

    READ_ONLY = "read_only"
    MUTATING = "mutating"
    UNCLASSIFIED = "unclassified"


def current_enforce_mode() -> EnforceMode:
    """Resolve :data:`ENFORCE_READONLY_ENV_VAR` into an :class:`EnforceMode`.

    Read from ``os.environ`` on every call so the mode reflects the current
    process state (``.env`` already loaded via :func:`mcp_common.env.load_env`,
    or a value a test set with ``monkeypatch.setenv``). Defaults to
    :attr:`EnforceMode.OFF` when unset or set to a recognized "off" value.
    """
    raw = os.environ.get(ENFORCE_READONLY_ENV_VAR)
    if raw is None:
        return EnforceMode.OFF
    value = raw.strip().lower()
    if value in _OFF_VALUES:
        return EnforceMode.OFF
    if value in _STRICT_VALUES:
        return EnforceMode.STRICT
    return EnforceMode.ENABLED


def classify_mutation(read_only: bool | None, tags: Iterable[str] | None) -> MutationClass:
    """Classify a tool from its explicit ``read_only`` flag and its ``tags``.

    Precedence: an explicit ``read_only`` flag wins over tags, so a tool can
    always force its classification. With ``read_only=None`` the ``{"write"}``
    tag convention applies; anything else is :attr:`MutationClass.UNCLASSIFIED`.
    """
    if read_only is True:
        return MutationClass.READ_ONLY
    if read_only is False:
        return MutationClass.MUTATING
    if tags is not None and WRITE_TAG in set(tags):
        return MutationClass.MUTATING
    return MutationClass.UNCLASSIFIED


def is_blocked(mode: EnforceMode, mutation: MutationClass) -> bool:
    """Decide whether a tool/command should be refused under ``mode``.

    * :attr:`EnforceMode.OFF` — never blocked.
    * :attr:`EnforceMode.ENABLED` — block only :attr:`MutationClass.MUTATING`.
    * :attr:`EnforceMode.STRICT` — block anything that is not
      :attr:`MutationClass.READ_ONLY` (mutating *and* unclassified).
    """
    if mode is EnforceMode.OFF:
        return False
    if mode is EnforceMode.STRICT:
        return mutation is not MutationClass.READ_ONLY
    return mutation is MutationClass.MUTATING


def _registry_read_only(mcp: FastMCP, tool_name: str) -> bool | None:
    """Return the ``read_only`` flag a ``@dual_mode_tool`` recorded for ``tool_name``.

    ``None`` when the tool is unknown to the dual-mode registry (e.g. a plain
    ``@mcp.tool``); its classification then rests entirely on FastMCP tags.
    """
    for meta in get_tools(mcp):
        if meta.tool_name == tool_name:
            return meta.read_only
    return None


async def _classify_registered_tool(mcp: FastMCP, tool_name: str) -> MutationClass:
    """Classify ``tool_name`` on ``mcp`` using registry + live FastMCP tags.

    Tags are read from the actual FastMCP tool so the guard covers tools
    registered by **any** path (``@dual_mode_tool`` or plain ``@mcp.tool``),
    which is what lets a ``{"write"}``-tagged plain tool be auto-blocked.
    """
    read_only = _registry_read_only(mcp, tool_name)
    tags: set[str] = set()
    # Classification must never raise: if the tool can't be resolved, fall back
    # to a tags-less classification rather than breaking the call path.
    try:
        tool = await mcp.get_tool(tool_name)
    except Exception:
        tool = None
    if tool is not None and tool.tags:
        tags = set(tool.tags)
    return classify_mutation(read_only, tags)


class ReadOnlyEnforcementMiddleware(Middleware):
    """FastMCP middleware that refuses mutating ``tools/call`` in enforce mode.

    Installed once per server by :func:`ensure_enforcement_installed`. When the
    mode is OFF (the default) it is a transparent pass-through, so a server with
    the middleware installed but the toggle unset behaves byte-identically to
    one without it.
    """

    def __init__(self, mcp: FastMCP) -> None:
        # Identity-only reference back to the owning server, used to look up the
        # called tool's classification. Stored as a plain attribute (not a
        # weakref) because the server already holds this middleware in its
        # ``middleware`` list, so their lifetimes coincide.
        self._mcp = mcp

    async def on_call_tool(
        self,
        context: MiddlewareContext[Any],
        call_next: Callable[[MiddlewareContext[Any]], Awaitable[ToolResult]],
    ) -> ToolResult:
        mode = current_enforce_mode()
        if mode is EnforceMode.OFF:
            return await call_next(context)
        mutation = await _classify_registered_tool(self._mcp, context.message.name)
        if is_blocked(mode, mutation):
            raise ToolError(READONLY_REFUSAL_MESSAGE)
        return await call_next(context)


def ensure_enforcement_installed(mcp: FastMCP) -> None:
    """Idempotently attach :class:`ReadOnlyEnforcementMiddleware` to ``mcp``.

    Called by ``@dual_mode_tool`` at decoration time so every dual-mode MCP
    server gets the server-side read-only backstop for free. Safe to call
    repeatedly: it no-ops if the middleware is already present.
    """
    for existing in getattr(mcp, "middleware", []):
        if isinstance(existing, ReadOnlyEnforcementMiddleware):
            return
    mcp.add_middleware(ReadOnlyEnforcementMiddleware(mcp))
