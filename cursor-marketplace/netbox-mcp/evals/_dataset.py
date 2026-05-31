"""Shared dataset loader for netbox-mcp eval scenarios.

Loads scenarios from the JSON file using the mcp-common ``Scenario`` model,
filters by eval mode, and converts them to an Inspect AI dataset via the
shared **lossless** ``scenarios_to_dataset`` loader.
"""

from __future__ import annotations

from pathlib import Path

from inspect_ai.dataset import MemoryDataset
from mcp_common.testing.eval.datasets import load_scenarios, scenarios_to_dataset

SCENARIOS_PATH = Path(__file__).parent / "scenarios.json"


def load_netbox_scenarios(mode_filter: set[str]) -> MemoryDataset:
    """Load scenarios from disk and convert to an Inspect AI dataset.

    Delegates to mcp-common's shared ``scenarios_to_dataset`` (>= v0.28.0),
    which maps every ``Scenario`` through ``scenario_to_sample`` — forwarding
    **all** scenario fields (including ``expected_commands``) into
    ``Sample.metadata`` via ``model_dump()``. This replaces the previous
    hand-rolled Sample builder, which only copied
    ``input``/``expected_tools``/``expected_behavior``/``mode``/``tags`` and so
    silently dropped ``expected_commands`` — the metadata ``cli_tool_use_scorer``
    reads (vhspace/netbox-mcp#125, vhspace/mcp-common#133).

    Args:
        mode_filter: Set of mode values to include (e.g. ``{"mcp", "both"}``).

    Returns:
        An Inspect AI ``MemoryDataset`` ready to pass to a ``Task``.
    """
    scenarios = load_scenarios(SCENARIOS_PATH)
    return scenarios_to_dataset(scenarios, mode_filter=mode_filter)
