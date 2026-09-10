"""CLI entry point for analyzing eval logs and filing issues.

Usage::

    python -m mcp_common.testing.eval --log-dir ./logs/ --dry-run
    python -m mcp_common.testing.eval --log-dir ./logs/ --create-issues
    python -m mcp_common.testing.eval --log-dir ./logs/ --create-issues --repo-prefix myorg
    python -m mcp_common.testing.eval --log-dir ./logs/ --create-issues --auto-fix
    python -m mcp_common.testing.eval --log-dir ./logs/ --auto-fix --agent cursor
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer

from mcp_common.testing.eval.analyzer import EvalFailure, analyze_eval_dir
from mcp_common.testing.eval.issue_filer import deduplicate, file_issues
from mcp_common.testing.eval.judge_usage import JudgePricing, JudgeUsage, judge_cost_block
from mcp_common.testing.eval.remediate import remediate_batch
from mcp_common.testing.eval.repo_discovery import DEFAULT_WORKSPACE_ROOT

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

_log = logging.getLogger(__name__)

app = typer.Typer(help="Analyze Inspect AI eval logs and optionally file GitHub issues.")


def _failure_key(failure: EvalFailure) -> str:
    """Build a stable key for matching a failure to its filed issue URL."""
    return f"{failure.server}|{failure.scenario}"


@app.command()
def report(
    log_dir: Path = typer.Option(..., "--log-dir", help="Directory containing .eval files"),  # noqa: B008
    dry_run: bool = typer.Option(True, "--dry-run/--create-issues", help="Preview without filing"),
    auto_fix: bool = typer.Option(
        False, "--auto-fix", help="Dispatch agent to fix failures after filing issues"
    ),
    agent_backend: str = typer.Option("claude", "--agent", help="Agent backend: claude or cursor"),
    repo_prefix: str = typer.Option("vhspace", "--repo-prefix", help="GitHub org prefix"),
    workspace_root: Path = typer.Option(  # noqa: B008
        DEFAULT_WORKSPACE_ROOT, "--workspace", help="Workspace root path"
    ),
) -> None:
    """Analyze eval logs and report or file issues for failures."""
    if not log_dir.is_dir():
        typer.echo(f"Error: {log_dir} is not a directory", err=True)
        raise typer.Exit(1)

    failures = analyze_eval_dir(log_dir)
    if not failures:
        typer.echo("No failures found.")
        raise typer.Exit(0)

    typer.echo(f"Found {len(failures)} failure(s) across eval logs.")

    unique = deduplicate(failures, repo_prefix=repo_prefix, workspace=workspace_root)
    typer.echo(f"After deduplication: {len(unique)} unique failure(s).")

    if not unique:
        typer.echo("All failures already have open issues. Nothing to file.")
        raise typer.Exit(0)

    urls = file_issues(unique, dry_run=dry_run, repo_prefix=repo_prefix, workspace=workspace_root)

    if dry_run:
        typer.echo(f"\nDry run complete. {len(unique)} issue(s) would be filed.")
    else:
        typer.echo(f"\nFiled {len(urls)} issue(s).")
        for url in urls:
            typer.echo(f"  {url}")

    if auto_fix:
        typer.echo("\n--- Remediation ---")
        issue_url_map: dict[str, str] = {}
        if not dry_run and urls:
            for failure, url in zip(unique, urls, strict=False):
                issue_url_map[_failure_key(failure)] = url
        elif dry_run:
            for failure in unique:
                issue_url_map[_failure_key(failure)] = (
                    f"https://github.com/{repo_prefix}/{failure.server}/issues/DRAFT"
                )

        pr_urls = remediate_batch(
            unique,
            issue_url_map,
            workspace_root=workspace_root,
            agent_backend=agent_backend,
            dry_run=dry_run,
        )
        if pr_urls:
            typer.echo(f"\nOpened {len(pr_urls)} PR(s):")
            for pr_url in pr_urls:
                typer.echo(f"  {pr_url}")
        elif not dry_run:
            typer.echo("\nNo PRs were opened.")


def add_judge_usage_to_summary(
    summary: dict[str, Any],
    *,
    usage: JudgeUsage | None = None,
    pricing: JudgePricing | None = None,
    key: str = "judge_cost",
) -> dict[str, Any]:
    """Inject the LLM-judge token/cost block into a run ``summary`` dict (#169).

    Records judge accounting as a **separate line item** (``summary[key]``,
    default ``"judge_cost"``) from the model-under-test cost the runner already
    writes (e.g. ``cost_runtime``), so true end-to-end cost = model-under-test +
    judge. Mutates and returns ``summary`` for convenience::

        # after running the matrix with judge tracking installed:
        add_judge_usage_to_summary(summary)  # uses the process-global accumulator

    Args:
        summary: The run summary mapping (the object written to ``summary.json``).
        usage: Judge usage to report (defaults to the process-global snapshot
            populated via ``tracked_judge_client`` / ``install_judge_usage_tracking``).
        pricing: Judge pricing (defaults to the ``EVAL_JUDGE_PRICE_*`` env vars).
        key: Summary key under which to store the judge block.

    Returns:
        The same ``summary`` mapping, with ``summary[key]`` set to the judge cost
        block (see :func:`mcp_common.testing.eval.judge_usage.judge_cost_block`).
    """
    summary[key] = judge_cost_block(usage=usage, pricing=pricing)
    return summary


# ---------------------------------------------------------------------------
# Release-over-release trend reporting (#125)
# ---------------------------------------------------------------------------
#
# Turn the per-run ``summary.json`` snapshots into a per-MCP, release-over-release
# trend that is viewable directly in GitHub. ``append_history`` accumulates each
# run into a ``history.jsonl`` time-series; ``render_trend`` turns that history
# into (1) a Markdown comparison table and (2) a Mermaid ``xychart`` headline
# line — both of which GitHub renders inline with no JS/hosting — plus a
# viz-mcp ``sections.json`` spec for the fully-interactive Plotly HTML/PNG
# (rendered by viz-mcp when it is installed; gracefully skipped otherwise).
#
# The helpers are schema-tolerant: a run record is just the ``summary`` dict, and
# the per-series metric is pulled defensively (with an ``extractor`` override) so
# each MCP's runner can adopt this without a rigid schema.

_RELEASE_LABEL_KEYS = ("mcp_version", "version", "date", "commit", "recorded_at")
"""Metadata keys tried (in order) to label a run on the trend x-axis."""

_DEFAULT_MAX_RELEASES = 12
"""Cap the releases shown in the inline Markdown/Mermaid so they stay readable."""


@dataclass
class TrendReport:
    """The artifacts and in-memory data produced by :func:`render_trend`.

    Attributes:
        metric: The metric charted (e.g. ``"accuracy"``).
        releases: Ordered release labels (the x-axis).
        series: ``{series_key: [value_per_release]}`` (``None`` where a series
            has no datum for that release).
        latest_rows: The last run's per-result rows (for the comparison table).
        markdown: The Markdown comparison table (GitHub-inline).
        mermaid: The Mermaid ``xychart-beta`` headline-line body.
        sections: viz-mcp ``sections`` spec (time-series + latest-run table).
        out_dir: Directory artifacts were written to (``None`` if ``write=False``).
        artifacts: ``{name: path}`` of files written (``markdown`` / ``sections``
            always; ``html`` / ``png`` when viz-mcp rendered them).
        notes: Human-readable notes (e.g. why PNG/HTML were skipped).
    """

    metric: str
    releases: list[str]
    series: dict[str, list[float | None]]
    latest_rows: list[Mapping[str, Any]]
    markdown: str
    mermaid: str
    sections: list[dict[str, Any]]
    out_dir: Path | None
    artifacts: dict[str, Path]
    notes: list[str]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _coerce_float(value: Any) -> float | None:
    """Coerce a metric value to ``float``, or ``None`` if not numeric (bools excluded)."""
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _metadata(record: Mapping[str, Any]) -> Mapping[str, Any]:
    meta = record.get("metadata")
    return meta if isinstance(meta, Mapping) else {}


def _lookup(record: Mapping[str, Any], key: str) -> Any:
    """Look up ``key`` at the record top level, then under ``metadata``."""
    if key in record:
        return record[key]
    return _metadata(record).get(key)


def _release_label(record: Mapping[str, Any]) -> str:
    """Best-effort x-axis label for a run (mcp_version -> date -> commit -> recorded_at)."""
    for key in _RELEASE_LABEL_KEYS:
        value = _lookup(record, key)
        if value:
            return str(value)
    return "?"


def append_history(
    summary: Mapping[str, Any],
    history_path: str | Path,
    *,
    recorded_at: str | None = None,
    unique_by: str | None = None,
) -> dict[str, Any]:
    """Append a run's ``summary`` to a per-MCP ``history.jsonl`` time-series (#125).

    Each line is one run's summary dict (the time-series source for
    :func:`render_trend`). A ``recorded_at`` ISO timestamp is added when absent.
    Parent directories are created as needed.

    Args:
        summary: The run summary (e.g. the contents of ``summary.json``).
        history_path: Path to the append-only ``history.jsonl``.
        recorded_at: Explicit timestamp to stamp (defaults to "now", UTC).
        unique_by: Optional record/metadata key (e.g. ``"commit"``); when its
            value already appears in the history the append is skipped, making
            re-runs idempotent.

    Returns:
        The record that was appended (or that already existed for ``unique_by``).
    """
    path = Path(history_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = dict(summary)
    record.setdefault("recorded_at", recorded_at or _now_iso())

    if unique_by is not None:
        new_key = _lookup(record, unique_by)
        if new_key is not None:
            for existing in load_history(path):
                if _lookup(existing, unique_by) == new_key:
                    return record

    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, default=str) + "\n")
    return record


def load_history(history_path: str | Path) -> list[dict[str, Any]]:
    """Load a ``history.jsonl`` into a list of run records (missing file -> ``[]``).

    Blank lines and unparseable / non-object lines are skipped so a partially
    written or hand-edited history never aborts a report.
    """
    path = Path(history_path)
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            records.append(obj)
    return records


def _default_points(record: Mapping[str, Any], metric: str) -> list[tuple[str, float]]:
    """Extract ``(series_key, value)`` points for one run from the default schema.

    Reads ``record["results"]`` (a list of ``{"model", "mode", <metric>, ...}``
    rows), keying each series ``"<model> / <mode>"``. Falls back to a single
    ``"overall"`` series from a top-level ``record[metric]`` scalar when there
    are no per-result rows.
    """
    points: list[tuple[str, float]] = []
    rows = record.get("results")
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            value = _coerce_float(row.get(metric))
            if value is None:
                continue
            model = row.get("model", "?")
            mode = row.get("mode")
            key = f"{model} / {mode}" if mode else str(model)
            points.append((key, value))
    if not points:
        overall = _coerce_float(record.get(metric))
        if overall is not None:
            points.append(("overall", overall))
    return points


def _extract_series(
    history: Sequence[Mapping[str, Any]],
    metric: str,
    extractor: Callable[[Mapping[str, Any]], list[tuple[str, float]]] | None,
) -> tuple[list[str], dict[str, list[float | None]]]:
    """Build ``(releases, {series_key: [value_per_release]})`` from run history."""
    extract = extractor or (lambda record: _default_points(record, metric))
    releases: list[str] = []
    per_series: dict[str, dict[int, float]] = {}
    for idx, record in enumerate(history):
        releases.append(_release_label(record))
        for key, value in extract(record):
            per_series.setdefault(key, {})[idx] = value
    aligned: dict[str, list[float | None]] = {
        key: [idx_map.get(i) for i in range(len(releases))] for key, idx_map in per_series.items()
    }
    return releases, aligned


def _latest_delta(values: Sequence[float | None]) -> float | None:
    """Delta between the last two non-``None`` values, or ``None`` if <2 exist."""
    present = [v for v in values if v is not None]
    if len(present) < 2:
        return None
    return present[-1] - present[-2]


def _fmt_delta(delta: float | None) -> str:
    if delta is None:
        return "-"
    sign = "+" if delta >= 0 else ""
    return f"{sign}{delta:.2f}"


def _latest_rows(history: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The last run's ``results`` rows (for the latest-run comparison table)."""
    if not history:
        return []
    rows = history[-1].get("results")
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, Mapping)]
    return []


