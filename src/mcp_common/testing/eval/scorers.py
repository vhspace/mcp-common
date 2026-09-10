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
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from inspect_ai.model import ChatMessageAssistant, ChatMessageTool
from inspect_ai.scorer import (
    CORRECT,
    INCORRECT,
    PARTIAL,
    Score,
    Scorer,
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


def _extract_tool_outputs(state: TaskState) -> list[str]:
    """Collect the text of every tool result in the transcript.

    Returns the non-empty ``ChatMessageTool`` texts in order — the underlying
    data the agent's final response is built from. Used by the DeepEval
    quality scorers as the faithfulness ``retrieval_context`` / hallucination
    ``context`` (the ground-truth the output is checked against).
    """
    outputs: list[str] = []
    for msg in state.messages:
        if isinstance(msg, ChatMessageTool):
            text = msg.text.strip() if msg.text else ""
            if text:
                outputs.append(text)
    return outputs


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
    """Build an OpenAI-compatible client for the LLM-as-judge.

    The judge can be pointed at a **separate** credential and endpoint from the
    model under test so its calls don't contend with the model's rate-limit
    budget (vhspace/mcp-common#132) — which is what lets a runner raise
    ``max_connections`` once the judge is on its own budget. Each piece is
    resolved independently:

    - **API key** — ``EVAL_JUDGE_API_KEY`` if set, else ``TOGETHER_API_KEY``.
    - **Base URL** — ``EVAL_JUDGE_BASE_URL`` if set, else the default Together
      endpoint (``https://api.together.xyz/v1``).
    - **Model** — ``EVAL_JUDGE_MODEL`` if set, else the built-in default.

    With none of the ``EVAL_JUDGE_*`` overrides set the behaviour is identical
    to the prior ``TOGETHER_API_KEY`` + default-endpoint client. Returns
    ``None`` when no API key is available at all (judge scoring disabled).
    """
    api_key = os.environ.get("EVAL_JUDGE_API_KEY") or os.environ.get("TOGETHER_API_KEY", "")
    if not api_key:
        _log.warning(
            "Neither EVAL_JUDGE_API_KEY nor TOGETHER_API_KEY is set — LLM-as-judge scoring disabled"
        )
        return None
    from openai import OpenAI

    base_url = os.environ.get("EVAL_JUDGE_BASE_URL") or _TOGETHER_BASE_URL
    model = os.environ.get("EVAL_JUDGE_MODEL", _DEFAULT_JUDGE_MODEL)
    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=60.0,
    )
    return client, model


# ---------------------------------------------------------------------------
# Rate-limit-aware retry backoff
# ---------------------------------------------------------------------------
#
# The LLM-as-judge is the serial bottleneck of a CLI eval sweep: it shares a
# rate budget with the model under test and, on a 429, used to back off blind
# with ``wait_exponential`` (vhspace/mcp-common#132, vhspace/netbox-mcp#120).
# Together returns ``x-ratelimit-reset`` (the suggested retry interval) on a
# 429 and the OpenAI client attaches the raw response headers to the error
# (``err.response.headers``), so we can wait exactly as long as the server asks
# instead of guessing.

_MAX_JUDGE_RETRY_WAIT_SECONDS = 60.0
"""Cap (seconds) for a header-derived 429 backoff, so a bogus/huge header
value can't stall the run."""

# Reset headers honored on a 429, in priority order. ``Retry-After`` (RFC 9110:
# delta-seconds or an HTTP-date) is the explicit directive; Together's
# ``x-ratelimit-reset`` reports the suggested retry interval for the model.
_RATE_LIMIT_RESET_HEADERS: tuple[str, ...] = ("retry-after", "x-ratelimit-reset")

# Numeric reset values larger than this are treated as an absolute Unix epoch
# timestamp rather than a delta. ``X-RateLimit-Reset`` is famously ambiguous
# (GitHub/Stripe send epoch seconds, others send seconds-until-reset); no real
# "seconds to wait" delta approaches this bound, so the split is unambiguous.
_EPOCH_RESET_THRESHOLD_SECONDS = 1_000_000_000.0


def _parse_http_date_seconds(value: str) -> float | None:
    """Seconds until an HTTP-date (the RFC 9110 ``Retry-After`` form), or ``None``."""
    from datetime import UTC, datetime
    from email.utils import parsedate_to_datetime

    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def _parse_reset_header_value(value: str) -> float | None:
    """Parse a rate-limit reset / ``Retry-After`` header value into seconds to wait.

    Handles the encodings seen from Together and the wider ecosystem:

    - **delta seconds** — ``"12"`` / ``"1.5"`` (Together ``x-ratelimit-reset`` and
      the usual ``Retry-After`` form);
    - **absolute Unix epoch** — ``"1771524477"`` (waits until that instant);
    - **HTTP-date** — the RFC 9110 alternate ``Retry-After`` form.

    Returns the non-negative seconds to wait, or ``None`` when the value is
    empty or uninterpretable.
    """
    text = value.strip()
    if not text:
        return None
    try:
        seconds = float(text)
    except ValueError:
        return _parse_http_date_seconds(text)
    if seconds > _EPOCH_RESET_THRESHOLD_SECONDS:
        seconds -= time.time()
    return max(0.0, seconds)


