"""Enforced read-only ("eval") mode for the dual-mode dispatch layer.

This is the server-side hard guarantee for enforced read-only mode: when
enabled, read-only tools/commands run normally but any **create / update /
destroy** (mutating) tool/command does **not** execute — the caller receives a
terse, non-tainting refusal (exactly :data:`READONLY_REFUSAL_MESSAGE`, nothing
else) so an eval's tool-selection results are never biased by a verbose
"writes are disabled because this is an eval" hint.

Because every dual-mode MCP funnels its tool calls through the same FastMCP
server and its CLI through :func:`mcpanvil.dual_mode.build_cli_from_mcp`, the
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

  A server that registers tools **only** via plain ``@mcp.tool`` (never
  ``@dual_mode_tool`` — e.g. awx-mcp, dc-support-mcp) has nothing to trigger
  that auto-install, so the toggle would be a silent no-op for it. Such servers
  must call :func:`install_read_only_enforcement` once at startup;
  :func:`verify_enforcement_installed` emits a :func:`logging.warning` when the
  toggle is on but the middleware is missing, so the gap is observable.
* **CLI side** — :func:`mcpanvil.dual_mode.builder` consults
  :func:`current_enforce_mode` + :func:`classify_mutation` + :func:`is_blocked`
  inside each synthesized command and, when blocked, prints the same one-liner
  to stderr and exits non-zero **without** executing the tool function.

Toggle (``MCP_ENFORCE_READONLY``)
---------------------------------
Read from the environment **at dispatch time** (so tests can toggle it and the
repo's ``.env`` loading — :func:`mcpanvil.env.load_env` — is honored), and
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

Relationship to ``read_only_tools``
-----------------------------------
``read_only_tools`` **trims** the exposed tool surface so the
model under test never *sees* write tools — a harness-side measure that relies
on the runner remembering to trim. Enforce mode is the **server-side backstop**:
even if a write tool is exposed or invoked directly (e.g. a ``bash`` tool runs
``netbox-cli`` in a ``cli`` / ``combined`` eval where an Inspect allow-list
cannot intercept it), the mutation still does not execute. Use both: trim to
shrink the surface, enforce mode to guarantee no write runs.
"""

from __future__ import annotations

import asyncio
import logging
import os
from enum import Enum
from typing import TYPE_CHECKING, Any

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware

from mcpanvil.dual_mode._registry import get_tools

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
    "install_read_only_enforcement",
    "is_blocked",
    "verify_enforcement_installed",
]

_LOGGER = logging.getLogger(__name__)

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
_ENABLED_VALUES = frozenset({"1", "true", "yes", "on", "enabled"})
"""Explicitly-recognized "on" values.

Any *other* non-off, non-strict value still resolves to :attr:`EnforceMode.ENABLED`
(fail-safe: a typo must never silently *disable* the guard) but is reported once
via :func:`logging.warning` so an unintended value — e.g. ``stict`` silently
degrading from the intended ``strict`` to ``enabled`` — is observable.
"""

