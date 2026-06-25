"""Tool-description quality checks for CI.

Validates that MCP tool descriptions are clear, unambiguous, and don't
conflict with tools exposed by other servers that an agent might see
simultaneously.

Includes fast heuristic rules (no LLM) for cheap CI gating **and**
LLM-as-judge scoring via Together AI for richer evaluation.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import re
from collections.abc import Callable, Coroutine
from difflib import SequenceMatcher
from enum import StrEnum
from typing import Annotated, Any, Literal, TypeVar

import anyio.from_thread
import typer
from fastmcp import Client, FastMCP
from pydantic import BaseModel, ValidationError

_T = TypeVar("_T")
_log = logging.getLogger(__name__)

IssueType = Literal[
    "too_vague",
    "missing_parameters",
    "missing_error_info",
    "too_long",
    "missing_return_info",
]

_DEFAULT_JUDGE_MODEL = "Qwen/Qwen3-235B-A22B-Instruct-2507-tput"
_TOGETHER_BASE_URL = "https://api.together.xyz/v1"


class LLMDescriptionScore(BaseModel):
    """LLM-generated quality score for a single tool description."""

    tool_name: str
    clarity: int
    completeness: int
    conciseness: int
    distinctiveness: int
    overall_score: float
    verdict: Literal["good", "needs_improvement", "poor"]
    suggested_improvement: str
    explanation: str


_MIN_DESC_LENGTH = 20
_MAX_DESC_LENGTH = 500
_SIMILARITY_THRESHOLD = 0.6


def _run_async(coro_fn: Callable[[], Coroutine[Any, Any, _T]]) -> _T:
    """Call an async zero-arg function from a synchronous context.

    Uses ``asyncio.run`` when no event loop is active, or
    ``anyio.from_thread.run`` when called from within an existing
    async context (e.g. a sync helper invoked from a pytest-anyio test).
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_fn())
    return anyio.from_thread.run(coro_fn)


_RETURN_PATTERNS = re.compile(
    r"(?i)\b(returns?|result|output|response|yields?|produces?|gives?\s+back)\b"
)
_ERROR_PATTERNS = re.compile(
    r"(?i)\b(error|fail|raises?|exception|invalid|not\s+found|missing|unable|cannot)\b"
)


class DescriptionIssue(BaseModel):
    """A quality problem found in a single tool description."""

    tool_name: str
    """Fully-qualified tool name (``server.tool``)."""

    issue_type: IssueType
    """Category of the issue."""

    message: str
    """Human-readable explanation of what's wrong."""

    score: float = 0.0
    """Quality score between 0 (worst) and 1 (best)."""


class SimilarityConflict(BaseModel):
    """A detected conflict between tool descriptions across servers."""

    tool_a: str
    """First tool's fully-qualified name."""

    tool_b: str
    """Second tool's fully-qualified name."""

    similarity: float
    """Sequence-matcher similarity ratio between the two descriptions."""

    score: float
    """Alias kept for downstream consumers (same value as ``similarity``)."""

    explanation: str
    """Why these descriptions may confuse an agent."""


def _get_fastmcp_instance(server_module: str) -> FastMCP:
    """Import *server_module* and return its ``FastMCP`` instance.

    Looks for a module-level attribute named ``mcp``, ``app``, or ``server``.
    """
    mod = importlib.import_module(server_module)
    for attr in ("mcp", "app", "server"):
        obj = getattr(mod, attr, None)
        if isinstance(obj, FastMCP):
            return obj
    raise ValueError(
        f"Module {server_module!r} has no FastMCP instance named 'mcp', 'app', or 'server'"
    )


async def _collect_tools(server: FastMCP) -> list[tuple[str, str, dict[str, Any]]]:
    """List tools from a FastMCP server. Returns (name, description, input_schema)."""
    async with Client(server) as client:
        tools = await client.list_tools()
    return [(t.name, t.description or "", t.inputSchema or {}) for t in tools]


