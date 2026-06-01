"""Optional Weights & Biases experiment tracking for MCP evals (vhspace/mcp-common#60).

The default eval pipeline (Inspect AI -> ``.eval`` logs -> ``analyzer`` -> issue
filing) gives a point-in-time pass/fail snapshot but no *historical* context.
This module adds an opt-in logging layer on top: it reads an Inspect ``EvalLog``
and ships its aggregate metrics, per-sample scores, run metadata, and the raw
``.eval`` artifact to a `Weights & Biases <https://wandb.ai/>`_ run, so eval
quality can be compared across runs while iterating on judge models, scorers,
prompts, and datasets.

W&B is an **optional** dependency (the ``eval-tracking`` extra). Every ``wandb``
import is deferred so importing this module — and the whole
``mcp_common.testing.eval`` package — never requires W&B; calling
:func:`log_eval_to_wandb` without the extra raises a clear
:class:`WandbUnavailableError` with an install hint::

    uv pip install "mcp-common[eval-tracking]"

This is purely additive: it does not change the existing Inspect -> analyze ->
file-issues flow. The metric/record-extraction helpers are pure functions
(no W&B import) so they're independently testable; only
:func:`log_eval_to_wandb` / :func:`log_eval_file` touch W&B.
"""

from __future__ import annotations

import json
import re
from importlib.util import find_spec
from typing import TYPE_CHECKING, Any

from inspect_ai.scorer import CORRECT, INCORRECT, NOANSWER, PARTIAL

if TYPE_CHECKING:
    from inspect_ai.log import EvalLog

_INSTALL_HINT = (
    'W&B eval tracking requires the "eval-tracking" extra. '
    'Install with: uv pip install "mcp-common[eval-tracking]"'
)

# Map Inspect's categorical score values to a comparable float so W&B can chart
# per-sample outcomes alongside numeric metrics. NOANSWER has no meaningful
# magnitude, so it maps to ``None`` (logged as a blank cell).
_VALUE_TO_FLOAT: dict[str, float | None] = {
    CORRECT: 1.0,
    PARTIAL: 0.5,
    INCORRECT: 0.0,
    NOANSWER: None,
}


class WandbUnavailableError(ImportError):
    """Raised when W&B tracking is requested but the ``eval-tracking`` extra is absent."""


def wandb_available() -> bool:
    """Whether the optional ``wandb`` dependency is importable (no import performed)."""
    return find_spec("wandb") is not None


def _ensure_wandb() -> None:
    """Raise :class:`WandbUnavailableError` with an install hint if W&B is missing."""
    if not wandb_available():
        raise WandbUnavailableError(_INSTALL_HINT)


def _server_from_task(task: str) -> str:
    """Derive a server name from an Inspect task name (``"netbox_mcp_eval"`` -> ``"netbox-mcp"``).

    Mirrors the convention in ``analyzer._server_from_task_name`` but is kept
    local so this module does not import the (Lane B2) analyzer.
    """
    return re.sub(r"_eval$", "", task).replace("_", "-")


def _score_to_float(value: Any) -> float | None:
    """Normalize an Inspect ``Score.value`` to a float for charting (or ``None``)."""
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return _VALUE_TO_FLOAT.get(value)
    return None


def _stringify_value(value: Any) -> str:
    """Render a score value for the table's raw-value column."""
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True, default=str)
    return str(value)


def summarize_eval_log(eval_log: EvalLog) -> dict[str, float]:
    """Flatten an ``EvalLog``'s aggregate metrics into a W&B-loggable dict.

    Produces ``{"<scorer>/<metric>": value}`` from ``results.scores[].metrics``
    (e.g. ``{"tool_use_scorer/accuracy": 0.85}``) plus ``total_samples`` /
    ``completed_samples``. Returns an empty dict when the log has no results
    (e.g. an errored eval). Tolerant of duck-typed stand-ins via ``getattr``.
    """
    out: dict[str, float] = {}
    results = getattr(eval_log, "results", None)
    if results is None:
        return out

    for escore in getattr(results, "scores", None) or []:
        scorer_name = getattr(escore, "name", None) or getattr(escore, "scorer", None) or "scorer"
        metrics = getattr(escore, "metrics", None) or {}
        for metric_name, metric in metrics.items():
            value = getattr(metric, "value", None)
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                out[f"{scorer_name}/{metric_name}"] = float(value)

    for field in ("total_samples", "completed_samples"):
        count = getattr(results, field, None)
        if isinstance(count, int) and not isinstance(count, bool):
            out[field] = float(count)
    return out


def build_sample_records(eval_log: EvalLog) -> tuple[list[str], list[list[Any]]]:
    """Build a per-sample ``(columns, rows)`` table of scores for W&B.

    One row per (sample, scorer): sample id/epoch, scorer name, the raw score
    value, its normalized float, and a truncated explanation. Returns the fixed
    column list and an empty row list when the log has no samples.
    """
    columns = ["sample_id", "epoch", "scorer", "value", "score", "explanation"]
    rows: list[list[Any]] = []
    for sample in getattr(eval_log, "samples", None) or []:
        sample_id = getattr(sample, "id", None)
        epoch = getattr(sample, "epoch", None)
        scores = getattr(sample, "scores", None) or {}
        for scorer_name, score in scores.items():
            value = getattr(score, "value", None)
            explanation = getattr(score, "explanation", "") or ""
            rows.append(
                [
                    str(sample_id),
                    epoch,
                    scorer_name,
                    _stringify_value(value),
                    _score_to_float(value),
                    explanation[:500],
                ]
            )
    return columns, rows