_warned_unrecognized_values: set[str] = set()
"""Unrecognized toggle values already warned about (de-duped to avoid spam)."""


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
    process state (``.env`` already loaded via :func:`mcpanvil.env.load_env`,
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
    if value not in _ENABLED_VALUES:
        _warn_unrecognized_value(value)
    return EnforceMode.ENABLED


def _warn_unrecognized_value(value: str) -> None:
    """Warn (once per distinct value) that an unrecognized toggle value fails safe to ENABLED.

    Enforce mode stays fail-safe-on for any non-off, non-strict value so a typo
    never silently *disables* the guard. But a typo that silently *degrades*
    behavior — e.g. a misspelled ``stict`` resolving to ``enabled`` (block only
    mutating tools) instead of the intended ``strict`` (also block unclassified
    tools) — would otherwise be invisible. De-duplicated so a hot dispatch path
    (``current_enforce_mode`` runs on every tool call / CLI command) does not
    spam the log.
    """
    if value in _warned_unrecognized_values:
        return
    _warned_unrecognized_values.add(value)
    _LOGGER.warning(
        "Unrecognized %s value %r; treating as ENABLED (fail-safe: writes are "
        "still refused). Recognized values: off=%s; strict; on=%s.",
        ENFORCE_READONLY_ENV_VAR,
        value,
        "/".join(sorted(v for v in _OFF_VALUES if v)),
        "/".join(sorted(_ENABLED_VALUES)),
    )


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


def _enforcement_installed(mcp: FastMCP) -> bool:
    """Return whether ``mcp`` already has the enforcement middleware attached."""
    return any(
        isinstance(existing, ReadOnlyEnforcementMiddleware)
        for existing in getattr(mcp, "middleware", [])
    )


def ensure_enforcement_installed(mcp: FastMCP) -> None:
    """Idempotently attach :class:`ReadOnlyEnforcementMiddleware` to ``mcp``.

    Called by ``@dual_mode_tool`` at decoration time so every dual-mode MCP
    server gets the server-side read-only backstop for free. Safe to call
    repeatedly: it no-ops if the middleware is already present.

    Servers that register tools **only** via plain ``@mcp.tool`` (never
    ``@dual_mode_tool``) do not trigger this path automatically — they must
    call :func:`install_read_only_enforcement` (its public alias) at startup.
    """
    if _enforcement_installed(mcp):
        return
    mcp.add_middleware(ReadOnlyEnforcementMiddleware(mcp))


def install_read_only_enforcement(mcp: FastMCP) -> None:
    """Install the server-side enforced read-only backstop on ``mcp`` (public, idempotent).

    The one-call entry point for **any** FastMCP server to opt into
    ``MCP_ENFORCE_READONLY`` — including a server whose tools are registered
    *only* with plain ``@mcp.tool`` (e.g. awx-mcp, dc-support-mcp). Such a
    server never goes through ``@dual_mode_tool``, so the middleware is not
    auto-installed and the toggle would otherwise be a silent no-op. Call this
    once at startup, after (or before) the tools are registered::

        from mcpanvil.dual_mode import install_read_only_enforcement

        mcp = FastMCP("awx-mcp")
        # ... @mcp.tool definitions, write tools tagged tags={"write"} ...
        install_read_only_enforcement(mcp)

    Thereafter mutating tools (``{"write"}`` tag or ``read_only=False``) are
    refused with exactly :data:`READONLY_REFUSAL_MESSAGE` when the toggle is on,
    and it is a transparent pass-through when the toggle is unset (the default).
    Safe to call repeatedly; thin wrapper over :func:`ensure_enforcement_installed`.
    """
    ensure_enforcement_installed(mcp)


def _server_has_tools(mcp: FastMCP) -> bool | None:
    """Best-effort, never-raising sync check of whether ``mcp`` exposes any tool.

    Returns ``True``/``False`` when the tool count can be determined, or ``None``
    when it cannot be determined safely (e.g. called from inside a running event
    loop, where blocking to enumerate the async tool list is unsafe). Callers
    treat ``None`` as "assume present" so the observability warning is not
    silently suppressed.
    """
    if get_tools(mcp):
        return True
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass  # no running loop → safe to block briefly to enumerate tools
    else:
        return None
    try:
        import anyio

        return bool(anyio.run(mcp.list_tools))
    except Exception:
        return None


def verify_enforcement_installed(mcp: FastMCP, *, logger: logging.Logger | None = None) -> bool:
    """Verify the read-only backstop is installed on ``mcp``; warn on a silent no-op gap.

    Intended to be called once at server startup (after tools are registered)
    or by an eval run/preflight. Returns whether
    :class:`ReadOnlyEnforcementMiddleware` is installed on ``mcp``.

    When ``MCP_ENFORCE_READONLY`` is enabled but the middleware is **not**
    installed on a server that has tools, this emits a clear
    :func:`logging.warning` (to ``logger`` when given, else this module's
    logger) so the gap — the toggle is set yet writes would NOT be refused — is
    observable rather than silent. The remedy is named in the message: call
    :func:`install_read_only_enforcement`. A no-op (no warning) when the toggle
    is unset, when the middleware is already installed, or when the server is
    known to have no tools.
    """
    if _enforcement_installed(mcp):
        return True
    if current_enforce_mode() is EnforceMode.OFF:
        return False
    if _server_has_tools(mcp) is False:
        return False
    (logger or _LOGGER).warning(
        "%s=%s is set but ReadOnlyEnforcementMiddleware is NOT installed on "
        "FastMCP(%r): mutating tools will NOT be refused (the enforce toggle is "
        "a no-op for this server). Call "
        "mcpanvil.dual_mode.install_read_only_enforcement(mcp) at startup, and "
        "ensure write tools are classified (tags={'write'} or read_only=False).",
        ENFORCE_READONLY_ENV_VAR,
        os.environ.get(ENFORCE_READONLY_ENV_VAR),
        mcp.name,
    )
    return False
