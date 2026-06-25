"""Post-hoc DeepEval quality scoring on INCORRECT/PARTIAL samples only (#88 Phase 4b).

The DeepEval scorers in :mod:`mcp_common.testing.eval.scorers`
(``faithfulness_scorer`` / ``hallucination_scorer``) run on **every** sample
when attached to a task, which doubles (or more) the judge-token cost of an eval
sweep. Most samples pass — their quality is implied by the structural score — so
the expensive semantic check is largely wasted.

This module is the cost-aware counterpart: a **post-run** hook that takes a
results directory (or ``.eval`` logs) the matrix already produced, filters to the
samples scored INCORRECT / PARTIAL by the existing analyzers, and runs the
DeepEval faithfulness + hallucination metrics on **just those** — reusing the
shared judge client and :mod:`~mcp_common.testing.eval.deepeval_backend`. The
result is a small Markdown + JSON report appended alongside the eval logs.

DeepEval is an **optional** ``[eval-scoring]`` extra. It is imported lazily via
:mod:`mcp_common.testing.eval.deepeval_backend`; if the extra is absent the hook
exits with a clear install hint instead of raising. No model calls happen in CI
(unit tests mock the backend); the hook is on-demand and documented, never a
default CI gate.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from inspect_ai.log import EvalLog, EvalSample, read_eval_log
from inspect_ai.model import ChatMessageAssistant, ChatMessageTool
from inspect_ai.scorer import CORRECT

from mcp_common.testing.eval.analyzer import _extract_input_text, _server_from_task_name
from mcp_common.testing.eval.deepeval_backend import (
    DeepEvalUnavailableError,
    deepeval_available,
)
from mcp_common.testing.eval.scorers import _get_llm_client, _require_llm_client

if TYPE_CHECKING:
    from collections.abc import Sequence

_log = logging.getLogger(__name__)

__all__ = [
    "DeepEvalFailureReport",
    "FailureSample",
    "QualityVerdict",
    "build_deepeval_failure_markdown",
    "collect_failure_samples",
    "deepeval_failures_main",
    "run_deepeval_on_failures",
    "summarize_deepeval_failures",
]


# ---------------------------------------------------------------------------
# Failure-sample extraction (INCORRECT / PARTIAL only)
# ---------------------------------------------------------------------------


def _final_response(sample: EvalSample) -> str:
    """Last non-empty assistant text in a sample (mirrors scorers._get_final_response)."""
    for msg in reversed(sample.messages):
        if isinstance(msg, ChatMessageAssistant) and msg.text and msg.text.strip():
            return msg.text.strip()
    return ""


def _tool_outputs(sample: EvalSample) -> list[str]:
    """Non-empty tool-result texts in order (mirrors scorers._extract_tool_outputs).

    These are the faithfulness ``retrieval_context`` / hallucination ``context``
    — the ground-truth the response is checked against.
    """
    outputs: list[str] = []
    for msg in sample.messages:
        if isinstance(msg, ChatMessageTool):
            text = msg.text.strip() if msg.text else ""
            if text:
                outputs.append(text)
    return outputs


def _is_failure(sample: EvalSample) -> bool:
    """True when the sample's primary scorer is not CORRECT.

    Mirrors the analyzer's failure filter (any non-``CORRECT`` value counts,
    so PARTIAL is included). A sample with no scores is **not** a failure here —
    there is nothing to judge quality against — matching the analyzer, which
    skips scoreless samples entirely.
    """
    if not sample.scores:
        return False
    for score_obj in sample.scores.values():
        value = score_obj.value
        value_str = value if isinstance(value, str) else str(value)
        if value_str != CORRECT:
            return True
    return False


@dataclass(frozen=True)
class FailureSample:
    """One INCORRECT/PARTIAL sample with the fields DeepEval needs.

    Attributes:
        server: MCP server name (derived from the Inspect task name).
        input: The user's scenario input text.
        actual_output: The agent's final assistant response (DeepEval
            ``actual_output``).
        context: Ordered tool-result texts (DeepEval ``context`` /
            ``retrieval_context``).
        score: The primary scorer's value string (e.g. ``"I"`` / ``"P"``).
        log_path: Source ``.eval`` file path (for traceability in the report).
    """

    server: str
    input: str
    actual_output: str
    context: list[str]
    score: str
    log_path: str


def collect_failure_samples(
    log_or_dir: str | Path,
) -> list[FailureSample]:
    """Collect the INCORRECT/PARTIAL samples from a ``.eval`` log or directory.

    Args:
        log_or_dir: A ``.eval`` file or a directory of ``.eval`` files.

    Returns:
        One :class:`FailureSample` per non-CORRECT sample, in log-then-sample
        order. Empty when the path is missing or every sample passed.
    """
    path = Path(log_or_dir)
    files: list[Path]
    if path.is_dir():
        files = sorted(path.glob("*.eval"))
    elif path.exists():
        files = [path]
    else:
        _log.warning("DeepEval-on-failures: path not found: %s", path)
        return []

    samples: list[FailureSample] = []
    for eval_file in files:
        try:
            eval_log = read_eval_log(eval_file)
        except Exception:
            _log.exception("Could not read eval log: %s", eval_file)
            continue
        samples.extend(_extract_failure_samples(eval_log, str(eval_file)))
    return samples


def _extract_failure_samples(eval_log: EvalLog, log_path: str) -> list[FailureSample]:
    """Pull :class:`FailureSample` records from one parsed log."""
    if not eval_log.samples:
        return []
    server = _server_from_task_name(eval_log.eval.task)
    out: list[FailureSample] = []
    for sample in eval_log.samples:
        if not _is_failure(sample):
            continue
        scores = sample.scores
        if not scores:  # pragma: no cover - _is_failure already guards this
            continue
        # The first non-CORRECT score's value labels the sample; the analyzer
        # does the same (it emits one EvalFailure per failing scorer, but for
        # quality scoring one record per sample is the right granularity — the
        # response is judged once, against all its tool outputs).
        score_value = ""
        for score_obj in scores.values():
            value = score_obj.value
            score_value = value if isinstance(value, str) else str(value)
            if score_value != CORRECT:
                break
        out.append(
            FailureSample(
                server=server,
                input=_extract_input_text(sample),
                actual_output=_final_response(sample),
                context=_tool_outputs(sample),
                score=score_value,
                log_path=log_path,
            )
        )
    return out


# ---------------------------------------------------------------------------
# DeepEval scoring (failures only)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QualityVerdict:
    """DeepEval metric outcome for one failure sample.

    Attributes:
        metric: ``"faithfulness"`` or ``"hallucination"``.
        score: Raw metric score in ``[0.0, 1.0]`` (``None`` when the metric
            could not run — no response / no context — or the judge failed).
        success: DeepEval pass/fail (``None`` when not run).
        reason: Judge justification (or a skip reason).
        threshold: The threshold the metric was evaluated against.
    """

    metric: str
    score: float | None
    success: bool | None
    reason: str
    threshold: float | None


@dataclass
class DeepEvalFailureReport:
    """Aggregate DeepEval-on-failures result.

    Attributes:
        samples: One entry per failure sample, each carrying its faithfulness +
            hallucination verdicts (``None`` where a metric was skipped).
        source_path: The log/dir the failures were collected from.
        skipped_no_response / skipped_no_context: Counts of samples a metric
            was skipped on (no agent response / no tool outputs to check
            against) — surfaced so the report distinguishes "unjudgable" from
            "judged and failed".
    """

    samples: list[dict[str, Any]] = field(default_factory=list)
    source_path: str = ""
    skipped_no_response: int = 0
    skipped_no_context: int = 0


def _score_sample(
    client: Any,
    model_name: str,
    sample: FailureSample,
    *,
    faithfulness_threshold: float,
    hallucination_threshold: float,
) -> tuple[QualityVerdict, QualityVerdict]:
    """Run faithfulness + hallucination on one failure sample.

    Skips (returns a ``None``-scored verdict) when the sample lacks a response
    or tool outputs — matching the short-circuits in the live
    ``faithfulness_scorer`` / ``hallucination_scorer`` so a harness failure
    (empty transcript / crash) is not burned against the judge budget.
    """
    from mcp_common.testing.eval import deepeval_backend as _de

    if not sample.actual_output:
        return (
            QualityVerdict("faithfulness", None, None, "no agent response", None),
            QualityVerdict("hallucination", None, None, "no agent response", None),
        )
    if not sample.context:
        return (
            QualityVerdict("faithfulness", None, None, "no tool outputs", None),
            QualityVerdict("hallucination", None, None, "no tool outputs", None),
        )

    faith = asyncio.run(
        asyncio.to_thread(
            _de.score_faithfulness,
            client,
            model_name,
            input=sample.input,
            actual_output=sample.actual_output,
            retrieval_context=sample.context,
            threshold=faithfulness_threshold,
        )
    )
    hall = asyncio.run(
        asyncio.to_thread(
            _de.score_hallucination,
            client,
            model_name,
            input=sample.input,
            actual_output=sample.actual_output,
            context=sample.context,
            threshold=hallucination_threshold,
        )
    )
    return (
        QualityVerdict("faithfulness", faith.score, faith.success, faith.reason, faith.threshold),
        QualityVerdict("hallucination", hall.score, hall.success, hall.reason, hall.threshold),
    )


def run_deepeval_on_failures(
    log_or_dir: str | Path,
    *,
    judge_model: str | None = None,
    faithfulness_threshold: float = 0.5,
    hallucination_threshold: float = 0.5,
) -> DeepEvalFailureReport:
    """Run DeepEval faithfulness + hallucination on the INCORRECT/PARTIAL samples.

    Filters the given log/dir to failures via :func:`collect_failure_samples`,
    then scores each through the shared judge + :mod:`deepeval_backend`. Requires
    the ``[eval-scoring]`` extra (imported lazily) and an API key
    (``EVAL_JUDGE_API_KEY`` / ``TOGETHER_API_KEY``); without DeepEval a
    :class:`~mcp_common.testing.eval.deepeval_backend.DeepEvalUnavailableError`
    is raised, and without an API key the standard :class:`RuntimeError`.

    Args:
        log_or_dir: A ``.eval`` file or directory of them.
        judge_model: Override the judge model name.
        faithfulness_threshold: Faithfulness pass threshold (higher is better).
        hallucination_threshold: Hallucination pass threshold (lower is better).

    Returns:
        A :class:`DeepEvalFailureReport`.
    """
    if not deepeval_available():
        # Raise the backend's typed error so a caller can present the install
        # hint; the CLI wraps this into a friendly message.
        raise DeepEvalUnavailableError(
            'DeepEval-on-failures requires the "eval-scoring" extra. '
            'Install with: uv pip install "mcp-common[eval-scoring]"'
        )

    failures = collect_failure_samples(log_or_dir)
    if not failures:
        _log.info("No INCORRECT/PARTIAL samples in %s — nothing to DeepEval-score", log_or_dir)
        return DeepEvalFailureReport(source_path=str(log_or_dir))

    client, model_name = _require_llm_client(judge_model)

    report = DeepEvalFailureReport(source_path=str(log_or_dir))
    for sample in failures:
        faith, hall = _score_sample(
            client,
            model_name,
            sample,
            faithfulness_threshold=faithfulness_threshold,
            hallucination_threshold=hallucination_threshold,
        )
        if faith.score is None and "no agent response" in faith.reason:
            report.skipped_no_response += 1
        elif faith.score is None and "no tool outputs" in faith.reason:
            report.skipped_no_context += 1
        report.samples.append(
            {
                "server": sample.server,
                "input": sample.input,
                "score": sample.score,
                "log_path": sample.log_path,
                "faithfulness": asdict(faith),
                "hallucination": asdict(hall),
            }
        )
    return report


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def summarize_deepeval_failures(report: DeepEvalFailureReport) -> dict[str, Any]:
    """Reduce a :class:`DeepEvalFailureReport` to a JSON-serializable summary."""
    judged_faith = [s for s in report.samples if s["faithfulness"]["score"] is not None]
    judged_hall = [s for s in report.samples if s["hallucination"]["score"] is not None]
    faith_pass = sum(1 for s in judged_faith if s["faithfulness"]["success"])
    hall_pass = sum(1 for s in judged_hall if s["hallucination"]["success"])
    return {
        "source_path": report.source_path,
        "failures_collected": len(report.samples),
        "faithfulness": {
            "judged": len(judged_faith),
            "pass": faith_pass,
            "fail": len(judged_faith) - faith_pass,
        },
        "hallucination": {
            "judged": len(judged_hall),
            "pass": hall_pass,
            "fail": len(judged_hall) - hall_pass,
        },
        "skipped_no_response": report.skipped_no_response,
        "skipped_no_context": report.skipped_no_context,
    }


def _truncate(text: str, limit: int = 80) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


def build_deepeval_failure_markdown(report: DeepEvalFailureReport) -> str:
    """Render a :class:`DeepEvalFailureReport` as a diffable Markdown report."""
    summary = summarize_deepeval_failures(report)
    f = summary["faithfulness"]
    h = summary["hallucination"]
    lines = [
        "## DeepEval on failures report",
        "",
        f"_source: `{report.source_path}`_",
        "",
        f"- failures collected: **{summary['failures_collected']}** "
        f"(skipped: no-response **{summary['skipped_no_response']}**, "
        f"no-context **{summary['skipped_no_context']}**)",
        f"- faithfulness: judged **{f['judged']}** — pass **{f['pass']}** / "
        f"fail **{f['fail']}** (higher is better)",
        f"- hallucination: judged **{h['judged']}** — pass **{h['pass']}** / "
        f"fail **{h['fail']}** (lower is better)",
        "",
        "| Server | Input | Score | Faithfulness | Hallucination | Reason |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for s in report.samples:
        faith = s["faithfulness"]
        hall = s["hallucination"]
        faith_cell = (
            f"{faith['score']:.2f} ({'pass' if faith['success'] else 'fail'})"
            if faith["score"] is not None
            else f"- ({faith['reason']})"
        )
        hall_cell = (
            f"{hall['score']:.2f} ({'pass' if hall['success'] else 'fail'})"
            if hall["score"] is not None
            else f"- ({hall['reason']})"
        )
        reason = faith["reason"] if faith["score"] is not None else hall["reason"]
        lines.append(
            f"| {s['server']} | {_truncate(s['input'])} | {s['score']} "
            f"| {faith_cell} | {hall_cell} | {_truncate(reason, 120)} |"
        )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def deepeval_failures_main(argv: Sequence[str] | None = None) -> int:
    """``python -m mcp_common.testing.eval deepeval-failures`` entry point.

    Collects INCORRECT/PARTIAL samples from a ``.eval`` log/dir, runs DeepEval
    faithfulness + hallucination on just those, and writes
    ``deepeval_failures.md`` + ``deepeval_failures.json`` to ``--out-dir``
    (default: the source dir). On-demand only — not a CI gate; requires the
    ``[eval-scoring]`` extra.
    """
    import typer

    app = typer.Typer(add_completion=False, help="DeepEval scoring on eval failures.")

    @app.command()
    def score(
        source: Path = typer.Option(  # noqa: B008
            ...,
            "--source",
            help=".eval log or directory of .eval logs to score failures from.",
        ),
        out_dir: Path | None = typer.Option(  # noqa: B008
            None,
            "--out-dir",
            help="Directory to write deepeval_failures.md + .json (default: source's dir).",
        ),
        judge_model: str | None = typer.Option(
            None, "--judge-model", help="Override the LLM judge model name."
        ),
        faithfulness_threshold: float = typer.Option(
            0.5, "--faithfulness-threshold", help="Faithfulness pass threshold (higher is better)."
        ),
        hallucination_threshold: float = typer.Option(
            0.5, "--hallucination-threshold", help="Hallucination pass threshold (lower is better)."
        ),
    ) -> None:
        if not deepeval_available():
            typer.echo(
                'DeepEval is not installed (the "eval-scoring" extra). '
                'Install with: uv pip install "mcp-common[eval-scoring]"',
                err=True,
            )
            raise typer.Exit(2)
        if _get_llm_client() is None:
            typer.echo(
                "Error: an API key is required for the DeepEval judge. Set "
                "EVAL_JUDGE_API_KEY (preferred) or TOGETHER_API_KEY.",
                err=True,
            )
            raise typer.Exit(2)

        try:
            report = run_deepeval_on_failures(
                source,
                judge_model=judge_model,
                faithfulness_threshold=faithfulness_threshold,
                hallucination_threshold=hallucination_threshold,
            )
        except DeepEvalUnavailableError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(2) from exc

        summary = summarize_deepeval_failures(report)
        typer.echo(
            f"Collected {summary['failures_collected']} failure(s); "
            f"faithfulness judged {summary['faithfulness']['judged']} "
            f"(pass {summary['faithfulness']['pass']}); "
            f"hallucination judged {summary['hallucination']['judged']} "
            f"(pass {summary['hallucination']['pass']})"
        )

        target = out_dir or (source.parent if source.exists() else Path("."))
        target.mkdir(parents=True, exist_ok=True)
        (target / "deepeval_failures.md").write_text(
            build_deepeval_failure_markdown(report), encoding="utf-8"
        )
        (target / "deepeval_failures.json").write_text(
            json.dumps({"summary": summary, "samples": report.samples}, indent=2, default=str),
            encoding="utf-8",
        )
        typer.echo(f"Report written to {target / 'deepeval_failures.md'}")

    app(argv, standalone_mode=False)
    return 0
