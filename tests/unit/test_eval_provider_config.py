"""Tests for provider-aware GenerateConfig presets (generate_config_for_provider_tier, #156)."""

from __future__ import annotations

import pytest

from mcp_common.testing.eval.model_configs import (
    _TIER_MAX_TOKENS,
    Tier,
    generate_config_for_tier,
)
from mcp_common.testing.eval.provider_config import (
    generate_config_for_provider_tier,
    provider_uses_vllm_chat_template,
)

ALL_TIERS: list[Tier] = ["fast", "medium", "high"]
_VLLM_EXTRA_BODY = {"chat_template_kwargs": {"enable_thinking": False}}


@pytest.mark.eval
class TestProviderUsesVllmChatTemplate:
    @pytest.mark.parametrize(
        "provider", ["together", "vllm", "vllm-openai", "Together", " TOGETHER "]
    )
    def test_vllm_style_providers(self, provider: str) -> None:
        assert provider_uses_vllm_chat_template(provider) is True

    @pytest.mark.parametrize(
        "provider", ["anthropic", "openai", "Anthropic", " OpenAI ", "google", "mistral", "bedrock"]
    )
    def test_non_vllm_providers(self, provider: str) -> None:
        assert provider_uses_vllm_chat_template(provider) is False

    def test_explicit_unknown_provider_excluded(self) -> None:
        # reconciled to the allowlist rule (vhspace/mcp-common#181): an
        # explicitly-named unknown provider is NOT treated as vLLM-style, so it
        # agrees with model_configs.generate_config_for_tier rather than keeping
        # the Together-only lever.
        assert provider_uses_vllm_chat_template("some-future-provider") is False

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_blank_provider_treated_as_unspecified_default(self, blank: str) -> None:
        # a blank/whitespace provider is "unspecified" -> Together-safe default
        # (kept), matching generate_config_for_tier's blank handling
        assert provider_uses_vllm_chat_template(blank) is True


@pytest.mark.eval
class TestGenerateConfigForProviderTier:
    @pytest.mark.parametrize("tier", ALL_TIERS)
    @pytest.mark.parametrize("provider", ["together", "vllm", "vllm-openai"])
    def test_vllm_provider_keeps_chat_template_extra_body(self, tier: Tier, provider: str) -> None:
        # Together / vLLM (incl. vllm-openai) honors the enable_thinking switch
        assert generate_config_for_provider_tier(tier, provider).extra_body == _VLLM_EXTRA_BODY

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
    def test_together_payload_includes_extra_body(self, tier: Tier) -> None:
        dumped = generate_config_for_provider_tier(tier, "together").model_dump(exclude_none=True)
        assert dumped["extra_body"] == _VLLM_EXTRA_BODY

    @pytest.mark.parametrize("tier", ALL_TIERS)
    def test_reliability_levers_preserved_across_providers(self, tier: Tier) -> None:
        # the tier max_tokens cap + temperature pinning survive the provider adaptation
        for provider in ("together", "anthropic", "openai"):
            config = generate_config_for_provider_tier(tier, provider)
            assert config.temperature == 0.0
            assert config.max_tokens == _TIER_MAX_TOKENS[tier]

    def test_default_provider_is_together(self) -> None:
        assert generate_config_for_provider_tier("fast").extra_body == _VLLM_EXTRA_BODY

    @pytest.mark.parametrize("tier", ALL_TIERS)
    def test_reasoning_effort_passthrough(self, tier: Tier) -> None:
        config = generate_config_for_provider_tier(tier, "anthropic", reasoning_effort="minimal")
        assert config.reasoning_effort == "minimal"

    def test_explicit_unknown_provider_drops_extra_body(self) -> None:
        # reconciled (vhspace/mcp-common#181): an explicitly-named unknown
        # provider drops the Together-only extra_body, agreeing with
        # generate_config_for_tier (previously this wrapper kept it).
        config = generate_config_for_provider_tier("fast", "some-future-provider")
        assert config.extra_body is None

    @pytest.mark.parametrize("tier", ALL_TIERS)
    @pytest.mark.parametrize(
        "provider",
        ["together", "vllm", "vllm-openai", "anthropic", "openai", "some-future-provider", "  "],
    )
    def test_delegates_to_generate_config_for_tier(self, tier: Tier, provider: str) -> None:
        # the wrapper must emit exactly what generate_config_for_tier produces for
        # the same provider, so the two helpers cannot diverge (#181). This is the
        # regression guard for the reconciliation.
        wrapped = generate_config_for_provider_tier(tier, provider)
        delegated = generate_config_for_tier(tier, provider=provider)
        assert wrapped.extra_body == delegated.extra_body
        assert wrapped.max_tokens == delegated.max_tokens
        assert wrapped.temperature == delegated.temperature
        assert wrapped.reasoning_effort == delegated.reasoning_effort

    def test_fresh_instance_each_call(self) -> None:
        first = generate_config_for_provider_tier("fast", "together")
        second = generate_config_for_provider_tier("fast", "together")
        assert first is not second

    def test_unknown_tier_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown tier"):
            generate_config_for_provider_tier("turbo", "together")  # type: ignore[arg-type]