def build_trend_markdown(
    releases: Sequence[str],
    series: Mapping[str, Sequence[float | None]],
    metric: str,
    *,
    title: str | None = None,
    max_releases: int = _DEFAULT_MAX_RELEASES,
) -> str:
    """Render the per-series metric-over-releases comparison as a Markdown table.

    One row per series, one column per (recent) release, plus a delta-vs-previous
    column — diffable and rendered inline by GitHub with no hosting.
    """
    shown = list(releases)[-max_releases:]
    offset = len(releases) - len(shown)
    heading = title or f"Eval trend: {metric}"
    latest = shown[-1] if shown else "n/a"

    lines = [
        f"## {heading}",
        "",
        f"_Metric: `{metric}` - {len(releases)} release(s) - latest: `{latest}`_",
        "",
    ]
    header = ["Series", *shown, "Delta latest"]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join(["---"] * len(header)) + "|")
    for key in sorted(series):
        values = list(series[key])
        cells = [key]
        cells.extend(f"{v:.2f}" if v is not None else "-" for v in values[offset:])
        cells.append(_fmt_delta(_latest_delta(values)))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def _mermaid_quote(label: str) -> str:
    """Quote an x-axis label for Mermaid (double quotes, inner quotes neutralized)."""
    return '"' + label.replace('"', "'") + '"'


