"""Read-only enforcement wiring for the dc-support-mcp server.

dc-support-mcp registers every tool with ``@dual_mode_tool(..., mcp_only=True)``,
so the ``MCP_ENFORCE_READONLY`` backstop middleware auto-installs via the
idempotent ``ensure_enforcement_installed`` the decorator calls — no explicit
``install_read_only_enforcement`` call is needed anymore. Mutating tools stay
tagged ``tags={"write"}``. These tests pin that contract without touching any
vendor portal or browser: the middleware refusal fires *before* the tool body
runs.
"""

from __future__ import annotations

import anyio
import pytest
from fastmcp.exceptions import ToolError
from mcp_common.dual_mode import (
    READONLY_REFUSAL_MESSAGE,
    EnforceMode,
    MutationClass,
    current_enforce_mode,
    is_blocked,
)
from mcp_common.dual_mode._enforce import (
    ReadOnlyEnforcementMiddleware,
    _classify_registered_tool,
)

import dc_support_mcp.mcp_server as srv

WRITE_TOOLS = {
    "add_vendor_comment",
    "update_vendor_ticket_status",
    "create_vendor_ticket",
    "create_vendor_service_request",
    "create_rtb_triage_ticket",
    "linear_attach_url",
    "silence_alert",
    "set_node_active",
}

READ_TOOLS = {
    "get_vendor_ticket",
    "list_vendor_tickets",
    "list_rtb_triage_tickets",
    "search_vendor_kb",
    "get_vendor_kb_article",
}


def _tool_tags(name: str) -> set[str]:
    async def _go() -> set[str]:
        tool = await srv.mcp.get_tool(name)
        return set(tool.tags or ())

    return anyio.run(_go)


def test_enforcement_middleware_installed() -> None:
    assert any(isinstance(m, ReadOnlyEnforcementMiddleware) for m in srv.mcp.middleware), (
        "@dual_mode_tool must auto-install the enforcement middleware at import "
        "so the MCP_ENFORCE_READONLY toggle is not a no-op"
    )


@pytest.mark.parametrize("name", sorted(WRITE_TOOLS))
def test_write_tools_carry_write_tag(name: str) -> None:
    assert "write" in _tool_tags(name)


@pytest.mark.parametrize("name", sorted(READ_TOOLS))
def test_read_tools_are_not_write_tagged(name: str) -> None:
    assert "write" not in _tool_tags(name)


@pytest.mark.parametrize("name", sorted(WRITE_TOOLS))
def test_write_tools_classified_mutating(name: str) -> None:
    mutation = anyio.run(_classify_registered_tool, srv.mcp, name)
    assert mutation is MutationClass.MUTATING
    assert is_blocked(EnforceMode.ENABLED, mutation)


@pytest.mark.parametrize("name", sorted(READ_TOOLS))
def test_read_tools_run_under_enabled_mode(name: str) -> None:
    mutation = anyio.run(_classify_registered_tool, srv.mcp, name)
    assert not is_blocked(EnforceMode.ENABLED, mutation)


def test_toggle_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_ENFORCE_READONLY", raising=False)
    assert current_enforce_mode() is EnforceMode.OFF


def test_middleware_refuses_write_before_running_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mutating call is refused with the terse message and never reaches the body."""
    monkeypatch.setenv("MCP_ENFORCE_READONLY", "1")
    middleware = next(m for m in srv.mcp.middleware if isinstance(m, ReadOnlyEnforcementMiddleware))

    class _Message:
        name = "create_vendor_ticket"

    class _Context:
        message = _Message()

    async def _call_next(_ctx: object) -> object:
        raise AssertionError("write tool body must not execute under enforced read-only mode")

    async def _go() -> object:
        return await middleware.on_call_tool(_Context(), _call_next)

    with pytest.raises(ToolError) as excinfo:
        anyio.run(_go)
    assert str(excinfo.value) == READONLY_REFUSAL_MESSAGE


def test_middleware_passthrough_when_toggle_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the toggle unset the middleware is a transparent pass-through."""
    monkeypatch.delenv("MCP_ENFORCE_READONLY", raising=False)
    middleware = next(m for m in srv.mcp.middleware if isinstance(m, ReadOnlyEnforcementMiddleware))
    sentinel = object()

    class _Message:
        name = "create_vendor_ticket"

    class _Context:
        message = _Message()

    async def _call_next(_ctx: object) -> object:
        return sentinel

    async def _go() -> object:
        return await middleware.on_call_tool(_Context(), _call_next)

    assert anyio.run(_go) is sentinel
