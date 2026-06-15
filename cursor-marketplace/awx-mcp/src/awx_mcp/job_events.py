"""Size-independent job-results extraction from the AWX ``job_events`` API.

AWX caps the ``txt``/``ansi`` stdout renderers at ``STDOUT_MAX_BYTES_DISPLAY``
(default 1 MiB), so for multi-MB jobs the blob-based log/results surfaces break
(see :mod:`awx_mcp.awx_client`). The ``job_events`` API has no such cap: it is
paginated, so per-host task outcomes (and optional diffs) can always be
retrieved regardless of total stdout size.

This module holds the *pure* transformations over already-fetched event dicts
so they can be unit-tested without any HTTP. Callers (the ``awx-cli results``
command and the ``awx_get_job_results`` MCP tool) own the pagination and pass
the collected ``results`` list here.

Relevant per-host runner events and their ``event_data`` fields:

* ``runner_on_ok`` / ``runner_item_on_ok`` — task succeeded (``changed`` flags
  whether it reported a change).
* ``runner_on_failed`` / ``runner_item_on_failed`` — task failed.
* ``runner_on_unreachable`` — host unreachable.
* ``runner_on_skipped`` / ``runner_item_on_skipped`` — task skipped.

``event_data`` typically carries ``task``, ``task_action`` (the module),
``play``, ``host``, ``role`` and ``res`` (the module result, which may include a
``diff`` when diff mode is on, plus ``msg``/``stdout`` for failures).
"""

from __future__ import annotations

import fnmatch
from typing import Any

#: Per-host runner event names we treat as task outcomes.
_HOST_RESULT_EVENTS = frozenset(
    {
        "runner_on_ok",
        "runner_on_failed",
        "runner_on_unreachable",
        "runner_on_skipped",
        "runner_on_async_ok",
        "runner_on_async_failed",
        "runner_item_on_ok",
        "runner_item_on_failed",
        "runner_item_on_skipped",
    }
)

#: Valid ``--status`` filter values (also the per-host summary counters).
STATUS_VALUES = ("ok", "changed", "failed", "unreachable", "skipped")

#: Characters that make a host value a pattern rather than an exact name.
_WILDCARD_CHARS = "*?["


def job_events_query(
    status: str | None = None,
    host: str | None = None,
    *,
    page_size: int = 200,
) -> dict[str, Any]:
    """Build the AWX ``job_events`` query params for a results/summary fetch.

    Pushes the cheap, unambiguous filters server-side to cut pagination volume:
    a ``status`` maps to ``failed``/``changed`` flags or an exact ``event`` name,
    and a *literal* (wildcard-free) host narrows by ``host_name``. Patterned
    hosts and the final status classification are still applied client-side by
    :func:`build_results` so behavior is identical regardless of what the server
    pre-filters.
    """
    params: dict[str, Any] = {"page_size": page_size, "order_by": "counter"}
    if status == "failed":
        params["failed"] = "true"
    elif status == "unreachable":
        params["event"] = "runner_on_unreachable"
    elif status == "changed":
        params["changed"] = "true"
    elif status == "ok":
        params["event"] = "runner_on_ok"
    if host and not any(c in host for c in _WILDCARD_CHARS):
        params["host_name"] = host
    return params


def event_status(event: dict[str, Any]) -> str | None:
    """Classify a job event into a per-host task status, or ``None``.

    Returns one of :data:`STATUS_VALUES` for per-host runner events, otherwise
    ``None`` (e.g. ``playbook_on_*`` framing events, warnings, verbose output).
    """
    name = str(event.get("event", ""))
    if name not in _HOST_RESULT_EVENTS:
        return None
    if "unreachable" in name:
        return "unreachable"
    if event.get("failed") or "failed" in name:
        return "failed"
    if "skipped" in name:
        return "skipped"
    # Remaining events are the *_on_ok family.
    return "changed" if event.get("changed") else "ok"


def _res(event: dict[str, Any]) -> dict[str, Any]:
    data = event.get("event_data") or {}
    res = data.get("res")
    return res if isinstance(res, dict) else {}


