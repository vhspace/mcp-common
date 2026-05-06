"""Shared dataset loader for netbox-mcp eval scenarios.

Loads scenarios from the JSON file using the mcp-common Scenario model,
filters by eval mode, and converts to Inspect AI Sample objects.
"""

from __future__ import annotations

from pathlib import Path

from inspect_ai.dataset import Sample
from mcp_common.testing.eval.datasets import load_scenarios

SCENARIOS_PATH = Path(__file__).parent / "scenarios.json"


def load_netbox_scenarios(mode_filter: set[str]) -> list[Sample]:
    """Load scenarios from disk and convert to Inspect AI samples.

    Args:
        mode_filter: Set of mode values to include (e.g. ``{"mcp", "both"}``).

    Returns:
        List of Inspect AI ``Sample`` objects ready for a ``Task``.
    """
    scenarios = load_scenarios(SCENARIOS_PATH)
    samples: list[Sample] = []
    for s in scenarios:
        if s.mode not in mode_filter:
            continue
        samples.append(
            Sample(
                input=s.input,
                target=",".join(s.expected_tools),
                metadata={
                    "input": s.input,
                    "expected_tools": s.expected_tools,
                    "expected_behavior": s.expected_behavior,
                    "mode": s.mode,
                    "tags": s.tags,
                },
            )
        )
    return samples
