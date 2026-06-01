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

from mcp_common.testing.eval.analyzer import EvalFailure, analyze_eval_dir, analyze_eval_log
from mcp_common.testing.eval.datasets import (
    Scenario,
    load_scenarios,
    scenario_to_sample,
    scenarios_to_dataset,
)
from mcp_common.testing.eval.description_qa import (
    DescriptionIssue,
    LLMDescriptionScore,
    SimilarityConflict,
    check_description_quality,
    check_description_quality_llm,
    check_similarity_conflicts,
)
from mcp_common.testing.eval.issue_filer import deduplicate, file_issues
from mcp_common.testing.eval.judge_usage import (
    JudgePricing,
    JudgeUsage,
    JudgeUsageAccumulator,
    get_judge_usage,
    install_judge_usage_tracking,
    judge_cost_block,
    reset_judge_usage,
    tracked_judge_client,
    uninstall_judge_usage_tracking,
)
from mcp_common.testing.eval.model_configs import generate_config_for_tier
from mcp_common.testing.eval.provider_config import generate_config_for_provider_tier
from mcp_common.testing.eval.remediate import remediate_batch, remediate_failure
from mcp_common.testing.eval.repo_discovery import RepoInfo, discover_repos, resolve_server_to_repo
from mcp_common.testing.eval.report import (
    TrendReport,
    add_judge_usage_to_summary,
    append_history,
    load_history,
    render_trend,
)
from mcp_common.testing.eval.scorers import (
    cli_tool_use_scorer,
    combined_scorer,
    faithfulness_scorer,
    hallucination_scorer,
    parity_scorer,
    relevancy_scorer,
    tool_use_scorer,
)
from mcp_common.testing.eval.tool_filters import (
    WRITE_TAG,
    ReadOnlySurface,
    ToolSafetyInfo,
    derive_read_only_surface,
    read_only_surface_from_dual_mode,
    read_only_tools,
    read_only_tools_from_dual_mode,
)
from mcp_common.testing.eval.tracking import log_eval_file, log_eval_to_wandb
from mcp_common.testing.eval.write_safety import (
    WriteSafetyError,
    assert_read_only_eval_mode,
    write_safety_preflight_facts,
)

__all__ = [
    "WRITE_TAG",
    "DescriptionIssue",
    "EvalFailure",
    "JudgePricing",
    "JudgeUsage",
    "JudgeUsageAccumulator",
    "LLMDescriptionScore",
    "ReadOnlySurface",
    "RepoInfo",
    "Scenario",
    "SimilarityConflict",
    "ToolSafetyInfo",
    "TrendReport",
    "WriteSafetyError",
    "add_judge_usage_to_summary",
    "analyze_eval_dir",
    "analyze_eval_log",
    "append_history",
    "assert_read_only_eval_mode",
    "check_description_quality",
    "check_description_quality_llm",
    "check_similarity_conflicts",
    "cli_tool_use_scorer",
    "combined_scorer",
    "deduplicate",
    "derive_read_only_surface",
    "discover_repos",
    "faithfulness_scorer",
    "file_issues",
    "generate_config_for_provider_tier",
    "generate_config_for_tier",
    "get_judge_usage",
    "hallucination_scorer",
    "install_judge_usage_tracking",
    "judge_cost_block",
    "load_history",
    "load_scenarios",
    "log_eval_file",
    "log_eval_to_wandb",
    "parity_scorer",
    "read_only_surface_from_dual_mode",
    "read_only_tools",
    "read_only_tools_from_dual_mode",
    "relevancy_scorer",
    "remediate_batch",
    "remediate_failure",
    "render_trend",
    "reset_judge_usage",
    "resolve_server_to_repo",
    "scenario_to_sample",
    "scenarios_to_dataset",
    "tool_use_scorer",
    "tracked_judge_client",
    "uninstall_judge_usage_tracking",
    "write_safety_preflight_facts",
]
