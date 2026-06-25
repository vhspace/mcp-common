"""Tiered model registry for the CLI-discovery eval matrix.

Declarative registry — edit :data:`MODELS` to add/remove models. Resolution,
env gating, and execution live in ``run_matrix.py`` via the shared matrix runner.

The primary model under test is ``together/moonshotai/Kimi-K2.7-Code`` — the
latest kimi model, the same one Hermes runs (togethercomputer/mcp-common#95).
It sits in the ``high`` tier (capable frontier code model). The judge is the
existing default Together judge.
"""

from __future__ import annotations

from dataclasses import dataclass

TIERS: tuple[str, ...] = ("fast", "medium", "high")


@dataclass(frozen=True)
class EvalModel:
    name: str
    tier: str
    open_weights: bool
    requires_env: str | None = None
    enabled: bool = True
    note: str = ""
    model_args: dict | None = None
    catalog_slug: str | None = None


MODELS: list[EvalModel] = [
    # Primary model under test — the kimi model Hermes uses (#95).
    EvalModel(
        "together/moonshotai/Kimi-K2.7-Code",
        "high",
        True,
        note="primary model under test — same kimi model Hermes runs",
    ),
    EvalModel("together/Qwen/Qwen3-235B-A22B-Instruct-2507-tput", "medium", True),
    EvalModel("together/Qwen/Qwen3.5-9B", "fast", True),
    EvalModel("together/meta-llama/Llama-3.3-70B-Instruct-Turbo", "fast", True),
    EvalModel(
        "anthropic/claude-haiku-4-5-20251001",
        "fast",
        False,
        requires_env="ANTHROPIC_API_KEY",
        note="Anthropic frontier coverage at the fast tier",
    ),
    EvalModel(
        "anthropic/claude-sonnet-4-6",
        "medium",
        False,
        requires_env="ANTHROPIC_API_KEY",
        note="Anthropic frontier coverage at the medium tier",
    ),
    EvalModel("openai/gpt-5.5", "high", False, requires_env="OPENAI_API_KEY"),
    EvalModel("anthropic/claude-opus-4-8", "high", False, requires_env="ANTHROPIC_API_KEY"),
]

# Default judge — the existing Together judge used across the per-server suites.
JUDGE_MODEL = "together/Qwen/Qwen3-235B-A22B-Instruct-2507-tput"


def provider_of(name: str) -> str:
    return name.split("/", 1)[0]


def together_api_model(name: str) -> str | None:
    prefix = "together/"
    return name[len(prefix) :] if name.startswith(prefix) else None


def routes_to_together(name: str) -> bool:
    return name.startswith("together/") or name.startswith("openai-api/together/")


def judge_api_string(judge: str = JUDGE_MODEL) -> str:
    return together_api_model(judge) or judge


def models_for_tier(tier: str) -> list[EvalModel]:
    if tier == "all":
        return list(MODELS)
    return [m for m in MODELS if m.tier == tier]
