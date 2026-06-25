"""Shared dataset loader for dc-support-mcp eval scenarios."""

from __future__ import annotations

from pathlib import Path

from inspect_ai.dataset import MemoryDataset
from mcp_common.testing.eval.datasets import load_scenarios, scenarios_to_dataset

SCENARIOS_PATH = Path(__file__).parent / "scenarios.json"


def load_dc_support_scenarios(mode_filter: set[str]) -> MemoryDataset:
    """Load scenarios from disk and convert to an Inspect AI dataset."""
    scenarios = load_scenarios(SCENARIOS_PATH)
    return scenarios_to_dataset(scenarios, mode_filter=mode_filter)
