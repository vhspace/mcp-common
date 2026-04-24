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
    """Tool names the agent should call."""

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


def scenarios_to_dataset(scenarios: list[Scenario]) -> Any:
    """Convert scenarios to an Inspect AI ``Dataset``.

    This is a placeholder — the actual conversion will be implemented in a
    follow-up issue once the Inspect AI task structure is finalised.

    Args:
        scenarios: Validated scenario objects to convert.

    Raises:
        NotImplementedError: Always; implementation tracked in issue #46.
    """
    raise NotImplementedError("See issue #46")