def _check_tool(
    server_name: str, name: str, description: str, param_names: list[str]
) -> list[DescriptionIssue]:
    """Run heuristic checks against a single tool's description."""
    fq = f"{server_name}.{name}"
    issues: list[DescriptionIssue] = []

    if len(description) < _MIN_DESC_LENGTH:
        issues.append(
            DescriptionIssue(
                tool_name=fq,
                issue_type="too_vague",
                message=(
                    f"Description is only {len(description)} chars "
                    f"(minimum {_MIN_DESC_LENGTH}). "
                    "Add a sentence explaining what the tool does."
                ),
                score=max(0.0, len(description) / _MIN_DESC_LENGTH),
            )
        )

    if len(description) > _MAX_DESC_LENGTH:
        issues.append(
            DescriptionIssue(
                tool_name=fq,
                issue_type="too_long",
                message=(
                    f"Description is {len(description)} chars "
                    f"(maximum {_MAX_DESC_LENGTH}). "
                    "Trim to avoid wasting tokens in the system prompt."
                ),
                score=max(0.0, 1 - (len(description) - _MAX_DESC_LENGTH) / _MAX_DESC_LENGTH),
            )
        )

    if param_names and not any(p.lower() in description.lower() for p in param_names):
        issues.append(
            DescriptionIssue(
                tool_name=fq,
                issue_type="missing_parameters",
                message=(
                    f"Tool has parameters {param_names!r} but the description "
                    "doesn't mention any of them."
                ),
                score=0.0,
            )
        )

    if not _ERROR_PATTERNS.search(description):
        issues.append(
            DescriptionIssue(
                tool_name=fq,
                issue_type="missing_error_info",
                message="Description doesn't mention error cases or failure modes.",
                score=0.0,
            )
        )

    if not _RETURN_PATTERNS.search(description):
        issues.append(
            DescriptionIssue(
                tool_name=fq,
                issue_type="missing_return_info",
                message="Description doesn't describe what the tool returns.",
                score=0.0,
            )
        )

    return issues


def check_description_quality(server_module: str) -> list[DescriptionIssue]:
    """Score every tool description exposed by *server_module*.

    Checks include:

    - **too_vague** — description under 20 chars or non-specific.
    - **missing_parameters** — tool has params but description ignores them.
    - **missing_error_info** — no mention of error / failure behaviour.
    - **too_long** — description exceeds 500 chars (wastes tokens).
    - **missing_return_info** — no mention of what the tool returns.

    The function is synchronous; async internals are handled transparently.

    Args:
        server_module: Dotted Python import path to the MCP server module
            (e.g. ``"netbox_mcp.server"``).

    Returns:
        A list of issues found, empty if all descriptions pass.
    """
    server = _get_fastmcp_instance(server_module)
    tools = _run_async(lambda: _collect_tools(server))
    server_name = server.name

    issues: list[DescriptionIssue] = []
    for name, description, schema in tools:
        param_names = list(schema.get("properties", {}).keys())
        issues.extend(_check_tool(server_name, name, description, param_names))
    return issues


_SCORING_RUBRIC = """\
You are an expert evaluator of MCP (Model Context Protocol) tool descriptions.
Score the following tool description on four dimensions (each 0-10):

1. **Clarity**: Would an AI agent understand when to use this tool vs others?
2. **Completeness**: Does the description cover parameters, return format, and error cases?
3. **Conciseness**: Is the description efficient with tokens while being informative?
4. **Distinctiveness**: Could this description be confused with another tool's purpose?

Tool name: {tool_name}

Tool description:
{description}

Parameter schema:
{param_schema}

Respond with ONLY a JSON object (no markdown fences) matching this exact structure:
{{
  "tool_name": "{tool_name}",
  "clarity": <int 0-10>,
  "completeness": <int 0-10>,
  "conciseness": <int 0-10>,
  "distinctiveness": <int 0-10>,
  "overall_score": <float 0-10, average of the four scores>,
  "verdict": "<one of: good, needs_improvement, poor>",
  "suggested_improvement": "<concrete rewrite suggestion or empty string if good>",
  "explanation": "<1-2 sentence summary of strengths and weaknesses>"
}}

Verdict thresholds: overall_score >= 7 → "good", >= 4 → "needs_improvement", else "poor".
"""


def _build_llm_prompt(tool_name: str, description: str, param_schema: dict[str, Any]) -> str:
    schema_str = json.dumps(param_schema, indent=2) if param_schema else "(no parameters)"
    return _SCORING_RUBRIC.format(
        tool_name=tool_name,
        description=description or "(empty)",
        param_schema=schema_str,
    )


def _call_llm(
    client: Any,
    model: str,
    prompt: str,
) -> Any:
    """Call the LLM with exponential-backoff retry on transient errors."""
    import openai
    from tenacity import (
        retry,
        retry_if_exception_type,
        stop_after_attempt,
        wait_exponential,
    )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type(
            (
                openai.APITimeoutError,
                openai.APIConnectionError,
                openai.RateLimitError,
                openai.InternalServerError,
            )
        ),
        reraise=True,
    )
    def _inner() -> Any:
        return client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.0,
        )

    return _inner()


