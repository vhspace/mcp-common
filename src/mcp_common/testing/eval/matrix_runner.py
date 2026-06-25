"""Shared tiered model-matrix runner for Inspect AI eval suites (#88).

Runs eval scenarios across many models (fast/cheap -> medium -> high) and modes
with one command, **serially** by default, and prints a comparison table at the
end. Server-specific runners (e.g. netbox-mcp's ``run_matrix.py``) supply a
model registry, mode-to-task mapping, and optional preflight hooks.

Why serial (by default): the LLM-as-judge rate-limits under concurrency on a
*shared* key, so by default every eval runs with ``max_connections=1``. When
``EVAL_JUDGE_API_KEY`` (a separate judge key/budget) is set the default
auto-bumps (an explicit ``--max-connections`` always wins).

Preflight hooks run before the eval loop and abort the matrix on failure,
writing ``summary.json`` with an empty ``results`` list so infra failures are
visible instead of silently scoring ~0 across every model.
"""

from __future__ import annotations

import importlib
import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from mcp_common.testing.eval.judge_usage import install_judge_usage_tracking, reset_judge_usage
from mcp_common.testing.eval.report import add_judge_usage_to_summary, append_history, render_trend

__all__ = [
    "JUDGE_DECOUPLED_DEFAULT_CONNECTIONS",
    "MatrixEvalModel",
    "MatrixPreflight",
    "MatrixPreflightError",
    "MatrixRunConfig",
    "classify_model",
    "fetch_together_catalog",
    "judge_api_string",
    "load_task",
    "provider_of",
    "resolve_max_connections",
    "resolve_modes",
    "routes_to_together",
    "run_matrix",
    "select_models",
    "summarize_log",
    "together_api_model",
]

JUDGE_DECOUPLED_DEFAULT_CONNECTIONS = 4

_TOGETHER_MODELS_URL = "https://api.together.xyz/v1/models"


@runtime_checkable
class MatrixEvalModel(Protocol):
    """Minimal model-registry entry consumed by the matrix runner."""

    name: str
    tier: str
    enabled: bool
    note: str
    requires_env: str | None
    model_args: dict | None
    catalog_slug: str | None


class MatrixPreflightError(Exception):
    """Raised by a preflight hook when the matrix must abort before running evals."""


@dataclass(frozen=True)
class MatrixPreflight:
    """One fail-fast preflight step run before the eval loop.

    Attributes:
        summary_key: Key under which the preflight fact dict is stored in
            ``summary.json`` (e.g. ``"preflight"``).
        skip_flag: When ``True``, the preflight is skipped.
        skip_arg: CLI flag suffix (e.g. ``"preflight"`` -> ``--skip-preflight``).
        plan_label: Left column in the plan header (e.g. ``"preflight"``).
        plan_value: Right column when the preflight will run.
        execute_intro: First line printed when the preflight runs (e.g.
            ``"PREFLIGHT: resolving NetBox creds ..."``).
        run: Callable returning a fact dict on success. Raise
            :class:`MatrixPreflightError` to abort the matrix.
        abort_message: Multi-line message printed when the preflight fails.
    """

    summary_key: str
    skip_flag: bool
    skip_arg: str
    plan_label: str
    plan_value: str
    execute_intro: str
    run: Callable[[], dict[str, Any]]
    abort_message: str


@dataclass(frozen=True)
class MatrixRunConfig:
    """Resolved configuration for one matrix run.

    Attributes:
        history_path: Optional ``history.jsonl`` to append each run's ``summary``
            to (#88 Phase 3b). When set, every run (including preflight aborts)
            appends a record, so ``render_trend`` can chart release-over-release
            accuracy. Off by default (CI doesn't append; evals are on-demand).
        trend_dir: Optional directory to render the trend report into after
            appending history (#88 Phase 3b). When set, ``render_trend`` writes
            ``trend.md`` + ``sections.json`` (the inline Markdown/Mermaid
            artifacts) here. Only meaningful together with ``history_path``.
    """

    title: str
    tier: str
    modes: list[str]
    mode_tasks: dict[str, tuple[str, str]]
    models: list[MatrixEvalModel]
    judge_model: str
    judge_api: str
    judge_decoupled: bool
    judge_endpoint: str
    max_connections: int
    max_conn_reason: str
    limit: int | None
    catalog_state: str
    log_dir: Path
    timestamp: str
    dry_run: bool
    preflights: list[MatrixPreflight]
    history_path: Path | None = None
    trend_dir: Path | None = None


