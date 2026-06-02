"""Shared eval scenario format and dataset loader.

Provides a ``Scenario`` Pydantic model that downstream MCP repos use to define
evaluation cases, plus helpers for loading scenarios from JSON and converting
them to Inspect AI dataset objects.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel


class Scenario(BaseModel):
    """A single evaluation scenario for an MCP server.

    Each scenario describes one prompt the agent will receive, along with
    metadata about which tools it should use and how to judge success.
    """

    input: str
    """The prompt to give the agent."""

    expected_tools: list[str] = []
    """MCP tool names the agent should call (used by ``tool_use_scorer``)."""

    expected_commands: list[str] = []
    """Optional explicit CLI commands the agent should run (for CLI evals).

    Consumed by ``cli_tool_use_scorer`` when present, taking precedence over the
    tool-name -> CLI-subcommand mapping derived from ``expected_tools``. Entries
    may be full invocations (``"netbox-cli devices --cluster X"``) or bare
    subcommands (``"lookup-device"``); only the subcommand token is matched.
    """

    expected_behavior: str = ""
    """Natural-language description for LLM-as-judge scoring."""

    mode: Literal["mcp", "cli", "both"] = "both"
    """Which eval mode this scenario applies to."""

    tags: list[str] = []
    """Categorization tags: ``"happy_path"``, ``"error_handling"``, etc."""


def load_scenarios(path: str | Path) -> list[Scenario]:
    """Load evaluation scenarios from a JSON file.

    The file should contain a JSON array of objects matching the
    :class:`Scenario` schema.

    Args:
        path: Filesystem path to a ``.json`` file.

    Returns:
        A list of validated :class:`Scenario` instances.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return [Scenario.model_validate(item) for item in raw]


def scenario_to_sample(scenario: Scenario) -> Any:
    """Convert one :class:`Scenario` to an Inspect AI ``Sample``.

    The conversion is intentionally **lossless**: every :class:`Scenario` field
    is forwarded into ``Sample.metadata`` (via :meth:`pydantic.BaseModel.model_dump`,
    so fields added to ``Scenario`` later are carried automatically). This is the
    generic fix for the bug where hand-rolled per-repo loaders dropped
    :attr:`Scenario.expected_commands` when building ``Sample`` metadata, so
    :func:`mcp_common.testing.eval.scorers.cli_tool_use_scorer` never saw the
    explicit CLI commands even when they were set (togethercomputer/mcp-common#133).

    The ``Sample`` is shaped to match what the scorers expect:

    * ``input`` — the scenario prompt.
    * ``target`` — the expected MCP tool names joined by ``","`` (the
      comma-separated form :func:`scorers._parse_expected_tools` parses).
    * ``metadata`` — the full scenario dump, so ``state.metadata["input"]``,
      ``state.metadata["expected_behavior"]``, ``state.metadata["expected_commands"]``,
      ``mode``, ``tags`` (and any future fields) are all available to scorers.

    Args:
        scenario: A validated :class:`Scenario`.

    Returns:
        An ``inspect_ai.dataset.Sample``.
    """
    from inspect_ai.dataset import Sample

    return Sample(
        input=scenario.input,
        target=",".join(scenario.expected_tools),
        metadata=scenario.model_dump(),
    )


def scenarios_to_dataset(
    scenarios: list[Scenario],
    *,
    mode_filter: set[str] | None = None,
    name: str | None = None,
) -> Any:
    """Convert scenarios to an Inspect AI ``MemoryDataset``.

    Shared, complete scenario → dataset loader so downstream MCP repos stop
    hand-rolling lossy converters (the #46 placeholder; bug surfaced in
    togethercomputer/mcp-common#133). Each scenario is mapped with
    :func:`scenario_to_sample`, which forwards **all** scenario fields —
    including :attr:`Scenario.expected_commands` — into ``Sample.metadata`` so
    ``cli_tool_use_scorer`` can read them.

    Args:
        scenarios: Validated scenario objects to convert.
        mode_filter: When given, keep only scenarios whose
            :attr:`Scenario.mode` is in this set (e.g. ``{"cli", "both"}`` for a
            CLI eval, ``{"mcp", "both"}`` for an MCP eval). When ``None`` (the
            default) all scenarios are included.
        name: Optional dataset name forwarded to ``MemoryDataset``.

    Returns:
        An ``inspect_ai.dataset.MemoryDataset`` ready to pass to a ``Task``.
    """
    from inspect_ai.dataset import MemoryDataset

    samples = [
        scenario_to_sample(s) for s in scenarios if mode_filter is None or s.mode in mode_filter
    ]
    return MemoryDataset(samples=samples, name=name)