def _default_title(metric: str) -> str:
    """Default chart/section title for ``metric`` when the caller supplies none."""
    return f"{metric} over releases"


def _mermaid_y_bounds(means: Sequence[float]) -> tuple[float, float]:
    """Y-axis ``(min, max)`` for the Mermaid headline chart.

    Defaults to ``0 --> 1`` — accuracy and most rate metrics live there, and a
    fixed range keeps charts comparable across runs. Widens to fit the data only
    when a value falls outside ``[0, 1]`` (e.g. a latency or token-count metric
    charted via an ``extractor``), so the headline line is never silently
    clipped at the top/bottom.
    """
    if not means:
        return 0.0, 1.0
    lo, hi = min(means), max(means)
    if lo >= 0.0 and hi <= 1.0:
        return 0.0, 1.0
    y_min = min(0.0, lo)
    y_max = max(hi, y_min + 1.0)
    return y_min, y_max


def build_mermaid_xychart(
    releases: Sequence[str],
    series: Mapping[str, Sequence[float | None]],
    metric: str,
    *,
    title: str | None = None,
    max_releases: int = _DEFAULT_MAX_RELEASES,
) -> str:
    """Render a Mermaid ``xychart-beta`` headline line (mean metric per release).

    GitHub renders Mermaid natively, so this is an inline, hosting-free trend.
    Mermaid ``xychart`` has no multi-series legend, so the headline is the mean
    across series per release; the per-series breakdown lives in the Markdown
    table and the viz-mcp Plotly report.
    """
    shown = list(releases)[-max_releases:]
    offset = len(releases) - len(shown)
    means: list[float] = []
    for i in range(offset, len(releases)):
        present: list[float] = []
        for series_values in series.values():
            value = series_values[i] if i < len(series_values) else None
            if value is not None:
                present.append(value)
        means.append(round(sum(present) / len(present), 4) if present else 0.0)

    chart_title = (title or _default_title(metric)).replace('"', "'")
    x_labels = ", ".join(_mermaid_quote(label) for label in shown)
    y_values = ", ".join(str(value) for value in means)
    y_min, y_max = _mermaid_y_bounds(means)
    return "\n".join(
        [
            "xychart-beta",
            f'    title "{chart_title}"',
            f"    x-axis [{x_labels}]",
            f'    y-axis "{metric}" {y_min:g} --> {y_max:g}',
            f"    line [{y_values}]",
        ]
    )


