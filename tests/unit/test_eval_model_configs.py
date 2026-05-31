"""Tests for per-tier GenerateConfig presets (generate_config_for_tier)."""

from __future__ import annotations

import pytest

from mcp_common.testing.eval.model_configs import (
    _TIER_MAX_TOKENS,
    Tier,
    generate_config_for_tier,
)

ALL_TIERS: list[Tier] = ["fast", "medium", "high"]


@pytest.mark.eval
class TestGenerateConfigForTier:
    @pytest.mark.parametrize("tier", ALL_TIERS)
    def test_temperature_pinned_to_zero(self, tier: Tier) -> None:
        # temperature=0 -> deterministic tool selection / argument generation
        assert generate_config_for_tier(tier).temperature == 0.0

    @pytest.mark.parametrize("tier", ALL_TIERS)
    def test_max_tokens_capped_per_tier(self, tier: Tier) -> None:
        config = generate_config_for_tier(tier)
        assert config.max_tokens == _TIER_MAX_TOKENS[tier]
        assert config.max_tokens is not None and config.max_tokens > 0

    def test_fast_tier_uses_1024_cap(self) -> None:
        # the ~1024 cap from the Together function-calling guidance
        assert generate_config_for_tier("fast").max_tokens == 1024

    def test_high_tier_allows_more_tokens_than_fast(self) -> None:
        # presets are tier-specific: the high tier gets a larger budget
        fast = generate_config_for_tier("fast").max_tokens
        medium = generate_config_for_tier("medium").max_tokens
        high = generate_config_for_tier("high").max_tokens
        assert fast is not None and medium is not None and high is not None
        assert fast < medium < high

    @pytest.mark.parametrize("tier", ALL_TIERS)
    def test_reasoning_effort_disabled(self, tier: Tier) -> None:
        # thinking off for hybrid models via the reasoning_effort field
        assert generate_config_for_tier(tier).reasoning_effort == "none"

    @pytest.mark.parametrize("tier", ALL_TIERS)
    def test_chat_template_disables_thinking(self, tier: Tier) -> None:
        # thinking off for Together / vLLM hybrid models via chat_template_kwargs
        config = generate_config_for_tier(tier)
        assert config.extra_body == {"chat_template_kwargs": {"enable_thinking": False}}

    @pytest.mark.parametrize("tier", ALL_TIERS)
    def test_no_response_schema_set(self, tier: Tier) -> None:
        # Together caveat: response_schema/response_format is OpenAI/Google/Mistral
        # only, so the preset must not set it.
        assert generate_config_for_tier(tier).response_schema is None

    def test_returns_fresh_instance_each_call(self) -> None:
        # callers may mutate/merge without affecting other tiers
        first = generate_config_for_tier("fast")
        second = generate_config_for_tier("fast")
        assert first is not second
        first.max_tokens = 1
        assert generate_config_for_tier("fast").max_tokens == 1024

    def test_unknown_tier_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown tier"):
            generate_config_for_tier("turbo")  # type: ignore[arg-type]

    def test_error_lists_valid_tiers(self) -> None:
        with pytest.raises(ValueError, match=r"fast.*medium.*high"):
            generate_config_for_tier("bogus")  # type: ignore[arg-type]