# ---------------------------------------------------------------------------
# Model-string helpers (inspect provider prefixes)
# ---------------------------------------------------------------------------
def provider_of(name: str) -> str:
    """Return the inspect provider prefix (segment before the first ``/``)."""
    return name.split("/", 1)[0]


def together_api_model(name: str) -> str | None:
    """Return the bare Together API model string for a ``together/...`` name."""
    prefix = "together/"
    return name[len(prefix) :] if name.startswith(prefix) else None


def routes_to_together(name: str) -> bool:
    """True if the inspect model string is served by the Together API."""
    return name.startswith("together/") or name.startswith("openai-api/together/")


def judge_api_string(judge: str) -> str:
    """Bare slug to export as ``EVAL_JUDGE_MODEL`` (the scorer is Together-only)."""
    return together_api_model(judge) or judge


# ---------------------------------------------------------------------------
# Live Together catalog (for slug verification)
# ---------------------------------------------------------------------------
def fetch_together_catalog(
    timeout: float = 20.0,
    *,
    user_agent: str = "mcp-common-eval-matrix/1.0",
) -> set[str] | None:
    """Return the set of live Together model ids, or ``None`` if unavailable."""
    api_key = os.environ.get("TOGETHER_API_KEY", "")
    if not api_key:
        return None
    req = urllib.request.Request(
        _TOGETHER_MODELS_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "User-Agent": user_agent,
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
def classify_model(model: MatrixEvalModel, catalog: set[str] | None) -> tuple[str, str]:
    """Decide whether *model* runs. Returns ``(status, reason)``.

    status is one of ``"run"`` | ``"skip"`` | ``"disabled"``.
    """
    if not model.enabled:
        return "disabled", (model.note or "disabled in registry")

    if model.requires_env and not os.environ.get(model.requires_env):
        return "skip", f"{model.requires_env} not set"

    bare = model.catalog_slug or together_api_model(model.name)
    if bare is not None:
        if not os.environ.get("TOGETHER_API_KEY"):
            return "skip", "TOGETHER_API_KEY not set"
        if catalog is not None and bare not in catalog:
            return "run", (
                f"WARNING: '{bare}' not in live Together catalog listing "
                "(unreliable serverless signal) — proceeding"
            )

    return "run", model.note


def select_models(
    models: Sequence[MatrixEvalModel],
    tier: str,
    name_filters: list[str],
) -> list[MatrixEvalModel]:
    """Models for *tier*, optionally narrowed by case-insensitive substring filters."""
    if tier == "all":
        chosen = list(models)
    else:
        chosen = [m for m in models if m.tier == tier]
    if name_filters:
        needles = [f.lower() for f in name_filters]
        chosen = [m for m in chosen if any(n in m.name.lower() for n in needles)]
    return chosen


def resolve_modes(mode: str, all_modes: Sequence[str]) -> list[str]:
    return list(all_modes) if mode == "all" else [mode]


def resolve_max_connections(
    explicit: int | None,
    *,
    judge_api_key: str | None,
) -> tuple[int, str]:
    """Resolve the effective inspect ``max_connections`` (+ a human-readable reason)."""
    if explicit is not None:
        return explicit, "explicit --max-connections"
    if judge_api_key:
        return (
            JUDGE_DECOUPLED_DEFAULT_CONNECTIONS,
            "auto-bump: EVAL_JUDGE_API_KEY set (separate judge budget)",
        )
    return 1, "default: shared judge key (serial to avoid judge 429 stall)"


def load_task(mode: str, mode_tasks: dict[str, tuple[str, str]]) -> Any:
    """Import and return the ``@task`` callable for *mode*."""
    module_name, attr = mode_tasks[mode]
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


def _plan_preflight_line(preflight: MatrixPreflight) -> str:
    if preflight.skip_flag:
        return f"skipped (--skip-{preflight.skip_arg})"
    return preflight.plan_value


def _build_summary(
    config: MatrixRunConfig,
    *,
    catalog_state: str,
    preflight_summaries: dict[str, dict[str, Any]],
    runnable: list[MatrixEvalModel],
    skipped: list[tuple[MatrixEvalModel, str]],
    disabled: list[tuple[MatrixEvalModel, str]],
    warnings: dict[str, str],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "timestamp": config.timestamp,
        "tier": config.tier,
        "modes": config.modes,
        "judge_model": config.judge_model,
        "judge_api_model": config.judge_api,
        "judge_decoupled": config.judge_decoupled,
        "max_connections": config.max_connections,
        "max_connections_reason": config.max_conn_reason,
        "limit": config.limit,
        "catalog_check": catalog_state,
        **preflight_summaries,
        "plan": {
            "run": [m.name for m in runnable],
            "skip": [{"model": m.name, "reason": r} for m, r in skipped],
            "disabled": [{"model": m.name, "reason": r} for m, r in disabled],
            "warnings": [{"model": n, "reason": r} for n, r in warnings.items()],
        },
        "results": results,
    }
    add_judge_usage_to_summary(summary)
    return summary


def _record_history(config: MatrixRunConfig, summary: dict[str, Any]) -> None:
    """Append ``summary`` to ``config.history_path`` and render trend if asked.

    No-op when ``history_path`` is unset (the default). History + trend are
    off-by-default in CI (#88 Phase 3b); a runner opts in via ``--history``.
    """
    if config.history_path is None:
        return
    append_history(summary, config.history_path)
    if config.trend_dir is not None:
        render_trend(config.history_path, config.trend_dir)
        print(f"Trend report rendered to: {config.trend_dir}")


def _run_preflights(
    config: MatrixRunConfig,
    summary_path: Path,
    *,
    catalog_state: str,
    preflight_summaries: dict[str, dict[str, Any]],
    runnable: list[MatrixEvalModel],
    skipped: list[tuple[MatrixEvalModel, str]],
    disabled: list[tuple[MatrixEvalModel, str]],
    warnings: dict[str, str],
) -> int | None:
    """Run preflight hooks. Returns an exit code when the matrix must abort."""
    for preflight in config.preflights:
        if preflight.skip_flag:
            preflight_summaries[preflight.summary_key] = {"status": "skipped"}
            skip_head = preflight.execute_intro.split(":", 1)[0]
            print(f"{skip_head}: skipped (--skip-{preflight.skip_arg})\n")
            continue

        print(_rule())
        print(preflight.execute_intro)
        try:
            preflight_summaries[preflight.summary_key] = preflight.run()
        except MatrixPreflightError as exc:
            preflight_summaries[preflight.summary_key] = {
                "status": "error",
                "error": str(exc),
            }
            fail_head = preflight.execute_intro.split(":", 1)[0]
            print(f"{fail_head} FAILED: {exc}")
            print(preflight.abort_message)
            summary = _build_summary(
                config,
                catalog_state=catalog_state,
                preflight_summaries=preflight_summaries,
                runnable=runnable,
                skipped=skipped,
                disabled=disabled,
                warnings=warnings,
                results=[],
            )
            summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
            _record_history(config, summary)
            print(f"\nAborted before running. summary.json written to: {config.log_dir}")
            return 2
        ok_head = preflight.execute_intro.split(":", 1)[0]
        print(f"{ok_head} OK: {preflight_summaries[preflight.summary_key]}\n")
    return None


def run_matrix(config: MatrixRunConfig) -> int:
    """Execute (or dry-run) a tiered model matrix and write ``summary.json``."""
    catalog: set[str] | None = None
    if config.catalog_state != "skipped (--no-verify)":
        catalog = fetch_together_catalog()
    catalog_state = config.catalog_state
    if catalog_state != "skipped (--no-verify)":
        catalog_state = f"{len(catalog)} live models" if catalog is not None else "unavailable"

    runnable: list[MatrixEvalModel] = []
    skipped: list[tuple[MatrixEvalModel, str]] = []
    disabled: list[tuple[MatrixEvalModel, str]] = []
    warnings: dict[str, str] = {}
    for model in config.models:
        status, reason = classify_model(model, catalog)
        if status == "run":
            runnable.append(model)
            if reason.startswith("WARNING"):
                warnings[model.name] = reason
        elif status == "disabled":
            disabled.append((model, reason))
        else:
            skipped.append((model, reason))

    n_evals = len(runnable) * len(config.modes)

    judge_warn = ""
    if provider_of(config.judge_model) != "together":
        judge_warn = " [WARN: judge always runs on Together regardless of prefix]"

    print(_rule("="))
    print(config.title)
    print(_rule("="))
    print(f"tiers selected : {config.tier}")
    print(f"modes selected : {', '.join(config.modes)}")
    print(
        f"judge model    : {config.judge_model}  ->  "
        f"EVAL_JUDGE_MODEL={config.judge_api}{judge_warn}"
    )
    print(f"judge endpoint : {config.judge_endpoint}")
    print(f"max-connections: {config.max_connections} ({config.max_conn_reason})")
    print(f"limit          : {config.limit if config.limit is not None else 'all samples'}")
    print(f"catalog check  : {catalog_state}")
    for preflight in config.preflights:
        print(f"{preflight.plan_label:<15}: {_plan_preflight_line(preflight)}")
    print(f"log dir        : {config.log_dir}")
    print()
    print(f"WILL RUN: {len(runnable)} model(s) x {len(config.modes)} mode(s) = {n_evals} eval(s)")
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

    if config.dry_run:
        print("DRY RUN — nothing executed.")
        return 0

    if not runnable:
        print("Nothing to run (no runnable models after filtering). Exiting.")
        return 0

    import inspect_ai

    from mcp_common.testing.eval import generate_config_for_provider_tier

    os.environ["EVAL_JUDGE_MODEL"] = config.judge_api
    os.environ.setdefault("TOGETHER_BASE_URL", "https://api.together.xyz/v1")

    reset_judge_usage()
    install_judge_usage_tracking()

    config.log_dir.mkdir(parents=True, exist_ok=True)
    summary_path = config.log_dir / "summary.json"
    preflight_summaries: dict[str, dict[str, Any]] = {
        pf.summary_key: {"status": "not_run"} for pf in config.preflights
    }

    abort_code = _run_preflights(
        config,
        summary_path,
        catalog_state=catalog_state,
        preflight_summaries=preflight_summaries,
        runnable=runnable,
        skipped=skipped,
        disabled=disabled,
        warnings=warnings,
    )
    if abort_code is not None:
        return abort_code

    rows: list[dict[str, Any]] = []
    run_no = 0
    for model in runnable:
        for mode in config.modes:
            run_no += 1
            gen_config_kwargs = generate_config_for_provider_tier(
                model.tier, provider_of(model.name)
            ).model_dump(exclude_none=True)
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
                "generate_config": gen_config_kwargs,
            }
            try:
                eval_kwargs: dict[str, Any] = {
                    "model": model.name,
                    "model_args": model.model_args or {},
                    "max_connections": config.max_connections,
                    "log_dir": str(config.log_dir),
                    "display": "plain",
                    **gen_config_kwargs,
                }
                if config.limit is not None:
                    eval_kwargs["limit"] = config.limit
                logs = inspect_ai.eval(load_task(mode, config.mode_tasks), **eval_kwargs)
                row.update(summarize_log(logs[0]))
            except Exception as exc:
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

    summary = _build_summary(
        config,
        catalog_state=catalog_state,
        preflight_summaries=preflight_summaries,
        runnable=runnable,
        skipped=skipped,
        disabled=disabled,
        warnings=warnings,
        results=rows,
    )
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _record_history(config, summary)
    print(f"\nLogs + summary.json written to: {config.log_dir}")

    n_failed = sum(1 for r in rows if r["status"] not in ("success",))
    return 1 if n_failed else 0
