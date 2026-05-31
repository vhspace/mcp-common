"""Regression tests for the CLI tool-selection mapping that closes #125.

The three ``netbox_get_objects`` CLI scenarios scored tool-selection 0 because
``cli_tool_use_scorer`` derived the subcommand ``get-objects`` from the MCP tool
name, while the netbox-lookups skill teaches (and agents run) ``netbox-cli
list`` / ``search`` / ``devices``. We fix that by declaring the real mapping in
``netbox_mcp.server.cli_subcommand_map()`` and feeding it to the scorer
(``tool_subcommands=...``), and by loading scenarios through mcp-common's
lossless ``scenarios_to_dataset`` so ``expected_commands`` is forwarded too.

These tests deliberately avoid importing ``mcp_common.testing.eval`` at module
level: that subpackage requires the ``mcp-common[eval]`` extra (inspect-ai),
which the default CI ``uv sync`` does not install. The mapping under test lives
in ``netbox_mcp.server`` (core deps only); the one assertion that needs the
shared loader is gated behind ``pytest.importorskip``.

Refs vhspace/netbox-mcp#125, vhspace/mcp-common#133.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from netbox_mcp.server import CLI_SUBCOMMAND_ALIASES, cli_subcommand_map

pytestmark = pytest.mark.unit

_SCENARIOS_PATH = Path(__file__).resolve().parent.parent / "evals" / "scenarios.json"

# The three scenarios from #125 that scored tool-selection 0 in cli mode.
_ISSUE_125_TAGS = (
    ["filtering", "precision"],
    ["filtering", "negative_filter"],
    ["large_result", "cluster", "pagination"],
)


def _load_scenarios_json() -> list[dict]:
    return json.loads(_SCENARIOS_PATH.read_text(encoding="utf-8"))


def test_get_objects_maps_to_real_cli_subcommands() -> None:
    """netbox_get_objects is credited for the commands the skill teaches."""
    subs = cli_subcommand_map()["netbox_get_objects"]
    assert subs == ["list", "search", "devices"]
    # The bogus kebab derivation must NOT be the expected subcommand (the bug).
    assert "get-objects" not in subs


def test_search_objects_maps_to_search() -> None:
    """Real CLI subcommand ``search`` differs from the kebab ``search-objects``."""
    assert cli_subcommand_map()["netbox_search_objects"] == ["search"]


def test_changelogs_alias_corrects_kebab_derivation() -> None:
    """mcp_only dual-mode tool: derived ``get-changelogs`` + real ``changelogs``."""
    assert "changelogs" in cli_subcommand_map()["netbox_get_changelogs"]


def test_dual_mode_tools_keep_kebab_mapping() -> None:
    """Tools whose CLI name already matches the kebab derivation need no alias."""
    m = cli_subcommand_map()
    assert m["netbox_lookup_device"] == ["lookup-device"]
    assert m["netbox_get_object_by_id"] == ["get-object-by-id"]
    assert m["netbox_get_objects_by_ids"] == ["get-objects-by-ids"]
    assert m["netbox_oob_summary"] == ["oob-summary"]


def test_cli_subcommand_aliases_only_cover_non_dual_mode_tools() -> None:
    """The hand-maintained dict is limited to the two non-dual-mode tools."""
    assert set(CLI_SUBCOMMAND_ALIASES) == {"netbox_get_objects", "netbox_search_objects"}


def test_issue_125_scenarios_are_covered_by_the_map() -> None:
    """Each #125 scenario expects netbox_get_objects, and the map credits the
    list/search/devices subcommands agents actually run for them."""
    by_tags = {tuple(s.get("tags", [])): s for s in _load_scenarios_json()}
    accepted = set(cli_subcommand_map()["netbox_get_objects"])
    for tags in _ISSUE_125_TAGS:
        scenario = by_tags[tuple(tags)]
        assert scenario["expected_tools"] == ["netbox_get_objects"]
        # at least one real subcommand the agent runs is an accepted match
        assert accepted & {"list", "search", "devices"}


def test_loader_forwards_expected_commands_metadata() -> None:
    """scenario_to_sample (used by the shared loader) forwards expected_commands
    into Sample.metadata — the field the previous hand-rolled loader dropped.

    Requires the mcp-common[eval] extra (inspect-ai); skipped otherwise.
    """
    pytest.importorskip("inspect_ai")
    from mcp_common.testing.eval.datasets import load_scenarios, scenario_to_sample

    sample = scenario_to_sample(load_scenarios(_SCENARIOS_PATH)[0])
    assert "expected_commands" in sample.metadata
