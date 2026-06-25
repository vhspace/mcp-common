"""Shared dataset loader for the cross-cutting CLI-discovery eval scenarios.

The discovery scenarios span ALL six mcp-common ``*-cli`` binaries, so they
live at the repo root under ``evals/cli_discovery/`` rather than under a single
server's per-binary ``cli_tool_use_scorer`` layout (togethercomputer/mcp-common#95).
The loader mirrors the per-server ``_dataset.py`` shape so ``cli_eval.py`` can
reuse :func:`mcp_common.testing.eval.datasets.scenarios_to_dataset` unchanged.
"""

from __future__ import annotations

from pathlib import Path

from inspect_ai.dataset import MemoryDataset
from mcp_common.testing.eval.datasets import load_scenarios, scenarios_to_dataset

SCENARIOS_PATH = Path(__file__).parent / "scenarios.json"


def load_cli_discovery_scenarios(mode_filter: set[str]) -> MemoryDataset:
    """Load the discovery scenarios from disk and convert to an Inspect dataset."""
    scenarios = load_scenarios(SCENARIOS_PATH)
    return scenarios_to_dataset(scenarios, mode_filter=mode_filter)
