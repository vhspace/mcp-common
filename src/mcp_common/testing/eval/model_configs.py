"""Per-tier ``GenerateConfig`` presets for MCP evals.

Standardizes the small-model **reliability levers** that the shared eval
harness previously left to each downstream runner, surfaced by the netbox-mcp
2026-05-30 matrix run (vhspace/netbox-mcp#121, vhspace/netbox-mcp#120). In that
run the fast tier (``Qwen3.5-9B``) frequently produced no final answer in CLI
mode and was the weakest tool-caller — consistent with no temperature pinning,
no thinking control, and token-budget truncation.

:func:`generate_config_for_tier` returns a ready-to-apply
:class:`~inspect_ai.model.GenerateConfig` per capability tier. Downstream
runners (netbox-mcp ``run_matrix.py``, etc.) apply the returned config to each
model under test::

    from mcp_common.testing.eval import generate_config_for_tier

    config = generate_config_for_tier("fast")
    eval(task, model=model, **config.model_dump(exclude_none=True))

Each preset carries the same three levers (Together function-calling best
practices):

* ``temperature=0`` — deterministic tool selection / argument generation.
* a **capped** ``max_tokens`` — keeps latency/cost down, but watch
  ``finish_reason == "length"``: a truncated completion cuts a tool call mid
  JSON and is read downstream as a malformed / missing tool call. The cap is
  tier-specific (the ``high`` tier allows a larger budget); see
  :data:`_TIER_MAX_TOKENS`.
* **thinking disabled** for hybrid (reasoning) models — set both
  ``reasoning_effort="none"`` (honored by providers that expose the field) and
  ``extra_body={"chat_template_kwargs": {"enable_thinking": False}}`` (the
  Together / vLLM chat-template switch). Belt-and-suspenders so a single preset
  turns thinking off across providers.

## Together caveat: no ``response_schema`` / ``response_format``

Structured-output constraints (``GenerateConfig.response_schema`` →
``response_format``) are **OpenAI / Google / Mistral only**; Together's API does
**not** support them. So tool-argument reliability must come from the tools'
own JSON-Schemas (clear parameter types, ``required`` lists, enums) — not from
``response_format``. These presets deliberately do **not** set
``response_schema`` for that reason.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, get_args

if TYPE_CHECKING:
    from inspect_ai.model import GenerateConfig

Tier = Literal["fast", "medium", "high"]
"""Capability tier of a model under eval (drives the ``max_tokens`` budget)."""

_TIER_MAX_TOKENS: dict[Tier, int] = {
    "fast": 1024,
    "medium": 2048,
    "high": 4096,
}
"""Per-tier ``max_tokens`` cap.

``fast`` keeps the ~1024 cap from the function-calling guidance; larger tiers
get a bigger budget because more capable models emit longer (but still
bounded) tool-call sequences and final answers. All caps are low enough that
``finish_reason == "length"`` should be treated as a truncated tool call.
"""


def generate_config_for_tier(tier: Tier) -> GenerateConfig:
    """Return the :class:`~inspect_ai.model.GenerateConfig` preset for ``tier``.

    The preset pins ``temperature=0``, applies the tier's ``max_tokens`` cap
    (see :data:`_TIER_MAX_TOKENS`), and disables hybrid-model "thinking" via
    both ``reasoning_effort="none"`` and
    ``extra_body={"chat_template_kwargs": {"enable_thinking": False}}``.

    A fresh ``GenerateConfig`` is returned on every call, so callers may mutate
    or ``merge`` it without affecting other tiers.

    Args:
        tier: One of ``"fast"``, ``"medium"``, ``"high"``.

    Returns:
        A ``GenerateConfig`` carrying the reliability levers for ``tier``.

    Raises:
        ValueError: If ``tier`` is not a recognized tier.
    """
    if tier not in _TIER_MAX_TOKENS:
        valid = ", ".join(repr(t) for t in get_args(Tier))
        raise ValueError(f"Unknown tier {tier!r}; expected one of {valid}.")

    from inspect_ai.model import GenerateConfig

    return GenerateConfig(
        temperature=0.0,
        max_tokens=_TIER_MAX_TOKENS[tier],
        reasoning_effort="none",
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
