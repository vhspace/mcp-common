#!/usr/bin/env python
"""Tiered model-matrix runner for the netbox-mcp Inspect AI eval suite.

Runs the eval scenarios across many models (fast/cheap -> medium -> high) and
across modes (mcp / cli / combined) with one command, **serially**, and prints a
comparison table at the end.

Why serial (by default): the LLM-as-judge (Together) rate-limits under
concurrency on a *shared* key, so by default every eval runs with
``--max-connections 1``. mcp-common #132 decoupled the judge client, so when
``EVAL_JUDGE_API_KEY`` (a separate judge key/budget) is set the default
auto-bumps (an explicit ``--max-connections`` always wins). The judge is fixed
to ``JUDGE_MODEL`` (via the ``EVAL_JUDGE_MODEL`` env var that ``mcp_common``'s
scorers read) so scores are comparable across the models under test.

Each model under test also gets a per-tier ``GenerateConfig`` (mcp-common
v0.29.0 ``generate_config_for_tier``): ``temperature=0``, a tier ``max_tokens``
cap, and thinking-off — applied via ``inspect_ai.eval()``'s generation kwargs.

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
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from models import (
    JUDGE_MODEL,
    TIERS,
    EvalModel,
    judge_api_string,
    models_for_tier,
    provider_of,
    routes_to_together,
    together_api_model,
)

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

# When EVAL_JUDGE_API_KEY is set the LLM-as-judge runs on its own key/budget
# (mcp-common #132 decoupled the judge client), so the matrix can parallelize
# without re-triggering the shared-key judge 429 stall that pinned prior runs
# to serial (#121). Conservative auto-bump (not unbounded) — the operator can
# still pass an explicit --max-connections to go higher or lower.
_JUDGE_DECOUPLED_DEFAULT_CONNECTIONS = 4

_TOGETHER_MODELS_URL = "https://api.together.xyz/v1/models"


# ---------------------------------------------------------------------------
# Live Together catalog (for slug verification)
# ---------------------------------------------------------------------------
def fetch_together_catalog(timeout: float = 20.0) -> set[str] | None:
    """Return the set of live Together model ids, or ``None`` if unavailable.

    Best-effort and read-only. Returns ``None`` when ``TOGETHER_API_KEY`` is
    missing or the request fails — callers then skip catalog-based verification.
    """
    api_key = os.environ.get("TOGETHER_API_KEY", "")
    if not api_key:
        return None
    req = urllib.request.Request(
        _TOGETHER_MODELS_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            # non-default UA: Together sits behind Cloudflare like NetBox does
            "User-Agent": "netbox-mcp-eval-matrix/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.load(resp)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None
    rows = payload if isinstance(payload, list) else payload.get("data", [])
    return {row["id"] for row in rows if isinstance(row, dict) and "id" in row}


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------
def classify_model(model: EvalModel, catalog: set[str] | None) -> tuple[str, str]:
    """Decide whether *model* runs. Returns ``(status, reason)``.

    status is one of ``"run"`` | ``"skip"`` | ``"disabled"``.
    """
    if not model.enabled:
        return "disabled", (model.note or "disabled in registry")

    if model.requires_env and not os.environ.get(model.requires_env):
        return "skip", f"{model.requires_env} not set"

    # Bare Together slug for the API-key / catalog gate. For the generic
    # ``openai-api/together/<slug>`` route (used by streaming-required models
    # like Qwen3.7-Max), ``together_api_model(name)`` is ``None``, so fall back
    # to the explicit ``catalog_slug`` carried on the entry.
    bare = model.catalog_slug or together_api_model(model.name)
    if bare is not None:  # a Together-served model (together/ or openai-api/together/)
        if not os.environ.get("TOGETHER_API_KEY"):
            return "skip", "TOGETHER_API_KEY not set"
        if catalog is not None and bare not in catalog:
            # Catalog *listing* is an UNRELIABLE serverless signal: many listed
            # models 400 as non-serverless, and some servable models are absent
            # or served under versioned ids. Warn and proceed rather than
            # hard-skipping on an ambiguous listing check.
            return "run", (
                f"WARNING: '{bare}' not in live Together catalog listing "
                "(unreliable serverless signal) — proceeding"
            )

    return "run", model.note


def select_models(tier: str, name_filters: list[str]) -> list[EvalModel]:
    """Models for *tier*, optionally narrowed by case-insensitive substring filters."""
    chosen = models_for_tier(tier)
    if name_filters:
        needles = [f.lower() for f in name_filters]
        chosen = [m for m in chosen if any(n in m.name.lower() for n in needles)]
    return chosen


def resolve_modes(mode: str) -> list[str]:
    return list(ALL_MODES) if mode == "all" else [mode]


def resolve_max_connections(explicit: int | None, *, judge_api_key: str | None) -> tuple[int, str]:
    """Resolve the effective inspect ``max_connections`` (+ a human-readable reason).

    Default policy (no explicit ``--max-connections``):

    * **Shared judge key** (only ``TOGETHER_API_KEY`` set) -> ``1`` (serial). The
      LLM-as-judge rate-limits (429) under concurrency on a shared key, which
      stalled prior matrix runs (netbox-mcp#121), so the safe default stays 1.
    * **Decoupled judge** (``EVAL_JUDGE_API_KEY`` set, i.e. the judge has its own
      key/budget — mcp-common#132) -> :data:`_JUDGE_DECOUPLED_DEFAULT_CONNECTIONS`.
      The judge no longer shares the model-under-test's budget, so a modest bump
      is safe.

    An explicit ``--max-connections`` always wins (either direction), so the
    operator can raise it further (or force serial) regardless of the env.
    """
    if explicit is not None:
        return explicit, "explicit --max-connections"
    if judge_api_key:
        return (
            _JUDGE_DECOUPLED_DEFAULT_CONNECTIONS,
            "auto-bump: EVAL_JUDGE_API_KEY set (separate judge budget)",
        )
    return 1, "default: shared judge key (serial to avoid judge 429 stall)"


def load_task(mode: str) -> Any:
    """Import and return the ``@task`` callable for *mode* (e.g. ``netbox_mcp_eval``)."""
    import importlib

    module_name, attr = MODE_TASK[mode]
    module = importlib.import_module(module_name)
    return getattr(module, attr)


# ---------------------------------------------------------------------------
# Result parsing
# ---------------------------------------------------------------------------
def _accuracy(log: Any) -> float | None:
    results = getattr(log, "results", None)
    if not results or not results.scores:
        return None
    for score in results.scores:
        metric = score.metrics.get("accuracy")
        if metric is not None:
            return float(metric.value)
    return None


def _count_cpi(log: Any) -> tuple[int, int, int, int, int]:
    """Return ``(correct, partial, incorrect, other, n)`` from a log's samples."""
    from inspect_ai.scorer import CORRECT, INCORRECT, PARTIAL

    samples = getattr(log, "samples", None)
    if not samples and getattr(log, "location", None):
        try:
            from inspect_ai.log import read_eval_log

            samples = read_eval_log(log.location).samples
        except Exception:
            samples = None

    c = p = i = other = 0
    n = 0
    for sample in samples or []:
        n += 1
        scores = getattr(sample, "scores", None)
        if not scores:
            other += 1
            continue
        value = next(iter(scores.values())).value
        if value == CORRECT:
            c += 1
        elif value == PARTIAL:
            p += 1
        elif value == INCORRECT:
            i += 1
        else:
            other += 1
    return c, p, i, other, n


def summarize_log(log: Any) -> dict[str, Any]:
    status = getattr(log, "status", "unknown")
    correct, partial, incorrect, other, n = _count_cpi(log)
    error = None
    if status == "error" and getattr(log, "error", None):
        error = str(log.error.message).splitlines()[0][:160]
    return {
        "status": status,
        "accuracy": _accuracy(log),
        "correct": correct,
        "partial": partial,
        "incorrect": incorrect,
        "other": other,
        "n": n,
        "error": error,
        "log": getattr(log, "location", None),
    }


# ---------------------------------------------------------------------------
# Pretty printing
# ---------------------------------------------------------------------------
def _rule(char: str = "-", width: int = 92) -> str:
    return char * width


def _print_table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [len(h) for h in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(cell))
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print(fmt.format(*row))


def _fmt_acc(acc: float | None) -> str:
    return f"{acc:.3f}" if isinstance(acc, float) else "-"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
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
            "mcp-common #132) the default auto-bumps to "
            f"{_JUDGE_DECOUPLED_DEFAULT_CONNECTIONS}. An explicit value always wins."
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    name_filters = [s.strip() for s in args.models.split(",") if s.strip()]
    modes = resolve_modes(args.mode)
    chosen = select_models(args.tier, name_filters)

    catalog = None if args.no_verify else fetch_together_catalog()
    catalog_state = (
        "skipped (--no-verify)"
        if args.no_verify
        else (f"{len(catalog)} live models" if catalog is not None else "unavailable")
    )

    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    log_dir = Path(args.log_dir) if args.log_dir else _HERE / "logs" / "matrix" / timestamp

    judge_api = judge_api_string(args.judge_model)
    judge_warn = ""
    if provider_of(args.judge_model) != "together":
        judge_warn = " [WARN: judge always runs on Together regardless of prefix]"

    # Judge decoupling (mcp-common #132): the scorer reads EVAL_JUDGE_API_KEY /
    # EVAL_JUDGE_BASE_URL at runtime, falling back to TOGETHER_API_KEY + the
    # default Together endpoint. A separate judge key means the judge no longer
    # shares the model-under-test budget, so max-connections can auto-bump.
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

    # ---- classify ----
    runnable: list[EvalModel] = []
    skipped: list[tuple[EvalModel, str]] = []
    disabled: list[tuple[EvalModel, str]] = []
    warnings: dict[str, str] = {}  # model.name -> warning surfaced for a runnable model
    for model in chosen:
        status, reason = classify_model(model, catalog)
        if status == "run":
            runnable.append(model)
            if reason.startswith("WARNING"):
                warnings[model.name] = reason
        elif status == "disabled":
            disabled.append((model, reason))
        else:
            skipped.append((model, reason))

    n_evals = len(runnable) * len(modes)

    # ---- header / plan ----
    print(_rule("="))
    print("netbox-mcp eval matrix")
    print(_rule("="))
    print(f"tiers selected : {args.tier}")
    print(f"modes selected : {', '.join(modes)}")
    print(f"judge model    : {args.judge_model}  ->  EVAL_JUDGE_MODEL={judge_api}{judge_warn}")
    print(f"judge endpoint : {judge_endpoint}")
    print(f"max-connections: {max_connections} ({max_conn_reason})")
    print(f"limit          : {args.limit if args.limit is not None else 'all samples'}")
    print(f"catalog check  : {catalog_state}")
    preflight_state = (
        "skipped (--skip-preflight)"
        if args.skip_preflight
        else "resolve NETBOX_TOKEN in parent + GET /api/status/ before run (fail-fast)"
    )
    print(f"preflight      : {preflight_state}")
    print(f"log dir        : {log_dir}")
    print()
    print(f"WILL RUN: {len(runnable)} model(s) x {len(modes)} mode(s) = {n_evals} eval(s)")
    for model in runnable:
        bare = model.catalog_slug or together_api_model(model.name)
        flag = " (unverified slug)" if catalog is None and bare else ""
        note = f"  # {model.note}" if model.note else ""
        print(f"  RUN   [{model.tier:<6}] {model.name}{flag}{note}")
        if model.name in warnings:
            print(f"        ^ {warnings[model.name]}")
    if skipped:
        print(f"\nSKIP: {len(skipped)} model(s)")
        for model, reason in skipped:
            print(f"  SKIP  [{model.tier:<6}] {model.name}: {reason}")
    if disabled:
        print(f"\nDISABLED: {len(disabled)} model(s)")
        for model, reason in disabled:
            print(f"  DISABLED [{model.tier:<6}] {model.name}: {reason}")
    print(_rule())

    if args.dry_run:
        print("DRY RUN — nothing executed.")
        return 0

    if not runnable:
        print("Nothing to run (no runnable models after filtering). Exiting.")
        return 0

    # ---- execute ----
    import inspect_ai

    # Per-tier reliability levers (mcp-common v0.29.0). Imported here (not at
    # module top) so --help / --dry-run stay importable without the eval extra:
    # mcp_common.testing.eval hard-requires inspect_ai at import time.
    from mcp_common.testing.eval import generate_config_for_tier

    os.environ["EVAL_JUDGE_MODEL"] = judge_api
    # Streaming-required models (e.g. Qwen3.7-Max) run via the generic
    # ``openai-api/together/<slug>`` provider, which has no built-in Together
    # base URL and therefore *requires* ``TOGETHER_BASE_URL``. The native
    # ``together/`` provider falls back to the same URL, so setting it globally
    # is safe. ``setdefault`` preserves any explicit user override.
    os.environ.setdefault("TOGETHER_BASE_URL", "https://api.together.xyz/v1")
    log_dir.mkdir(parents=True, exist_ok=True)
    summary_path = log_dir / "summary.json"

    # ---- preflight: resolve creds in parent, export plain token, prove NetBox ----
    # Every eval mode needs NetBox. The spawned MCP child can't resolve op://
    # references (no op/1Password in the child), so resolve once HERE and verify
    # one cheap authenticated call works. Abort loudly on failure instead of
    # silently scoring ~0 for every model (netbox-mcp#117).
    preflight_summary: dict[str, Any]
    if args.skip_preflight:
        preflight_summary = {"status": "skipped"}
        print("PREFLIGHT: skipped (--skip-preflight)\n")
    else:
        from _netbox_env import NetboxPreflightError, preflight_netbox

        print(_rule())
        print("PREFLIGHT: resolving NetBox creds in parent + GET /api/status/ ...")
        try:
            preflight_summary = preflight_netbox()
        except NetboxPreflightError as exc:
            preflight_summary = {"status": "error", "error": str(exc)}
            print(f"PREFLIGHT FAILED: {exc}")
            print(
                "ABORT: NetBox is unreachable / token unresolved. Every model "
                "would score ~0 for an infra reason, not skill — aborting the "
                "run instead. Fix NETBOX_URL/NETBOX_TOKEN (resolve op:// in this "
                "shell) and retry, or pass --skip-preflight to bypass."
            )
            summary = {
                "timestamp": timestamp,
                "tier": args.tier,
                "modes": modes,
                "judge_model": args.judge_model,
                "judge_api_model": judge_api,
                "judge_decoupled": judge_decoupled,
                "max_connections": max_connections,
                "max_connections_reason": max_conn_reason,
                "limit": args.limit,
                "catalog_check": catalog_state,
                "preflight": preflight_summary,
                "plan": {
                    "run": [m.name for m in runnable],
                    "skip": [{"model": m.name, "reason": r} for m, r in skipped],
                    "disabled": [{"model": m.name, "reason": r} for m, r in disabled],
                    "warnings": [{"model": n, "reason": r} for n, r in warnings.items()],
                },
                "results": [],
            }
            summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
            print(f"\nAborted before running. summary.json written to: {log_dir}")
            return 2
        print(f"PREFLIGHT OK: {preflight_summary}\n")

    rows: list[dict[str, Any]] = []
    run_no = 0
    for model in runnable:
        for mode in modes:
            run_no += 1
            # Per-tier GenerateConfig: temperature=0, tier max_tokens cap,
            # thinking-off (reasoning_effort=none + enable_thinking=False).
            gen_config_kwargs = generate_config_for_tier(model.tier).model_dump(exclude_none=True)
            # Together rejects reasoning_effort="none" with HTTP 400 ("Input
            # validation error") for several served models (live-probed
            # 2026-05-31: Qwen3.5-9B and gpt-oss-20b reject it; DeepSeek-V4-Pro
            # accepts it). The preset's OTHER thinking-off lever —
            # extra_body.chat_template_kwargs.enable_thinking=False, the
            # Together/vLLM chat-template switch — disables thinking WITHOUT the
            # 400, so for Together-routed models (the matrix's only providers)
            # drop the unsupported reasoning_effort and keep extra_body. Other
            # providers (e.g. a future OpenAI/Anthropic entry) keep it.
            if routes_to_together(model.name):
                gen_config_kwargs.pop("reasoning_effort", None)
            print(
                f"\n[{run_no}/{n_evals}] {mode:<8} {model.name}  "
                f"[tier={model.tier} temperature={gen_config_kwargs.get('temperature')} "
                f"max_tokens={gen_config_kwargs.get('max_tokens')} thinking=off]",
                flush=True,
            )
            row: dict[str, Any] = {
                "model": model.name,
                "tier": model.tier,
                "mode": mode,
                # Surface the applied reliability levers for durable evidence.
                "generate_config": gen_config_kwargs,
            }
            try:
                eval_kwargs: dict[str, Any] = {
                    "model": model.name,
                    # Provider-constructor args (e.g. {"stream": True} for the
                    # openai-api streaming route). Empty dict for plain models.
                    "model_args": model.model_args or {},
                    "max_connections": max_connections,
                    "log_dir": str(log_dir),
                    "display": "plain",
                    # inspect's eval() takes GenerateConfig fields as **kwargs (it
                    # builds GenerateConfig(**kwargs) internally) while model_args
                    # is a SEPARATE param, so spreading the dumped per-tier config
                    # neither clobbers the streaming model_args nor the explicit
                    # max_connections above (the preset never sets it).
                    **gen_config_kwargs,
                }
                if args.limit is not None:
                    eval_kwargs["limit"] = args.limit
                logs = inspect_ai.eval(load_task(mode), **eval_kwargs)
                row.update(summarize_log(logs[0]))
            except Exception as exc:  # record failure, don't abort the whole matrix
                row.update(
                    {
                        "status": "exception",
                        "accuracy": None,
                        "correct": 0,
                        "partial": 0,
                        "incorrect": 0,
                        "other": 0,
                        "n": 0,
                        "error": f"{type(exc).__name__}: {exc}".splitlines()[0][:160],
                        "log": None,
                    }
                )
            rows.append(row)

    # ---- summary ----
    print("\n" + _rule("="))
    print("SUMMARY")
    print(_rule("="))
    headers = ["MODEL", "TIER", "MODE", "STATUS", "ACC", "C", "P", "I", "N", "NOTE"]
    table_rows: list[list[str]] = []
    for row in rows:
        table_rows.append(
            [
                row["model"],
                row["tier"],
                row["mode"],
                str(row["status"]),
                _fmt_acc(row.get("accuracy")),
                str(row.get("correct", 0)),
                str(row.get("partial", 0)),
                str(row.get("incorrect", 0)),
                str(row.get("n", 0)),
                (row.get("error") or "")[:60],
            ]
        )
    _print_table(headers, table_rows)

    summary = {
        "timestamp": timestamp,
        "tier": args.tier,
        "modes": modes,
        "judge_model": args.judge_model,
        "judge_api_model": judge_api,
        "judge_decoupled": judge_decoupled,
        "max_connections": max_connections,
        "max_connections_reason": max_conn_reason,
        "limit": args.limit,
        "catalog_check": catalog_state,
        "preflight": preflight_summary,
        "plan": {
            "run": [m.name for m in runnable],
            "skip": [{"model": m.name, "reason": r} for m, r in skipped],
            "disabled": [{"model": m.name, "reason": r} for m, r in disabled],
            "warnings": [{"model": name, "reason": r} for name, r in warnings.items()],
        },
        "results": rows,
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nLogs + summary.json written to: {log_dir}")

    n_failed = sum(1 for r in rows if r["status"] not in ("success",))
    return 1 if n_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