def _retry_after_seconds(exc: object) -> float | None:
    """Seconds to wait derived from a 429's rate-limit headers, or ``None``.

    Reads ``exc.response.headers`` — the OpenAI client attaches the raw ``httpx``
    response to ``APIStatusError`` subclasses such as ``RateLimitError`` — and
    returns the first parseable value among :data:`_RATE_LIMIT_RESET_HEADERS`.
    Tolerant of a missing ``response``/``headers`` and of a plain ``dict`` of
    headers (used in tests) in addition to the case-insensitive ``httpx.Headers``.
    """
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        normalized = {str(k).lower(): v for k, v in headers.items()}
    except AttributeError:
        return None
    for name in _RATE_LIMIT_RESET_HEADERS:
        raw = normalized.get(name)
        if raw is None:
            continue
        seconds = _parse_reset_header_value(str(raw))
        if seconds is not None:
            return seconds
    return None


def _make_judge_wait(
    rate_limit_errors: tuple[type[BaseException], ...],
    max_wait: float = _MAX_JUDGE_RETRY_WAIT_SECONDS,
) -> Callable[[Any], float]:
    """Build a ``tenacity`` wait callable that honors 429 reset headers.

    On a rate-limit error (an instance of ``rate_limit_errors``) carrying a
    usable ``Retry-After`` / ``x-ratelimit-reset`` header, the wait is taken
    from that header and capped at ``max_wait``. Every other retryable error —
    and a 429 with no usable header — falls back to the original exponential
    backoff (max 30s), so behaviour is unchanged when no header is present.
    """
    from tenacity import wait_exponential

    exponential = wait_exponential(multiplier=1, min=2, max=30)

    def _wait(retry_state: Any) -> float:
        fallback: float = exponential(retry_state)
        outcome = getattr(retry_state, "outcome", None)
        if outcome is None or not outcome.failed:
            return fallback
        exc = outcome.exception()
        if not isinstance(exc, rate_limit_errors):
            return fallback
        header_wait = _retry_after_seconds(exc)
        if header_wait is None:
            return fallback
        return min(header_wait, max_wait)

    return _wait


# ---------------------------------------------------------------------------
# Provider-aware structured-output ("JSON mode") capability
# ---------------------------------------------------------------------------
#
# The judge enforces JSON replies with ``response_format={"type":
# "json_object"}``. Together and OpenAI support that field, but Anthropic's
# OpenAI-compatibility endpoint (``https://api.anthropic.com/v1/``) does not:
# its compat layer documents ``response_format`` as *ignored* (and sending it
# was disruptive enough that the netbox-mcp judge runs vhspace/netbox-mcp#137
# and #140 had to strip it at runtime as a shim). Rather than push that shim
# onto every caller, the judge omits ``response_format`` automatically for
# endpoints known not to support it and keeps it everywhere else — so a native
# Anthropic judge (``EVAL_JUDGE_BASE_URL=https://api.anthropic.com/v1/``, #132)
# works with no per-run patching. The score is parsed from the response *text*
# in :func:`_judge` (``json.loads`` of a prompt that already mandates a bare
# JSON object), so dropping the field never changes how the score is extracted.
#
# Detection is host-based (not a whole-URL substring match) against a small
# capability denylist, so an unrelated ``anthropic`` token in a path or query
# string can't trip it, and any unknown/other provider defaults to "supported"
# — preserving the historical Together/OpenAI behaviour.

_JSON_OBJECT_UNSUPPORTED_HOSTS: frozenset[str] = frozenset({"anthropic.com"})
"""Judge-endpoint hostnames whose OpenAI-compatible API does **not** accept
``response_format={"type": "json_object"}``. Compared by host suffix so
``api.anthropic.com`` and any ``*.anthropic.com`` are covered."""


