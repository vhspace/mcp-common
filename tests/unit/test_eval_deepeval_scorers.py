"""Tests for the DeepEval quality scorer backend (vhspace/mcp-common#61).

DeepEval is an OPTIONAL extra (``eval-scoring``) that is **not** installed in
the default dev/CI sync, so these tests must run without it:

* The **scorer-level** tests (``faithfulness_scorer`` etc. in ``scorers.py``)
  patch the backend ``score_*`` functions — verifying transcript extraction
  (tool outputs -> context, final response -> actual_output) and the
  ``DeepEvalResult`` -> Inspect ``Score`` mapping, without DeepEval at all.
* The **backend-level** tests inject fake ``deepeval`` modules into
  ``sys.modules`` so the real adapter code paths (custom judge-model adapter,
  metric + test-case construction, result extraction) execute against a
  faithful stand-in of the ``deepeval==3.9.7`` API.
"""

from __future__ import annotations

import importlib.machinery
import os
import sys
import types
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from inspect_ai.model import ChatMessageAssistant, ChatMessageTool, ChatMessageUser
from inspect_ai.scorer import CORRECT, INCORRECT, Target
from inspect_ai.solver import TaskState
from inspect_ai.tool import ToolCall

from mcp_common.testing.eval import deepeval_backend as deb
from mcp_common.testing.eval.deepeval_backend import DeepEvalResult, DeepEvalUnavailableError
from mcp_common.testing.eval.scorers import (
    _extract_tool_outputs,
    faithfulness_scorer,
    hallucination_scorer,
    relevancy_scorer,
)

# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _state(messages: list[Any], metadata: dict[str, Any] | None = None) -> TaskState:
    return TaskState(
        model="test/model",
        sample_id=1,
        epoch=1,
        input="test input",
        messages=messages,
        metadata=metadata,
    )


def _tool_call(function: str, arguments: dict[str, Any] | None = None) -> ToolCall:
    return ToolCall(id=f"call_{function}", function=function, arguments=arguments or {})


def _llm_response(content: str) -> MagicMock:
    choice = MagicMock()
    choice.message.content = content
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _patch_client():
    """Make ``_require_llm_client`` succeed with a dummy (client, model)."""
    return patch(
        "mcp_common.testing.eval.scorers._get_llm_client",
        return_value=(MagicMock(), "judge-model"),
    )


# ---------------------------------------------------------------------------
# Fake deepeval API (stand-in matching the deepeval==3.9.7 contract)
# ---------------------------------------------------------------------------


