"""Provider-aware ``GenerateConfig`` presets for MCP evals.

:func:`mcp_common.testing.eval.model_configs.generate_config_for_tier` emits a
**Together / vLLM-tuned** preset: it disables hybrid-model "thinking" via
``extra_body={"chat_template_kwargs": {"enable_thinking": False}}`` — the vLLM
chat-template switch Together accepts — and omits ``reasoning_effort`` because
Together's serverless API 400s on ``reasoning_effort="none"`` for some models
(vhspace/mcp-common#141, vhspace/netbox-mcp#133).

The awx/dc-support eval matrices add **Claude Haiku (fast)** and **Claude Sonnet
(medium)** as models *under test* (vhspace/mcp-common#156). For an
Anthropic-routed (or OpenAI-routed) model that Together-only ``extra_body`` is
meaningless and may error — the provider never sees a vLLM chat template. This
helper makes the preset **provider-aware** without duplicating the per-tier
reliability levers: it builds on :func:`generate_config_for_tier` and strips the
vLLM-only ``extra_body`` for providers that don't use it, leaving every other
lever (``temperature=0``, the tier ``max_tokens`` cap, opt-in
``reasoning_effort``) intact.

This mirrors the judge ``response_format`` provider-aware follow-up
(``fix/judge-response-format-provider-aware``, vhspace/mcp-common#132): keep the
Together default, special-case the providers whose OpenAI-compatible surface
differs.

Usage::

    from mcp_common.testing.eval import generate_config_for_provider_tier

    together_cfg = generate_config_for_provider_tier("fast", "together")   # vLLM extra_body kept
    claude_cfg = generate_config_for_provider_tier("medium", "anthropic")  # extra_body dropped
    eval(task, model=model, **claude_cfg.model_dump(exclude_none=True))
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp_common.testing.eval.model_configs import generate_config_for_tier

if TYPE_CHECKING:
    from inspect_ai.model import GenerateConfig

    from mcp_common.testing.eval.model_configs import ReasoningEffort, Tier

__all__ = [
    "VLLM_CHAT_TEMPLATE_PROVIDERS",
    "generate_config_for_provider_tier",
    "provider_uses_vllm_chat_template",
]

VLLM_CHAT_TEMPLATE_PROVIDERS: frozenset[str] = frozenset({"together", "vllm", "vllm-openai"})
"""Providers whose backend honors the vLLM ``chat_template_kwargs`` switch.

Only these keep the Together/vLLM ``extra_body={"chat_template_kwargs":
{"enable_thinking": False}}`` lever. Every other provider (Anthropic, OpenAI,
Google, …) has it stripped because that field is meaningless there and an
Anthropic/OpenAI endpoint may reject an unknown body field. An **unknown**
provider is treated as vLLM-style so behaviour for existing Together runners is
unchanged unless a caller names a non-vLLM provider explicitly.
"""

# Providers that are definitively NOT vLLM-style and must have the
# chat-template ``extra_body`` removed. Kept as an explicit denylist (rather
# than "anything not in the allowlist") so a typo'd / unknown provider name
# fails safe to the historical Together behaviour instead of silently dropping
# the thinking-off lever for a Together model.
_NON_VLLM_PROVIDERS: frozenset[str] = frozenset(
    {
        "anthropic",
        "openai",
        "azureai",
        "azure",
        "google",
        "vertex",
        "mistral",
        "bedrock",
        "groq",
        "cohere",
    }
)


def provider_uses_vllm_chat_template(provider: str) -> bool:
    """Whether ``provider`` honors the vLLM ``chat_template_kwargs`` ``extra_body``.

    ``True`` for Together / vLLM-style providers (and unknown providers, which
    default to the historical Together behaviour); ``False`` for the known
    non-vLLM providers in :data:`_NON_VLLM_PROVIDERS` (Anthropic, OpenAI, …),
    whose endpoints don't use the switch and may reject the unknown body field.
    Case- and whitespace-insensitive.
    """
    normalized = provider.strip().lower()
    if normalized in _NON_VLLM_PROVIDERS:
        return False
    if normalized in VLLM_CHAT_TEMPLATE_PROVIDERS:
        return True
    # Unknown provider: preserve the Together-safe default.
    return True


def generate_config_for_provider_tier(
    tier: Tier,
    provider: str = "together",
    *,
    reasoning_effort: ReasoningEffort | None = None,
) -> GenerateConfig:
    """Return the per-tier preset adapted for ``provider``.

    Builds on :func:`generate_config_for_tier` — so the tier ``max_tokens`` cap,
    ``temperature=0`` pinning, and opt-in ``reasoning_effort`` are identical —
    then makes the **thinking-off** lever provider-appropriate:

    * **Together / vLLM** (and unknown providers): keep
      ``extra_body={"chat_template_kwargs": {"enable_thinking": False}}``, the
      backend-honored switch.
    * **Anthropic / OpenAI / other non-vLLM providers**: drop ``extra_body``
      entirely. The vLLM chat-template field is meaningless there and an
      Anthropic/OpenAI endpoint may reject it; Anthropic models default to
      extended-thinking *off*, and a provider that honors ``reasoning_effort``
      can be tuned via the opt-in kwarg.

    A fresh ``GenerateConfig`` is returned on every call (callers may mutate /
    ``merge`` it freely).

    Args:
        tier: One of ``"fast"``, ``"medium"``, ``"high"`` (validated by
            :func:`generate_config_for_tier`).
        provider: Inspect model provider the model-under-test routes through
            (e.g. ``"together"``, ``"anthropic"``, ``"openai"``). Case- and
            whitespace-insensitive; unknown providers preserve the Together
            default.
        reasoning_effort: Optional value forwarded to
            ``GenerateConfig.reasoning_effort`` (omitted when ``None``). Opt-in
            for providers that honor the field; left unset keeps the preset
            Together-safe.

    Returns:
        A ``GenerateConfig`` with the tier reliability levers and a
        provider-appropriate thinking-off configuration.

    Raises:
        ValueError: If ``tier`` is not a recognized tier (from
            :func:`generate_config_for_tier`).
    """
    config = generate_config_for_tier(tier, reasoning_effort=reasoning_effort)
    if not provider_uses_vllm_chat_template(provider):
        # The vLLM ``chat_template_kwargs`` switch does not apply; drop it so the
        # provider never receives the unknown body field. ``model_dump(
        # exclude_none=True)`` then omits it entirely from the request payload.
        config.extra_body = None
    return config
