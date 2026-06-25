#!/usr/bin/env python
"""Tiered model-matrix runner for the netbox-mcp Inspect AI eval suite.

Thin wrapper around :mod:`mcp_common.testing.eval.matrix_runner` — supplies the
netbox model registry, mode-to-task mapping, and NetBox-specific preflight hooks.

Examples:
    # Primary validation — print the plan, run nothing:
    uv run python evals/run_matrix.py --dry-run --mode all

    # Full open-weights sweep, MCP mode (on-demand; not in CI):
    uv run python evals/run_matrix.py --tier all --mode mcp

    # Cheap smoke — one model, mcp, 2 samples:
    uv run python evals/run_matrix.py --tier fast --mode mcp --limit 2 \
        --models Qwen3.5-9B
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

# mode -> (module, @task attribute). One @task per eval file. Imported lazily
# and passed to inspect_ai.eval() as a callable — inspect's string-path loader
# rejects absolute paths ("Non-relative patterns are unsupported"), and a bare
# relative path would be cwd-dependent, so we pass the task object instead.
MODE_TASK: dict[str, tuple[str, str]] = {
    "mcp": ("mcp_eval", "netbox_mcp_eval"),
    "cli": ("cli_eval", "netbox_cli_eval"),
    "combined": ("combined_eval", "netbox_combined_eval"),
}
ALL_MODES: tuple[str, ...] = ("mcp", "cli", "combined")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_matrix.py",
        description="Tiered model-matrix runner for the netbox-mcp inspect eval suite.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--tier",
        choices=[*TIERS, "all"],
        default="all",
        help="Model tier to run (default: all enabled tiers).",
    )
    parser.add_argument(
        "--mode",
        choices=[*ALL_MODES, "all"],
        default="all",
        help="Eval mode(s) to run (default: all).",
    )
    parser.add_argument(
        "--models",
        default="",
        help="Comma-separated case-insensitive substrings to restrict models "
        "(e.g. 'Qwen3.5-9B'). Useful for cheap smoke runs.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap samples per eval (passed through to inspect). Great for smoke runs.",
    )
    parser.add_argument(
        "--max-connections",
        type=int,
        default=None,
        help=(
            "inspect max-connections. Default 1 (serial) on a SHARED judge key — "
            "the LLM-as-judge rate-limits (429) under concurrency. When "
            "EVAL_JUDGE_API_KEY is set (the judge runs on a separate key/budget, "
            "mcp-common #132) the default auto-bumps to 4. An explicit value always wins."
        ),
    )
    parser.add_argument(
        "--judge-model",
        default=JUDGE_MODEL,
        help=f"Judge model, inspect together/... form (default: {JUDGE_MODEL}).",
    )
    parser.add_argument(
        "--log-dir",
        default=None,
        help="Where inspect writes logs (default: evals/logs/matrix/<timestamp>/).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the resolved plan (runs/skips/disabled) and exit without running.",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip the live Together catalog check used to verify slugs resolve.",
    )
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Skip the NetBox credential/connectivity preflight (resolve "
        "NETBOX_TOKEN in the parent + one GET /api/status/). The preflight "
        "fails fast so a cred failure errors loudly instead of silently "
        "scoring ~0 across every model (netbox-mcp#117).",
    )
    parser.add_argument(
        "--skip-version-preflight",
        action="store_true",
        help="Skip the binary/version preflight that asserts the resolved "
        "netbox-cli AND the spawned netbox-mcp are the REPO's current build "
        "(version == pyproject), not a stale global PATH binary. The preflight "
        "fails fast so the eval cannot silently test the wrong code (the "
        "netbox-mcp#137 stale-global root cause).",
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

    from _netbox_env import (
        EvalBinaryVersionError,
        NetboxPreflightError,
        preflight_eval_binaries,
        preflight_netbox,
    )

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
            summary_key="version_preflight",
            skip_flag=args.skip_version_preflight,
            skip_arg="version-preflight",
            plan_label="version check",
            plan_value="assert repo's current netbox-cli/netbox-mcp under test (fail-fast)",
            execute_intro=(
                "VERSION PREFLIGHT: asserting repo's current netbox-cli/netbox-mcp under test ..."
            ),
            run=_wrap_preflight(preflight_eval_binaries, EvalBinaryVersionError),
            abort_message=(
                "ABORT: the eval would test a stale/wrong netbox-cli/netbox-mcp "
                "build (the netbox-mcp#137 stale-global root cause) — aborting "
                "instead of producing artifact results. Run from the worktree "
                "with `uv run` (so the eval venv ships the current build), or "
                "pass --skip-version-preflight to bypass."
            ),
        ),
        MatrixPreflight(
            summary_key="preflight",
            skip_flag=args.skip_preflight,
            skip_arg="preflight",
            plan_label="preflight",
            plan_value="resolve NETBOX_TOKEN in parent + GET /api/status/ before run (fail-fast)",
            execute_intro="PREFLIGHT: resolving NetBox creds in parent + GET /api/status/ ...",
            run=_wrap_preflight(preflight_netbox, NetboxPreflightError),
            abort_message=(
                "ABORT: NetBox is unreachable / token unresolved. Every model "
                "would score ~0 for an infra reason, not skill — aborting the "
                "run instead. Fix NETBOX_URL/NETBOX_TOKEN (resolve op:// in this "
                "shell) and retry, or pass --skip-preflight to bypass."
            ),
        ),
    ]

    config = MatrixRunConfig(
        title="netbox-mcp eval matrix",
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
    )

    return run_matrix(config)


if __name__ == "__main__":
    raise SystemExit(main())