class _FakeLLMTestCase:
    """Records the fields a metric was handed (input/actual_output/context/...)."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        for key, value in kwargs.items():
            setattr(self, key, value)


class _FakeDeepEvalBaseLLM:
    """Mimics ``deepeval.models.base_model.DeepEvalBaseLLM`` init behaviour.

    The real base ``__init__`` calls ``self.load_model()``, so this stand-in
    does too — which exercises the adapter's attribute-ordering (``_client``
    must be set before ``super().__init__``).
    """

    def __init__(self, model: str | None = None, *args: Any, **kwargs: Any) -> None:
        self.name = model
        self.model = self.load_model()

    def load_model(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def generate(self, *args: Any, **kwargs: Any) -> str:
        raise NotImplementedError

    async def a_generate(self, *args: Any, **kwargs: Any) -> str:
        raise NotImplementedError

    def get_model_name(self, *args: Any, **kwargs: Any) -> str:
        raise NotImplementedError


def _make_fake_metric() -> type:
    class _FakeMetric:
        # Configurable per test via class attributes; the backend reads
        # instance .score/.success/.reason/.threshold after measure().
        result_score = 0.83
        result_reason = "stub reason"
        result_success = True
        last_instance: Any = None

        def __init__(
            self,
            threshold: float = 0.5,
            model: Any = None,
            include_reason: bool = False,
            async_mode: bool = True,
            **kwargs: Any,
        ) -> None:
            self.threshold = threshold
            self.model = model
            self.include_reason = include_reason
            self.async_mode = async_mode
            self.extra_kwargs = kwargs
            self.measured: Any = None
            self.score: Any = None
            self.reason: Any = None
            self.success: Any = None
            type(self).last_instance = self

        def measure(self, test_case: Any, *args: Any, **kwargs: Any) -> float:
            self.measured = test_case
            self.score = type(self).result_score
            self.reason = type(self).result_reason
            self.success = type(self).result_success
            return float(self.score)

    return _FakeMetric


@pytest.fixture
def fake_deepeval(monkeypatch: pytest.MonkeyPatch) -> types.SimpleNamespace:
    """Inject a fake ``deepeval`` package so the backend's lazy imports resolve."""
    faith = _make_fake_metric()
    hall = _make_fake_metric()
    rel = _make_fake_metric()

    metrics_mod = types.ModuleType("deepeval.metrics")
    metrics_mod.FaithfulnessMetric = faith  # type: ignore[attr-defined]
    metrics_mod.HallucinationMetric = hall  # type: ignore[attr-defined]
    metrics_mod.AnswerRelevancyMetric = rel  # type: ignore[attr-defined]

    test_case_mod = types.ModuleType("deepeval.test_case")
    test_case_mod.LLMTestCase = _FakeLLMTestCase  # type: ignore[attr-defined]

    base_model_mod = types.ModuleType("deepeval.models.base_model")
    base_model_mod.DeepEvalBaseLLM = _FakeDeepEvalBaseLLM  # type: ignore[attr-defined]

    models_mod = types.ModuleType("deepeval.models")
    models_mod.base_model = base_model_mod  # type: ignore[attr-defined]
    models_mod.DeepEvalBaseLLM = _FakeDeepEvalBaseLLM  # type: ignore[attr-defined]

    deepeval_mod = types.ModuleType("deepeval")
    deepeval_mod.__spec__ = importlib.machinery.ModuleSpec("deepeval", loader=None)
    deepeval_mod.metrics = metrics_mod  # type: ignore[attr-defined]
    deepeval_mod.test_case = test_case_mod  # type: ignore[attr-defined]
    deepeval_mod.models = models_mod  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "deepeval", deepeval_mod)
    monkeypatch.setitem(sys.modules, "deepeval.metrics", metrics_mod)
    monkeypatch.setitem(sys.modules, "deepeval.test_case", test_case_mod)
    monkeypatch.setitem(sys.modules, "deepeval.models", models_mod)
    monkeypatch.setitem(sys.modules, "deepeval.models.base_model", base_model_mod)
    monkeypatch.setattr(deb, "deepeval_available", lambda: True)

    return types.SimpleNamespace(
        FaithfulnessMetric=faith,
        HallucinationMetric=hall,
        AnswerRelevancyMetric=rel,
        LLMTestCase=_FakeLLMTestCase,
        DeepEvalBaseLLM=_FakeDeepEvalBaseLLM,
    )


# ---------------------------------------------------------------------------
# _extract_tool_outputs
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestExtractToolOutputs:
    def test_collects_tool_messages_in_order(self) -> None:
        state = _state(
            [
                ChatMessageUser(content="q"),
                ChatMessageTool(content="first result", tool_call_id="c1"),
                ChatMessageAssistant(content="thinking"),
                ChatMessageTool(content="second result", tool_call_id="c2"),
            ]
        )
        assert _extract_tool_outputs(state) == ["first result", "second result"]

    def test_skips_empty_tool_messages(self) -> None:
        state = _state(
            [
                ChatMessageTool(content="   ", tool_call_id="c1"),
                ChatMessageTool(content="real", tool_call_id="c2"),
            ]
        )
        assert _extract_tool_outputs(state) == ["real"]

    def test_ignores_non_tool_messages(self) -> None:
        state = _state(
            [
                ChatMessageUser(content="q"),
                ChatMessageAssistant(content="a"),
            ]
        )
        assert _extract_tool_outputs(state) == []


