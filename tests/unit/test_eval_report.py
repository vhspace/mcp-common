"""Tests for release-over-release trend reporting (report.py, #125)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mcp_common.testing.eval.report import (
    TrendReport,
    append_history,
    build_mermaid_xychart,
    build_trend_markdown,
    build_viz_sections,
    load_history,
    render_trend,
)

RUN1: dict[str, Any] = {
    "metadata": {"mcp": "netbox-mcp", "mcp_version": "1.0.0"},
    "results": [
        {"model": "qwen", "mode": "mcp", "accuracy": 0.80},
        {"model": "qwen", "mode": "cli", "accuracy": 0.50},
    ],
}
RUN2: dict[str, Any] = {
    "metadata": {"mcp": "netbox-mcp", "mcp_version": "1.1.0"},
    "results": [
        {"model": "qwen", "mode": "mcp", "accuracy": 0.85},
        {"model": "qwen", "mode": "cli", "accuracy": 0.60},
    ],
}


# ---------------------------------------------------------------------------
# History accumulation
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestHistory:
    def test_append_creates_dirs_and_appends(self, tmp_path: Path) -> None:
        history_path = tmp_path / "nested" / "history.jsonl"
        append_history(RUN1, history_path)
        append_history(RUN2, history_path)
        assert history_path.exists()
        history = load_history(history_path)
        assert len(history) == 2
        assert history[0]["metadata"]["mcp_version"] == "1.0.0"

    def test_append_stamps_recorded_at(self, tmp_path: Path) -> None:
        record = append_history({"x": 1}, tmp_path / "h.jsonl")
        assert "recorded_at" in record

    def test_append_honors_explicit_recorded_at(self, tmp_path: Path) -> None:
        record = append_history(
            {"x": 1}, tmp_path / "h.jsonl", recorded_at="2026-01-01T00:00:00+00:00"
        )
        assert record["recorded_at"] == "2026-01-01T00:00:00+00:00"

    def test_unique_by_dedupes_reruns(self, tmp_path: Path) -> None:
        history_path = tmp_path / "h.jsonl"
        append_history(RUN1, history_path, unique_by="mcp_version")
        append_history(RUN1, history_path, unique_by="mcp_version")  # same -> skipped
        assert len(load_history(history_path)) == 1
        append_history(RUN2, history_path, unique_by="mcp_version")  # new -> appended
        assert len(load_history(history_path)) == 2

    def test_does_not_mutate_input(self, tmp_path: Path) -> None:
        append_history(RUN1, tmp_path / "h.jsonl")
        assert "recorded_at" not in RUN1

    def test_load_missing_returns_empty(self, tmp_path: Path) -> None:
        assert load_history(tmp_path / "nope.jsonl") == []

    def test_load_skips_blank_and_corrupt_lines(self, tmp_path: Path) -> None:
        history_path = tmp_path / "h.jsonl"
        history_path.write_text('{"a": 1}\n\nnot json\n{"b": 2}\n', encoding="utf-8")
        assert load_history(history_path) == [{"a": 1}, {"b": 2}]


# ---------------------------------------------------------------------------
# render_trend
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestRenderTrend:
    def test_builds_series_and_writes_inline_artifacts(self, tmp_path: Path) -> None:
        out = tmp_path / "out"
        report = render_trend([RUN1, RUN2], out)
        assert isinstance(report, TrendReport)
        assert report.releases == ["1.0.0", "1.1.0"]
        assert report.series["qwen / mcp"] == [0.80, 0.85]
        assert report.series["qwen / cli"] == [0.50, 0.60]
        assert (out / "trend.md").exists()
        assert (out / "sections.json").exists()
        assert report.artifacts["markdown"].exists()
        assert report.artifacts["sections"].exists()

    def test_viz_absent_degrades_gracefully(self, tmp_path: Path) -> None:
        # viz_mcp is not installed: no html/png artifacts, but a note explains it
        report = render_trend([RUN1, RUN2], tmp_path / "out")
        assert "html" not in report.artifacts
        assert "png" not in report.artifacts
        assert report.notes and any("viz-mcp" in note for note in report.notes)

    def test_markdown_table_has_releases_and_delta(self, tmp_path: Path) -> None:
        report = render_trend([RUN1, RUN2], tmp_path / "out")
        md = report.markdown
        assert "| Series |" in md
        assert "1.0.0" in md
        assert "1.1.0" in md
        assert "qwen / mcp" in md
        assert "+0.05" in md  # 0.85 - 0.80

    def test_mermaid_headline_is_mean_per_release(self, tmp_path: Path) -> None:
        report = render_trend([RUN1, RUN2], tmp_path / "out")
        mermaid = report.mermaid
        assert mermaid.startswith("xychart-beta")
        assert "x-axis" in mermaid
        assert "line [" in mermaid
        assert "0.65" in mermaid  # mean(0.80, 0.50)
        assert "0.725" in mermaid  # mean(0.85, 0.60)

    def test_trend_md_embeds_mermaid_block(self, tmp_path: Path) -> None:
        out = tmp_path / "out"
        render_trend([RUN1, RUN2], out)
        text = (out / "trend.md").read_text(encoding="utf-8")
        assert "```mermaid" in text
        assert "xychart-beta" in text

    def test_sections_have_timeseries_and_table(self, tmp_path: Path) -> None:
        report = render_trend([RUN1, RUN2], tmp_path / "out")
        types = [section["type"] for section in report.sections]
        assert "timeseries" in types
        assert "table" in types
        timeseries = next(s for s in report.sections if s["type"] == "timeseries")
        assert timeseries["x"] == ["1.0.0", "1.1.0"]
        names = {series["name"] for series in timeseries["series"]}
        assert names == {"qwen / mcp", "qwen / cli"}

    def test_accepts_history_file_path(self, tmp_path: Path) -> None:
        history_path = tmp_path / "h.jsonl"
        append_history(RUN1, history_path)
        append_history(RUN2, history_path)
        report = render_trend(history_path, tmp_path / "out")
        assert report.releases == ["1.0.0", "1.1.0"]

    def test_overall_fallback_when_no_result_rows(self, tmp_path: Path) -> None:
        runs = [
            {"metadata": {"mcp_version": "1"}, "accuracy": 0.7},
            {"metadata": {"mcp_version": "2"}, "accuracy": 0.9},
        ]
        report = render_trend(runs, tmp_path / "out")
        assert report.series["overall"] == [0.7, 0.9]

    def test_extractor_override(self, tmp_path: Path) -> None:
        runs = [
            {"version": "1", "score": {"x": 1.0}},
            {"version": "2", "score": {"x": 2.0}},
        ]

        def extract(record: Any) -> list[tuple[str, float]]:
            return [("x", float(record["score"]["x"]))]

        report = render_trend(runs, tmp_path / "out", metric="x", extractor=extract)
        assert report.releases == ["1", "2"]
        assert report.series["x"] == [1.0, 2.0]

    def test_write_false_produces_no_files(self, tmp_path: Path) -> None:
        out = tmp_path / "out"
        report = render_trend([RUN1], out, write=False)
        assert report.out_dir is None
        assert not out.exists()
        assert report.markdown  # still computed in-memory

    def test_empty_history_does_not_crash(self, tmp_path: Path) -> None:
        out = tmp_path / "out"
        report = render_trend([], out)
        assert report.releases == []
        assert report.series == {}
        assert "xychart-beta" in report.mermaid
        assert (out / "trend.md").exists()


# ---------------------------------------------------------------------------
# Pure builders
# ---------------------------------------------------------------------------


@pytest.mark.eval
class TestBuilders:
    def test_markdown_caps_releases(self) -> None:
        releases = [f"v{i}" for i in range(20)]
        series = {"s": [float(i) for i in range(20)]}
        md = build_trend_markdown(releases, series, "accuracy", max_releases=5)
        assert "v19" in md
        assert "v15" in md
        assert "v0" not in md  # older releases dropped from the inline table

    def test_mermaid_quotes_labels_with_spaces(self) -> None:
        mermaid = build_mermaid_xychart(["a b", "c"], {"s": [0.1, 0.2]}, "accuracy")
        assert '"a b"' in mermaid

    def test_mermaid_y_axis_stays_unit_for_in_range_metric(self) -> None:
        # accuracy (and other 0..1 metrics) keep a fixed, comparable 0 --> 1 axis
        mermaid = build_mermaid_xychart(["a", "b"], {"s": [0.5, 0.9]}, "accuracy")
        assert 'y-axis "accuracy" 0 --> 1' in mermaid

    def test_mermaid_y_axis_expands_for_out_of_unit_metric(self) -> None:
        # a metric whose values exceed 1 (e.g. latency) must not be clipped at 1
        mermaid = build_mermaid_xychart(["a", "b"], {"s": [2.0, 5.0]}, "latency_ms")
        assert "0 --> 5" in mermaid

    def test_viz_sections_omit_table_without_rows(self) -> None:
        sections = build_viz_sections(["1"], {"overall": [0.5]}, "accuracy", [])
        types = [section["type"] for section in sections]
        assert types == ["timeseries"]
