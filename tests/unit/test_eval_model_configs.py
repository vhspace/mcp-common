"""Tests for per-tier GenerateConfig presets (generate_config_for_tier)."""

from __future__ import annotations

import pytest

from mcpanvil.testing.eval.model_configs import (
    _TIER_MAX_TOKENS,
    Tier,
    _provider_from_model,
    _uses_thinking_template,
    generate_config_for_tier,
)

ALL_TIERS: list[Tier] = ["fast", "medium", "high"]

_THINKING_EXTRA_BODY = {"chat_template_kwargs": {"enable_thinking": False}}


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
        # the ~1024 cap from the function-calling guidance
        assert generate_config_for_tier("fast").max_tokens == 1024

    def test_high_tier_allows_more_tokens_than_fast(self) -> None:
        # presets are tier-specific: the high tier gets a larger budget
        fast = generate_config_for_tier("fast").max_tokens
        medium = generate_config_for_tier("medium").max_tokens
        high = generate_config_for_tier("high").max_tokens
        assert fast is not None and medium is not None and high is not None
        assert fast < medium < high

    @pytest.mark.parametrize("tier", ALL_TIERS)
    def test_reasoning_effort_unset_by_default(self, tier: Tier) -> None:
        # vLLM-safe: reasoning_effort="none" 400s on some serverless inference
        # APIs, so the base preset must NOT set it — thinking-off comes
        # from the enable_thinking chat-template switch instead.
        assert generate_config_for_tier(tier).reasoning_effort is None

    @pytest.mark.parametrize("tier", ALL_TIERS)
    def test_reasoning_effort_opt_in_passthrough(self, tier: Tier) -> None:
        # callers targeting OpenAI/Anthropic-style providers that honor the
        # field can opt in; the value is forwarded verbatim.
        assert generate_config_for_tier(tier, reasoning_effort="none").reasoning_effort == "none"
        assert generate_config_for_tier(tier, reasoning_effort="minimal").reasoning_effort == (
            "minimal"
        )

    @pytest.mark.parametrize("tier", ALL_TIERS)
    def test_reasoning_effort_excluded_from_payload_when_unset(self, tier: Tier) -> None:
        # downstream runners apply the config via model_dump(exclude_none=True),
        # so an unset reasoning_effort never reaches the provider (the whole
        # point) while the vLLM-safe thinking-off lever survives.
        dumped = generate_config_for_tier(tier).model_dump(exclude_none=True)
        assert "reasoning_effort" not in dumped
        assert dumped["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}

    @pytest.mark.parametrize("tier", ALL_TIERS)
    def test_chat_template_disables_thinking(self, tier: Tier) -> None:
        # thinking off for vLLM hybrid models via chat_template_kwargs
        config = generate_config_for_tier(tier)
        assert config.extra_body == {"chat_template_kwargs": {"enable_thinking": False}}

    @pytest.mark.parametrize("tier", ALL_TIERS)
    def test_no_response_schema_set(self, tier: Tier) -> None:
        # caveat: response_schema/response_format is OpenAI/Google/Mistral
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


# ---------------------------------------------------------------------------
# Provider-aware extra_body: the vLLM enable_thinking chat-template switch must
# NOT be sent to Anthropic (or any other non-vLLM) models-under-test.
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestProviderFromModel:
    def test_anthropic_prefix(self) -> None:
        assert _provider_from_model("anthropic/claude-3-5-haiku-latest") == "anthropic"

    def test_vllm_prefix_keeps_only_first_segment(self) -> None:
        # vllm model paths carry extra slashes; only the provider segment matters
        assert _provider_from_model("vllm/Qwen/Qwen3-235B-A22B-Instruct-2507") == ("vllm")

    def test_lowercases_provider(self) -> None:
        assert _provider_from_model("Anthropic/Claude-Sonnet") == "anthropic"

    def test_no_prefix_returns_none(self) -> None:
        # a bare model name with no "<provider>/" prefix is undeterminable
        assert _provider_from_model("gpt-4o-mini") is None

    def test_empty_provider_segment_returns_none(self) -> None:
        assert _provider_from_model("/foo") is None


@pytest.mark.eval
class TestUsesThinkingTemplate:
    def test_none_defaults_to_vllm_safe_include(self) -> None:
        # unspecified provider preserves the historical vLLM-safe default
        assert _uses_thinking_template(None) is True

    @pytest.mark.parametrize("provider", ["vllm", "VLLM", " vllm "])
    def test_vllm_included_case_insensitive(self, provider: str) -> None:
        assert _uses_thinking_template(provider) is True

    @pytest.mark.parametrize("provider", ["anthropic", "openai", "google", "bedrock", "mistral"])
    def test_other_providers_excluded(self, provider: str) -> None:
        assert _uses_thinking_template(provider) is False


@pytest.mark.eval
class TestGenerateConfigProviderAware:
    @pytest.mark.parametrize("tier", ALL_TIERS)
    def test_default_keeps_extra_body_backward_compatible(self, tier: Tier) -> None:
        # no provider/model -> unchanged behaviour: extra_body still present
        assert generate_config_for_tier(tier).extra_body == _THINKING_EXTRA_BODY

    @pytest.mark.parametrize("provider", ["vllm", "VLLM"])
    def test_vllm_provider_includes_extra_body(self, provider: str) -> None:
        config = generate_config_for_tier("fast", provider=provider)
        assert config.extra_body == _THINKING_EXTRA_BODY

    @pytest.mark.parametrize("provider", ["anthropic", "openai", "google"])
    def test_non_vllm_provider_omits_extra_body(self, provider: str) -> None:
        # the vLLM-only chat-template switch must not be sent here
        config = generate_config_for_tier("fast", provider=provider)
        assert config.extra_body is None

    def test_anthropic_provider_case_insensitive(self) -> None:
        assert generate_config_for_tier("fast", provider="Anthropic").extra_body is None

    def test_infers_anthropic_from_model_string(self) -> None:
        config = generate_config_for_tier("medium", model="anthropic/claude-3-5-haiku-latest")
        assert config.extra_body is None

    def test_infers_vllm_from_model_string(self) -> None:
        config = generate_config_for_tier("high", model="vllm/Qwen/Qwen3-235B-A22B-Instruct-2507")
        assert config.extra_body == _THINKING_EXTRA_BODY

    def test_unprefixed_model_falls_back_to_default_include(self) -> None:
        # provider undeterminable -> vLLM-safe default (include)
        config = generate_config_for_tier("fast", model="some-bare-model")
        assert config.extra_body == _THINKING_EXTRA_BODY

    def test_explicit_provider_takes_precedence_over_model(self) -> None:
        # provider="vllm" wins even though the model string says anthropic
        config = generate_config_for_tier(
            "fast", provider="vllm", model="anthropic/claude-3-5-haiku-latest"
        )
        assert config.extra_body == _THINKING_EXTRA_BODY

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_blank_provider_falls_through_to_default_include(self, blank: str) -> None:
        # a blank/whitespace provider is "unspecified", not a non-vLLM
        # provider, so it must NOT drop the extra_body
        assert generate_config_for_tier("fast", provider=blank).extra_body == _THINKING_EXTRA_BODY

    def test_blank_provider_defers_to_model_inference(self) -> None:
        # blank provider falls through to the model string, which here infers
        # anthropic -> extra_body omitted
        config = generate_config_for_tier(
            "fast", provider="  ", model="anthropic/claude-3-5-haiku-latest"
        )
        assert config.extra_body is None

    def test_anthropic_dump_excludes_extra_body(self) -> None:
        # downstream runners apply via model_dump(exclude_none=True): an omitted
        # extra_body must not reach the Anthropic provider at all
        dumped = generate_config_for_tier("fast", provider="anthropic").model_dump(
            exclude_none=True
        )
        assert "extra_body" not in dumped

    def test_other_levers_unchanged_for_anthropic(self) -> None:
        # only extra_body is provider-gated; the rest of the preset is intact
        config = generate_config_for_tier("medium", provider="anthropic")
        assert config.temperature == 0.0
        assert config.max_tokens == _TIER_MAX_TOKENS["medium"]

    def test_reasoning_effort_still_applies_with_provider(self) -> None:
        config = generate_config_for_tier("fast", provider="anthropic", reasoning_effort="minimal")
        assert config.reasoning_effort == "minimal"
        assert config.extra_body is None
