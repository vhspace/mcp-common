"""Inspect AI scorers for MCP server evaluations.

Scorers judge agent behaviour along several dimensions:

- **Tool selection** — did the agent pick the right tool(s)?
- **Task completion** — did the agent achieve the user's goal?
- **Interface choice** — did the agent prefer CLI when appropriate?
- **Interface parity** — do MCP and CLI paths produce equivalent results?
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import posixpath
import re
import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from inspect_ai.model import ChatMessageAssistant
from inspect_ai.scorer import (
    CORRECT,
    INCORRECT,
    PARTIAL,
    Score,
    Target,
    accuracy,
    scorer,
)
from inspect_ai.solver import TaskState

_log = logging.getLogger(__name__)

_DEFAULT_JUDGE_MODEL = "Qwen/Qwen3-235B-A22B-Instruct-2507-tput"
_TOGETHER_BASE_URL = "https://api.together.xyz/v1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_tool_calls(state: TaskState) -> list[dict[str, Any]]:
    """Pull tool-call records from the agent message history."""
    calls: list[dict[str, Any]] = []
    for msg in state.messages:
        if isinstance(msg, ChatMessageAssistant) and msg.tool_calls:
            for tc in msg.tool_calls:
                calls.append(
                    {
                        "id": tc.id,
                        "function": tc.function,
                        "arguments": tc.arguments,
                    }
                )
    return calls


def _get_final_response(state: TaskState) -> str:
    """Return the last assistant text in the conversation."""
    for msg in reversed(state.messages):
        if isinstance(msg, ChatMessageAssistant) and msg.content:
            if msg.text.strip():
                return msg.text.strip()
    return ""


def _compute_tool_selection_score(
    tools_called: list[str],
    expected_tools: list[str],
) -> float:
    """Fraction of expected tools that were actually called (handles duplicates)."""
    if not expected_tools:
        return 1.0
    remaining = list(tools_called)
    matched = 0
    for t in expected_tools:
        if t in remaining:
            remaining.remove(t)
            matched += 1
    return matched / len(expected_tools)


# ---------------------------------------------------------------------------
# CLI-aware tool-selection helpers
# ---------------------------------------------------------------------------
#
# In CLI eval mode the agent does not call MCP tools by name — it calls a
# ``bash``/``bash_session`` tool and runs ``<mcp>-cli <subcommand> ...`` inside
# it. The deterministic tool-selection check in :func:`tool_use_scorer` only
# sees ``bash`` and therefore scores tool selection ~0 regardless of whether
# the agent ran the right command (vhspace/mcp-common#59). The helpers below
# parse the bash command strings and map expected MCP tool names to the CLI
# subcommands the dual-mode framework derives from them.
#
# Naming note: the tool -> CLI-subcommand mapping below intentionally mirrors
# ``mcp_common.dual_mode._naming.derive_cli_name`` rather than importing it.
# Importing that helper transitively loads the whole dual-mode framework
# (fastmcp / typer / click) via ``mcp_common.dual_mode.__init__``, which the
# optional ``eval`` extra should not require just to kebab-case a string, and
# it lives in a private module with no public re-export. The duplicated logic
# is tiny and stable; a parity unit test asserts it stays in agreement with the
# canonical implementation so the convention can never silently drift.

_BASH_TOOL_NAMES: tuple[str, ...] = ("bash", "bash_session")
"""Default tool-call function names treated as shell invocations."""

# Argument keys that carry the command text across Inspect's shell tools:
# ``bash()`` uses ``command``; ``bash_session()`` uses ``input`` (with an
# ``action``); ``python()`` uses ``code``. ``cmd`` is accepted defensively.
_BASH_COMMAND_ARG_KEYS: tuple[str, ...] = ("cmd", "command", "input", "code")

# Shell control/redirection operators that terminate a command's argument list.
# A subcommand always immediately follows its binary, so hitting any of these
# while scanning forward means the binary had no subcommand (e.g. a bare
# ``netbox-cli`` piped into something).
_SHELL_OPERATORS: frozenset[str] = frozenset(
    {"&&", "||", ";", "|", "|&", "&", "(", ")", ">", ">>", "<", "<<", "\n"}
)


def _to_kebab_case(name: str) -> str:
    """Convert ``snake_case``/``camelCase``/``PascalCase`` to ``kebab-case``.

    Mirrors ``mcp_common.dual_mode._naming.to_kebab_case`` (see module note).
    """
    stripped = name.lstrip("_")
    with_dashes = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", stripped)
    return with_dashes.replace("_", "-").lower()


def _strip_mcp_namespace(tool_name: str, mcp_name: str) -> str:
    """Strip a FastMCP instance's name prefix from ``tool_name`` if present.

    ``("netbox_lookup_device", "netbox")`` -> ``"lookup_device"``. Matching
    tolerates kebab-/snake-case variants of ``mcp_name`` and is case-insensitive.
    Mirrors ``mcp_common.dual_mode._naming.strip_mcp_namespace``.
    """
    if not mcp_name:
        return tool_name
    normalized_mcp = mcp_name.lower().replace("-", "").replace("_", "")
    candidates = {
        mcp_name.lower(),
        mcp_name.lower().replace("-", "_"),
        mcp_name.lower().replace("_", "-"),
        normalized_mcp,
    }
    lowered = tool_name.lower()
    for candidate in candidates:
        for sep in ("_", "-"):
            prefix = f"{candidate}{sep}"
            if lowered.startswith(prefix) and len(tool_name) > len(prefix):
                return tool_name[len(prefix) :]
    return tool_name


def _derive_cli_subcommand(tool_name: str, mcp_name: str) -> str:
    """Map an MCP tool name to the CLI subcommand the dual-mode builder emits.

    ``("netbox_lookup_device", "netbox")`` -> ``"lookup-device"``. Mirrors
    ``mcp_common.dual_mode._naming.derive_cli_name``.
    """
    return _to_kebab_case(_strip_mcp_namespace(tool_name, mcp_name))


def _infer_mcp_name(cli_binary: str) -> str:
    """Infer the MCP namespace from a CLI binary name.

    ``"netbox-cli"`` -> ``"netbox"`` (strips a trailing ``-cli``/``_cli``).
    Falls back to the (basename of the) binary unchanged when no suffix matches.
    """
    base = posixpath.basename(cli_binary)
    for suffix in ("-cli", "_cli"):
        if base.endswith(suffix) and len(base) > len(suffix):
            return base[: -len(suffix)]
    return base


def _extract_bash_commands(
    state: TaskState,
    bash_tools: tuple[str, ...] = _BASH_TOOL_NAMES,
) -> list[str]:
    """Pull command strings out of every shell tool call in the transcript.

    Walks assistant messages for tool calls whose function name is in
    ``bash_tools`` and returns the first non-empty string argument found among
    :data:`_BASH_COMMAND_ARG_KEYS` (covering ``bash``/``bash_session``/``python``).
    """
    commands: list[str] = []
    for msg in state.messages:
        if isinstance(msg, ChatMessageAssistant) and msg.tool_calls:
            for tc in msg.tool_calls:
                if tc.function not in bash_tools:
                    continue
                args = tc.arguments or {}
                for key in _BASH_COMMAND_ARG_KEYS:
                    val = args.get(key)
                    if isinstance(val, str) and val.strip():
                        commands.append(val)
                        break
    return commands


def _tokenize_shell(command: str) -> list[str]:
    """Tokenize a shell command line, isolating ``&& || ; | ( )`` as tokens.

    Uses ``shlex`` in POSIX mode with ``punctuation_chars`` + ``whitespace_split``
    (the configuration the stdlib recommends for shell-like parsing). Falls back
    to a naive whitespace split when the input is malformed (e.g. unbalanced
    quotes), which raises ``ValueError`` in POSIX mode.
    """
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        return list(lexer)
    except ValueError:
        return command.split()


def _extract_cli_subcommands(command: str, cli_binary: str) -> list[str]:
    """Return the subcommand token(s) invoked for ``cli_binary`` in ``command``.

    Robust to ``uv run`` / env-var prefixes (``FOO=bar netbox-cli ...``), absolute
    or relative paths to the binary (matched by basename), global flags before
    the subcommand (``netbox-cli --json lookup-device``), and chained or piped
    invocations (each ``cli_binary`` occurrence contributes its subcommand). The
    subcommand is the first token after the binary that is neither a flag nor a
    shell operator.
    """
    tokens = _tokenize_shell(command)
    subcommands: list[str] = []
    for idx, tok in enumerate(tokens):
        if tok != cli_binary and posixpath.basename(tok) != cli_binary:
            continue
        for nxt in tokens[idx + 1 :]:
            if nxt in _SHELL_OPERATORS:
                break
            if nxt.startswith("-"):
                continue
            subcommands.append(nxt)
            break
    return subcommands


def _normalize_expected_command(cmd: str, cli_binary: str) -> str | None:
    """Reduce an ``expected_commands`` entry to its CLI subcommand token.

    Accepts both full invocations (``"netbox-cli devices --cluster"`` -> ``"devices"``)
    and bare subcommands (``"lookup-device"`` -> ``"lookup-device"``).
    """
    parsed = _extract_cli_subcommands(cmd, cli_binary)
    if parsed:
        return parsed[0]
    for tok in _tokenize_shell(cmd):
        if tok in _SHELL_OPERATORS or tok.startswith("-"):
            continue
        if tok == cli_binary or posixpath.basename(tok) == cli_binary:
            continue
        return tok
    return None


@dataclass(frozen=True)
class _ExpectedCliItem:
    """One expected tool-selection target for the CLI-aware scorer.

    Attributes:
        subcommands: CLI subcommands that satisfy this item — **any one** of
            them counts. A single MCP tool may map to multiple acceptable CLI
            subcommands (e.g. ``netbox_get_objects`` -> ``list`` / ``search`` /
            ``devices``), so this is a tuple rather than a single string.
        mcp_tool: Paired MCP tool name when the item was derived from an
            expected MCP tool (``None`` for explicit ``expected_commands``
            entries). Used for ``accept_mcp_names`` credit in combined evals.
    """

    subcommands: tuple[str, ...]
    mcp_tool: str | None


def _acceptable_subcommands(
    tool_name: str,
    mcp_name: str,
    tool_subcommands: Mapping[str, Sequence[str]] | None,
) -> tuple[str, ...]:
    """Acceptable CLI subcommand(s) for an expected MCP tool.

    Prefers an explicit declaration in ``tool_subcommands`` — the
    ``{tool_name: [cli_name, *cli_aliases]}`` mapping produced by
    :func:`mcp_common.dual_mode.tool_cli_subcommands` — so a tool whose real
    CLI subcommand differs from the derived kebab-name maps correctly and may
    cover MULTIPLE subcommands (``netbox_get_objects`` -> ``list`` / ``search``
    / ``devices``). Falls back to the dual-mode naming derivation
    (``netbox_get_objects`` -> ``get-objects``) when the tool is not declared,
    preserving the prior behavior for un-annotated tools.
    """
    if tool_subcommands is not None:
        declared = tool_subcommands.get(tool_name)
        if declared:
            subs = tuple(s for s in declared if s)
            if subs:
                return subs
    return (_derive_cli_subcommand(tool_name, mcp_name),)


def _build_expected_cli_items(
    target: Target,
    metadata: dict[str, Any] | None,
    cli_binary: str,
    mcp_name: str,
    tool_subcommands: Mapping[str, Sequence[str]] | None = None,
) -> list[_ExpectedCliItem]:
    """Build the list of expected CLI tool-selection items.

    When the scenario metadata carries an explicit ``expected_commands`` list it
    takes precedence (authoritative CLI spec); each entry is reduced to its
    subcommand and has no paired MCP tool name. Otherwise the expected MCP tool
    names (from the target) are mapped to their acceptable CLI subcommand(s) via
    :func:`_acceptable_subcommands` (declared ``cli_aliases`` first, else the
    dual-mode naming convention), keeping the MCP name paired so a combined eval
    can credit either form.
    """
    explicit = metadata.get("expected_commands") if metadata else None
    if explicit:
        items: list[_ExpectedCliItem] = []
        for cmd in explicit:
            if not isinstance(cmd, str):
                continue
            sub = _normalize_expected_command(cmd, cli_binary)
            if sub:
                items.append(_ExpectedCliItem(subcommands=(sub,), mcp_tool=None))
        return items
    expected_tools = _parse_expected_tools(target)
    return [
        _ExpectedCliItem(
            subcommands=_acceptable_subcommands(t, mcp_name, tool_subcommands),
            mcp_tool=t,
        )
        for t in expected_tools
    ]


def _compute_cli_tool_selection_score(
    expected_items: list[_ExpectedCliItem],
    invoked_subcommands: list[str],
    invoked_mcp_tools: list[str],
    accept_mcp_names: bool,
) -> float:
    """Fraction of expected items satisfied by the agent's actions.

    An expected item is credited when **any** of its acceptable CLI subcommands
    appears in the agent's bash invocations or — when ``accept_mcp_names`` is
    set and the item has a paired MCP tool name — when that MCP tool was called
    directly (relevant for a combined MCP+CLI eval). ``accept_mcp_names`` is
    ``False`` by default so a CLI-only run cannot be credited for a hallucinated
    MCP tool call that ran no CLI command (vhspace/mcp-common#133); combined
    evals opt in with ``accept_mcp_names=True``. Returns ``1.0`` when nothing is
    expected, matching :func:`_compute_tool_selection_score`.
    """
    if not expected_items:
        return 1.0
    invoked_sub = set(invoked_subcommands)
    invoked_mcp = set(invoked_mcp_tools)
    matched = 0
    for item in expected_items:
        ran_cli_subcommand = any(sub in invoked_sub for sub in item.subcommands)
        called_mcp_tool = (
            accept_mcp_names and item.mcp_tool is not None and item.mcp_tool in invoked_mcp
        )
        if ran_cli_subcommand or called_mcp_tool:
            matched += 1
    return matched / len(expected_items)


def _get_llm_client() -> tuple[Any, str] | None:
    """Build an OpenAI client pointed at Together AI. Returns ``None`` when creds are missing."""
    api_key = os.environ.get("TOGETHER_API_KEY", "")
    if not api_key:
        _log.warning("TOGETHER_API_KEY not set — LLM-as-judge scoring disabled")
        return None
    from openai import OpenAI

    model = os.environ.get("EVAL_JUDGE_MODEL", _DEFAULT_JUDGE_MODEL)
    client = OpenAI(
        api_key=api_key,
        base_url=_TOGETHER_BASE_URL,
        timeout=60.0,
    )
    return client, model


def _call_llm_judge(client: Any, model: str, prompt: str) -> str:
    """Call the LLM judge with retry.  Returns the response text."""
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
    def _inner() -> str:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        return resp.choices[0].message.content or "{}"

    return _inner()


_TASK_COMPLETION_PROMPT = """\
You are an expert evaluator.  Given the user's original request and the \
agent's final response, judge whether the agent successfully completed \
the task.

User request:
{user_input}

Expected behaviour:
{expected_behavior}

Agent response:
{agent_response}

Respond with ONLY a JSON object (no markdown fences):
{{
  "score": <float 0.0-1.0>,
  "explanation": "<1-2 sentences>"
}}

Score 1.0 = fully correct, 0.5 = partially correct, 0.0 = wrong/irrelevant.
"""

_PARITY_PROMPT = """\
You are an expert evaluator.  Two different agent runs attempted the same \
task — one using MCP tools and the other using CLI commands.  Judge whether \
their outputs are semantically equivalent.

User request:
{user_input}

Run A response:
{response_a}

Run B response:
{response_b}

Respond with ONLY a JSON object (no markdown fences):
{{
  "equivalent": <bool>,
  "score": <float 0.0-1.0>,
  "explanation": "<1-2 sentences explaining differences, if any>"
}}

Score 1.0 = identical meaning, 0.5 = mostly equivalent with minor gaps, \
0.0 = contradictory or very different.
"""

_INTERFACE_CHOICE_PROMPT = """\
You are an expert evaluator.  The agent had access to both MCP tools and \
CLI tools (via a bash/shell tool).  Given the tool calls below, judge \
whether the agent made appropriate interface choices.

Preferred rule: when a CLI equivalent exists, prefer CLI over MCP.

Tool calls made:
{tool_calls_json}

Respond with ONLY a JSON object (no markdown fences):
{{
  "score": <float 0.0-1.0>,
  "explanation": "<1-2 sentences>"
}}

Score 1.0 = all choices appropriate, 0.5 = some unnecessary MCP usage, \
0.0 = consistently chose MCP when CLI was available.
"""

_MISSING_API_KEY_MSG = (
    "TOGETHER_API_KEY is required for LLM-as-judge scoring. "
    "Set the environment variable or pass judge_model with a configured API key."
)


def _judge(
    client: Any,
    model: str,
    prompt: str,
) -> tuple[float | None, str]:
    """Call LLM judge with a prompt and return (score, explanation). Returns (None, reason) on failure."""
    raw = _call_llm_judge(client, model, prompt)
    try:
        data = json.loads(raw)
        score = max(0.0, min(1.0, float(data.get("score", 0.0))))
        explanation = str(data.get("explanation", ""))
        return score, explanation
    except (json.JSONDecodeError, TypeError, ValueError):
        _log.warning("Unparseable LLM judge response: %s", raw[:200])
        return None, "LLM judge returned unparseable response"


def _classify(tool_score: float, completion_score: float) -> str:
    """Map numeric sub-scores to CORRECT / PARTIAL / INCORRECT."""
    if tool_score >= 0.8 and completion_score >= 0.7:
        return CORRECT
    if tool_score >= 0.5 or completion_score >= 0.5:
        return PARTIAL
    return INCORRECT


def _parse_expected_tools(target: Target) -> list[str]:
    """Extract expected tool names from target text.

    Target text is a comma-separated list produced by the dataset builder,
    e.g. ``"get_device,list_ips"`` or just ``"get_device"``.
    """
    raw = target.text.strip()
    if not raw:
        return []
    return [t.strip() for t in raw.split(",") if t.strip()]


async def _score_base(
    state: TaskState,
    target: Target,
    client: Any,
    model: str,
) -> dict[str, Any]:
    """Shared scoring logic for tool_use and combined scorers."""
    tool_calls = _extract_tool_calls(state)
    tools_called = [tc["function"] for tc in tool_calls]
    expected_tools = _parse_expected_tools(target)
    tool_sel_score = _compute_tool_selection_score(tools_called, expected_tools)

    agent_response = _get_final_response(state)
    user_input = state.metadata.get("input", "") if state.metadata else ""
    expected_behavior = state.metadata.get("expected_behavior", "") if state.metadata else ""

    prompt = _TASK_COMPLETION_PROMPT.format(
        user_input=user_input,
        expected_behavior=expected_behavior or "(no specific expected behaviour provided)",
        agent_response=agent_response or "(no response)",
    )
    completion_score, completion_explanation = await asyncio.to_thread(
        _judge, client, model, prompt
    )

    return {
        "tool_sel_score": tool_sel_score,
        "completion_score": completion_score,
        "completion_explanation": completion_explanation,
        "tools_called": tools_called,
        "expected_tools": expected_tools,
        "tool_calls": tool_calls,
        "agent_response": agent_response,
        "user_input": user_input,
        "expected_behavior": expected_behavior,
    }


def _require_llm_client(judge_model: str | None) -> tuple[Any, str]:
    """Return (client, model) or raise RuntimeError if API key is missing."""
    llm = _get_llm_client()
    if llm is None:
        raise RuntimeError(_MISSING_API_KEY_MSG)
    client, model_name = llm
    if judge_model:
        model_name = judge_model
    return client, model_name


# ---------------------------------------------------------------------------
# Scorers
# ---------------------------------------------------------------------------


@scorer(metrics=[accuracy()])
def tool_use_scorer(judge_model: str | None = None):
    """Score agent tool usage: tool selection (deterministic) + task completion (LLM judge).

    Evaluates:
      1. **Tool selection** — correct tool(s) chosen from the available set.
      2. **Task completion** — the final output satisfies the user's request
         (assessed by an LLM judge via Together AI).

    The target text should be a comma-separated list of expected tool names.
    Scenario metadata (``expected_behavior``, ``input``) is read from
    ``state.metadata``.
    """

    async def score(state: TaskState, target: Target) -> Score:
        client, model_name = _require_llm_client(judge_model)

        base = await _score_base(state, target, client, model_name)

        completion_score = base["completion_score"]
        if completion_score is None:
            return Score(
                value=INCORRECT,
                answer=base["agent_response"],
                explanation=f"Scoring failed: {base['completion_explanation']}",
                metadata={
                    "tool_selection_score": base["tool_sel_score"],
                    "task_completion_score": None,
                    "tools_called": base["tools_called"],
                    "expected_tools": base["expected_tools"],
                },
            )

        value = _classify(base["tool_sel_score"], completion_score)
        explanation = (
            f"Tool selection: {base['tool_sel_score']:.2f} "
            f"(called {base['tools_called']}, expected {base['expected_tools']}). "
            f"Task completion: {completion_score:.2f} — {base['completion_explanation']}"
        )

        return Score(
            value=value,
            answer=base["agent_response"],
            explanation=explanation,
            metadata={
                "tool_selection_score": base["tool_sel_score"],
                "task_completion_score": completion_score,
                "tools_called": base["tools_called"],
                "expected_tools": base["expected_tools"],
            },
        )

    return score


@scorer(metrics=[accuracy()])
def combined_scorer(judge_model: str | None = None):
    """Extend :func:`tool_use_scorer` with interface-choice scoring.

    In addition to tool selection and task completion, this scorer checks
    whether the agent chose the appropriate interface (MCP tool call vs.
    CLI subprocess) when both were available.  The ``prefer-cli-over-mcp``
    rule says agents should prefer CLI when a CLI equivalent exists.
    """

    async def score(state: TaskState, target: Target) -> Score:
        client, model_name = _require_llm_client(judge_model)

        base = await _score_base(state, target, client, model_name)

        completion_score = base["completion_score"]

        interface_prompt = _INTERFACE_CHOICE_PROMPT.format(
            tool_calls_json=json.dumps(base["tool_calls"], indent=2),
        )
        interface_score, interface_explanation = await asyncio.to_thread(
            _judge, client, model_name, interface_prompt
        )

        if completion_score is None:
            return Score(
                value=INCORRECT,
                answer=base["agent_response"],
                explanation=f"Scoring failed: {base['completion_explanation']}",
                metadata={
                    "tool_selection_score": base["tool_sel_score"],
                    "task_completion_score": None,
                    "interface_choice_score": interface_score,
                    "tools_called": base["tools_called"],
                    "expected_tools": base["expected_tools"],
                },
            )

        if interface_score is None:
            interface_score_display = "N/A"
            interface_explanation = "Scoring failed: " + interface_explanation
        else:
            interface_score_display = f"{interface_score:.2f}"

        value = _classify(base["tool_sel_score"], completion_score)
        explanation = (
            f"Tool selection: {base['tool_sel_score']:.2f} "
            f"(called {base['tools_called']}, expected {base['expected_tools']}). "
            f"Task completion: {completion_score:.2f} — {base['completion_explanation']}. "
            f"Interface choice: {interface_score_display} — {interface_explanation}"
        )

        return Score(
            value=value,
            answer=base["agent_response"],
            explanation=explanation,
            metadata={
                "tool_selection_score": base["tool_sel_score"],
                "task_completion_score": completion_score,
                "interface_choice_score": interface_score,
                "tools_called": base["tools_called"],
                "expected_tools": base["expected_tools"],
            },
        )

    return score


@scorer(metrics=[accuracy()])
def cli_tool_use_scorer(
    judge_model: str | None = None,
    cli_binary: str = "netbox-cli",
    mcp_name: str | None = None,
    bash_tools: tuple[str, ...] = _BASH_TOOL_NAMES,
    accept_mcp_names: bool = False,
    tool_subcommands: Mapping[str, Sequence[str]] | None = None,
):
    """CLI-aware variant of :func:`tool_use_scorer` for ``bash``-driven evals.

    In CLI eval mode the agent answers by running ``<mcp>-cli <subcommand>``
    inside a ``bash``/``bash_session`` tool, so :func:`tool_use_scorer` (which
    matches MCP tool *names*) scores tool selection ~0 even when the right
    command was run, dragging accuracy down (vhspace/mcp-common#59). This scorer
    instead credits tool selection by inspecting the **bash command content**:

    1. **Tool selection (deterministic)** — extract every shell tool call's
       command string, parse out the ``cli_binary`` invocations (``shlex``-based;
       handles ``uv run``/env prefixes, absolute paths, global flags, and
       chained ``&&``/piped commands), and map the scenario's expected MCP tool
       names to their acceptable CLI subcommand(s). The mapping prefers an
       explicit ``tool_subcommands`` declaration (so ``netbox_get_objects`` can
       map to ``list`` / ``search`` / ``devices`` instead of the derived
       ``get-objects``, and one tool may satisfy MULTIPLE subcommands), and
       falls back to the dual-mode naming convention (``netbox_lookup_device``
       -> ``lookup-device``) for un-annotated tools. A scenario may instead
       carry an explicit ``expected_commands`` list in its metadata, which takes
       precedence over both.
    2. **Task completion (LLM judge)** — unchanged; reuses the existing judge
       path from :func:`tool_use_scorer`.

    Returns the same :class:`~inspect_ai.scorer.Score` shape as
    :func:`tool_use_scorer` (plus CLI-specific metadata) so CLI eval tasks can
    adopt it without other changes.

    Args:
        judge_model: Override the LLM judge model name.
        cli_binary: The CLI executable to look for in bash commands
            (e.g. ``"netbox-cli"``). Not netbox-specific — set it per MCP.
        mcp_name: MCP namespace stripped from expected tool names before
            kebab-casing. Defaults to ``cli_binary`` with a trailing
            ``-cli``/``_cli`` removed (``"netbox-cli"`` -> ``"netbox"``).
        bash_tools: Tool-call function names treated as shell invocations.
        accept_mcp_names: Also credit an expected tool when its MCP name was
            called *directly* (as a tool call rather than via the CLI). This is
            meaningful only for **combined** MCP+CLI evals where the MCP tools
            actually exist. It defaults to ``False`` (changed in
            vhspace/mcp-common#133): in a CLI-only eval the MCP tools are not
            available, so a model that *hallucinates* an MCP tool call and runs
            no CLI command would otherwise be spuriously credited tool-selection
            ``1.0``. Set ``accept_mcp_names=True`` for combined evals to restore
            crediting a direct MCP tool call.
        tool_subcommands: Optional ``{mcp_tool_name: [cli_subcommand, ...]}``
            mapping declaring the canonical CLI subcommand(s) / aliases for each
            MCP tool, normally built with
            :func:`mcp_common.dual_mode.tool_cli_subcommands` from the
            dual-mode tool definitions (``cli_name`` + ``cli_aliases``). Takes a
            plain mapping rather than importing the dual-mode framework so the
            ``eval`` extra stays dependency-light. Tools absent from the mapping
            fall back to the kebab-name derivation.
    """
    resolved_mcp_name = mcp_name if mcp_name is not None else _infer_mcp_name(cli_binary)

    async def score(state: TaskState, target: Target) -> Score:
        client, model_name = _require_llm_client(judge_model)

        base = await _score_base(state, target, client, model_name)

        bash_commands = _extract_bash_commands(state, bash_tools)
        invoked_subcommands: list[str] = []
        for command in bash_commands:
            invoked_subcommands.extend(_extract_cli_subcommands(command, cli_binary))

        invoked_mcp_tools = base["tools_called"]
        expected_items = _build_expected_cli_items(
            target, state.metadata, cli_binary, resolved_mcp_name, tool_subcommands
        )
        tool_sel_score = _compute_cli_tool_selection_score(
            expected_items, invoked_subcommands, invoked_mcp_tools, accept_mcp_names
        )

        expected_cli_commands: list[str] = []
        for item in expected_items:
            for sub in item.subcommands:
                if sub not in expected_cli_commands:
                    expected_cli_commands.append(sub)
        completion_score = base["completion_score"]

        cli_metadata = {
            "tool_selection_score": tool_sel_score,
            "task_completion_score": completion_score,
            "tools_called": invoked_mcp_tools,
            "expected_tools": base["expected_tools"],
            "cli_binary": cli_binary,
            "cli_commands_invoked": invoked_subcommands,
            "expected_cli_commands": expected_cli_commands,
        }

        if completion_score is None:
            return Score(
                value=INCORRECT,
                answer=base["agent_response"],
                explanation=f"Scoring failed: {base['completion_explanation']}",
                metadata={**cli_metadata, "task_completion_score": None},
            )

        value = _classify(tool_sel_score, completion_score)
        explanation = (
            f"CLI tool selection: {tool_sel_score:.2f} "
            f"(ran {cli_binary} {invoked_subcommands}, expected {expected_cli_commands}). "
            f"Task completion: {completion_score:.2f} — {base['completion_explanation']}"
        )

        return Score(
            value=value,
            answer=base["agent_response"],
            explanation=explanation,
            metadata=cli_metadata,
        )

    return score


@scorer(metrics=[accuracy()])
def parity_scorer(reference_log: str | None = None, judge_model: str | None = None):
    """Compare MCP and CLI execution paths for result equivalence.

    For each sample, finds the matching sample in a reference eval log
    (by input text) and uses an LLM judge to assess whether both runs
    produced semantically equivalent results.

    Args:
        reference_log: Path to a previous ``.eval`` log file (JSON lines).
            If ``None``, the scorer returns an incomplete score with an
            explanatory note.
        judge_model: Override the LLM judge model name.
    """

    async def score(state: TaskState, target: Target) -> Score:
        if not reference_log:
            return Score(
                value=INCORRECT,
                explanation="No reference_log provided — parity comparison skipped",
                metadata={"parity_score": 0.0},
            )

        reference_response = _load_reference_response(reference_log, state)
        if reference_response is None:
            return Score(
                value=INCORRECT,
                explanation="No matching sample found in reference log",
                metadata={"parity_score": 0.0},
            )

        client, model_name = _require_llm_client(judge_model)

        agent_response = _get_final_response(state)
        user_input = state.metadata.get("input", "") if state.metadata else ""

        prompt = _PARITY_PROMPT.format(
            user_input=user_input,
            response_a=agent_response or "(no response)",
            response_b=reference_response or "(no response)",
        )
        parity_score, parity_explanation = await asyncio.to_thread(
            _judge, client, model_name, prompt
        )

        if parity_score is None:
            return Score(
                value=INCORRECT,
                answer=agent_response,
                explanation=f"Scoring failed: {parity_explanation}",
                metadata={"parity_score": None},
            )

        if parity_score >= 0.8:
            value = CORRECT
        elif parity_score >= 0.5:
            value = PARTIAL
        else:
            value = INCORRECT

        return Score(
            value=value,
            answer=agent_response,
            explanation=f"Parity: {parity_score:.2f} — {parity_explanation}",
            metadata={
                "parity_score": parity_score,
                "reference_response": reference_response[:500],
            },
        )

    return score


def _load_reference_response(log_path: str, state: TaskState) -> str | None:
    """Find the matching sample in a reference eval log and return its final response.

    The log file is expected to be JSON lines where each line has at minimum
    ``{"input": "...", "response": "..."}``.  Falls back to scanning for
    a ``messages`` list if ``response`` is absent.
    """
    from pathlib import Path

    path = Path(log_path)
    if not path.exists():
        _log.warning("Reference log not found: %s", log_path)
        return None

    current_input = state.metadata.get("input", "") if state.metadata else ""
    if not current_input:
        for msg in state.messages:
            if hasattr(msg, "role") and msg.role == "user":
                current_input = msg.text if hasattr(msg, "text") else str(msg.content)
                break

    if not current_input:
        return None

    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("input", "").strip() == current_input.strip():
                if "response" in record:
                    return str(record["response"])
                messages = record.get("messages", [])
                for m in reversed(messages):
                    if m.get("role") == "assistant" and m.get("content", "").strip():
                        return str(m["content"]).strip()
    except OSError:
        _log.warning("Could not read reference log: %s", log_path)

    return None
