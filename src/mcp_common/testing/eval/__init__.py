"""Eval helpers for MCP server quality assurance.

This module provides the evaluation layer of the four-tier MCP testing pyramid:

1. **Unit tests** — fast, isolated tests for individual tool functions
   (``mcp_common.testing.fixtures`` / ``assertions``).
2. **Integration tests** — end-to-end tests against a running MCP server
   using ``mcp_common.testing.mcp_client``.
3. **Eval suite** (this module) — LLM-as-judge evaluations powered by
   `Inspect AI <https://inspect.ai-safety-institute.org.uk/>`_ that
   measure tool selection, argument quality, error handling, and
   interface parity between MCP and CLI modes.
4. **Description QA** — static analysis of tool descriptions to catch
   ambiguity and inter-server conflicts before they reach an agent.

Install with::

    uv pip install "mcp-common[eval]"

"""

from importlib.util import find_spec

if find_spec("inspect_ai") is None:
    raise ImportError(
        "mcp-common[eval] extra required. Install with: uv pip install mcp-common[eval]"
    )

from mcp_common.testing.eval.analyzer import EvalFailure, analyze_eval_log
from mcp_common.testing.eval.datasets import Scenario, load_scenarios, scenarios_to_dataset
from mcp_common.testing.eval.description_qa import (
    DescriptionIssue,
    SimilarityConflict,
    check_description_quality,
    check_similarity_conflicts,
)
from mcp_common.testing.eval.issue_filer import deduplicate, file_issues
from mcp_common.testing.eval.scorers import combined_scorer, parity_scorer, tool_use_scorer

__all__ = [
    "DescriptionIssue",
    "EvalFailure",
    "Scenario",
    "SimilarityConflict",
    "analyze_eval_log",
    "check_description_quality",
    "check_similarity_conflicts",
    "combined_scorer",
    "deduplicate",
    "file_issues",
    "load_scenarios",
    "parity_scorer",
    "scenarios_to_dataset",
    "tool_use_scorer",
]
