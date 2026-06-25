"""CLI subcommand mapping tests for awx-mcp eval cli_tool_use_scorer."""

from __future__ import annotations

import pytest

from awx_mcp.server import CLI_SUBCOMMAND_ALIASES, cli_subcommand_map

pytestmark = pytest.mark.unit


def test_list_resources_maps_to_list_subcommand() -> None:
    subs = cli_subcommand_map()["awx_list_resources"]
    assert subs == ["list"]
    assert "list-resources" not in subs


def test_get_resource_maps_to_get_subcommand() -> None:
    subs = cli_subcommand_map()["awx_get_resource"]
    assert subs == ["get"]


def test_dual_mode_ping_and_me_present() -> None:
    m = cli_subcommand_map()
    assert m["awx_ping"] == ["ping"]
    assert m["awx_get_me"] == ["me"]


def test_aliases_cover_generic_resource_tools() -> None:
    assert set(CLI_SUBCOMMAND_ALIASES) == {"awx_list_resources", "awx_get_resource"}
