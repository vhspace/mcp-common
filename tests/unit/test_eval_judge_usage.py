"""Tests for LLM-judge token accounting (judge_usage, #169)."""

from __future__ import annotations

from typing import Any

import pytest

from mcp_common.testing.eval.judge_usage import (
    PRICE_INPUT_ENV_VAR,
    PRICE_OUTPUT_ENV_VAR,
    JudgeModelUsage,
    JudgePricing,
    JudgeUsage,
    JudgeUsageAccumulator,
    TrackedJudgeClient,
    estimate_judge_cost,
    install_judge_usage_tracking,
    judge_cost_block,
    record_judge_usage,
    reset_judge_usage,
    tracked_judge_client,
    uninstall_judge_usage_tracking,
)

# ---------------------------------------------------------------------------
# Fakes mimicking the OpenAI-compatible judge client surface
# ---------------------------------------------------------------------------


class _FakeUsage:
    def __init__(self, prompt: int, completion: int, total: int | None = None) -> None:
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.total_tokens = total if total is not None else prompt + completion


class _FakeResponse:
    def __init__(self, *, model: str | None, usage: Any) -> None:
        self.model = model
        self.usage = usage


class _FakeCompletions:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self._responses.pop(0)


class _FakeChat:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, completions: _FakeCompletions, *, marker: str = "EXTRA") -> None:
        self.chat = _FakeChat(completions)
        self.marker = marker


# ---------------------------------------------------------------------------
# Accumulator + tracked client (the seam)
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestTrackedJudgeClient:
    def test_records_usage_and_passes_response_through(self) -> None:
        acc = JudgeUsageAccumulator()
        response = _FakeResponse(model="claude-sonnet", usage=_FakeUsage(100, 20))
        client = _FakeClient(_FakeCompletions([response]))
        tracked = tracked_judge_client(client, acc)

        result = tracked.chat.completions.create(model="claude-sonnet", messages=[])
        # the judge still sees the unmodified response object
        assert result is response
        snap = acc.snapshot()
        assert snap.calls == 1
        assert snap.input_tokens == 100
        assert snap.output_tokens == 20
        assert snap.total_tokens == 120
        assert snap.by_model["claude-sonnet"].calls == 1

    def test_delegates_unknown_attributes(self) -> None:
        client = _FakeClient(_FakeCompletions([]), marker="hello")
        tracked = tracked_judge_client(client, JudgeUsageAccumulator())
        assert tracked.marker == "hello"
        assert isinstance(tracked, TrackedJudgeClient)

    def test_accumulates_across_calls_by_model(self) -> None:
        acc = JudgeUsageAccumulator()
        client = _FakeClient(
            _FakeCompletions(
                [
                    _FakeResponse(model="m1", usage=_FakeUsage(10, 5)),
                    _FakeResponse(model="m1", usage=_FakeUsage(20, 7)),
                    _FakeResponse(model="m2", usage=_FakeUsage(1, 1)),
                ]
            )
        )
        tracked = tracked_judge_client(client, acc)
        for _ in range(3):
            tracked.chat.completions.create(model="ignored")

        snap = acc.snapshot()
        assert snap.calls == 3
        assert snap.input_tokens == 31
        assert snap.output_tokens == 13
        assert snap.by_model["m1"].calls == 2
        assert snap.by_model["m1"].input_tokens == 30
        assert snap.by_model["m2"].total_tokens == 2

    def test_missing_usage_does_not_break_call(self) -> None:
        acc = JudgeUsageAccumulator()
        client = _FakeClient(_FakeCompletions([_FakeResponse(model="m", usage=None)]))
        tracked = tracked_judge_client(client, acc)
        # the call must still succeed and return the response
        assert tracked.chat.completions.create(model="m") is not None
        assert acc.snapshot().calls == 0


@pytest.mark.eval
class TestJudgeUsageAccumulator:
    def test_record_defaults_total_to_sum(self) -> None:
        acc = JudgeUsageAccumulator()
        acc.record(model="m", input_tokens=4, output_tokens=6)
        assert acc.snapshot().total_tokens == 10

    def test_record_response_uses_request_model_fallback(self) -> None:
        acc = JudgeUsageAccumulator()

        class _R:
            usage = _FakeUsage(5, 5)
            model = None

        assert acc.record_response(_R(), request_model="fallback-model") is True
        assert "fallback-model" in acc.snapshot().by_model

    def test_record_response_without_usage_returns_false(self) -> None:
        acc = JudgeUsageAccumulator()

        class _R:
            usage = None
            model = "m"

        assert acc.record_response(_R()) is False
        assert acc.snapshot().calls == 0

    def test_reset_clears_all_state(self) -> None:
        acc = JudgeUsageAccumulator()
        acc.record(model="m", input_tokens=1, output_tokens=1)
        acc.reset()
        snap = acc.snapshot()
        assert snap.calls == 0
        assert snap.total_tokens == 0
        assert dict(snap.by_model) == {}


