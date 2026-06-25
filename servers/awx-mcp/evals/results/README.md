# AWX MCP eval results

This directory holds durable, committed eval run summaries and the
release-over-release trend history for the `awx-mcp` eval suite (#88 Phase 3b).

## Trend history

`history.jsonl` is an append-only time-series of run summaries, one JSON record
per line. It is produced on demand (off by default — CI does not append) by
passing `--history` to the matrix runner:

```bash
uv run python evals/run_matrix.py --tier fast --mode mcp --limit 5 \
    --history evals/results/history.jsonl \
    --trend-dir evals/results/trend
```

`--trend-dir` additionally renders `trend.md` (a Markdown comparison table + a
Mermaid `xychart` headline, both GitHub-inline) and `sections.json` (a viz-mcp
spec) from the accumulated history.

Re-runs are idempotent when you pass a `unique_by` key — see
`mcp_common.testing.eval.report.append_history`.

## Preserved run snapshots

Curated per-release run summaries (a human-readable `RESULTS.md`, the merged
`summary.json`, and `per_scenario.csv`) can be committed under
`results/<date>/`, mirroring the netbox-mcp convention. Raw inspect `.eval`
logs stay local/git-ignored under `evals/logs/`.