def extract_diffs(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the structured diff(s) attached to *event*, if any.

    Ansible attaches diffs to the result ``res`` either as a single ``diff``
    dict or as a list of them (e.g. loops / multiple files). Each diff is a
    ``{"before": ..., "after": ..., "before_header": ..., "after_header": ...}``
    mapping; we normalise to a list and drop empties.
    """
    res = _res(event)
    raw = res.get("diff")
    if raw is None:
        return []
    candidates = raw if isinstance(raw, list) else [raw]
    diffs: list[dict[str, Any]] = []
    for d in candidates:
        if isinstance(d, dict) and d:
            diffs.append(d)
    return diffs


def _message(event: dict[str, Any], status: str) -> str:
    """Best-effort human-readable message for a result (failures mostly)."""
    res = _res(event)
    for key in ("msg", "stderr", "module_stderr", "reason"):
        val = res.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    if status == "unreachable":
        return "host unreachable"
    return ""


def _task_label(event: dict[str, Any]) -> str:
    data = event.get("event_data") or {}
    task = data.get("task") or event.get("task") or ""
    item = data.get("res", {}).get("item") if isinstance(data.get("res"), dict) else None
    if item in (None, ""):
        item = data.get("item")
    if item not in (None, ""):
        return f"{task} (item={item})"
    return str(task)


def event_to_result(event: dict[str, Any], status: str, *, include_diff: bool) -> dict[str, Any]:
    """Build a single per-host task-outcome record from a runner event."""
    data = event.get("event_data") or {}
    entry: dict[str, Any] = {
        "host": event.get("host_name") or data.get("host") or "",
        "task": _task_label(event),
        "status": status,
        "changed": bool(event.get("changed")),
        "module": data.get("task_action") or "",
        "play": data.get("play") or "",
    }
    message = _message(event, status)
    if message:
        entry["message"] = message
    if include_diff:
        diffs = extract_diffs(event)
        if diffs:
            entry["diff"] = diffs
    return entry


def _matches(
    status: str,
    entry_host: str,
    entry_task: str,
    *,
    task: str | None,
    host: str | None,
    status_filter: str | None,
) -> bool:
    if status_filter is not None and status != status_filter:
        return False
    if host is not None and not fnmatch.fnmatch(entry_host.lower(), host.lower()):
        return False
    return not (task is not None and not fnmatch.fnmatch(entry_task.lower(), task.lower()))


def build_results(
    job_id: int,
    events: list[dict[str, Any]],
    *,
    task: str | None = None,
    host: str | None = None,
    status: str | None = None,
    include_diff: bool = False,
    max_results: int = 0,
) -> dict[str, Any]:
    """Reduce raw job events into filtered per-host task outcomes.

    Args:
        job_id: The job these events belong to (echoed into the result).
        events: Raw event dicts (any mix; non-runner events are ignored).
        task: Optional task-name pattern (``fnmatch``, case-insensitive).
        host: Optional hostname pattern (``fnmatch``, case-insensitive).
        status: Optional exact status filter (:data:`STATUS_VALUES`).
        include_diff: Attach structured diffs to each record when present.
        max_results: Cap on returned records (0 = unlimited).

    Returns:
        ``{"job_id", "count", "results", "host_summary", "filters", "truncated"}``.
    """
    results: list[dict[str, Any]] = []
    host_summary: dict[str, dict[str, int]] = {}
    truncated = False

    for event in events:
        st = event_status(event)
        if st is None:
            continue
        entry = event_to_result(event, st, include_diff=include_diff)
        if not _matches(
            st, entry["host"], entry["task"], task=task, host=host, status_filter=status
        ):
            continue
        counts = host_summary.setdefault(entry["host"], dict.fromkeys(STATUS_VALUES, 0))
        counts[st] += 1
        if max_results and len(results) >= max_results:
            truncated = True
            continue
        results.append(entry)

    return {
        "job_id": job_id,
        "count": len(results),
        "results": results,
        "host_summary": host_summary,
        "filters": {"task": task, "host": host, "status": status, "diff": include_diff},
        "truncated": truncated,
    }


def summarize_events(job_id: int, events: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a log-summary-shaped dict from job events (size-independent).

    Mirrors the keys produced by :meth:`awx_mcp.log_parser.ParsedLog.to_dict`
    so the existing ``log-summary`` rendering works unchanged when it falls back
    to events because the stdout blob was size-gated. Plays come from
    ``playbook_on_play_start`` events and tasks from ``playbook_on_task_start``.
    """
    plays: list[str] = []
    total_tasks = 0
    failed_tasks: list[dict[str, Any]] = []
    host_stats: dict[str, dict[str, int]] = {}

    for event in events:
        name = str(event.get("event", ""))
        data = event.get("event_data") or {}
        if name == "playbook_on_play_start":
            play = data.get("play") or data.get("name")
            if play:
                plays.append(str(play))
            continue
        if name == "playbook_on_task_start":
            total_tasks += 1
            continue
        st = event_status(event)
        if st is None:
            continue
        host = event.get("host_name") or data.get("host") or ""
        counts = host_stats.setdefault(
            host,
            {"ok": 0, "changed": 0, "unreachable": 0, "failed": 0, "skipped": 0},
        )
        # An "ok with changed" counts toward both ok and changed in PLAY RECAP.
        if st == "changed":
            counts["ok"] += 1
            counts["changed"] += 1
        elif st in counts:
            counts[st] += 1
        if st in ("failed", "unreachable"):
            failed_tasks.append(
                {
                    "host": host,
                    "task": _task_label(event),
                    "module": data.get("task_action") or "FAILED",
                    "message": _message(event, st) or "",
                }
            )

    host_stats_list: list[dict[str, Any]] = [
        {
            "host": h,
            "ok": c["ok"],
            "changed": c["changed"],
            "unreachable": c["unreachable"],
            "failed": c["failed"],
            "skipped": c["skipped"],
            "rescued": 0,
            "ignored": 0,
        }
        for h, c in sorted(host_stats.items())
    ]
    any_failed = any(c["failed"] > 0 for c in host_stats.values())
    any_unreachable = any(c["unreachable"] > 0 for c in host_stats.values())
    has_failures = bool(failed_tasks) or any_failed or any_unreachable
    if not host_stats:
        overall = "failed" if failed_tasks else "unknown"
    elif any_unreachable:
        overall = "unreachable"
    elif any_failed:
        overall = "failed"
    else:
        overall = "successful"

    recap_lines = [
        f"{h['host']} : ok={h['ok']} changed={h['changed']} "
        f"unreachable={h['unreachable']} failed={h['failed']} skipped={h['skipped']}"
        for h in host_stats_list
    ]
    recap_text = "PLAY RECAP\n" + "\n".join(recap_lines) if recap_lines else ""

    return {
        "plays": plays,
        "total_tasks": total_tasks,
        "failed_tasks": failed_tasks,
        "warnings": [],
        "host_stats": host_stats_list,
        "recap_text": recap_text,
        "total_lines": 0,
        "has_failures": has_failures,
        "overall_result": overall,
        "source": "job_events",
    }


def render_events_text(
    events: list[dict[str, Any]],
    *,
    filter_mode: str = "all",
    host: str | None = None,
    task: str | None = None,
) -> str:
    """Reconstruct filtered stdout text from job events (size-independent).

    Used as the fallback for ``stdout --host``/``--filter`` when the blob is
    size-gated: each event carries a rendered ``stdout`` chunk, so concatenating
    the chunks of matching task outcomes reproduces a filtered view without ever
    fetching the multi-MB blob.

    Args:
        events: Raw event dicts.
        filter_mode: ``all`` (default), ``errors`` (failed/unreachable) or
            ``changed``.
        host: Optional hostname ``fnmatch`` pattern.
        task: Optional task-name ``fnmatch`` pattern.
    """
    error_statuses = {"failed", "unreachable"}
    chunks: list[str] = []
    for event in events:
        st = event_status(event)
        if st is None:
            continue
        if filter_mode == "errors" and st not in error_statuses:
            continue
        if filter_mode == "changed" and st != "changed":
            continue
        data = event.get("event_data") or {}
        entry_host = str(event.get("host_name") or data.get("host") or "")
        if host is not None and not fnmatch.fnmatch(entry_host.lower(), host.lower()):
            continue
        if task is not None and not fnmatch.fnmatch(_task_label(event).lower(), task.lower()):
            continue
        text = event.get("stdout")
        if isinstance(text, str) and text:
            chunks.append(text.rstrip("\n"))
        else:
            entry = event_to_result(event, st, include_diff=False)
            line = f"{st}: [{entry['host']}] {entry['task']}".rstrip()
            if entry.get("message"):
                line += f" => {entry['message']}"
            chunks.append(line)
    return "\n".join(chunks)
