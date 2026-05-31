"""Tiered model registry for the netbox-mcp eval matrix.

This is a *declarative* registry — edit :data:`MODELS` to add/remove models or
flip them on/off. It carries no runtime/network logic; resolution against the
live Together catalog, env-var gating, and execution all live in
``run_matrix.py``.

Model-string format
-------------------
Inspect AI selects a provider by the prefix before the first ``/`` of the model
string. Together models use the ``together`` provider, so the string is
``together/<together-api-model>`` and inspect strips ``together/`` and sends the
remainder (e.g. ``Qwen/Qwen3.7-Max``) to ``https://api.together.xyz/v1``.
Confirmed against ``inspect_ai`` 0.3.211 (``inspect_ai.model._providers.together``).

Streaming-required models — the ``openai-api`` route
----------------------------------------------------
Some Together models *require* ``stream=true`` (the API 400s a non-stream
request with ``streaming_required``; e.g. ``Qwen/Qwen3.7-Max``). The inspect
``together/`` provider **cannot** stream in 0.3.211: ``TogetherAIAPI`` always
calls the non-streaming ``client.chat.completions.create()``, its constructor
rejects a ``stream`` arg, and ``GenerateConfig`` has no ``stream`` field. The
generic ``openai-api`` provider (``OpenAICompatibleAPI``) *does* honor a
``stream`` flag and streams via ``client.chat.completions.stream()``.

So streaming models use the ``openai-api/together/<slug>`` form:

- ``name = "openai-api/together/Qwen/Qwen3.7-Max"`` — the provider derives
  ``service="together"`` from the first path segment, auto-resolves
  ``TOGETHER_API_KEY``, and (because the generic provider has no built-in
  Together base URL) **requires ``TOGETHER_BASE_URL=https://api.together.xyz/v1``**.
  ``run_matrix.py`` exports that env var automatically.
- ``model_args = {"stream": True}`` — forwarded to ``inspect_ai.eval(model_args=...)``
  and delivered as the provider's ``stream`` constructor arg (NOT a
  ``GenerateConfig`` field).
- ``catalog_slug = "Qwen/Qwen3.7-Max"`` — the bare Together id used by the
  API-key / catalog gate, since ``together_api_model("openai-api/...")`` is ``None``.

Confirmed against ``inspect_ai`` 0.3.211 source + live Together API probes
(``Qwen/Qwen3.7-Max`` non-stream -> HTTP 400 ``streaming_required``;
``stream:true`` with tools -> 200).

The LLM-as-judge in ``mcp_common.testing.eval.scorers`` is *separate* from the
model under test: it always talks to Together and reads the bare model slug from
the ``EVAL_JUDGE_MODEL`` env var (default ``Qwen/Qwen3-235B-A22B-Instruct-2507-tput``).
:data:`JUDGE_MODEL` is stored in inspect ``together/...`` form for consistency;
``run_matrix.py`` strips the prefix when exporting ``EVAL_JUDGE_MODEL``.
"""

from __future__ import annotations

from dataclasses import dataclass

TIERS: tuple[str, ...] = ("fast", "medium", "high")


@dataclass(frozen=True)
class EvalModel:
    """A single model entry in the eval matrix.

    Attributes:
        name: Inspect model string, e.g. ``"together/Qwen/Qwen3.7-Max"``. For
            models that must stream (see ``catalog_slug``/``model_args``) this is
            the generic ``openai-api/together/<slug>`` form instead.
        tier: One of ``"fast"`` | ``"medium"`` | ``"high"`` (cost/capability).
        open_weights: ``True`` for open-weights models served via Together.
        requires_env: Env var that must be set for the model to be usable
            (e.g. ``"OPENAI_API_KEY"``). ``None`` means no extra key beyond the
            Together key that open-weights models already rely on.
        enabled: ``False`` parks a model in the registry without running it.
        note: Free-text caveat shown in plans/skips (e.g. why it's disabled, or
            that the slug is not yet in the live Together catalog).
        model_args: Extra provider-constructor args forwarded verbatim to
            ``inspect_ai.eval(model_args=...)``. Used to pass ``{"stream": True}``
            to the generic ``openai-api`` provider for models that *require*
            streaming (the ``together/`` provider in inspect 0.3.211 cannot
            stream — see the module docstring). ``None`` means no extra args.
        catalog_slug: Bare Together API slug (e.g. ``"Qwen/Qwen3.7-Max"``) for
            the catalog / ``TOGETHER_API_KEY`` gate when ``name`` is *not* a
            ``together/...`` string. ``together_api_model("openai-api/...")``
            returns ``None``, so without this the runner couldn't resolve the
            slug to apply the Together API-key check. ``None`` for plain
            ``together/...`` names (the slug is derived from ``name``).
    """

    name: str
    tier: str
    open_weights: bool
    requires_env: str | None = None
    enabled: bool = True
    note: str = ""
    model_args: dict | None = None
    catalog_slug: str | None = None