def build_viz_sections(
    releases: Sequence[str],
    series: Mapping[str, Sequence[float | None]],
    metric: str,
    latest_rows: Sequence[Mapping[str, Any]],
    *,
    title: str | None = None,
) -> list[dict[str, Any]]:
    """Build the viz-mcp ``sections`` spec: a multi-series time-series + a table.

    The ``timeseries`` section is a data-only, multi-series line spec (the small
    viz-mcp section type tracked alongside #125); viz-mcp renders it to the
    interactive Plotly HTML/PNG. The ``table`` section is the latest-run
    comparison. Both are plain JSON-serializable dicts so this works with no
    viz-mcp / Plotly installed.
    """
    chart_title = title or _default_title(metric)
    sections: list[dict[str, Any]] = [
        {
            "type": "timeseries",
            "title": chart_title,
            "x_label": "release",
            "y_label": metric,
            "x": list(releases),
            "series": [{"name": key, "points": list(series[key])} for key in sorted(series)],
        }
    ]
    if latest_rows:
        sections.append(
            {
                "type": "table",
                "title": f"Latest run ({releases[-1] if releases else 'n/a'})",
                "records": [dict(row) for row in latest_rows],
            }
        )
    return sections


def _try_render_viz(
    sections: list[dict[str, Any]], out_dir: Path, *, title: str
) -> dict[str, Path]:
    """Best-effort render the viz-mcp interactive HTML (+ PNG) when viz-mcp exists.

    Returns ``{name: path}`` for whatever was produced. Importing or rendering
    failures (including viz-mcp not being installed, the common case in CI) are
    swallowed so the inline Markdown + Mermaid artifacts are always produced.
    """
    produced: dict[str, Path] = {}
    try:
        # viz-mcp is an optional renderer (not a declared dependency of the eval
        # extra); the ImportError path below is the supported "not installed" case.
        from viz_mcp.render import to_html  # type: ignore[import-not-found]
    except Exception:
        return produced
    html_path = out_dir / "trend.html"
    try:
        to_html(sections, str(html_path), title=title)
        if html_path.exists():
            produced["html"] = html_path
    except Exception:
        _log.debug("viz-mcp to_html failed for trend report", exc_info=True)
        return produced
    try:
        from viz_mcp.render import to_png

        png_path = out_dir / "trend.png"
        to_png(html_path.read_text(encoding="utf-8"), str(png_path))
        if png_path.exists():
            produced["png"] = png_path
    except Exception:
        _log.debug("viz-mcp to_png failed for trend report", exc_info=True)
    return produced


