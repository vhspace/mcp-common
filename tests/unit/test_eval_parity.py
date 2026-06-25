"""Tests for the MCP ↔ CLI parity log-comparison helper (#88 Phase 4a).

The parity helper compares two Inspect ``.eval`` logs (or dirs) by pairing
samples on input text and judging response equivalence through the shared
LLM-as-judge. These tests use mock :class:`EvalSample` / :class:`EvalLog`
objects and patch ``read_eval_log`` + the judge client — no real ``.eval`` files
and no live model calls.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from inspect_ai.model import ChatMessageAssistant, ChatMessageUser
from inspect_ai.scorer import Score

from mcp_common.testing.eval.parity import (
    ParityComparison,
    ParityReport,
    build_parity_markdown,
    compare_eval_logs,
    compare_logs,
    load_samples_by_input,
    summarize_parity,
)

# ---------------------------------------------------------------------------
# Mock Inspect log builders
# ---------------------------------------------------------------------------


def _sample(
    *,
    input_text: str,
    response: str,
    sample_id: int | str = 1,
    scores: dict[str, Score] | None = None,
) -> MagicMock:
    sample = MagicMock()
    sample.id = sample_id
    sample.input = input_text
    sample.scores = scores
    sample.messages = [
        ChatMessageUser(content=input_text),
        ChatMessageAssistant(content=response),
    ]
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
    """Create empty placeholder files so path-existence checks pass before read_eval_log is patched."""
    for p in paths:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()


def _patch_judge(score: float, explanation: str = "equivalent") -> Any:
    """Patch the shared judge (_judge) to return a fixed (score, explanation)."""
    return patch(
        "mcp_common.testing.eval.parity._judge",
        return_value=(score, explanation),
    )


def _patch_client() -> Any:
    """Make _require_llm_client succeed with a dummy (client, model).

    _require_llm_client lives in scorers and calls scorers._get_llm_client, so
    patch there (not parity's re-export) for deterministic behavior without an
    env API key.
    """
    return patch(
        "mcp_common.testing.eval.scorers._get_llm_client",
        return_value=(MagicMock(), "judge-model"),
    )


# ---------------------------------------------------------------------------
# load_samples_by_input
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestLoadSamplesByInput:
    def test_single_log_file(self, tmp_path: Path) -> None:
        log = _eval_log(samples=[_sample(input_text="list devices", response="srv1, srv2")])
        path = tmp_path / "ref.eval"
        _touch(path)
        with patch("mcp_common.testing.eval.parity.read_eval_log", return_value=log):
            result = load_samples_by_input(path)
        assert "list devices" in result
        assert result["list devices"].input == "list devices"

    def test_directory_reads_all_eval_files(self, tmp_path: Path) -> None:
        (tmp_path / "a.eval").touch()
        (tmp_path / "b.eval").touch()
        (tmp_path / "ignore.json").touch()
        log_a = _eval_log(samples=[_sample(input_text="q a", response="a", sample_id=1)])
        log_b = _eval_log(samples=[_sample(input_text="q b", response="b", sample_id=2)])

        files_seen: list[str] = []

        def fake_read(p: Path) -> MagicMock:
            files_seen.append(p.name)
            return log_a if p.name == "a.eval" else log_b

        with patch("mcp_common.testing.eval.parity.read_eval_log", side_effect=fake_read):
            result = load_samples_by_input(tmp_path)

        assert set(result) == {"q a", "q b"}
        assert set(files_seen) == {"a.eval", "b.eval"}

    def test_missing_path_returns_empty(self, tmp_path: Path) -> None:
        assert load_samples_by_input(tmp_path / "nope.eval") == {}

    def test_duplicate_input_latest_wins(self, tmp_path: Path) -> None:
        # Two files in a dir with the same input: the second (sorted) file's
        # sample overwrites the first's, so "latest wins" on duplicate inputs.
        first = _eval_log(samples=[_sample(input_text="dup", response="old", sample_id=1)])
        second = _eval_log(samples=[_sample(input_text="dup", response="new", sample_id=2)])
        _touch(tmp_path / "a.eval", tmp_path / "b.eval")

        def fake_read(p: Path) -> MagicMock:
            return first if p.name == "a.eval" else second

        with patch("mcp_common.testing.eval.parity.read_eval_log", side_effect=fake_read):
            result = load_samples_by_input(tmp_path)
        assert "dup" in result
        # b.eval sorts after a.eval, so its sample (response "new") wins
        assert result["dup"].messages[-1].text == "new"

    def test_empty_input_skipped(self, tmp_path: Path) -> None:
        log = _eval_log(samples=[_sample(input_text="   ", response="r")])
        path = tmp_path / "x.eval"
        _touch(path)
        with patch("mcp_common.testing.eval.parity.read_eval_log", return_value=log):
            result = load_samples_by_input(path)
        assert result == {}

    def test_read_error_is_swallowed(self, tmp_path: Path) -> None:
        _touch(tmp_path / "bad.eval", tmp_path / "good.eval")
        good_log = _eval_log(samples=[_sample(input_text="ok", response="r")])

        def fake_read(p: Path) -> MagicMock:
            if p.name == "bad.eval":
                raise ValueError("corrupt")
            return good_log

        with patch("mcp_common.testing.eval.parity.read_eval_log", side_effect=fake_read):
            result = load_samples_by_input(tmp_path)
        assert set(result) == {"ok"}


# ---------------------------------------------------------------------------
# compare_eval_logs
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestCompareEvalLogs:
    def test_pairs_and_judges_shared_inputs(self, tmp_path: Path) -> None:
        ref_log = _eval_log(
            task="netbox_mcp_eval",
            samples=[_sample(input_text="list devices", response="srv1, srv2")],
        )
        cand_log = _eval_log(
            task="netbox_cli_eval",
            samples=[_sample(input_text="list devices", response="Found srv1 and srv2")],
        )
        ref_path, cand_path = tmp_path / "ref.eval", tmp_path / "cand.eval"
        _touch(ref_path, cand_path)

        with (
            patch(
                "mcp_common.testing.eval.parity.read_eval_log",
                side_effect=[ref_log, cand_log],
            ),
            _patch_client(),
            _patch_judge(0.95, "same meaning"),
        ):
            report = compare_eval_logs(ref_path, cand_path)

        assert len(report.comparisons) == 1
        c = report.comparisons[0]
        assert c.input == "list devices"
        assert c.reference_response == "srv1, srv2"
        assert c.candidate_response == "Found srv1 and srv2"
        assert c.score == 0.95
        assert c.equivalent is True
        assert c.reference_task == "netbox_mcp_eval"
        assert c.candidate_task == "netbox_cli_eval"

    def test_below_threshold_is_not_equivalent(self, tmp_path: Path) -> None:
        ref_log = _eval_log(samples=[_sample(input_text="q", response="answer A")])
        cand_log = _eval_log(samples=[_sample(input_text="q", response="totally different")])
        ref_path, cand_path = tmp_path / "ref.eval", tmp_path / "cand.eval"
        _touch(ref_path, cand_path)
        with (
            patch(
                "mcp_common.testing.eval.parity.read_eval_log",
                side_effect=[ref_log, cand_log],
            ),
            _patch_client(),
            _patch_judge(0.2, "contradictory"),
        ):
            report = compare_eval_logs(ref_path, cand_path)
        assert report.comparisons[0].equivalent is False
        assert report.comparisons[0].score == 0.2

    def test_partial_threshold_is_not_equivalent(self, tmp_path: Path) -> None:
        # 0.5 is below the 0.8 CORRECT threshold -> not equivalent
        ref_log = _eval_log(samples=[_sample(input_text="q", response="A")])
        cand_log = _eval_log(samples=[_sample(input_text="q", response="B")])
        ref_path, cand_path = tmp_path / "ref.eval", tmp_path / "cand.eval"
        _touch(ref_path, cand_path)
        with (
            patch(
                "mcp_common.testing.eval.parity.read_eval_log",
                side_effect=[ref_log, cand_log],
            ),
            _patch_client(),
            _patch_judge(0.5, "mostly equivalent with minor gaps"),
        ):
            report = compare_eval_logs(ref_path, cand_path)
        assert report.comparisons[0].equivalent is False
        assert report.comparisons[0].score == 0.5

    def test_coverage_drift_recorded(self, tmp_path: Path) -> None:
        ref_log = _eval_log(
            samples=[
                _sample(input_text="shared", response="r"),
                _sample(input_text="ref-only", response="r", sample_id=2),
            ]
        )
        cand_log = _eval_log(
            samples=[
                _sample(input_text="shared", response="c"),
                _sample(input_text="cand-only", response="c", sample_id=2),
            ]
        )
        ref_path, cand_path = tmp_path / "ref.eval", tmp_path / "cand.eval"
        _touch(ref_path, cand_path)
        with (
            patch(
                "mcp_common.testing.eval.parity.read_eval_log",
                side_effect=[ref_log, cand_log],
            ),
            _patch_client(),
            _patch_judge(1.0, "eq"),
        ):
            report = compare_eval_logs(ref_path, cand_path)
        assert report.reference_only == ["ref-only"]
        assert report.candidate_only == ["cand-only"]
        assert [c.input for c in report.comparisons] == ["shared"]

    def test_empty_side_response_is_skipped(self, tmp_path: Path) -> None:
        ref_log = _eval_log(samples=[_sample(input_text="q", response="real answer")])
        # candidate produced no assistant text
        cand_sample = MagicMock()
        cand_sample.id = 1
        cand_sample.input = "q"
        cand_sample.messages = [ChatMessageUser(content="q")]
        cand_sample.metadata = {"input": "q"}
        cand_log = _eval_log(samples=[cand_sample])
        ref_path, cand_path = tmp_path / "ref.eval", tmp_path / "cand.eval"
        _touch(ref_path, cand_path)

        with (
            patch(
                "mcp_common.testing.eval.parity.read_eval_log",
                side_effect=[ref_log, cand_log],
            ),
            _patch_client(),
            patch("mcp_common.testing.eval.parity._judge") as mock_judge,
        ):
            report = compare_eval_logs(ref_path, cand_path)
        c = report.comparisons[0]
        assert c.score is None
        assert c.equivalent is None
        assert "no response" in c.explanation.lower()
        mock_judge.assert_not_called()

    def test_no_shared_inputs(self, tmp_path: Path) -> None:
        ref_log = _eval_log(samples=[_sample(input_text="a", response="r")])
        cand_log = _eval_log(samples=[_sample(input_text="b", response="c")])
        ref_path, cand_path = tmp_path / "ref.eval", tmp_path / "cand.eval"
        _touch(ref_path, cand_path)
        with (
            patch(
                "mcp_common.testing.eval.parity.read_eval_log",
                side_effect=[ref_log, cand_log],
            ),
            _patch_client(),
            patch("mcp_common.testing.eval.parity._judge") as mock_judge,
        ):
            report = compare_eval_logs(ref_path, cand_path)
        assert report.comparisons == []
        assert report.reference_only == ["a"]
        assert report.candidate_only == ["b"]
        mock_judge.assert_not_called()

    def test_judge_failure_is_none(self, tmp_path: Path) -> None:
        ref_log = _eval_log(samples=[_sample(input_text="q", response="A")])
        cand_log = _eval_log(samples=[_sample(input_text="q", response="B")])
        ref_path, cand_path = tmp_path / "ref.eval", tmp_path / "cand.eval"
        _touch(ref_path, cand_path)
        with (
            patch(
                "mcp_common.testing.eval.parity.read_eval_log",
                side_effect=[ref_log, cand_log],
            ),
            _patch_client(),
            patch("mcp_common.testing.eval.parity._judge", return_value=(None, "unparseable")),
        ):
            report = compare_eval_logs(ref_path, cand_path)
        assert report.comparisons[0].score is None
        assert report.comparisons[0].equivalent is None

    def test_raises_without_api_key(self, tmp_path: Path) -> None:
        ref_log = _eval_log(samples=[_sample(input_text="q", response="A")])
        cand_log = _eval_log(samples=[_sample(input_text="q", response="B")])
        ref_path, cand_path = tmp_path / "ref.eval", tmp_path / "cand.eval"
        _touch(ref_path, cand_path)
        with (
            patch(
                "mcp_common.testing.eval.parity.read_eval_log",
                side_effect=[ref_log, cand_log],
            ),
            # _require_llm_client lives in scorers and calls scorers._get_llm_client
            patch("mcp_common.testing.eval.scorers._get_llm_client", return_value=None),
        ):
            with pytest.raises(RuntimeError, match="TOGETHER_API_KEY"):
                compare_eval_logs(ref_path, cand_path)

    def test_compare_logs_alias(self, tmp_path: Path) -> None:
        """compare_logs is a public alias for compare_eval_logs."""
        ref_log = _eval_log(samples=[_sample(input_text="q", response="A")])
        cand_log = _eval_log(samples=[_sample(input_text="q", response="B")])
        ref_path, cand_path = tmp_path / "ref.eval", tmp_path / "cand.eval"
        _touch(ref_path, cand_path)
        with (
            patch(
                "mcp_common.testing.eval.parity.read_eval_log",
                side_effect=[ref_log, cand_log],
            ),
            _patch_client(),
            _patch_judge(1.0, "eq"),
        ):
            report = compare_logs(ref_path, cand_path)
        assert len(report.comparisons) == 1


# ---------------------------------------------------------------------------
# summarize_parity / build_parity_markdown
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestParityReporting:
    def test_summarize_parity_full(self) -> None:
        report = ParityReport(
            comparisons=[
                ParityComparison(
                    input="q1",
                    reference_response="r1",
                    candidate_response="c1",
                    score=0.9,
                    equivalent=True,
                    explanation="eq",
                    reference_task="netbox_mcp_eval",
                    candidate_task="netbox_cli_eval",
                ),
                ParityComparison(
                    input="q2",
                    reference_response="r2",
                    candidate_response="c2",
                    score=0.3,
                    equivalent=False,
                    explanation="diff",
                    reference_task="netbox_mcp_eval",
                    candidate_task="netbox_cli_eval",
                ),
                ParityComparison(
                    input="q3",
                    reference_response="",
                    candidate_response="c3",
                    score=None,
                    equivalent=None,
                    explanation="Skipped: one side produced no response",
                    reference_task="netbox_mcp_eval",
                    candidate_task="netbox_cli_eval",
                ),
            ],
            reference_path="ref.eval",
            candidate_path="cand.eval",
            reference_only=["only-ref"],
            candidate_only=[],
        )
        summary = summarize_parity(report)
        assert summary["paired"] == 3
        assert summary["judged"] == 2
        assert summary["equivalent"] == 1
        assert summary["parity_rate"] == 0.5
        assert summary["reference_only"] == 1
        assert summary["candidate_only"] == 0
        assert summary["reference_task"] == "netbox_mcp_eval"

    def test_summarize_no_judged_pairs(self) -> None:
        report = ParityReport(comparisons=[], reference_path="r", candidate_path="c")
        summary = summarize_parity(report)
        assert summary["judged"] == 0
        assert summary["parity_rate"] is None

    def test_markdown_contains_summary_and_rows(self) -> None:
        report = ParityReport(
            comparisons=[
                ParityComparison(
                    input="list devices",
                    reference_response="srv1, srv2",
                    candidate_response="Found srv1 and srv2",
                    score=0.95,
                    equivalent=True,
                    explanation="same meaning",
                )
            ],
            reference_path="ref.eval",
            candidate_path="cand.eval",
            reference_only=["ref-only-q"],
            candidate_only=[],
        )
        md = build_parity_markdown(report)
        assert "## MCP ↔ CLI parity report" in md
        assert "parity rate" in md
        assert "list devices" in md
        assert "Reference-only inputs" in md
        assert "ref-only-q" in md
        # equivalent row shows a check mark
        assert "✓" in md

    def test_markdown_handles_no_comparisons(self) -> None:
        report = ParityReport(reference_path="r", candidate_path="c")
        md = build_parity_markdown(report)
        assert "parity rate" in md
        assert "paired: **0**" in md
