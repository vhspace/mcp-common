"""Tiered model registry for the dc-support-mcp eval matrix."""

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
    EvalModel("together/Qwen/Qwen3.5-9B", "fast", True),
    EvalModel("together/meta-llama/Llama-3.3-70B-Instruct-Turbo", "fast", True),
    EvalModel(
        "together/openai/gpt-oss-20b",
        "fast",
        True,
        note="cheap fast-tier tool-caller baseline",
    ),
    EvalModel("together/Qwen/Qwen3-235B-A22B-Instruct-2507-tput", "medium", True),
    EvalModel("together/deepseek-ai/DeepSeek-V4-Pro", "medium", True),
    EvalModel(
        "anthropic/claude-haiku-4-5-20251001",
        "fast",
        False,
        requires_env="ANTHROPIC_API_KEY",
        note="Anthropic frontier coverage at the fast tier (#156)",
    ),
    EvalModel(
        "anthropic/claude-sonnet-4-6",
        "medium",
        False,
        requires_env="ANTHROPIC_API_KEY",
        note="Anthropic frontier coverage at the medium tier (#156)",
    ),
    EvalModel("openai/gpt-5.5", "high", False, requires_env="OPENAI_API_KEY"),
    EvalModel("anthropic/claude-opus-4-8", "high", False, requires_env="ANTHROPIC_API_KEY"),
]

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
