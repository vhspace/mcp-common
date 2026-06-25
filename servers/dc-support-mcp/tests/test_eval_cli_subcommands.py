"""CLI subcommand mapping tests for dc-support-mcp eval cli_tool_use_scorer."""

from __future__ import annotations

import pytest

from dc_support_mcp.mcp_server import CLI_SUBCOMMAND_ALIASES, cli_subcommand_map

pytestmark = pytest.mark.unit


def test_list_tickets_maps_to_tickets_subcommand() -> None:
    subs = cli_subcommand_map()["list_vendor_tickets"]
    assert subs == ["tickets"]
    assert "list-vendor-tickets" not in subs


def test_get_ticket_maps_to_get_ticket_subcommand() -> None:
    subs = cli_subcommand_map()["get_vendor_ticket"]
    assert subs == ["get-ticket"]


def test_kb_tools_map_to_kb_subcommands() -> None:
    m = cli_subcommand_map()
    assert m["search_vendor_kb"] == ["kb-search"]
    assert m["get_vendor_kb_article"] == ["kb-article"]


def test_aliases_cover_read_only_mcp_only_tools() -> None:
    assert set(CLI_SUBCOMMAND_ALIASES) == {
        "get_vendor_ticket",
        "list_vendor_tickets",
        "list_rtb_triage_tickets",
        "search_vendor_kb",
        "get_vendor_kb_article",
    }
