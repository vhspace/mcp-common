"""Unit tests for dc-support-mcp eval scenario loading."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_SCENARIOS_PATH = Path(__file__).resolve().parent.parent / "evals" / "scenarios.json"
_WRITE_TOOLS = {
    "add_vendor_comment",
    "update_vendor_ticket_status",
    "create_vendor_ticket",
    "create_vendor_service_request",
    "create_rtb_triage_ticket",
    "linear_attach_url",
    "silence_alert",
    "set_node_active",
}


def _load_scenarios_json() -> list[dict]:
    return json.loads(_SCENARIOS_PATH.read_text(encoding="utf-8"))


def test_scenarios_file_has_minimum_count() -> None:
    scenarios = _load_scenarios_json()
    assert len(scenarios) >= 2


def test_scenarios_never_expect_write_tools() -> None:
    for scenario in _load_scenarios_json():
        for tool in scenario.get("expected_tools", []):
            assert tool not in _WRITE_TOOLS, f"write tool in scenario: {tool}"


def test_loader_respects_mode_filter() -> None:
    pytest.importorskip("inspect_ai")
    from mcp_common.testing.eval.datasets import load_scenarios, scenarios_to_dataset

    cli_only = scenarios_to_dataset(load_scenarios(_SCENARIOS_PATH), mode_filter={"cli"})
    assert len(cli_only) == 2

    both = scenarios_to_dataset(load_scenarios(_SCENARIOS_PATH), mode_filter={"both"})
    assert len(both) == 1
