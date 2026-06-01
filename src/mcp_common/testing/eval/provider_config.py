"""Provider-aware ``GenerateConfig`` presets for MCP evals.

:func:`mcp_common.testing.eval.model_configs.generate_config_for_tier` is the
**single source of truth** for the per-tier reliability levers and the
provider-aware thinking-off gate: it pins ``temperature=0``, applies the tier
``max_tokens`` cap, omits ``reasoning_effort`` (Together's serverless API 400s on
``reasoning_effort="none"`` for some models — vhspace/mcp-common#141,
vhspace/netbox-mcp#133), and injects the Together/vLLM
``extra_body={"chat_template_kwargs": {"enable_thinking": False}}`` switch **only**
for vLLM-style providers (vhspace/mcp-common#170).

The awx/dc-support eval matrices add **Claude Haiku (fast)** and **Claude Sonnet
(medium)** as models *under test* (vhspace/mcp-common#156). For an
Anthropic-routed (or OpenAI-routed) model that Together-only ``extra_body`` is
meaningless and may error — the provider never sees a vLLM chat template.

:func:`generate_config_for_provider_tier` is a thin **provider-positional**
convenience wrapper around :func:`generate_config_for_tier`: it forwards its
``provider`` straight through to ``generate_config_for_tier(provider=...)`` so the
two helpers share one gating implementation and cannot diverge
(vhspace/mcp-common#181). Earlier this wrapper carried its own denylist, which
made it *keep* the ``extra_body`` for an explicitly-named unknown provider while
``generate_config_for_tier`` *dropped* it; delegating removes that edge case by
adopting the allowlist rule (an explicitly-named unknown provider ⇒ drop). The
absent/``None`` and default ``"together"`` cases still keep the lever, so existing
Together runners are unaffected.

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

from mcp_common.testing.eval.model_configs import (
    _THINKING_TEMPLATE_PROVIDERS,
    _uses_thinking_template,
    generate_config_for_tier,
)

if TYPE_CHECKING:
    from inspect_ai.model import GenerateConfig

    from mcp_common.testing.eval.model_configs import ReasoningEffort, Tier

__all__ = [
    "VLLM_CHAT_TEMPLATE_PROVIDERS",
    "generate_config_for_provider_tier",
    "provider_uses_vllm_chat_template",
]

VLLM_CHAT_TEMPLATE_PROVIDERS: frozenset[str] = _THINKING_TEMPLATE_PROVIDERS
"""Providers whose backend honors the vLLM ``chat_template_kwargs`` switch.

Aliases the canonical allowlist owned by
:mod:`mcp_common.testing.eval.model_configs`
(``_THINKING_TEMPLATE_PROVIDERS == {together, vllm, vllm-openai}``) so this module
and ``generate_config_for_tier`` share one set and cannot drift
(vhspace/mcp-common#181). Only these keep the Together/vLLM
``extra_body={"chat_template_kwargs": {"enable_thinking": False}}`` lever; every
other provider (Anthropic, OpenAI, Google, …) — and any explicitly-named unknown
provider — has it stripped, because the field is meaningless there and an
Anthropic/OpenAI endpoint may reject an unknown body field (vhspace/mcp-common#170).
"""


def provider_uses_vllm_chat_template(provider: str) -> bool:
    """Whether ``provider`` honors the vLLM ``chat_template_kwargs`` ``extra_body``.

    Thin wrapper over the canonical gate ``_uses_thinking_template`` in
    :mod:`mcp_common.testing.eval.model_configs`, so this predicate always agrees
    with the config that ``generate_config_for_tier`` /
    :func:`generate_config_for_provider_tier` actually emit (vhspace/mcp-common#181).

    ``True`` for the Together / vLLM-style providers in
    :data:`VLLM_CHAT_TEMPLATE_PROVIDERS` (and for a blank/whitespace ``provider``,
    treated as unspecified ⇒ the historical Together-safe default); ``False`` for
    every other provider, **including an explicitly-named unknown one** — its
    endpoint doesn't use the switch and may reject the unknown body field. Case-
    and whitespace-insensitive.
    """
    # A blank/whitespace provider is "unspecified" -> Together-safe default,
    # matching generate_config_for_tier's blank handling; a non-blank token is
    # checked against the canonical vLLM allowlist.
    return _uses_thinking_template(provider.strip() or None)


def generate_config_for_provider_tier(
    tier: Tier,
    provider: str = "together",
    *,
    reasoning_effort: ReasoningEffort | None = None,
) -> GenerateConfig:
    """Return the per-tier preset adapted for ``provider``.

    A **provider-positional** convenience wrapper that delegates straight to
    :func:`generate_config_for_tier` — forwarding ``provider`` through its
    ``provider=`` parameter — so the tier ``max_tokens`` cap, ``temperature=0``
    pinning, opt-in ``reasoning_effort``, **and** the provider-aware thinking-off
    gate are all the single ``generate_config_for_tier`` implementation
    (vhspace/mcp-common#181). There is no second copy of the gating logic to drift
    from it.

    The thinking-off lever therefore follows ``generate_config_for_tier``'s
    allowlist:

    * **Together / vLLM** (:data:`VLLM_CHAT_TEMPLATE_PROVIDERS`): keep
      ``extra_body={"chat_template_kwargs": {"enable_thinking": False}}``, the
      backend-honored switch.
    * **Anthropic / OpenAI / any other (including an explicitly-named unknown)
      provider**: drop ``extra_body`` entirely. The vLLM chat-template field is
      meaningless there and an Anthropic/OpenAI endpoint may reject it; Anthropic
      models default to extended-thinking *off*, and a provider that honors
      ``reasoning_effort`` can be tuned via the opt-in kwarg.

    A fresh ``GenerateConfig`` is returned on every call (callers may mutate /
    ``merge`` it freely).

    Args:
        tier: One of ``"fast"``, ``"medium"``, ``"high"`` (validated by
            :func:`generate_config_for_tier`).
        provider: Inspect model provider the model-under-test routes through
            (e.g. ``"together"``, ``"anthropic"``, ``"openai"``). Case- and
            whitespace-insensitive. The default ``"together"`` and a
            blank/whitespace value keep the Together-safe ``extra_body``; an
            explicitly-named provider outside the vLLM allowlist drops it.
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
    return generate_config_for_tier(tier, reasoning_effort=reasoning_effort, provider=provider)
