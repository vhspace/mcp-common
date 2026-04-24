"""Placeholder Inspect AI scorers for MCP server evaluations.

Scorers judge agent behaviour along several dimensions:

- **Tool selection** — did the agent pick the right tool(s)?
- **Argument quality** — were the arguments well-formed and complete?
- **Error handling** — did the agent recover gracefully from errors?
- **Task completion** — did the agent achieve the user's goal?
- **Interface parity** — do MCP and CLI paths produce equivalent results?
"""

from __future__ import annotations

from typing import Any


def tool_use_scorer() -> Any:
    """Score agent tool usage across four dimensions.

    Evaluates:
      1. **Tool selection** — correct tool(s) chosen from the available set.
      2. **Argument quality** — arguments are well-formed, complete, and use
         reasonable defaults when the prompt is ambiguous.
      3. **Error handling** — the agent retries or explains failures rather
         than silently dropping them.
      4. **Task completion** — the final output satisfies the user's request.

    Returns an Inspect AI ``Scorer`` that produces a structured rubric result.

    Raises:
        NotImplementedError: Always; implementation tracked in issue #45.
    """
    raise NotImplementedError("See issue #45")


def combined_scorer() -> Any:
    """Extend :func:`tool_use_scorer` with interface-choice scoring.

    In addition to the four dimensions of ``tool_use_scorer``, this scorer
    also checks whether the agent chose the appropriate interface (MCP tool
    call vs. CLI subprocess) for scenarios that support both modes.

    Returns an Inspect AI ``Scorer``.

    Raises:
        NotImplementedError: Always; implementation tracked in issue #45.
    """
    raise NotImplementedError("See issue #45")


def parity_scorer() -> Any:
    """Compare MCP and CLI execution paths for result equivalence.

    For scenarios tagged with ``mode="both"``, runs the prompt through both
    the MCP tool interface and the CLI subprocess interface, then checks:

    - Semantic equivalence of the outputs.
    - Similar tool-call counts (within a tolerance).
    - Matching error/success status.

    Returns an Inspect AI ``Scorer``.

    Raises:
        NotImplementedError: Always; implementation tracked in issue #45.
    """
    raise NotImplementedError("See issue #45")