def _llm_evaluate_description(
    tool_name: str,
    description: str,
    param_schema: dict[str, Any],
    *,
    client: Any,
    model: str,
) -> LLMDescriptionScore | None:
    """Send tool metadata to an LLM judge and return a structured score.

    Returns ``None`` if the LLM response cannot be parsed.
    """
    prompt = _build_llm_prompt(tool_name, description, param_schema)
    response = _call_llm(client, model, prompt)

    try:
        raw = response.choices[0].message.content or "{}"
        data = json.loads(raw)
        data["tool_name"] = tool_name
        data["overall_score"] = (
            data.get("clarity", 0)
            + data.get("completeness", 0)
            + data.get("conciseness", 0)
            + data.get("distinctiveness", 0)
        ) / 4.0
        return LLMDescriptionScore.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as exc:
        _log.warning("LLM returned unparseable response for tool %s: %s", tool_name, exc)
        return None


def check_description_quality_llm(
    server_module: str,
    *,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> list[LLMDescriptionScore]:
    """Score every tool description using an LLM judge.

    Sends each tool's name, description, and parameter schema to a Together AI
    model for multi-dimensional quality evaluation.

    Args:
        server_module: Dotted Python import path to the MCP server module.
        model: Override the judge model (default: ``EVAL_JUDGE_MODEL`` env var
            or ``Qwen/Qwen3-235B-A22B-Instruct-2507-tput``).
        api_key: Override the API key (default: ``TOGETHER_API_KEY`` env var).
        base_url: Override the API base URL (default: Together AI endpoint).

    Returns:
        A list of :class:`LLMDescriptionScore` objects, one per tool.
    """
    from openai import OpenAI

    resolved_key = api_key or os.environ.get("TOGETHER_API_KEY", "")
    if not resolved_key:
        _log.warning(
            "TOGETHER_API_KEY not set — skipping LLM-based description scoring for %s",
            server_module,
        )
        return []

    resolved_model = model or os.environ.get("EVAL_JUDGE_MODEL", _DEFAULT_JUDGE_MODEL)
    server = _get_fastmcp_instance(server_module)
    tools = _run_async(lambda: _collect_tools(server))

    client = OpenAI(
        api_key=resolved_key,
        base_url=base_url or _TOGETHER_BASE_URL,
        timeout=60.0,
    )

    scores: list[LLMDescriptionScore] = []
    for name, description, schema in tools:
        fq = f"{server.name}.{name}"
        score = _llm_evaluate_description(
            fq,
            description,
            schema,
            client=client,
            model=resolved_model,
        )
        if score is not None:
            scores.append(score)
    return scores


def _collect_all_tools(
    server_modules: list[str],
) -> list[tuple[str, str, str]]:
    """Return ``(server_name, tool_name, description)`` across all servers."""

    async def _gather() -> list[tuple[str, str, str]]:
        results: list[tuple[str, str, str]] = []
        for module in server_modules:
            server = _get_fastmcp_instance(module)
            tools = await _collect_tools(server)
            for name, desc, _ in tools:
                results.append((server.name, name, desc))
        return results

    return _run_async(_gather)


def check_similarity_conflicts(
    server_modules: list[str],
) -> list[SimilarityConflict]:
    """Detect inter-server description conflicts.

    Compares tool descriptions across all *server_modules* to find pairs
    whose descriptions are similar enough that an agent might confuse them.

    Uses ``difflib.SequenceMatcher`` for fast, deterministic comparison
    (no LLM required).

    The function is synchronous; async internals are handled transparently.

    Args:
        server_modules: List of dotted Python import paths to MCP server
            modules to compare.

    Returns:
        A list of detected conflicts, empty if no problematic overlaps.
    """
    all_tools = _collect_all_tools(server_modules)
    conflicts: list[SimilarityConflict] = []

    for i, (srv_a, name_a, desc_a) in enumerate(all_tools):
        for srv_b, name_b, desc_b in all_tools[i + 1 :]:
            if srv_a == srv_b:
                continue
            if not desc_a or not desc_b:
                continue
            ratio = SequenceMatcher(None, desc_a.lower(), desc_b.lower()).ratio()
            if ratio > _SIMILARITY_THRESHOLD:
                conflicts.append(
                    SimilarityConflict(
                        tool_a=f"{srv_a}.{name_a}",
                        tool_b=f"{srv_b}.{name_b}",
                        similarity=round(ratio, 3),
                        score=round(ratio, 3),
                        explanation=(
                            f"Descriptions are {ratio:.0%} similar. "
                            "An agent may confuse these tools when both servers are loaded."
                        ),
                    )
                )

    return conflicts


# ---------------------------------------------------------------------------
# CI gate / CLI (#88 Phase 3a)
# ---------------------------------------------------------------------------
#
# A fast, token-free description-quality gate for every PR. Reuses the
# heuristic checks above + ``check_similarity_conflicts`` (inter-server
# collisions) and prints a compact report. The default failing set is the
# *clear correctness* signals (``too_vague`` + cross-server conflicts) so the
# gate is green on a healthy repo and trips on real regressions; the softer
# heuristics (``missing_error_info`` / ``missing_return_info`` / ``too_long``)
# are reported as advisory unless ``--strict`` widens the failing set.


# Issue types the gate can fail on. ``all`` is a synonym for every heuristic.
class _FailOn(StrEnum):
    too_vague = "too_vague"
    missing_parameters = "missing_parameters"
    missing_error_info = "missing_error_info"
    too_long = "too_long"
    missing_return_info = "missing_return_info"
    all = "all"


# The default failing set: the clearest correctness signals. Picked so a
# healthy repo is green (these are 0 today across netbox/awx/dc-support) while
# a real regression — an empty/vague description or two servers colliding —
# trips the gate. ``--strict`` (or ``--fail-on all``) widens it to everything.
_DEFAULT_FAIL_ON: tuple[str, ...] = ("too_vague",)


def _format_issue_line(tool: str, issue_type: str, message: str, score: float) -> str:
    return f"  [{issue_type}] {tool} (score={score:.2f}): {message}"


def run_description_qa(
    server_modules: list[str],
    *,
    fail_on: list[str] | None = None,
    strict: bool = False,
    similarity: bool = True,
    json_output: bool = False,
    out: Any = None,
) -> int:
    """Run the heuristic description-quality gate across *server_modules*.

    Heuristic only — no LLM, no tokens. For each server, runs
    :func:`check_description_quality`; across all servers, runs
    :func:`check_similarity_conflicts` (unless ``similarity=False``).

    Exit code is ``1`` when any issue in the *failing set* is found, else
    ``0``. The failing set is:

    - ``strict=True``  → every issue type + cross-server conflicts.
    - ``fail_on`` set  → the listed issue types (``"all"`` = every type),
      plus cross-server conflicts (when ``similarity=True``).
    - default          → ``too_vague`` + cross-server conflicts.

    Advisory findings outside the failing set are always reported but never
    flip the exit code, so the gate surfaces softer quality gaps without
    blocking PRs on pre-existing nits.

    Args:
        server_modules: Dotted import paths of the MCP server modules to check.
        fail_on: Issue types that fail the gate (``"all"`` widens to all).
        strict: Fail on every issue type (shorthand for ``fail_on=["all"]``).
        similarity: Run the cross-server similarity-conflict check.
        json_output: Emit a machine-readable JSON report instead of text.
        out: Output stream (defaults to :data:`sys.stdout`).

    Returns:
        ``1`` if the gate failed, ``0`` otherwise.
    """
    import sys

    stream = out if out is not None else sys.stdout

    all_issue_types = [t.value for t in _FailOn if t.value != "all"]
    if strict or (fail_on and "all" in fail_on):
        failing_types: set[str] = set(all_issue_types)
    elif fail_on is not None:
        # An explicit list (possibly empty) is honored as-is: ``[]`` means
        # advisory-only (no heuristic type blocks); ``None`` keeps the default.
        failing_types = {t for t in fail_on if t != "all"}
    else:
        failing_types = set(_DEFAULT_FAIL_ON)

    per_server: dict[str, list[DescriptionIssue]] = {}
    for module in server_modules:
        per_server[module] = check_description_quality(module)

    conflicts: list[SimilarityConflict] = (
        check_similarity_conflicts(server_modules) if similarity and len(server_modules) > 1 else []
    )

    blocking_issues: list[DescriptionIssue] = [
        issue
        for issues in per_server.values()
        for issue in issues
        if issue.issue_type in failing_types
    ]
    failed = bool(blocking_issues) or (similarity and bool(conflicts))

    if json_output:
        payload = {
            "servers": {
                module: [issue.model_dump() for issue in issues]
                for module, issues in per_server.items()
            },
            "similarity_conflicts": [c.model_dump() for c in conflicts],
            "failing_types": sorted(failing_types),
            "failed": failed,
        }
        stream.write(json.dumps(payload, indent=2, default=str) + "\n")
        return 1 if failed else 0

    total_issues = sum(len(v) for v in per_server.values())
    print(
        f"Description QA: {len(server_modules)} server(s), "
        f"{total_issues} heuristic issue(s), {len(conflicts)} cross-server conflict(s).",
        file=stream,
    )
    for module, issues in per_server.items():
        if not issues:
            print(f"  {module}: OK (no heuristic issues)", file=stream)
            continue
        by_type: dict[str, list[DescriptionIssue]] = {}
        for issue in issues:
            by_type.setdefault(issue.issue_type, []).append(issue)
        print(f"  {module}: {len(issues)} issue(s)", file=stream)
        for issue_type, grouped in sorted(by_type.items()):
            flag = "FAIL" if issue_type in failing_types else "warn"
            print(f"    [{flag}] {issue_type}: {len(grouped)}", file=stream)
            for issue in grouped:
                print(
                    _format_issue_line(
                        issue.tool_name, issue.issue_type, issue.message, issue.score
                    ),
                    file=stream,
                )
    if conflicts:
        label = "FAIL" if similarity else "warn"
        print(f"  [{label}] cross-server similarity conflicts: {len(conflicts)}", file=stream)
        for conflict in conflicts:
            print(
                f"    {conflict.tool_a} <-> {conflict.tool_b} "
                f"(similarity={conflict.similarity:.2f}): {conflict.explanation}",
                file=stream,
            )
    elif similarity and len(server_modules) > 1:
        print("  cross-server similarity: OK (no conflicts)", file=stream)

    failing_summary = ", ".join(sorted(failing_types)) or "(none)"
    print(
        f"\nFailing set: {failing_summary}"
        + (" + cross-server conflicts" if similarity else "")
        + ("  [strict]" if strict else ""),
        file=stream,
    )
    if failed:
        print(
            f"FAIL: {len(blocking_issues)} blocking issue(s)"
            + (f" + {len(conflicts)} conflict(s)" if conflicts else ""),
            file=stream,
        )
        return 1
    print("PASS", file=stream)
    return 0


def _build_description_qa_app() -> Any:
    """Build (and cache) the Typer app for the ``description-qa`` command."""
    app = typer.Typer(
        name="description-qa",
        help=(
            "Heuristic MCP tool-description quality gate (no LLM, no tokens). "
            "Checks each --server for description issues and, across all "
            "--server args, for inter-server description collisions."
        ),
        no_args_is_help=True,
        add_completion=False,
    )

    @app.command("description-qa")
    def description_qa(
        server: Annotated[
            list[str] | None,
            typer.Option(
                "--server",
                help="Dotted import path of an MCP server module (repeat for multiple).",
            ),
        ] = None,
        fail_on: Annotated[
            list[str] | None,
            typer.Option(
                "--fail-on",
                help="Issue type that fails the gate (repeat). 'all' widens to every type.",
            ),
        ] = None,
        strict: Annotated[
            bool,
            typer.Option(
                "--strict",
                help="Fail on every issue type + conflicts (shorthand for --fail-on all).",
            ),
        ] = False,
        no_similarity: Annotated[
            bool,
            typer.Option(
                "--no-similarity",
                help="Skip the cross-server similarity-conflict check.",
            ),
        ] = False,
        json_output: Annotated[
            bool,
            typer.Option(
                "--json",
                help="Emit a machine-readable JSON report instead of text.",
            ),
        ] = False,
    ) -> None:
        """Run the heuristic description-quality gate across one or more servers."""
        servers = server or []
        if not servers:
            # Typer requires --server, but guard anyway for direct callers/tests.
            raise typer.BadParameter("at least one --server is required")
        code = run_description_qa(
            servers,
            fail_on=fail_on,
            strict=strict,
            similarity=not no_similarity,
            json_output=json_output,
        )
        raise typer.Exit(code)

    return app


_qa_app = _build_description_qa_app()


def qa_app() -> Any:
    """Return the cached ``description-qa`` Typer app (for tests / direct invoke)."""
    return _qa_app


def qa_main(argv: list[str] | None = None) -> None:
    """Entry point for ``python -m mcp_common.testing.eval description-qa ...``."""
    _qa_app(argv)
