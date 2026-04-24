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
