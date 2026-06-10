"""Tests for provider-aware GenerateConfig presets (generate_config_for_provider_tier, #156)."""

from __future__ import annotations

import pytest

from mcpanvil.testing.eval.model_configs import _TIER_MAX_TOKENS, Tier
from mcpanvil.testing.eval.provider_config import (
    generate_config_for_provider_tier,
    provider_uses_vllm_chat_template,
)

ALL_TIERS: list[Tier] = ["fast", "medium", "high"]
_VLLM_EXTRA_BODY = {"chat_template_kwargs": {"enable_thinking": False}}


@pytest.mark.eval
class TestProviderUsesVllmChatTemplate:
    @pytest.mark.parametrize("provider", ["vllm", "vllm-openai", "VLLM", " vllm "])
    def test_vllm_style_providers(self, provider: str) -> None:
        assert provider_uses_vllm_chat_template(provider) is True

    @pytest.mark.parametrize(
        "provider", ["anthropic", "openai", "Anthropic", " OpenAI ", "google", "mistral", "bedrock"]
    )
    def test_non_vllm_providers(self, provider: str) -> None:
        assert provider_uses_vllm_chat_template(provider) is False

    def test_unknown_provider_defaults_to_vllm_style(self) -> None:
        # preserve the historical vLLM-safe behaviour for unrecognized providers
        assert provider_uses_vllm_chat_template("some-future-provider") is True


@pytest.mark.eval
class TestGenerateConfigForProviderTier:
    @pytest.mark.parametrize("tier", ALL_TIERS)
    def test_vllm_keeps_chat_template_extra_body(self, tier: Tier) -> None:
        # vLLM honors the enable_thinking chat-template switch
        assert generate_config_for_provider_tier(tier, "vllm").extra_body == _VLLM_EXTRA_BODY

    @pytest.mark.parametrize("tier", ALL_TIERS)
    @pytest.mark.parametrize("provider", ["anthropic", "openai"])
    def test_non_vllm_drops_extra_body(self, tier: Tier, provider: str) -> None:
        # the vLLM chat-template field is meaningless for Anthropic/OpenAI and may error
        assert generate_config_for_provider_tier(tier, provider).extra_body is None

    @pytest.mark.parametrize("tier", ALL_TIERS)
    def test_anthropic_payload_omits_extra_body(self, tier: Tier) -> None:
        dumped = generate_config_for_provider_tier(tier, "anthropic").model_dump(exclude_none=True)
        assert "extra_body" not in dumped

    @pytest.mark.parametrize("tier", ALL_TIERS)
    def test_vllm_payload_includes_extra_body(self, tier: Tier) -> None:
        dumped = generate_config_for_provider_tier(tier, "vllm").model_dump(exclude_none=True)
        assert dumped["extra_body"] == _VLLM_EXTRA_BODY

    @pytest.mark.parametrize("tier", ALL_TIERS)
    def test_reliability_levers_preserved_across_providers(self, tier: Tier) -> None:
        # the tier max_tokens cap + temperature pinning survive the provider adaptation
        for provider in ("vllm", "anthropic", "openai"):
            config = generate_config_for_provider_tier(tier, provider)
            assert config.temperature == 0.0
            assert config.max_tokens == _TIER_MAX_TOKENS[tier]

    def test_default_provider_is_vllm(self) -> None:
        assert generate_config_for_provider_tier("fast").extra_body == _VLLM_EXTRA_BODY

    @pytest.mark.parametrize("tier", ALL_TIERS)
    def test_reasoning_effort_passthrough(self, tier: Tier) -> None:
        config = generate_config_for_provider_tier(tier, "anthropic", reasoning_effort="minimal")
        assert config.reasoning_effort == "minimal"

    def test_unknown_provider_keeps_extra_body(self) -> None:
        config = generate_config_for_provider_tier("fast", "some-future-provider")
        assert config.extra_body == _VLLM_EXTRA_BODY

    def test_fresh_instance_each_call(self) -> None:
        first = generate_config_for_provider_tier("fast", "vllm")
        second = generate_config_for_provider_tier("fast", "vllm")
        assert first is not second

    def test_unknown_tier_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown tier"):
            generate_config_for_provider_tier("turbo", "vllm")  # type: ignore[arg-type]