def render_trend(
    history: Sequence[Mapping[str, Any]] | str | Path,
    out_dir: str | Path,
    *,
    metric: str = "accuracy",
    title: str | None = None,
    extractor: Callable[[Mapping[str, Any]], list[tuple[str, float]]] | None = None,
    max_releases: int = _DEFAULT_MAX_RELEASES,
    write: bool = True,
) -> TrendReport:
    """Render a release-over-release eval trend from run history (#125).

    Builds, from ``history`` (a list of run-summary dicts or a path to a
    ``history.jsonl``), a :class:`TrendReport` with a Markdown comparison table,
    a Mermaid ``xychart`` headline line (both GitHub-inline), and a viz-mcp
    ``sections`` spec. When ``write`` is set, ``trend.md`` (Markdown + embedded
    Mermaid) and ``sections.json`` are written to ``out_dir``; the interactive
    Plotly HTML/PNG are additionally rendered **iff viz-mcp is installed** and
    are otherwise skipped with a note (the inline artifacts always succeed).

    Args:
        history: Run records, or a path to a ``history.jsonl`` to load.
        out_dir: Directory to write artifacts into (when ``write=True``).
        metric: The metric to chart (default ``"accuracy"``).
        title: Optional chart/section title.
        extractor: Optional ``record -> [(series_key, value), ...]`` override for
            a non-default summary schema.
        max_releases: Cap on releases shown inline (Markdown/Mermaid).
        write: Whether to write artifacts to ``out_dir``.

    Returns:
        A :class:`TrendReport`.
    """
    records = (
        load_history(history) if isinstance(history, (str, Path)) else [dict(r) for r in history]
    )
    releases, series = _extract_series(records, metric, extractor)
    latest_rows = _latest_rows(records)
    markdown = build_trend_markdown(
        releases, series, metric, title=title, max_releases=max_releases
    )
    mermaid = build_mermaid_xychart(
        releases, series, metric, title=title, max_releases=max_releases
    )
    sections = build_viz_sections(releases, series, metric, latest_rows, title=title)

    artifacts: dict[str, Path] = {}
    notes: list[str] = []
    out_path: Path | None = None
    if write:
        out_path = Path(out_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        md_file = out_path / "trend.md"
        md_file.write_text(markdown + "\n```mermaid\n" + mermaid + "\n```\n", encoding="utf-8")
        artifacts["markdown"] = md_file
        sections_file = out_path / "sections.json"
        sections_file.write_text(json.dumps(sections, indent=2, default=str), encoding="utf-8")
        artifacts["sections"] = sections_file
        rendered = _try_render_viz(sections, out_path, title=title or _default_title(metric))
        artifacts.update(rendered)
        if "html" not in rendered:
            notes.append(
                "viz-mcp interactive HTML/PNG not produced (viz-mcp not installed or its "
                "render failed); wrote the Markdown table + Mermaid chart + sections.json "
                "(install viz-mcp to also render the interactive Plotly HTML/PNG)."
            )

    return TrendReport(
        metric=metric,
        releases=releases,
        series=series,
        latest_rows=latest_rows,
        markdown=markdown,
        mermaid=mermaid,
        sections=sections,
        out_dir=out_path,
        artifacts=artifacts,
        notes=notes,
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