# ---------------------------------------------------------------------------
# Pricing + cost block
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestJudgePricing:
    def test_from_env_none_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(PRICE_INPUT_ENV_VAR, raising=False)
        monkeypatch.delenv(PRICE_OUTPUT_ENV_VAR, raising=False)
        assert JudgePricing.from_env() is None

    def test_from_env_parses_both(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(PRICE_INPUT_ENV_VAR, "3")
        monkeypatch.setenv(PRICE_OUTPUT_ENV_VAR, "15")
        assert JudgePricing.from_env() == JudgePricing(3.0, 15.0)

    def test_from_env_partial_defaults_missing_to_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(PRICE_INPUT_ENV_VAR, "3")
        monkeypatch.delenv(PRICE_OUTPUT_ENV_VAR, raising=False)
        assert JudgePricing.from_env() == JudgePricing(3.0, 0.0)

    def test_from_env_unparseable_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(PRICE_INPUT_ENV_VAR, "not-a-number")
        monkeypatch.setenv(PRICE_OUTPUT_ENV_VAR, "15")
        assert JudgePricing.from_env() is None

    def test_estimate_cost(self) -> None:
        usage = JudgeUsage(
            calls=1, input_tokens=1_000_000, output_tokens=2_000_000, total_tokens=3_000_000
        )
        pricing = JudgePricing(input_usd_per_mtok=3.0, output_usd_per_mtok=15.0)
        assert estimate_judge_cost(usage, pricing) == pytest.approx(3.0 + 30.0)


@pytest.mark.eval
class TestJudgeCostBlock:
    def test_block_without_pricing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(PRICE_INPUT_ENV_VAR, raising=False)
        monkeypatch.delenv(PRICE_OUTPUT_ENV_VAR, raising=False)
        usage = JudgeUsage(
            calls=2,
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            by_model={"m": JudgeModelUsage(2, 100, 50, 150)},
        )
        block = judge_cost_block(usage=usage)
        assert block["calls"] == 2
        assert block["input_tokens"] == 100
        assert block["cost_usd"] is None
        assert block["pricing"] is None
        assert block["by_model"]["m"]["total_tokens"] == 150

    def test_block_with_explicit_pricing(self) -> None:
        usage = JudgeUsage(calls=1, input_tokens=1_000_000, output_tokens=0, total_tokens=1_000_000)
        block = judge_cost_block(usage=usage, pricing=JudgePricing(3.0, 15.0))
        assert block["cost_usd"] == pytest.approx(3.0)
        assert block["pricing"] == {"input_usd_per_mtok": 3.0, "output_usd_per_mtok": 15.0}

    def test_block_reads_global_accumulator(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(PRICE_INPUT_ENV_VAR, raising=False)
        monkeypatch.delenv(PRICE_OUTPUT_ENV_VAR, raising=False)
        reset_judge_usage()
        try:
            record_judge_usage(model="m", input_tokens=7, output_tokens=3)
            block = judge_cost_block()
            assert block["total_tokens"] == 10
        finally:
            reset_judge_usage()


# ---------------------------------------------------------------------------
# Guarded runtime hook into scorers._get_llm_client (no scorers.py edit)
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestInstallJudgeUsageTracking:
    def test_wraps_and_restores_get_llm_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from mcp_common.testing.eval import scorers

        monkeypatch.setenv("TOGETHER_API_KEY", "test-key-not-real")
        # ensure a clean baseline regardless of prior tests
        uninstall_judge_usage_tracking()
        try:
            assert install_judge_usage_tracking() is True
            # idempotent: a second install is a no-op that still reports installed
            assert install_judge_usage_tracking() is True

            result = scorers._get_llm_client()
            assert result is not None
            client, _model = result
            assert isinstance(client, TrackedJudgeClient)
        finally:
            assert uninstall_judge_usage_tracking() is True

        # restored: the factory once again returns the plain (untracked) client
        restored = scorers._get_llm_client()
        assert restored is not None
        plain_client, _ = restored
        assert not isinstance(plain_client, TrackedJudgeClient)

    def test_uninstall_without_install_returns_false(self) -> None:
        uninstall_judge_usage_tracking()  # make sure nothing is installed
        assert uninstall_judge_usage_tracking() is False


# ---------------------------------------------------------------------------
# summary.json integration (report.add_judge_usage_to_summary)
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestAddJudgeUsageToSummary:
    def test_injects_judge_block_as_separate_line_item(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(PRICE_INPUT_ENV_VAR, raising=False)
        monkeypatch.delenv(PRICE_OUTPUT_ENV_VAR, raising=False)
        from mcp_common.testing.eval.report import add_judge_usage_to_summary

        usage = JudgeUsage(calls=1, input_tokens=10, output_tokens=5, total_tokens=15)
        summary: dict[str, Any] = {"cost_runtime": {"total_tokens": 999}}
        out = add_judge_usage_to_summary(summary, usage=usage)

        assert out is summary
        assert summary["judge_cost"]["total_tokens"] == 15
        # the model-under-test cost stays a distinct line item
        assert summary["cost_runtime"]["total_tokens"] == 999

    def test_custom_key_and_pricing(self) -> None:
        from mcp_common.testing.eval.report import add_judge_usage_to_summary

        usage = JudgeUsage(calls=0)
        summary: dict[str, Any] = {}
        add_judge_usage_to_summary(
            summary, usage=usage, pricing=JudgePricing(1.0, 1.0), key="judge"
        )
        assert summary["judge"]["cost_usd"] == 0.0
