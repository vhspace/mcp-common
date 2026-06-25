#!/usr/bin/env python
"""Tiered model-matrix runner for the CLI-discovery Inspect AI eval suite.

Thin wrapper around :mod:`mcp_common.testing.eval.matrix_runner`. The
discovery suite is CLI-only (no MCP mode) and needs NO credentials —
``--version`` / ``--help`` are eager, no-creds introspection paths on every
``*-cli`` — so there are no AWX/NetBox preflights.

The primary model under test is ``together/moonshotai/Kimi-K2.7-Code`` (the
kimi model Hermes runs). Run just kimi with:

    uv run python evals/cli_discovery/run_matrix.py --tier high --models Kimi

Examples:
    uv run python evals/cli_discovery/run_matrix.py --dry-run
    uv run python evals/cli_discovery/run_matrix.py --tier high --models Kimi --limit 1
    uv run python evals/cli_discovery/run_matrix.py --tier high --models Kimi
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from mcp_common.testing.eval.matrix_runner import (
    MatrixRunConfig,
    resolve_max_connections,
    resolve_modes,
    run_matrix,
    select_models,
)
from models import JUDGE_MODEL, MODELS, TIERS, judge_api_string

# CLI-only suite: single mode, single task. (module_path, task_attr)
MODE_TASK: dict[str, tuple[str, str]] = {
    "cli": ("cli_eval", "cli_discovery_eval"),
}
ALL_MODES: tuple[str, ...] = ("cli",)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_matrix.py",
        description="Tiered model-matrix runner for the CLI-discovery inspect eval suite.",
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
    parser.add_argument("--no-verify", action="store_true", help="Skip live Together catalog slug check.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

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

    config = MatrixRunConfig(
        title="cli-discovery eval matrix",
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
        preflights=[],  # no creds needed for --version / --help
    )

    return run_matrix(config)


if __name__ == "__main__":
    raise SystemExit(main())