def derive_run_config(eval_log: EvalLog, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Assemble the W&B run ``config`` (metadata) from an ``EvalLog``.

    Captures what an eval-quality comparison wants to slice by: the task, the
    model under test (+ its base URL), the derived server name, the scorers that
    ran, the run status, and the judge model (from ``EVAL_JUDGE_MODEL`` when
    set). ``extra`` is merged last so callers can add the bits not in the log —
    dataset version, scorer-config notes, etc. — and override anything derived.
    """
    import os

    config: dict[str, Any] = {}
    spec = getattr(eval_log, "eval", None)
    task = getattr(spec, "task", None) if spec is not None else None
    if task:
        config["task"] = task
        config["server"] = _server_from_task(task)
    if spec is not None:
        model = getattr(spec, "model", None)
        if model:
            config["model_under_test"] = model
        base_url = getattr(spec, "model_base_url", None)
        if base_url:
            config["model_base_url"] = base_url
        dataset = getattr(spec, "dataset", None)
        dataset_name = getattr(dataset, "name", None) if dataset is not None else None
        if dataset_name:
            config["dataset"] = dataset_name

    results = getattr(eval_log, "results", None)
    if results is not None:
        scorers = [
            name
            for s in (getattr(results, "scores", None) or [])
            if (name := getattr(s, "name", None))
        ]
        if scorers:
            config["scorers"] = scorers

    status = getattr(eval_log, "status", None)
    if status:
        config["status"] = status

    judge_model = os.environ.get("EVAL_JUDGE_MODEL")
    if judge_model:
        config["judge_model"] = judge_model

    if extra:
        config.update(extra)
    return config


def _artifact_name(config: dict[str, Any]) -> str:
    """W&B-safe artifact name derived from the server/task (``[A-Za-z0-9_.-]``)."""
    base = config.get("server") or config.get("task") or "eval"
    safe = re.sub(r"[^A-Za-z0-9_.-]", "-", str(base))
    return f"{safe}-eval-log"


def log_eval_to_wandb(
    eval_log: EvalLog,
    *,
    project: str,
    entity: str | None = None,
    run_name: str | None = None,
    tags: list[str] | None = None,
    extra_config: dict[str, Any] | None = None,
    upload_artifact: bool = True,
    eval_log_path: str | None = None,
    job_type: str = "eval",
) -> str | None:
    """Log an Inspect ``EvalLog`` to a Weights & Biases run.

    Opt-in only — requires the ``eval-tracking`` extra (raises
    :class:`WandbUnavailableError` otherwise). Creates a W&B run tagged with the
    derived metadata (:func:`derive_run_config`), logs the aggregate metrics
    (:func:`summarize_eval_log`) to both history and the run summary, logs a
    per-sample score table (:func:`build_sample_records`), and — when
    ``upload_artifact`` — uploads the ``.eval`` file as a versioned artifact.

    Args:
        eval_log: A parsed Inspect ``EvalLog``.
        project: W&B project name (required).
        entity: W&B entity/team (defaults to the wandb-configured default).
        run_name: W&B run name (defaults to the task name).
        tags: Optional W&B run tags.
        extra_config: Extra run config merged over the derived metadata
            (dataset version, scorer-config notes, judge model, …).
        upload_artifact: Upload the ``.eval`` log file as a W&B artifact.
        eval_log_path: Path to the ``.eval`` file to upload; defaults to the
            log's own ``location``.
        job_type: W&B ``job_type`` for the run.

    Returns:
        The W&B run URL, or ``None`` if the run object exposes none.
    """
    _ensure_wandb()
    import wandb

    config = derive_run_config(eval_log, extra=extra_config)
    metrics = summarize_eval_log(eval_log)
    columns, rows = build_sample_records(eval_log)

    run = wandb.init(
        project=project,
        entity=entity,
        name=run_name or config.get("task"),
        tags=tags,
        config=config,
        job_type=job_type,
        reinit=True,
    )
    try:
        if metrics:
            run.log(metrics)
            run.summary.update(metrics)
        if rows:
            run.log({"samples": wandb.Table(columns=columns, data=rows)})

        path = eval_log_path or getattr(eval_log, "location", None)
        if upload_artifact and path:
            artifact = wandb.Artifact(name=_artifact_name(config), type="eval-log")
            artifact.add_file(str(path))
            run.log_artifact(artifact)

        url = getattr(run, "url", None)
        return str(url) if url else None
    finally:
        run.finish()


def log_eval_file(eval_log_path: str, **kwargs: Any) -> str | None:
    """Read an ``.eval`` file and log it to W&B (convenience over :func:`log_eval_to_wandb`).

    Reads ``eval_log_path`` with Inspect's ``read_eval_log`` and forwards to
    :func:`log_eval_to_wandb`, defaulting the uploaded artifact path to the same
    file. This is the entry point a CLI (e.g. ``report.py``'s ``--wandb-project``
    / ``--wandb-entity`` flags) would call.
    """
    from inspect_ai.log import read_eval_log

    eval_log = read_eval_log(str(eval_log_path))
    kwargs.setdefault("eval_log_path", str(eval_log_path))
    return log_eval_to_wandb(eval_log, **kwargs)