def _judge_base_url(client: Any) -> str:
    """Best-effort judge endpoint URL used for provider capability detection.

    Prefers the OpenAI client's own ``base_url`` (an ``httpx.URL``, stringified)
    — the authoritative endpoint the request will hit, itself derived from
    ``EVAL_JUDGE_BASE_URL`` in :func:`_get_llm_client` — and falls back to
    ``EVAL_JUDGE_BASE_URL`` / the Together default when it can't be read (e.g. a
    bare test double whose ``base_url`` isn't a URL).
    """
    base = getattr(client, "base_url", None)
    if base is not None:
        text = str(base).strip()
        if text:
            return text
    return os.environ.get("EVAL_JUDGE_BASE_URL") or _TOGETHER_BASE_URL


def _supports_json_object_response_format(base_url: str) -> bool:
    """Whether ``base_url`` accepts ``response_format={"type": "json_object"}``.

    Returns ``False`` for hosts in :data:`_JSON_OBJECT_UNSUPPORTED_HOSTS`
    (Anthropic), matched by hostname **suffix** so ``api.anthropic.com`` counts;
    every other or unrecognized endpoint (including a URL with no parseable
    host) defaults to ``True`` to preserve the prior Together/OpenAI behaviour.
    """
    host = (urlsplit(base_url).hostname or "").lower()
    if not host:
        return True
    return not any(
        host == bad or host.endswith(f".{bad}") for bad in _JSON_OBJECT_UNSUPPORTED_HOSTS
    )