# ---------------------------------------------------------------------------
# Backend: judge-model adapter
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestBuildJudgeModel:
    def test_generate_calls_client(self, fake_deepeval: types.SimpleNamespace) -> None:
        client = MagicMock()
        client.chat.completions.create.return_value = _llm_response("judge says")

        model = deb.build_judge_model(client, "Qwen/Judge")

        assert model.get_model_name() == "Qwen/Judge"
        assert model.load_model() is client
        assert model.generate("hello") == "judge says"
        kwargs = client.chat.completions.create.call_args.kwargs
        assert kwargs["model"] == "Qwen/Judge"
        assert kwargs["messages"] == [{"role": "user", "content": "hello"}]
        assert kwargs["temperature"] == 0.0

    def test_generate_handles_none_content(self, fake_deepeval: types.SimpleNamespace) -> None:
        client = MagicMock()
        client.chat.completions.create.return_value = _llm_response(None)  # type: ignore[arg-type]
        model = deb.build_judge_model(client, "judge")
        assert model.generate("hi") == ""

    @pytest.mark.anyio
    async def test_a_generate_delegates_to_generate(
        self, fake_deepeval: types.SimpleNamespace
    ) -> None:
        client = MagicMock()
        client.chat.completions.create.return_value = _llm_response("async judge")
        model = deb.build_judge_model(client, "judge")
        assert await model.a_generate("hi") == "async judge"


# ---------------------------------------------------------------------------
# Backend: metric scoring functions
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestScoreFaithfulness:
    def test_builds_metric_and_test_case(self, fake_deepeval: types.SimpleNamespace) -> None:
        fake_deepeval.FaithfulnessMetric.result_score = 0.91
        fake_deepeval.FaithfulnessMetric.result_success = True
        fake_deepeval.FaithfulnessMetric.result_reason = "well grounded"

        result = deb.score_faithfulness(
            MagicMock(),
            "judge",
            input="what is the status?",
            actual_output="it is active",
            retrieval_context=["ctx-a", "ctx-b"],
            threshold=0.7,
        )

        assert isinstance(result, DeepEvalResult)
        assert result.metric == "faithfulness"
        assert result.score == 0.91
        assert result.success is True
        assert result.reason == "well grounded"
        assert result.threshold == 0.7

        inst = fake_deepeval.FaithfulnessMetric.last_instance
        assert inst.threshold == 0.7
        assert inst.include_reason is True
        # async_mode must be off so measure() doesn't spin a nested event loop
        assert inst.async_mode is False
        # faithfulness uses retrieval_context (not context)
        assert inst.measured.kwargs == {
            "input": "what is the status?",
            "actual_output": "it is active",
            "retrieval_context": ["ctx-a", "ctx-b"],
        }
        # the metric judges through our Together-client adapter
        assert inst.model.get_model_name() == "judge"


@pytest.mark.eval
class TestScoreHallucination:
    def test_uses_context_field(self, fake_deepeval: types.SimpleNamespace) -> None:
        result = deb.score_hallucination(
            MagicMock(),
            "judge",
            input="q",
            actual_output="a",
            context=["ground truth"],
            threshold=0.3,
        )
        assert result.metric == "hallucination"
        inst = fake_deepeval.HallucinationMetric.last_instance
        assert inst.threshold == 0.3
        assert inst.measured.kwargs == {
            "input": "q",
            "actual_output": "a",
            "context": ["ground truth"],
        }
        assert "retrieval_context" not in inst.measured.kwargs

    def test_success_mirrors_metric_verdict(self, fake_deepeval: types.SimpleNamespace) -> None:
        # hallucination is "lower is better"; the metric owns the direction, we
        # surface its .success verbatim (here: a failing/hallucinated output).
        fake_deepeval.HallucinationMetric.result_score = 0.8
        fake_deepeval.HallucinationMetric.result_success = False
        result = deb.score_hallucination(
            MagicMock(), "judge", input="q", actual_output="a", context=["c"]
        )
        assert result.score == 0.8
        assert result.success is False