# ---------------------------------------------------------------------------
# The registry.  Edit this list to change what the matrix runs.
# ---------------------------------------------------------------------------
MODELS: list[EvalModel] = [
    # --- fast / cheap (open-weights, Together) -----------------------------
    EvalModel("together/Qwen/Qwen3.5-9B", "fast", True),
    EvalModel("together/meta-llama/Llama-3.3-70B-Instruct-Turbo", "fast", True),
    # --- medium (open-weights, Together) -----------------------------------
    EvalModel("together/Qwen/Qwen3-235B-A22B-Instruct-2507-tput", "medium", True),
    EvalModel("together/deepseek-ai/DeepSeek-V4-Pro", "medium", True),
    EvalModel("together/MiniMaxAI/MiniMax-M2.7", "medium", True),
    # --- high (open-weights, Together) -------------------------------------
    # Qwen3.7-Max *requires* streaming (non-stream -> HTTP 400 streaming_required).
    # The together/ provider can't stream in inspect 0.3.211, so route via the
    # generic openai-api provider with stream=True + TOGETHER_BASE_URL (set by
    # run_matrix.py). See the module docstring for the full rationale.
    EvalModel(
        "openai-api/together/Qwen/Qwen3.7-Max",
        "high",
        True,
        catalog_slug="Qwen/Qwen3.7-Max",
        model_args={"stream": True},
        note="streaming_required; together/ provider can't stream in inspect 0.3.211 — openai-api route + TOGETHER_BASE_URL",
    ),
    # Serverless Kimi-2.5-class model with tool calls (live-confirmed serverless).
    # Replaces the non-serverless Kimi-K2.5-fp4 (kept disabled below for reference).
    EvalModel(
        "together/moonshotai/Kimi-K2.6",
        "high",
        True,
        note="serverless Kimi-2.5-class replacement for non-serverless Kimi-K2.5-fp4 (live-confirmed serverless + tools)",
    ),
    # Exact Kimi-K2.5-fp4 — disabled: confirmed NON-serverless (live: 400
    # model_not_available). Needs a dedicated endpoint (per-minute GPU pricing),
    # so it's documented here but not run by default.
    EvalModel(
        "together/moonshotai/Kimi-K2.5-fp4",
        "high",
        True,
        enabled=False,
        note="non-serverless (400 model_not_available); requires a dedicated Together endpoint (per-minute GPU pricing) — see Kimi-K2.6 for the serverless swap",
    ),
    # --- high (closed) — skip unless the matching key is present -----------
    EvalModel("openai/gpt-5.5", "high", False, requires_env="OPENAI_API_KEY"),
    EvalModel("anthropic/claude-opus-4-8", "high", False, requires_env="ANTHROPIC_API_KEY"),
    # --- agentic — NOT wired (needs a Cursor SDK bridge); disabled ---------
    EvalModel(
        "cursor/composer-2.5",
        "high",
        False,
        enabled=False,
        note=(
            "Cursor SDK is agentic, not an inspect chat provider; needs a custom bridge (deferred)"
        ),
    ),
]

# Fixed judge for cross-model comparability (inspect ``together/...`` form).
# Matches mcp_common's default judge so scores are consistent with prior runs.
JUDGE_MODEL = "together/Qwen/Qwen3-235B-A22B-Instruct-2507-tput"


# ---------------------------------------------------------------------------
# Small pure helpers (no network / no env access)
# ---------------------------------------------------------------------------
def provider_of(name: str) -> str:
    """Return the inspect provider prefix (segment before the first ``/``)."""
    return name.split("/", 1)[0]


def together_api_model(name: str) -> str | None:
    """Return the bare Together API model string for a ``together/...`` name.

    ``"together/Qwen/Qwen3.7-Max"`` -> ``"Qwen/Qwen3.7-Max"``. Returns ``None``
    for non-Together model strings.
    """
    prefix = "together/"
    return name[len(prefix) :] if name.startswith(prefix) else None


def judge_api_string(judge: str = JUDGE_MODEL) -> str:
    """Bare slug to export as ``EVAL_JUDGE_MODEL`` (the scorer is Together-only).

    Strips a leading ``together/`` if present; otherwise returns the string
    unchanged (the caller should warn — the judge always runs on Together).
    """
    return together_api_model(judge) or judge


def models_for_tier(tier: str) -> list[EvalModel]:
    """All registered models for ``tier`` (``"all"`` returns every model)."""
    if tier == "all":
        return list(MODELS)
    return [m for m in MODELS if m.tier == tier]