def _call_llm_judge(client: Any, model: str, prompt: str) -> str:
    """Call the LLM judge with retry.  Returns the response text.

    The request enforces JSON output with ``response_format={"type":
    "json_object"}`` **only when the judge endpoint supports it**. Together and
    OpenAI honor that field; Anthropic's OpenAI-compatibility endpoint does not
    (it lists ``response_format`` as ignored, and the netbox-mcp judge runs
    vhspace/netbox-mcp#137 / #140 had to strip it at runtime), so for
    Anthropic-style hosts the field is omitted automatically and a native
    Anthropic judge needs no runtime shim. Provider detection is host-based off
    the client's ``base_url`` (derived from ``EVAL_JUDGE_BASE_URL``); see
    :func:`_supports_json_object_response_format`. The judge prompts already
    mandate a bare JSON object and the score is parsed from the response text in
    :func:`_judge`, so omitting the field does not change score extraction.

    Retries transient OpenAI/Together failures. On a 429 ``RateLimitError`` the
    backoff honors the response's ``Retry-After`` / ``x-ratelimit-reset`` header
    (see :func:`_make_judge_wait`); other retryable errors use exponential
    backoff.
    """
    import openai
    from tenacity import (
        retry,
        retry_if_exception_type,
        stop_after_attempt,
    )

    request: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
    }
    if _supports_json_object_response_format(_judge_base_url(client)):
        request["response_format"] = {"type": "json_object"}

    @retry(
        stop=stop_after_attempt(3),
        wait=_make_judge_wait((openai.RateLimitError,)),
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
        resp = client.chat.completions.create(**request)
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
    "An API key is required for LLM-as-judge scoring. Set EVAL_JUDGE_API_KEY "
    "(to point the judge at a dedicated key/endpoint) or TOGETHER_API_KEY, or "
    "pass judge_model with a configured API key."
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
         (assessed by an OpenAI-compatible LLM judge).

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


# ---------------------------------------------------------------------------
# DeepEval quality scorers (vhspace/mcp-common#61)
# ---------------------------------------------------------------------------
#
# The scorers above check *structural* correctness (right tool / right CLI
# command / task complete). The three scorers below add a *semantic* output-
# quality layer using DeepEval's peer-reviewed metrics — faithfulness,
# hallucination, answer relevancy — to judge the natural-language response
# itself. They complement, and do not replace, the existing scorers.
#
# DeepEval is an OPTIONAL dependency (the ``eval-scoring`` extra). All DeepEval
# imports are deferred into :mod:`mcp_common.testing.eval.deepeval_backend`,
# which is itself imported lazily inside each scorer's ``score`` coroutine — so
# importing this module (and the whole eval package) never requires DeepEval.
# Running one of these scorers without the extra raises a clear
# ``DeepEvalUnavailableError`` with an install hint. The judge LLM is the same
# Together-backed (``EVAL_JUDGE_*``-aware) client the other scorers use.


def _deepeval_to_score(result: Any, answer: str) -> Score:
    """Map a :class:`~mcp_common.testing.eval.deepeval_backend.DeepEvalResult` to an Inspect ``Score``.

    DeepEval metrics are threshold pass/fail, so the value is CORRECT when the
    metric passed and INCORRECT otherwise; ``result.success`` already encodes
    each metric's direction (e.g. *lower* hallucination is a pass). The raw
    score, threshold, and verdict are preserved in metadata under a
    metric-specific key (``<metric>_score``) for downstream analysis.
    """
    value = CORRECT if result.success else INCORRECT
    verdict = "pass" if result.success else "fail"
    return Score(
        value=value,
        answer=answer,
        explanation=(
            f"{result.metric.capitalize()}: {result.score:.2f} "
            f"(threshold {result.threshold:.2f}, {verdict}) — {result.reason}"
        ),
        metadata={
            f"{result.metric}_score": result.score,
            "deepeval_metric": result.metric,
            "threshold": result.threshold,
            "success": result.success,
        },
    )


def _no_response_score(metric: str) -> Score:
    """INCORRECT score for an empty agent response (nothing to quality-check)."""
    return Score(
        value=INCORRECT,
        answer="",
        explanation=f"{metric.capitalize()}: no agent response to score",
        metadata={f"{metric}_score": None, "deepeval_metric": metric},
    )


def _no_context_score(metric: str, answer: str) -> Score:
    """INCORRECT score when there are no tool outputs to check the response against."""
    return Score(
        value=INCORRECT,
        answer=answer,
        explanation=(f"{metric.capitalize()}: no tool outputs to check the response against"),
        metadata={f"{metric}_score": None, "deepeval_metric": metric},
    )


@scorer(metrics=[accuracy()])
def faithfulness_scorer(judge_model: str | None = None, threshold: float = 0.5) -> Scorer:
    """Score how faithfully the agent's response represents the tool outputs.

    DeepEval ``FaithfulnessMetric``: penalizes claims in the final response that
    are not supported by the tool results the agent saw (the ``retrieval_context``).
    Requires the ``eval-scoring`` extra; the judge is the Together-backed LLM
    client shared with the other scorers.

    Args:
        judge_model: Override the judge model name.
        threshold: Minimum faithfulness score (0..1) to count as a pass.
    """

    async def score(state: TaskState, target: Target) -> Score:
        client, model_name = _require_llm_client(judge_model)
        from mcp_common.testing.eval import deepeval_backend as _de

        answer = _get_final_response(state)
        if not answer:
            return _no_response_score("faithfulness")
        retrieval_context = _extract_tool_outputs(state)
        if not retrieval_context:
            return _no_context_score("faithfulness", answer)
        user_input = state.metadata.get("input", "") if state.metadata else ""

        result = await asyncio.to_thread(
            _de.score_faithfulness,
            client,
            model_name,
            input=user_input,
            actual_output=answer,
            retrieval_context=retrieval_context,
            threshold=threshold,
        )
        return _deepeval_to_score(result, answer)

    return score


@scorer(metrics=[accuracy()])
def hallucination_scorer(judge_model: str | None = None, threshold: float = 0.5) -> Scorer:
    """Score whether the agent fabricated information absent from the tool outputs.

    DeepEval ``HallucinationMetric``: a *lower* score is better, and the pass/fail
    verdict already accounts for that direction. The agent's tool outputs are the
    factual ``context`` the response is checked against. Requires the
    ``eval-scoring`` extra.

    Args:
        judge_model: Override the judge model name.
        threshold: Maximum hallucination score (0..1) to count as a pass.
    """

    async def score(state: TaskState, target: Target) -> Score:
        client, model_name = _require_llm_client(judge_model)
        from mcp_common.testing.eval import deepeval_backend as _de

        answer = _get_final_response(state)
        if not answer:
            return _no_response_score("hallucination")
        context = _extract_tool_outputs(state)
        if not context:
            return _no_context_score("hallucination", answer)
        user_input = state.metadata.get("input", "") if state.metadata else ""

        result = await asyncio.to_thread(
            _de.score_hallucination,
            client,
            model_name,
            input=user_input,
            actual_output=answer,
            context=context,
            threshold=threshold,
        )
        return _deepeval_to_score(result, answer)

    return score


@scorer(metrics=[accuracy()])
def relevancy_scorer(judge_model: str | None = None, threshold: float = 0.5) -> Scorer:
    """Score whether the agent's response is relevant to the user's request.

    DeepEval ``AnswerRelevancyMetric``: needs no tool-output context — just the
    user request and the final response. Requires the ``eval-scoring`` extra.

    Args:
        judge_model: Override the judge model name.
        threshold: Minimum relevancy score (0..1) to count as a pass.
    """

    async def score(state: TaskState, target: Target) -> Score:
        client, model_name = _require_llm_client(judge_model)
        from mcp_common.testing.eval import deepeval_backend as _de

        answer = _get_final_response(state)
        if not answer:
            return _no_response_score("relevancy")
        user_input = state.metadata.get("input", "") if state.metadata else ""

        result = await asyncio.to_thread(
            _de.score_answer_relevancy,
            client,
            model_name,
            input=user_input,
            actual_output=answer,
            threshold=threshold,
        )
        return _deepeval_to_score(result, answer)

    return score