@pytest.mark.eval
class TestScoreAnswerRelevancy:
    def test_no_context_fields(self, fake_deepeval: types.SimpleNamespace) -> None:
        result = deb.score_answer_relevancy(
            MagicMock(), "judge", input="q", actual_output="a", threshold=0.6
        )
        assert result.metric == "relevancy"
        inst = fake_deepeval.AnswerRelevancyMetric.last_instance
        assert inst.threshold == 0.6
        assert inst.measured.kwargs == {"input": "q", "actual_output": "a"}


# ---------------------------------------------------------------------------
# Backend: guarded import + telemetry opt-out
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestDeepEvalGuard:
    def test_raises_when_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(deb, "deepeval_available", lambda: False)
        with pytest.raises(DeepEvalUnavailableError, match="eval-scoring"):
            deb.score_faithfulness(
                MagicMock(), "m", input="q", actual_output="a", retrieval_context=["c"]
            )

    def test_prepare_sets_telemetry_opt_out(
        self, fake_deepeval: types.SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for key in (
            "DEEPEVAL_TELEMETRY_OPT_OUT",
            "ERROR_REPORTING",
            "DEEPEVAL_DISABLE_PROGRESS_BAR",
        ):
            monkeypatch.delenv(key, raising=False)
        deb._prepare_deepeval()
        assert os.environ["DEEPEVAL_TELEMETRY_OPT_OUT"] == "YES"
        assert os.environ["ERROR_REPORTING"] == "NO"
        assert os.environ["DEEPEVAL_DISABLE_PROGRESS_BAR"] == "YES"


# ---------------------------------------------------------------------------
# Scorers (backend patched) — transcript extraction + Score mapping
# ---------------------------------------------------------------------------


def _grounded_state() -> TaskState:
    tc = _tool_call("get_device", {"id": "1"})
    return _state(
        [
            ChatMessageUser(content="find device 1"),
            ChatMessageAssistant(content="calling", tool_calls=[tc]),
            ChatMessageTool(content="device 1: srv1, active", tool_call_id="call_get_device"),
            ChatMessageAssistant(content="Device 1 is srv1 and active."),
        ],
        metadata={"input": "find device 1"},
    )


@pytest.mark.eval
class TestFaithfulnessScorer:
    @pytest.mark.anyio
    async def test_pass_maps_to_correct(self) -> None:
        result_obj = DeepEvalResult("faithfulness", 0.9, True, "grounded", 0.5)
        with (
            _patch_client(),
            patch(
                "mcp_common.testing.eval.deepeval_backend.score_faithfulness",
                return_value=result_obj,
            ) as mock_score,
        ):
            score = await faithfulness_scorer()(_grounded_state(), Target(""))

        assert score.value == CORRECT
        assert score.metadata["faithfulness_score"] == 0.9
        assert score.metadata["success"] is True
        assert score.metadata["deepeval_metric"] == "faithfulness"
        assert score.answer == "Device 1 is srv1 and active."
        kwargs = mock_score.call_args.kwargs
        assert kwargs["retrieval_context"] == ["device 1: srv1, active"]
        assert kwargs["input"] == "find device 1"
        assert kwargs["actual_output"] == "Device 1 is srv1 and active."

    @pytest.mark.anyio
    async def test_fail_maps_to_incorrect(self) -> None:
        result_obj = DeepEvalResult("faithfulness", 0.2, False, "unsupported claims", 0.5)
        with (
            _patch_client(),
            patch(
                "mcp_common.testing.eval.deepeval_backend.score_faithfulness",
                return_value=result_obj,
            ),
        ):
            score = await faithfulness_scorer()(_grounded_state(), Target(""))
        assert score.value == INCORRECT
        assert score.metadata["faithfulness_score"] == 0.2

    @pytest.mark.anyio
    async def test_no_response_short_circuits(self) -> None:
        state = _state(
            [
                ChatMessageUser(content="q"),
                ChatMessageTool(content="some data", tool_call_id="c1"),
            ],
            metadata={"input": "q"},
        )
        with (
            _patch_client(),
            patch("mcp_common.testing.eval.deepeval_backend.score_faithfulness") as mock_score,
        ):
            score = await faithfulness_scorer()(state, Target(""))
        assert score.value == INCORRECT
        assert score.metadata["faithfulness_score"] is None
        mock_score.assert_not_called()

    @pytest.mark.anyio
    async def test_no_tool_output_short_circuits(self) -> None:
        state = _state(
            [
                ChatMessageUser(content="q"),
                ChatMessageAssistant(content="I think it is active."),
            ],
            metadata={"input": "q"},
        )
        with (
            _patch_client(),
            patch("mcp_common.testing.eval.deepeval_backend.score_faithfulness") as mock_score,
        ):
            score = await faithfulness_scorer()(state, Target(""))
        assert score.value == INCORRECT
        assert "no tool outputs" in score.explanation.lower()
        mock_score.assert_not_called()

    @pytest.mark.anyio
    async def test_raises_without_api_key(self) -> None:
        with patch("mcp_common.testing.eval.scorers._get_llm_client", return_value=None):
            with pytest.raises(RuntimeError, match="TOGETHER_API_KEY"):
                await faithfulness_scorer()(_grounded_state(), Target(""))


@pytest.mark.eval
class TestHallucinationScorer:
    @pytest.mark.anyio
    async def test_passes_tool_output_as_context(self) -> None:
        result_obj = DeepEvalResult("hallucination", 0.0, True, "no fabrication", 0.5)
        with (
            _patch_client(),
            patch(
                "mcp_common.testing.eval.deepeval_backend.score_hallucination",
                return_value=result_obj,
            ) as mock_score,
        ):
            score = await hallucination_scorer()(_grounded_state(), Target(""))
        assert score.value == CORRECT
        assert score.metadata["hallucination_score"] == 0.0
        kwargs = mock_score.call_args.kwargs
        assert kwargs["context"] == ["device 1: srv1, active"]

    @pytest.mark.anyio
    async def test_no_tool_output_short_circuits(self) -> None:
        state = _state(
            [ChatMessageAssistant(content="answer with no tool data")],
            metadata={"input": "q"},
        )
        with (
            _patch_client(),
            patch("mcp_common.testing.eval.deepeval_backend.score_hallucination") as mock_score,
        ):
            score = await hallucination_scorer()(state, Target(""))
        assert score.value == INCORRECT
        mock_score.assert_not_called()


@pytest.mark.eval
class TestRelevancyScorer:
    @pytest.mark.anyio
    async def test_scores_without_tool_context(self) -> None:
        # relevancy needs no retrieval context: it scores even with no tool calls
        state = _state(
            [
                ChatMessageUser(content="what is 2+2?"),
                ChatMessageAssistant(content="The answer is 4."),
            ],
            metadata={"input": "what is 2+2?"},
        )
        result_obj = DeepEvalResult("relevancy", 0.95, True, "on topic", 0.5)
        with (
            _patch_client(),
            patch(
                "mcp_common.testing.eval.deepeval_backend.score_answer_relevancy",
                return_value=result_obj,
            ) as mock_score,
        ):
            score = await relevancy_scorer()(state, Target(""))
        assert score.value == CORRECT
        assert score.metadata["relevancy_score"] == 0.95
        kwargs = mock_score.call_args.kwargs
        assert kwargs["input"] == "what is 2+2?"
        assert kwargs["actual_output"] == "The answer is 4."
        assert "context" not in kwargs

    @pytest.mark.anyio
    async def test_no_response_short_circuits(self) -> None:
        state = _state([ChatMessageUser(content="q")], metadata={"input": "q"})
        with (
            _patch_client(),
            patch("mcp_common.testing.eval.deepeval_backend.score_answer_relevancy") as mock_score,
        ):
            score = await relevancy_scorer()(state, Target(""))
        assert score.value == INCORRECT
        mock_score.assert_not_called()

    @pytest.mark.anyio
    async def test_raises_without_api_key(self) -> None:
        state = _state([ChatMessageAssistant(content="answer")], metadata={"input": "q"})
        with patch("mcp_common.testing.eval.scorers._get_llm_client", return_value=None):
            with pytest.raises(RuntimeError, match="TOGETHER_API_KEY"):
                await relevancy_scorer()(state, Target(""))
