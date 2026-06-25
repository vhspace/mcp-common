"""Tests for DeepEval-on-failures post-run hook (#88 Phase 4b).

DeepEval is an OPTIONAL ``[eval-scoring]`` extra not installed in the default
dev/CI sync, so these tests must run without it:

* ``collect_failure_samples`` extracts INCORRECT/PARTIAL samples (with the
  response + tool outputs DeepEval needs) from mock Inspect logs — no DeepEval.
* ``run_deepeval_on_failures`` patches ``deepeval_available`` + the backend
  ``score_faithfulness`` / ``score_hallucination`` functions, verifying the
  failure filter + skip short-circuits + verdict aggregation without any real
  DeepEval or model calls.
* The unavailability path raises the backend's typed error with the install hint.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from inspect_ai.model import ChatMessageAssistant, ChatMessageTool, ChatMessageUser
from inspect_ai.scorer import CORRECT, INCORRECT, PARTIAL, Score

from mcp_common.testing.eval.deepeval_backend import DeepEvalResult
from mcp_common.testing.eval.deepeval_on_failures import (
    DeepEvalFailureReport,
    DeepEvalUnavailableError,
    FailureSample,
    build_deepeval_failure_markdown,
    collect_failure_samples,
    run_deepeval_on_failures,
    summarize_deepeval_failures,
)

# ---------------------------------------------------------------------------
# Mock Inspect log builders
# ---------------------------------------------------------------------------


def _sample(
    *,
    input_text: str,
    response: str,
    tool_outputs: list[str] | None = None,
    sample_id: int | str = 1,
    score_value: str = INCORRECT,
) -> MagicMock:
    msgs: list[Any] = [ChatMessageUser(content=input_text)]
    for i, out in enumerate(tool_outputs or []):
        msgs.append(ChatMessageTool(content=out, tool_call_id=f"c{i}"))
    if response:
        msgs.append(ChatMessageAssistant(content=response))
    sample = MagicMock()
    sample.id = sample_id
    sample.input = input_text
    sample.messages = msgs
    sample.scores = {"tool_use": Score(value=score_value, explanation="")}
    sample.metadata = {"input": input_text}
    return sample


def _eval_log(
    *,
    task: str = "netbox_mcp_eval",
    samples: list[Any] | None = None,
) -> MagicMock:
    log = MagicMock()
    log.eval.task = task
    log.samples = samples
    log.location = "/tmp/test.eval"
    return log


def _touch(*paths: Path) -> None:
    for p in paths:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()


def _patch_client() -> Any:
    """Make _require_llm_client succeed with a dummy (client, model).

    _require_llm_client lives in scorers and calls scorers._get_llm_client.
    """
    return patch(
        "mcp_common.testing.eval.scorers._get_llm_client",
        return_value=(MagicMock(), "judge-model"),
    )


# ---------------------------------------------------------------------------
# collect_failure_samples
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestCollectFailureSamples:
    def test_single_log_filters_to_failures(self, tmp_path: Path) -> None:
        log = _eval_log(
            samples=[
                _sample(input_text="fail q", response="bad answer", tool_outputs=["ctx"]),
                _sample(input_text="pass q", response="good", score_value=CORRECT, sample_id=2),
            ]
        )
        path = tmp_path / "x.eval"
        _touch(path)
        with patch(
            "mcp_common.testing.eval.deepeval_on_failures.read_eval_log",
            return_value=log,
        ):
            failures = collect_failure_samples(path)
        assert len(failures) == 1
        f = failures[0]
        assert f.server == "netbox-mcp"
        assert f.input == "fail q"
        assert f.actual_output == "bad answer"
        assert f.context == ["ctx"]
        assert f.score == INCORRECT
        assert f.log_path == str(path)

    def test_partial_is_collected(self, tmp_path: Path) -> None:
        log = _eval_log(
            samples=[_sample(input_text="partial q", response="r", score_value=PARTIAL)]
        )
        path = tmp_path / "x.eval"
        _touch(path)
        with patch(
            "mcp_common.testing.eval.deepeval_on_failures.read_eval_log",
            return_value=log,
        ):
            failures = collect_failure_samples(path)
        assert len(failures) == 1
        assert failures[0].score == PARTIAL

    def test_scoreless_sample_skipped(self, tmp_path: Path) -> None:
        sample = MagicMock()
        sample.id = 1
        sample.input = "no scores"
        sample.messages = [ChatMessageUser(content="no scores")]
        sample.scores = None
        sample.metadata = {"input": "no scores"}
        log = _eval_log(samples=[sample])
        path = tmp_path / "x.eval"
        _touch(path)
        with patch(
            "mcp_common.testing.eval.deepeval_on_failures.read_eval_log",
            return_value=log,
        ):
            failures = collect_failure_samples(path)
        assert failures == []

    def test_directory_reads_all(self, tmp_path: Path) -> None:
        _touch(tmp_path / "a.eval", tmp_path / "b.eval")
        log_a = _eval_log(samples=[_sample(input_text="a", response="r", sample_id=1)])
        log_b = _eval_log(samples=[_sample(input_text="b", response="r", sample_id=2)])

        def fake_read(p: Path) -> MagicMock:
            return log_a if p.name == "a.eval" else log_b

        with patch(
            "mcp_common.testing.eval.deepeval_on_failures.read_eval_log",
            side_effect=fake_read,
        ):
            failures = collect_failure_samples(tmp_path)
        assert {f.input for f in failures} == {"a", "b"}

    def test_missing_path_returns_empty(self, tmp_path: Path) -> None:
        assert collect_failure_samples(tmp_path / "nope.eval") == []

    def test_all_passing_returns_empty(self, tmp_path: Path) -> None:
        log = _eval_log(samples=[_sample(input_text="q", response="r", score_value=CORRECT)])
        path = tmp_path / "x.eval"
        _touch(path)
        with patch(
            "mcp_common.testing.eval.deepeval_on_failures.read_eval_log",
            return_value=log,
        ):
            failures = collect_failure_samples(path)
        assert failures == []

    def test_no_tool_outputs_collected_as_empty(self, tmp_path: Path) -> None:
        log = _eval_log(samples=[_sample(input_text="q", response="r", tool_outputs=[])])
        path = tmp_path / "x.eval"
        _touch(path)
        with patch(
            "mcp_common.testing.eval.deepeval_on_failures.read_eval_log",
            return_value=log,
        ):
            failures = collect_failure_samples(path)
        assert failures[0].context == []


# ---------------------------------------------------------------------------
# run_deepeval_on_failures
# ---------------------------------------------------------------------------


def _patch_deepeval_available() -> Any:
    return patch(
        "mcp_common.testing.eval.deepeval_on_failures.deepeval_available",
        return_value=True,
    )


@pytest.mark.eval
class TestRunDeepEvalOnFailures:
    def test_scores_each_failure(self, tmp_path: Path) -> None:
        log = _eval_log(
            samples=[
                _sample(
                    input_text="fail q",
                    response="grounded answer",
                    tool_outputs=["ctx data"],
                )
            ]
        )
        path = tmp_path / "x.eval"
        _touch(path)
        faith = DeepEvalResult("faithfulness", 0.9, True, "grounded", 0.5)
        hall = DeepEvalResult("hallucination", 0.1, True, "no fabrication", 0.5)

        with (
            patch(
                "mcp_common.testing.eval.deepeval_on_failures.read_eval_log",
                return_value=log,
            ),
            _patch_deepeval_available(),
            _patch_client(),
            patch(
                "mcp_common.testing.eval.deepeval_backend.score_faithfulness",
                return_value=faith,
            ) as mock_faith,
            patch(
                "mcp_common.testing.eval.deepeval_backend.score_hallucination",
                return_value=hall,
            ) as mock_hall,
        ):
            report = run_deepeval_on_failures(path)

        assert len(report.samples) == 1
        s = report.samples[0]
        assert s["faithfulness"]["score"] == 0.9
        assert s["faithfulness"]["success"] is True
        assert s["hallucination"]["score"] == 0.1
        assert s["hallucination"]["success"] is True
        # backend received the right DeepEval fields
        faith_kwargs = mock_faith.call_args.kwargs
        assert faith_kwargs["actual_output"] == "grounded answer"
        assert faith_kwargs["retrieval_context"] == ["ctx data"]
        hall_kwargs = mock_hall.call_args.kwargs
        assert hall_kwargs["context"] == ["ctx data"]

    def test_no_failures_returns_empty_report(self, tmp_path: Path) -> None:
        log = _eval_log(samples=[_sample(input_text="q", response="r", score_value=CORRECT)])
        path = tmp_path / "x.eval"
        _touch(path)
        with (
            patch(
                "mcp_common.testing.eval.deepeval_on_failures.read_eval_log",
                return_value=log,
            ),
            _patch_deepeval_available(),
            _patch_client(),
            patch("mcp_common.testing.eval.deepeval_backend.score_faithfulness") as mock_faith,
        ):
            report = run_deepeval_on_failures(path)
        assert report.samples == []
        mock_faith.assert_not_called()

    def test_skip_no_response(self, tmp_path: Path) -> None:
        log = _eval_log(samples=[_sample(input_text="q", response="", tool_outputs=["ctx"])])
        path = tmp_path / "x.eval"
        _touch(path)
        with (
            patch(
                "mcp_common.testing.eval.deepeval_on_failures.read_eval_log",
                return_value=log,
            ),
            _patch_deepeval_available(),
            _patch_client(),
            patch("mcp_common.testing.eval.deepeval_backend.score_faithfulness") as mock_faith,
            patch("mcp_common.testing.eval.deepeval_backend.score_hallucination") as mock_hall,
        ):
            report = run_deepeval_on_failures(path)
        assert report.skipped_no_response == 1
        s = report.samples[0]
        assert s["faithfulness"]["score"] is None
        assert s["hallucination"]["score"] is None
        mock_faith.assert_not_called()
        mock_hall.assert_not_called()

    def test_skip_no_context(self, tmp_path: Path) -> None:
        log = _eval_log(samples=[_sample(input_text="q", response="answer", tool_outputs=[])])
        path = tmp_path / "x.eval"
        _touch(path)
        with (
            patch(
                "mcp_common.testing.eval.deepeval_on_failures.read_eval_log",
                return_value=log,
            ),
            _patch_deepeval_available(),
            _patch_client(),
            patch("mcp_common.testing.eval.deepeval_backend.score_faithfulness") as mock_faith,
        ):
            report = run_deepeval_on_failures(path)
        assert report.skipped_no_context == 1
        assert report.samples[0]["faithfulness"]["score"] is None
        mock_faith.assert_not_called()

    def test_raises_when_deepeval_unavailable(self, tmp_path: Path) -> None:
        path = tmp_path / "x.eval"
        _touch(path)
        with (
            patch(
                "mcp_common.testing.eval.deepeval_on_failures.deepeval_available",
                return_value=False,
            ),
            patch(
                "mcp_common.testing.eval.deepeval_on_failures.read_eval_log",
            ) as mock_read,
        ):
            with pytest.raises(DeepEvalUnavailableError, match="eval-scoring"):
                run_deepeval_on_failures(path)
        # The availability guard fires before any log is read.
        mock_read.assert_not_called()

    def test_raises_without_api_key(self, tmp_path: Path) -> None:
        log = _eval_log(samples=[_sample(input_text="q", response="r", tool_outputs=["c"])])
        path = tmp_path / "x.eval"
        _touch(path)
        with (
            patch(
                "mcp_common.testing.eval.deepeval_on_failures.read_eval_log",
                return_value=log,
            ),
            _patch_deepeval_available(),
            patch("mcp_common.testing.eval.scorers._get_llm_client", return_value=None),
        ):
            with pytest.raises(RuntimeError, match="TOGETHER_API_KEY"):
                run_deepeval_on_failures(path)

    def test_thresholds_forwarded(self, tmp_path: Path) -> None:
        log = _eval_log(samples=[_sample(input_text="q", response="r", tool_outputs=["c"])])
        path = tmp_path / "x.eval"
        _touch(path)
        faith = DeepEvalResult("faithfulness", 0.9, True, "ok", 0.7)
        hall = DeepEvalResult("hallucination", 0.1, True, "ok", 0.3)
        with (
            patch(
                "mcp_common.testing.eval.deepeval_on_failures.read_eval_log",
                return_value=log,
            ),
            _patch_deepeval_available(),
            _patch_client(),
            patch(
                "mcp_common.testing.eval.deepeval_backend.score_faithfulness",
                return_value=faith,
            ) as mock_faith,
            patch(
                "mcp_common.testing.eval.deepeval_backend.score_hallucination",
                return_value=hall,
            ) as mock_hall,
        ):
            run_deepeval_on_failures(path, faithfulness_threshold=0.7, hallucination_threshold=0.3)
        assert mock_faith.call_args.kwargs["threshold"] == 0.7
        assert mock_hall.call_args.kwargs["threshold"] == 0.3


# ---------------------------------------------------------------------------
# summarize / markdown
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestDeepEvalFailureReporting:
    def test_summarize_full(self) -> None:
        report = DeepEvalFailureReport(
            source_path="x.eval",
            samples=[
                {
                    "server": "netbox-mcp",
                    "input": "q1",
                    "score": "I",
                    "log_path": "x.eval",
                    "faithfulness": {
                        "metric": "faithfulness",
                        "score": 0.9,
                        "success": True,
                        "reason": "grounded",
                        "threshold": 0.5,
                    },
                    "hallucination": {
                        "metric": "hallucination",
                        "score": 0.1,
                        "success": True,
                        "reason": "ok",
                        "threshold": 0.5,
                    },
                },
                {
                    "server": "netbox-mcp",
                    "input": "q2",
                    "score": "I",
                    "log_path": "x.eval",
                    "faithfulness": {
                        "metric": "faithfulness",
                        "score": 0.2,
                        "success": False,
                        "reason": "unsupported",
                        "threshold": 0.5,
                    },
                    "hallucination": {
                        "metric": "hallucination",
                        "score": 0.8,
                        "success": False,
                        "reason": "fabricated",
                        "threshold": 0.5,
                    },
                },
            ],
            skipped_no_response=0,
            skipped_no_context=0,
        )
        summary = summarize_deepeval_failures(report)
        assert summary["failures_collected"] == 2
        assert summary["faithfulness"]["judged"] == 2
        assert summary["faithfulness"]["pass"] == 1
        assert summary["faithfulness"]["fail"] == 1
        assert summary["hallucination"]["pass"] == 1
        assert summary["hallucination"]["fail"] == 1

    def test_summarize_skips_none_scores(self) -> None:
        report = DeepEvalFailureReport(
            samples=[
                {
                    "server": "s",
                    "input": "q",
                    "score": "I",
                    "log_path": "x",
                    "faithfulness": {
                        "metric": "faithfulness",
                        "score": None,
                        "success": None,
                        "reason": "no agent response",
                        "threshold": None,
                    },
                    "hallucination": {
                        "metric": "hallucination",
                        "score": None,
                        "success": None,
                        "reason": "no agent response",
                        "threshold": None,
                    },
                }
            ],
            skipped_no_response=1,
        )
        summary = summarize_deepeval_failures(report)
        assert summary["faithfulness"]["judged"] == 0
        assert summary["skipped_no_response"] == 1

    def test_markdown_contains_summary_and_rows(self) -> None:
        report = DeepEvalFailureReport(
            source_path="x.eval",
            samples=[
                {
                    "server": "netbox-mcp",
                    "input": "fail q",
                    "score": "I",
                    "log_path": "x.eval",
                    "faithfulness": {
                        "metric": "faithfulness",
                        "score": 0.9,
                        "success": True,
                        "reason": "grounded",
                        "threshold": 0.5,
                    },
                    "hallucination": {
                        "metric": "hallucination",
                        "score": 0.1,
                        "success": True,
                        "reason": "no fabrication",
                        "threshold": 0.5,
                    },
                }
            ],
        )
        md = build_deepeval_failure_markdown(report)
        assert "## DeepEval on failures report" in md
        assert "faithfulness" in md
        assert "fail q" in md
        assert "pass" in md

    def test_markdown_handles_empty_report(self) -> None:
        report = DeepEvalFailureReport(source_path="x.eval")
        md = build_deepeval_failure_markdown(report)
        assert "failures collected: **0**" in md


# ---------------------------------------------------------------------------
# FailureSample dataclass
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestFailureSample:
    def test_construction(self) -> None:
        s = FailureSample(
            server="netbox-mcp",
            input="q",
            actual_output="a",
            context=["c1", "c2"],
            score="I",
            log_path="/tmp/x.eval",
        )
        assert s.server == "netbox-mcp"
        assert s.context == ["c1", "c2"]
        assert s.score == "I"
