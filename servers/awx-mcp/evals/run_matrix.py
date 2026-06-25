#!/usr/bin/env python
"""Tiered model-matrix runner for the awx-mcp Inspect AI eval suite.

Thin wrapper around :mod:`mcp_common.testing.eval.matrix_runner`.

Examples:
    uv run python evals/run_matrix.py --dry-run --mode all
    uv run python evals/run_matrix.py --tier fast --mode mcp --limit 2
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from mcp_common.testing.eval.matrix_runner import (
    MatrixPreflight,
    MatrixPreflightError,
    MatrixRunConfig,
    resolve_max_connections,
    resolve_modes,
    run_matrix,
    select_models,
)
from models import JUDGE_MODEL, MODELS, TIERS, judge_api_string

MODE_TASK: dict[str, tuple[str, str]] = {
    "mcp": ("mcp_eval", "awx_mcp_eval"),
    "cli": ("cli_eval", "awx_cli_eval"),
}
ALL_MODES: tuple[str, ...] = ("mcp", "cli")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_matrix.py",
        description="Tiered model-matrix runner for the awx-mcp inspect eval suite.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--tier", choices=[*TIERS, "all"], default="all")
    parser.add_argument("--mode", choices=[*ALL_MODES, "all"], default="all")
    parser.add_argument("--models", default="", help="Comma-separated model substring filters.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-connections", type=int, default=None)
    parser.add_argument("--judge-model", default=JUDGE_MODEL)
    parser.add_argument("--log-dir", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-verify", action="store_true")
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Skip AWX credential/connectivity preflight (resolve AWX_TOKEN + GET /ping/).",
    )
    parser.add_argument(
        "--skip-write-safety-preflight",
        action="store_true",
        help="Skip MCP_ENFORCE_READONLY write-safety preflight (not recommended).",
    )
    parser.add_argument(
        "--history",
        default=None,
        help="Optional path to append this run's summary to (history.jsonl). "
        "Off by default — set to evals/results/history.jsonl to accumulate "
        "release-over-release trend data (#88 Phase 3b).",
    )
    parser.add_argument(
        "--trend-dir",
        default=None,
        help="Optional directory to render the trend report into after appending "
        "history (trend.md + sections.json). Only used with --history.",
    )
    return parser


def _wrap_preflight(fn, error_type: type[Exception]):
    def wrapped() -> dict[str, Any]:
        try:
            return fn()
        except error_type as exc:
            raise MatrixPreflightError(str(exc)) from exc

    return wrapped


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # Write-capable server: enforce read-only mode for the whole matrix run.
    os.environ.setdefault("MCP_ENFORCE_READONLY", "1")

    from _env import AwxPreflightError, preflight_awx, preflight_write_safety
    from mcp_common.testing.eval import WriteSafetyError

    name_filters = [s.strip() for s in args.models.split(",") if s.strip()]
    modes = resolve_modes(args.mode, ALL_MODES)
    chosen = select_models(MODELS, args.tier, name_filters)

    catalog_state = "skipped (--no-verify)" if args.no_verify else "pending"
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    log_dir = Path(args.log_dir) if args.log_dir else _HERE / "logs" / "matrix" / timestamp

    judge_api = judge_api_string(args.judge_model)
    judge_api_key = os.environ.get("EVAL_JUDGE_API_KEY")
    judge_decoupled = bool(judge_api_key)
    judge_endpoint = (
        "EVAL_JUDGE_API_KEY (separate budget)" if judge_decoupled else "TOGETHER_API_KEY (shared)"
    )
    if os.environ.get("EVAL_JUDGE_BASE_URL"):
        judge_endpoint += f"; EVAL_JUDGE_BASE_URL={os.environ['EVAL_JUDGE_BASE_URL']}"
    max_connections, max_conn_reason = resolve_max_connections(
        args.max_connections, judge_api_key=judge_api_key
    )

    preflights = [
        MatrixPreflight(
            summary_key="write_safety_preflight",
            skip_flag=args.skip_write_safety_preflight,
            skip_arg="write-safety-preflight",
            plan_label="write safety",
            plan_value="assert MCP_ENFORCE_READONLY + server middleware before run (fail-fast)",
            execute_intro="WRITE-SAFETY PREFLIGHT: asserting enforced read-only eval mode ...",
            run=_wrap_preflight(preflight_write_safety, WriteSafetyError),
            abort_message=(
                "ABORT: write-capable AWX eval must run with MCP_ENFORCE_READONLY enabled "
                "and ReadOnlyEnforcementMiddleware installed — aborting instead of risking "
                "real side effects. Set MCP_ENFORCE_READONLY=1 and retry, or pass "
                "--skip-write-safety-preflight to bypass (not recommended)."
            ),
        ),
        MatrixPreflight(
            summary_key="preflight",
            skip_flag=args.skip_preflight,
            skip_arg="preflight",
            plan_label="preflight",
            plan_value="resolve AWX_TOKEN in parent + GET /api/v2/ping/ before run (fail-fast)",
            execute_intro="PREFLIGHT: resolving AWX creds in parent + GET /api/v2/ping/ ...",
            run=_wrap_preflight(preflight_awx, AwxPreflightError),
            abort_message=(
                "ABORT: AWX is unreachable / token unresolved. Every model would score ~0 "
                "for an infra reason — fix AWX_HOST/AWX_TOKEN (resolve op:// in this shell) "
                "and retry, or pass --skip-preflight to bypass."
            ),
        ),
    ]

    config = MatrixRunConfig(
        title="awx-mcp eval matrix",
        tier=args.tier,
        modes=modes,
        mode_tasks=MODE_TASK,
        models=chosen,
        judge_model=args.judge_model,
        judge_api=judge_api,
        judge_decoupled=judge_decoupled,
        judge_endpoint=judge_endpoint,
        max_connections=max_connections,
        max_conn_reason=max_conn_reason,
        limit=args.limit,
        catalog_state=catalog_state,
        log_dir=log_dir,
        timestamp=timestamp,
        dry_run=args.dry_run,
        preflights=preflights,
        history_path=Path(args.history) if args.history else None,
        trend_dir=Path(args.trend_dir) if args.trend_dir else None,
    )

    return run_matrix(config)


if __name__ == "__main__":
    raise SystemExit(main())
