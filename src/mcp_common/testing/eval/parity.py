"""MCP ↔ CLI equivalence regression via Inspect ``.eval`` log comparison (#88 Phase 4a).

``parity_scorer`` (in :mod:`mcp_common.testing.eval.scorers`) compares a live
sample against a reference *JSON-lines* log captured out of band. This module is
the matching **post-run** capability: given two Inspect ``.eval`` logs — one
produced by an MCP-mode run and one by a CLI-mode run of the same scenario set —
it pairs samples by their input text and asks the shared LLM-as-judge whether
the two runs produced semantically equivalent results.

Why a standalone post-run helper (and not a ``--parity`` matrix mode):

* A parity matrix *mode* would run every scenario twice (MCP + CLI) inside one
  ``run_matrix`` invocation, doubling model + judge tokens for the whole sweep.
  Parity is a **regression** check, not a per-PR gate — it belongs on demand, not
  in the default matrix. Running two normal evals and comparing their logs keeps
  the cost opt-in and the matrix untouched (KISS).
* Comparing the already-written ``.eval`` logs reuses Inspect's
  :func:`~inspect_ai.log.read_eval_log` (the same path the analyzer uses) and the
  scorers' parity judge prompt + retry/backoff, so no judge logic is reinvented.

The output is a small, diffable report (Markdown + a JSON sidecar) plus a
:data:`ParityReport` in memory. Both reference and candidate may be a single
``.eval`` file or a directory of ``.eval`` files (a matrix run's ``logs/`` dir).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from inspect_ai.log import EvalSample, read_eval_log
from inspect_ai.model import ChatMessageAssistant

from mcp_common.testing.eval.analyzer import _extract_input_text
from mcp_common.testing.eval.scorers import (
    _PARITY_PROMPT,
    _get_llm_client,
    _judge,
    _require_llm_client,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

_log = logging.getLogger(__name__)

__all__ = [
    "ParityComparison",
    "ParityReport",
    "build_parity_markdown",
    "compare_eval_logs",
    "compare_logs",
    "load_samples_by_input",
    "parity_main",
    "summarize_parity",
]


# ---------------------------------------------------------------------------
# Sample pairing
# ---------------------------------------------------------------------------


def _final_response(sample: EvalSample) -> str:
    """Last non-empty assistant text in a sample (mirrors ``scorers._get_final_response``).

    The analyzer already extracts input text; this is the parity-specific
    counterpart for the *response* side. Kept local rather than importing the
    TaskState-shaped helper because :class:`EvalSample` carries ``.messages``
    directly (no TaskState wrapping) and the logic is one short loop.
    """
    for msg in reversed(sample.messages):
        if isinstance(msg, ChatMessageAssistant) and msg.text and msg.text.strip():
            return msg.text.strip()
    return ""


def load_samples_by_input(
    log_or_dir: str | Path,
) -> dict[str, EvalSample]:
    """Load every sample in a log/dir, keyed by trimmed input text.

    A later sample with the same input text overwrites an earlier one (the most
    recent run of a repeated scenario wins), matching how a matrix ``logs/``
    directory would settle duplicate inputs across model runs. Returns ``{}``
    when the path is missing or no samples are present.

    Args:
        log_or_dir: A ``.eval`` file or a directory containing ``.eval`` files.

    Returns:
        ``{input_text: EvalSample}`` for every sample with a non-empty input.
    """
    by_input, _task = _load(log_or_dir)
    return by_input


def _load(log_or_dir: str | Path) -> tuple[dict[str, EvalSample], str]:
    """Load samples + the Inspect task name (``""`` for a directory/multi-file)."""
    path = Path(log_or_dir)
    files: list[Path]
    if path.is_dir():
        files = sorted(path.glob("*.eval"))
    elif path.exists():
        files = [path]
    else:
        _log.warning("Parity reference/candidate path not found: %s", path)
        return {}, ""

    by_input: dict[str, EvalSample] = {}
    task_name = ""
    for eval_file in files:
        try:
            eval_log = read_eval_log(eval_file)
        except Exception:
            _log.exception("Could not read eval log: %s", eval_file)
            continue
        # A directory may mix tasks (e.g. mcp + cli logs); only attribute a task
        # name when every file agrees, otherwise leave it blank so the report
        # doesn't mislabel a multi-mode run.
        this_task = str(eval_log.eval.task)
        if not task_name:
            task_name = this_task
        elif task_name != this_task:
            task_name = ""
        for sample in eval_log.samples or []:
            input_text = _extract_input_text(sample).strip()
            if input_text:
                by_input[input_text] = sample
    return by_input, task_name


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParityComparison:
    """One reference ↔ candidate sample pair and its equivalence verdict.

    Attributes:
        input: The shared scenario input text (the pairing key).
        reference_response: Final assistant response from the reference run.
        candidate_response: Final assistant response from the candidate run.
        score: Judge equivalence score in ``[0.0, 1.0]`` (``None`` if the judge
            call failed or the pair was skipped).
        equivalent: ``True`` when ``score >= 0.8`` (the parity scorer's CORRECT
            threshold). ``False`` for a scored-but-divergent pair. ``None`` when
            the pair was not judged (missing side or judge failure).
        explanation: Judge justification (or a skip/failure reason).
        reference_task / candidate_task: Inspect task names of the source logs,
            surfaced so a report can name the mode that produced each side.
    """

    input: str
    reference_response: str
    candidate_response: str
    score: float | None
    equivalent: bool | None
    explanation: str
    reference_task: str = ""
    candidate_task: str = ""


@dataclass
class ParityReport:
    """Aggregate parity result over a set of sample pairs.

    Attributes:
        comparisons: One :class:`ParityComparison` per paired input (in input
            order), including skipped/un-judgeable pairs so the report shows
            coverage.
        reference_path / candidate_path: The paths compared.
        reference_only / candidate_only: Inputs present on only one side (a
            scenario that ran in one mode but not the other — a coverage drift
            signal, not a parity failure).
    """

    comparisons: list[ParityComparison] = field(default_factory=list)
    reference_path: str = ""
    candidate_path: str = ""
    reference_only: list[str] = field(default_factory=list)
    candidate_only: list[str] = field(default_factory=list)


def compare_eval_logs(
    reference_log: str | Path,
    candidate_log: str | Path,
    *,
    judge_model: str | None = None,
) -> ParityReport:
    """Compare two Inspect ``.eval`` logs (or dirs) for MCP ↔ CLI equivalence.

    Pairs samples by trimmed input text, then asks the shared LLM-as-judge
    (the same ``EVAL_JUDGE_*``-aware client + parity prompt the
    :func:`~mcp_common.testing.eval.scorers.parity_scorer` uses) whether each
    pair's responses are semantically equivalent. Inputs present on only one
    side are recorded as coverage drift, not judged.

    Requires the ``[eval]`` extra (Inspect). The judge needs an API key
    (``EVAL_JUDGE_API_KEY`` or ``TOGETHER_API_KEY``); without one a
    :class:`RuntimeError` is raised with the standard install hint.

    Args:
        reference_log: ``.eval`` file or directory for the reference run
            (typically the MCP-mode run).
        candidate_log: ``.eval`` file or directory for the candidate run
            (typically the CLI-mode run).
        judge_model: Override the judge model name.

    Returns:
        A :class:`ParityReport` with one :class:`ParityComparison` per paired
        input plus the coverage-drift lists.
    """
    ref_samples, ref_task = _load(reference_log)
    cand_samples, cand_task = _load(candidate_log)

    reference_only = [k for k in ref_samples if k not in cand_samples]
    candidate_only = [k for k in cand_samples if k not in ref_samples]
    shared = [k for k in ref_samples if k in cand_samples]

    # Stable, human-friendly ordering (by input text) so re-runs diff cleanly.
    reference_only.sort()
    candidate_only.sort()
    shared.sort()

    if not shared:
        _log.warning(
            "No shared inputs between %s and %s — nothing to compare",
            reference_log,
            candidate_log,
        )

    client, model_name = _require_llm_client(judge_model)

    comparisons: list[ParityComparison] = []
    for input_text in shared:
        ref_resp = _final_response(ref_samples[input_text])
        cand_resp = _final_response(cand_samples[input_text])
        # Skip pairs where a side produced no answer: there is nothing to call
        # equivalent, and judging "(no response)" against a real answer would
        # burn a judge call on a harness failure (empty transcript / crash).
        if not ref_resp or not cand_resp:
            comparisons.append(
                ParityComparison(
                    input=input_text,
                    reference_response=ref_resp,
                    candidate_response=cand_resp,
                    score=None,
                    equivalent=None,
                    explanation="Skipped: one side produced no response",
                    reference_task=ref_task,
                    candidate_task=cand_task,
                )
            )
            continue

        prompt = _PARITY_PROMPT.format(
            user_input=input_text,
            response_a=ref_resp,
            response_b=cand_resp,
        )
        score, explanation = asyncio.run(asyncio.to_thread(_judge, client, model_name, prompt))
        if score is None:
            equivalent = None
        else:
            equivalent = score >= 0.8  # parity_scorer's CORRECT threshold
        comparisons.append(
            ParityComparison(
                input=input_text,
                reference_response=ref_resp,
                candidate_response=cand_resp,
                score=score,
                equivalent=equivalent,
                explanation=explanation,
                reference_task=ref_task,
                candidate_task=cand_task,
            )
        )

    return ParityReport(
        comparisons=comparisons,
        reference_path=str(reference_log),
        candidate_path=str(candidate_log),
        reference_only=reference_only,
        candidate_only=candidate_only,
    )


def compare_logs(
    reference_log: str | Path,
    candidate_log: str | Path,
    *,
    judge_model: str | None = None,
) -> ParityReport:
    """Alias for :func:`compare_eval_logs` (the public parity entry point)."""
    return compare_eval_logs(reference_log, candidate_log, judge_model=judge_model)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def summarize_parity(report: ParityReport) -> dict[str, Any]:
    """Reduce a :class:`ParityReport` to a small JSON-serializable summary.

    The summary is what gets written as the ``parity.json`` sidecar and embedded
    in ``summary.json`` by callers; the full per-pair detail stays in the
    :class:`ParityReport`.
    """
    judged = [c for c in report.comparisons if c.score is not None]
    equivalent = sum(1 for c in judged if c.equivalent)
    return {
        "reference_path": report.reference_path,
        "candidate_path": report.candidate_path,
        "reference_task": judged[0].reference_task if judged else "",
        "candidate_task": judged[0].candidate_task if judged else "",
        "paired": len(report.comparisons),
        "judged": len(judged),
        "equivalent": equivalent,
        "parity_rate": (equivalent / len(judged)) if judged else None,
        "reference_only": len(report.reference_only),
        "candidate_only": len(report.candidate_only),
    }


def _truncate(text: str, limit: int = 80) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


def build_parity_markdown(report: ParityReport) -> str:
    """Render a :class:`ParityReport` as a diffable Markdown report.

    GitHub renders this inline with no hosting. The table is one row per paired
    input; coverage-drift (inputs that ran in only one mode) is listed below.
    """
    summary = summarize_parity(report)
    rate = summary["parity_rate"]
    rate_str = f"{rate:.2f}" if isinstance(rate, float) else "-"
    lines = [
        "## MCP ↔ CLI parity report",
        "",
        f"_reference: `{report.reference_path}` "
        f"({summary['reference_task'] or 'n/a'}) — "
        f"candidate: `{report.candidate_path}` "
        f"({summary['candidate_task'] or 'n/a'})_",
        "",
        f"- paired: **{summary['paired']}** | judged: **{summary['judged']}** | "
        f"equivalent: **{summary['equivalent']}** | parity rate: **{rate_str}**",
        f"- coverage drift: reference-only **{summary['reference_only']}**, "
        f"candidate-only **{summary['candidate_only']}**",
        "",
        "| Input | Ref | Cand | Score | Equivalent | Explanation |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for c in report.comparisons:
        score_str = f"{c.score:.2f}" if c.score is not None else "-"
        if c.equivalent is True:
            verdict = "✓"
        elif c.equivalent is False:
            verdict = "✗"
        else:
            verdict = "-"
        lines.append(
            f"| {_truncate(c.input)} "
            f"| {_truncate(c.reference_response)} "
            f"| {_truncate(c.candidate_response)} "
            f"| {score_str} | {verdict} | {_truncate(c.explanation, 120)} |"
        )
    if report.reference_only:
        lines.append("")
        lines.append("### Reference-only inputs (ran in reference, not candidate)")
        for item in report.reference_only:
            lines.append(f"- {_truncate(item, 120)}")
    if report.candidate_only:
        lines.append("")
        lines.append("### Candidate-only inputs (ran in candidate, not reference)")
        for item in report.candidate_only:
            lines.append(f"- {_truncate(item, 120)}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parity_main(argv: Sequence[str] | None = None) -> int:
    """``python -m mcp_common.testing.eval parity`` entry point.

    Compares two ``.eval`` logs (or directories of them) and writes a Markdown
    report (``parity.md``) + JSON sidecar (``parity.json``) to ``--out-dir``
    (default: the candidate log's directory). On-demand only — not a CI gate.
    """
    import typer

    app = typer.Typer(add_completion=False, help="MCP ↔ CLI parity comparison.")

    @app.command()
    def compare(
        reference_log: Path = typer.Option(  # noqa: B008
            ...,
            "--reference",
            help="Reference .eval log or directory (typically the MCP run).",
        ),
        candidate_log: Path = typer.Option(  # noqa: B008
            ...,
            "--candidate",
            help="Candidate .eval log or directory (typically the CLI run).",
        ),
        out_dir: Path | None = typer.Option(  # noqa: B008
            None,
            "--out-dir",
            help="Directory to write parity.md + parity.json (default: candidate's dir).",
        ),
        judge_model: str | None = typer.Option(
            None, "--judge-model", help="Override the LLM judge model name."
        ),
    ) -> None:
        if _get_llm_client() is None:
            typer.echo(
                "Error: an API key is required for the parity judge. Set "
                "EVAL_JUDGE_API_KEY (preferred) or TOGETHER_API_KEY.",
                err=True,
            )
            raise typer.Exit(2)

        report = compare_eval_logs(reference_log, candidate_log, judge_model=judge_model)
        summary = summarize_parity(report)
        rate = summary["parity_rate"]
        rate_str = f", parity rate {rate:.2f}" if isinstance(rate, float) else ""
        typer.echo(
            f"Paired {summary['paired']} input(s); judged {summary['judged']}; "
            f"equivalent {summary['equivalent']}{rate_str}"
        )

        target = out_dir or (candidate_log.parent if candidate_log.exists() else Path("."))
        target.mkdir(parents=True, exist_ok=True)
        (target / "parity.md").write_text(build_parity_markdown(report), encoding="utf-8")
        payload = {"summary": summary, "comparisons": [asdict(c) for c in report.comparisons]}
        (target / "parity.json").write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8"
        )
        typer.echo(f"Report written to {target / 'parity.md'}")

    app(argv, standalone_mode=False)
    return 0
