"""awx-cli: Thin CLI wrapper around the AWX REST API.

Provides the same capabilities as awx-mcp but via shell commands,
enabling AI agents to use AWX with ~40-90% fewer tokens than MCP.
"""

from __future__ import annotations

import ipaddress
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

import typer
from mcp_common.agent_remediation import install_cli_exception_handler
from mcp_common.logging import setup_logging

from awx_mcp.awx_client import AwxRestClient

_logger = setup_logging(name="awx-cli", level="INFO", system_log=True)

MAX_CONSECUTIVE_POLL_ERRORS = 10


def _poll_until_terminal(
    client: "AwxRestClient",
    endpoint: str,
    resource_id: int,
    label: str,
    *,
    timeout: int,
    poll_interval: float,
    json_output: bool,
    on_complete: Callable[[dict], None] | None = None,
    error_context: dict[str, Any] | None = None,
) -> dict:
    """Poll an AWX resource until it reaches a terminal state."""
    start_time = time.monotonic()
    deadline = start_time + timeout
    terminal_states = {"successful", "failed", "error", "canceled"}
    last_status = "unknown"
    consecutive_errors = 0
    extra = error_context or {}

    while time.monotonic() < deadline:
        time.sleep(poll_interval)
        try:
            job_data = client.get(f"{endpoint}/{resource_id}")
            consecutive_errors = 0
        except Exception as exc:
            consecutive_errors += 1
            typer.echo(
                f"  {label} {resource_id}: poll error ({consecutive_errors}/{MAX_CONSECUTIVE_POLL_ERRORS}): {exc}",
                err=True,
            )
            if consecutive_errors >= MAX_CONSECUTIVE_POLL_ERRORS:
                typer.echo(
                    f"  Giving up after {MAX_CONSECUTIVE_POLL_ERRORS} consecutive poll errors",
                    err=True,
                )
                if json_output:
                    _output(
                        {
                            "error": {"type": type(exc).__name__, "message": str(exc)},
                            "job_id": resource_id,
                            "last_status": last_status,
                            "consecutive_errors": consecutive_errors,
                            **extra,
                        },
                        as_json=True,
                    )
                    raise typer.Exit(1) from None
                raise
            continue
        if not isinstance(job_data, dict):
            consecutive_errors += 1
            typer.echo(f"  {label} {resource_id}: unexpected response type, retrying...", err=True)
            continue
        last_status = job_data.get("status", "unknown")
        elapsed_wall = time.monotonic() - start_time
        typer.echo(f"  {label} {resource_id}: {last_status} ({elapsed_wall:.0f}s)", err=True)
        if last_status in terminal_states:
            typer.echo(f"FINISHED: {last_status}", err=True)
            if on_complete:
                on_complete(job_data)
            _output(job_data, as_json=json_output)
            if last_status != "successful":
                raise typer.Exit(1)
            return job_data

    typer.echo(
        f"Timed out after {timeout}s ({label.lower()}_id={resource_id}). Last status: {last_status}",
        err=True,
    )
    if json_output:
        _output(
            {
                "error": {"type": "Timeout", "message": f"Timed out after {timeout}s"},
                "job_id": resource_id,
                "last_status": last_status,
                **extra,
            },
            as_json=True,
        )
    raise typer.Exit(2)


app = typer.Typer(
    name="awx-cli",
    help="Interact with Ansible AWX / Automation Controller. Use --help on any subcommand.",
    no_args_is_help=True,
)
install_cli_exception_handler(app, project_repo="vhspace/awx-mcp", logger=_logger)


def _client() -> AwxRestClient:
    host = os.environ.get("AWX_HOST") or os.environ.get("CONTROLLER_HOST")
    token = os.environ.get("AWX_TOKEN") or os.environ.get("CONTROLLER_OAUTH_TOKEN")
    if not host or not token:
        typer.echo("Error: AWX_HOST and AWX_TOKEN env vars required", err=True)
        raise typer.Exit(1)
    verify = os.environ.get("VERIFY_SSL", "true").lower() not in ("false", "0", "no")
    api_base = os.environ.get("API_BASE_PATH", "/api/v2")
    timeout = float(os.environ.get("TIMEOUT_SECONDS", "30"))
    return AwxRestClient(
        host=host,
        token=token,
        api_base_path=api_base,
        verify_ssl=verify,
        timeout_seconds=timeout,
    )


def _pick_fields(data: dict | list, fields: list[str] | None) -> dict | list:
    if not fields:
        return data
    if isinstance(data, list):
        return [{k: v for k, v in item.items() if k in fields} for item in data]
    return {k: v for k, v in data.items() if k in fields}


def _apply_fields_filter(resp: dict | list, fields: str | None) -> dict | list:
    """Apply --fields filtering to a response."""
    if not fields:
        return resp
    field_list = [f.strip() for f in fields.split(",")]
    if isinstance(resp, dict) and "results" in resp:
        return {**resp, "results": _pick_fields(resp["results"], field_list)}
    if isinstance(resp, dict):
        return _pick_fields(resp, field_list)
    return resp


def _resolve_id(client: AwxRestClient, resource_type: str, name_or_id: str) -> int:
    """Resolve a resource name or ID to a numeric ID.

    If name_or_id is numeric, returns it as int.
    Otherwise, queries AWX by name__iexact and returns the match.
    Raises typer.Exit on no match or ambiguous matches.
    """
    if name_or_id.isdigit():
        return int(name_or_id)
    resp = client.get(resource_type, params={"name__iexact": name_or_id})
    results = resp.get("results", []) if isinstance(resp, dict) else []
    if len(results) == 0:
        typer.echo(f"Error: no {resource_type} found matching '{name_or_id}'", err=True)
        raise typer.Exit(1)
    if len(results) > 1:
        names = ", ".join(f"[{r['id']}] {r.get('name','?')}" for r in results)
        typer.echo(f"Error: ambiguous — {len(results)} matches for '{name_or_id}': {names}", err=True)
        raise typer.Exit(1)
    return results[0]["id"]


def _format_job_line(j: dict) -> str:
    jid = j.get("id", "?")
    name = j.get("name", j.get("summary_fields", {}).get("job_template", {}).get("name", "?"))
    status = j.get("status", "?")
    created = j.get("created", "")
    if isinstance(created, str) and len(created) > 19:
        created = created[:19]
    elapsed = j.get("elapsed")
    parts = [f"[{jid}]", name, f"status={status}"]
    if created:
        parts.append(f"created={created}")
    if elapsed is not None:
        parts.append(f"elapsed={elapsed:.1f}s")
    return "  ".join(parts)


def _format_template_line(t: dict) -> str:
    tid = t.get("id", "?")
    name = t.get("name", "?")
    playbook = t.get("playbook", "")
    parts = [f"[{tid}]", name]
    if playbook:
        parts.append(f"playbook={playbook}")
    return "  ".join(parts)


def _format_event_line(e: dict) -> str:
    eid = e.get("id", "?")
    event_type = e.get("event_display", e.get("event", "?"))
    host = e.get("host_name", "")
    task = e.get("event_data", {}).get("task", "")
    failed = e.get("failed", False)
    changed = e.get("changed", False)
    parts = [f"[{eid}]", event_type]
    if host:
        parts.append(f"host={host}")
    if task:
        parts.append(f"task={task}")
    if failed:
        parts.append("FAILED")
    elif changed:
        parts.append("changed")
    return "  ".join(parts)


def _format_resource_line(r: dict) -> str:
    rid = r.get("id", "?")
    name = r.get("name", r.get("display", "?"))
    status = r.get("status", "")
    parts = [f"[{rid}]", str(name)]
    if status:
        parts.append(f"status={status}")
    return "  ".join(parts)


def _paginate_all(
    client: AwxRestClient, endpoint: str, params: dict[str, Any], max_results: int = 0
) -> dict[str, Any]:
    """Fetch all pages from a paginated AWX endpoint."""
    all_results: list[dict] = []
    page = params.pop("page", 1)
    while True:
        params["page"] = page
        resp = client.get(endpoint, params=params)
        if not isinstance(resp, dict) or "results" not in resp:
            return resp
        all_results.extend(resp["results"])
        if max_results and len(all_results) >= max_results:
            all_results = all_results[:max_results]
            break
        if not resp.get("next"):
            break
        page += 1
    return {"count": resp.get("count", len(all_results)), "results": all_results}


def _output(data: object, as_json: bool = False, line_fmt: Any = None) -> None:
    if as_json:
        typer.echo(json.dumps(data, indent=2, default=str))
        return

    if isinstance(data, dict) and "results" in data:
        count = data.get("count", len(data["results"]))
        results = data["results"]
        shown = len(results)
        if count > shown:
            typer.echo(
                f"# {count} total, showing {shown} — use --limit {count} for all", err=True
            )
        typer.echo(f"# {count} result(s)")
        fmt = line_fmt or _format_resource_line
        for item in results:
            typer.echo(fmt(item))
    elif isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, dict):
                v = v.get("name", v.get("display", v))
            elif isinstance(v, list) and len(v) > 5:
                v = f"[{len(v)} items]"
            typer.echo(f"  {k}: {v}")
    elif isinstance(data, str):
        typer.echo(data)
    else:
        typer.echo(json.dumps(data, indent=2, default=str))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command()
def templates(
    search: str | None = typer.Option(
        None, "--search", "-s", help="Filter by name (case-insensitive contains)"
    ),
    page_size: int = typer.Option(20, "--limit", "-l"),
    page: int = typer.Option(1, "--page", "-p"),
    fields: str | None = typer.Option(None, "--fields", "-f", help="Comma-separated fields"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """List job templates."""
    client = _client()
    params: dict[str, Any] = {"page_size": page_size, "page": page}
    if search:
        params["name__icontains"] = search
    resp = client.get("job_templates", params=params)
    resp = _apply_fields_filter(resp, fields)
    _output(resp, as_json=json_output, line_fmt=_format_template_line)


@app.command()
def workflows(
    search: str | None = typer.Option(None, "--search", "-s", help="Filter by name"),
    page_size: int = typer.Option(20, "--limit", "-l"),
    page: int = typer.Option(1, "--page", "-p"),
    fields: str | None = typer.Option(None, "--fields", "-f"),
    json_output: bool = typer.Option(False, "--json", "-j"),
):
    """List workflow job templates."""
    client = _client()
    params: dict[str, Any] = {"page_size": page_size, "page": page}
    if search:
        params["name__icontains"] = search
    resp = client.get("workflow_job_templates", params=params)
    resp = _apply_fields_filter(resp, fields)
    _output(resp, as_json=json_output, line_fmt=_format_template_line)


@app.command()
def jobs(
    status: str | None = typer.Option(
        None, "--status", help="Filter by status (running, successful, failed, etc.)"
    ),
    template_id: int | None = typer.Option(
        None, "--template", "-t", help="Filter by job template ID"
    ),
    template_name: str | None = typer.Option(
        None, "--template-name", help="Filter by template name (case-insensitive contains)"
    ),
    page_size: int = typer.Option(20, "--limit", "-l"),
    page: int = typer.Option(1, "--page", "-p"),
    order_by: str = typer.Option(
        "-created", "--order", "-o", help="Sort field (prefix - for desc)"
    ),
    fields: str | None = typer.Option(None, "--fields", "-f"),
    json_output: bool = typer.Option(False, "--json", "-j"),
):
    """List jobs (recent first by default)."""
    client = _client()
    params: dict[str, Any] = {"page_size": page_size, "page": page, "order_by": order_by}
    if status:
        params["status"] = status
    if template_id:
        params["job_template"] = template_id
    if template_name:
        params["name__icontains"] = template_name
    endpoint = "jobs" if template_id or template_name else "unified_jobs"
    resp = client.get(endpoint, params=params)
    resp = _apply_fields_filter(resp, fields)
    _output(resp, as_json=json_output, line_fmt=_format_job_line)


@app.command()
def job(
    job_id: int = typer.Argument(help="Job ID"),
    fields: str | None = typer.Option(None, "--fields", "-f"),
    json_output: bool = typer.Option(False, "--json", "-j"),
):
    """Get details for a single job."""
    client = _client()
    resp = client.get(f"jobs/{job_id}")
    resp = _apply_fields_filter(resp, fields)
    _output(resp, as_json=json_output)


@app.command()
def stdout(
    job_id: int = typer.Argument(help="Job ID"),
    format: str = typer.Option("txt", "--format", help="Output format: txt, ansi, json, html"),
    limit_chars: int = typer.Option(50000, "--limit-chars", help="Max characters to return"),
    start_line: int | None = typer.Option(
        None, "--start-line", help="Starting line number (AWX API pagination)"
    ),
    end_line: int | None = typer.Option(
        None, "--end-line", help="Ending line number (AWX API pagination)"
    ),
    truncation: str = typer.Option(
        "tail",
        "--truncation",
        "-T",
        help="Truncation strategy: head, tail (default), head_tail, recap_context",
    ),
    filter_mode: str = typer.Option(
        "all",
        "--filter",
        help="Filter output: all (default), errors (failed/fatal only), changed (changed only)",
    ),
    play: str | None = typer.Option(
        None, "--play", help="Filter by play name (substring) or 1-based index"
    ),
    host: str | None = typer.Option(
        None, "--host", help="Filter by hostname pattern (supports wildcards via fnmatch)"
    ),
    task_filter: str | None = typer.Option(
        None, "--task", help="Filter by task name pattern (supports wildcards via fnmatch)"
    ),
    json_output: bool = typer.Option(False, "--json", "-j"),
):
    """Get stdout/output for a job.

    By default shows the tail (end) of the log, which contains failures and PLAY RECAP.
    Use --truncation=head for original first-N-chars behavior.

    Filter flags (--filter, --play, --host, --task) apply before truncation:

        awx-cli stdout 4348 --filter errors
        awx-cli stdout 4348 --host "gpu*" --filter changed
        awx-cli stdout 4348 --play 1 --task "Configure *"
    """
    from awx_mcp.log_parser import filter_stdout, smart_truncate

    client = _client()
    params: dict[str, Any] = {"format": format}
    if start_line is not None:
        params["start_line"] = start_line
    if end_line is not None:
        params["end_line"] = end_line

    has_filters = (
        filter_mode != "all" or play is not None or host is not None or task_filter is not None
    )

    if format in ("txt", "ansi", "html"):
        content = client.get_text(f"jobs/{job_id}/stdout", params=params, accept="text/plain")
        if has_filters:
            content = filter_stdout(
                content, filter_mode=filter_mode, play=play, host=host, task=task_filter
            )
        trunc = smart_truncate(content, limit_chars, strategy=truncation)
        if json_output:
            _output(
                {
                    "job_id": job_id,
                    "format": format,
                    "truncated": trunc["truncated"],
                    "truncation_strategy": trunc["strategy"],
                    "original_length": trunc["original_length"],
                    "filtered": has_filters,
                    "content": trunc["content"],
                },
                as_json=True,
            )
        else:
            typer.echo(trunc["content"])
            if trunc["truncated"]:
                typer.echo(
                    f"\n--- {trunc.get('note', f'truncated at {limit_chars} chars')} ---",
                    err=True,
                )
    else:
        resp = client.get(f"jobs/{job_id}/stdout", params=params)
        _output(resp, as_json=json_output)


@app.command(name="log-summary")
def log_summary(
    job_id: int = typer.Argument(help="Job ID"),
    sections: str = typer.Option(
        "all",
        "--sections",
        "-s",
        help="Comma-separated: all, summary, failures, warnings, recap",
    ),
    json_output: bool = typer.Option(False, "--json", "-j"),
):
    """Parse job log into structured summary (plays, failures, recap, warnings).

    Fetches the complete log to find PLAY RECAP at the end, but returns only
    structured data. Much faster for triage than reading raw stdout.
    """
    from awx_mcp.log_parser import parse_ansible_log

    client = _client()
    content = client.get_text(
        f"jobs/{job_id}/stdout", params={"format": "txt"}, accept="text/plain"
    )

    parsed = parse_ansible_log(content)
    full = parsed.to_dict()

    requested = {s.strip() for s in sections.split(",")}
    if "all" in requested:
        result = full
    else:
        result: dict[str, Any] = {
            "total_lines": full["total_lines"],
            "has_failures": full["has_failures"],
            "overall_result": full["overall_result"],
        }
        if "summary" in requested:
            result["plays"] = full["plays"]
            result["total_tasks"] = full["total_tasks"]
        if "failures" in requested:
            result["failed_tasks"] = full["failed_tasks"]
        if "warnings" in requested:
            result["warnings"] = full["warnings"]
        if "recap" in requested:
            result["recap_text"] = full["recap_text"]
            result["host_stats"] = full["host_stats"]

    result["job_id"] = job_id
    result["log_chars"] = len(content)

    if json_output:
        _output(result, as_json=True)
        return

    typer.echo(f"Job {job_id} \u2014 {full['overall_result'].upper()}")
    typer.echo(
        f"  Lines: {full['total_lines']}  Plays: {len(full['plays'])}  Tasks: {full['total_tasks']}  Log: {len(content):,} chars"
    )
    typer.echo()

    if "all" in requested or "failures" in requested:
        if full["failed_tasks"]:
            typer.echo("FAILURES:")
            for f in full["failed_tasks"]:
                typer.echo(f"  [{f['host']}] {f['task']} ({f['module']})")
                msg = f["message"]
                if len(msg) > 200:
                    msg = msg[:200] + "..."
                typer.echo(f"    {msg}")
            typer.echo()

    if "all" in requested or "warnings" in requested:
        if full["warnings"]:
            typer.echo(f"WARNINGS ({len(full['warnings'])}):")
            for w in full["warnings"][:20]:
                typer.echo(f"  - {w}")
            if len(full["warnings"]) > 20:
                typer.echo(f"  ... and {len(full['warnings']) - 20} more")
            typer.echo()

    if "all" in requested or "recap" in requested:
        if full["host_stats"]:
            typer.echo("HOST STATS:")
            for h in full["host_stats"]:
                parts = [f"ok={h['ok']}", f"changed={h['changed']}"]
                if h["failed"]:
                    parts.append(f"failed={h['failed']}")
                if h["unreachable"]:
                    parts.append(f"unreachable={h['unreachable']}")
                if h["skipped"]:
                    parts.append(f"skipped={h['skipped']}")
                typer.echo(f"  {h['host']}: {' '.join(parts)}")


@app.command()
def events(
    job_id: int = typer.Argument(help="Job ID"),
    failed_only: bool = typer.Option(False, "--failed", help="Only show failed events"),
    host: str | None = typer.Option(None, "--host", help="Filter by hostname"),
    page_size: int = typer.Option(20, "--limit", "-l"),
    page: int = typer.Option(1, "--page", "-p"),
    fields: str | None = typer.Option(None, "--fields", "-f", help="Comma-separated fields"),
    json_output: bool = typer.Option(False, "--json", "-j"),
):
    """List job events (task results) for a job."""
    client = _client()
    params: dict[str, Any] = {"page_size": page_size, "page": page}
    if failed_only:
        params["failed"] = "true"
    if host:
        params["host_name__icontains"] = host
    resp = client.get(f"jobs/{job_id}/job_events", params=params)
    resp = _apply_fields_filter(resp, fields)
    _output(resp, as_json=json_output, line_fmt=_format_event_line)


def _format_poll_line(job_id: int, job_data: dict, start_time: float) -> str:
    """Build a rich status line for --wait polling."""
    status = job_data.get("status", "unknown")
    elapsed_wall = time.monotonic() - start_time
    parts = [f"Job {job_id}: {status}"]

    if status in ("pending", "waiting", "new"):
        reason = job_data.get("job_explanation", "")
        if reason:
            parts.append(f"({reason})")
        else:
            parts.append("(waiting for capacity)")
    elif status == "running":
        parts.append(f"elapsed={elapsed_wall:.0f}s")
        exec_node = job_data.get("execution_node", "")
        if exec_node:
            parts.append(f"node={exec_node}")
        pct = job_data.get("percent_complete")
        if pct is not None:
            parts.append(f"{pct}%")

    return "  ".join(parts)


def _format_completion_summary(job_data: dict) -> str:
    """Build a one-line summary when a waited job finishes."""
    status = job_data.get("status", "unknown")
    elapsed = job_data.get("elapsed")
    parts = [f"FINISHED: {status}"]
    if elapsed is not None:
        parts.append(f"in {elapsed:.1f}s")
    sf = job_data.get("summary_fields", {})
    host_status = sf.get("job_host_summaries", {})
    changed = host_status.get("changed", 0) if isinstance(host_status, dict) else 0
    failed = host_status.get("failures", 0) if isinstance(host_status, dict) else 0
    if changed or failed:
        parts.append(f"changed={changed}")
        parts.append(f"failed={failed}")
    return "  ".join(parts)


def _warn_zero_hosts(job_id: int, job_data: dict) -> None:
    """Emit a stderr warning if a successful job ran 0 plays or touched 0 hosts."""
    play_count = job_data.get("playbook_counts", {}).get(
        "play_count", job_data.get("play_count")
    )
    host_counts = job_data.get("host_status_counts")
    if host_counts is None:
        return  # field missing (workflow jobs, etc.) — can't determine host count
    total_hosts = sum(host_counts.values()) if isinstance(host_counts, dict) else None

    if play_count == 0 or total_hosts == 0:
        typer.echo(
            f"\u26a0 Job {job_id} succeeded but ran 0 plays/0 hosts \u2014 "
            "the inventory may be empty or --limit matched nothing.",
            err=True,
        )


def _warn_empty_inventory(
    client: AwxRestClient, ttype: str, template_id: int
) -> None:
    """Best-effort check: warn if the template's inventory has 0 hosts."""
    try:
        template_data = client.get(f"{ttype}/{template_id}")
        inv_id = template_data.get("inventory")
        if inv_id:
            inv_data = client.get(f"inventories/{inv_id}")
            total_hosts = inv_data.get("total_hosts", inv_data.get("host_count"))
            if total_hosts == 0:
                typer.echo(
                    f"\u26a0 Template inventory (id={inv_id}) has 0 hosts",
                    err=True,
                )
    except Exception:
        pass


@app.command()
def launch(
    template: str = typer.Argument(help="Job template ID or name"),
    workflow: bool = typer.Option(
        False, "--workflow", "-w", help="Launch a workflow template instead of job template"
    ),
    extra_vars: str | None = typer.Option(
        None, "--extra-vars", "-e", help="Extra variables as JSON string"
    ),
    limit: str | None = typer.Option(None, "--limit", help="Host limit pattern"),
    inventory: str | None = typer.Option(
        None, "--inventory", help="Override inventory (ID or name)"
    ),
    tags: str | None = typer.Option(None, "--tags", help="Comma-separated job tags"),
    skip_tags: str | None = typer.Option(None, "--skip-tags", help="Comma-separated skip tags"),
    scm_branch: str | None = typer.Option(
        None, "--scm-branch", help="SCM branch override (template must allow branch override)"
    ),
    check_mode: bool = typer.Option(False, "--check", help="Run in check mode (template must have ask_job_type_on_launch enabled)"),
    diff_mode: bool = typer.Option(False, "--diff", help="Show diff of changes (template must have ask_diff_mode_on_launch enabled)"),
    forks: int | None = typer.Option(
        None, "--forks", help="Override forks count (not safe for concurrent launches of same template)"
    ),
    wait: bool = typer.Option(False, "--wait", help="Wait for job to complete"),
    timeout: int = typer.Option(300, "--timeout", help="Timeout in seconds when --wait is used"),
    poll_interval: float = typer.Option(5.0, "--poll-interval", help="Poll interval in seconds"),
    json_output: bool = typer.Option(False, "--json", "-j"),
):
    """Launch a job template (or workflow with --workflow)."""
    client = _client()
    ttype = "workflow_job_templates" if workflow else "job_templates"
    template_id = _resolve_id(client, ttype, template)
    payload: dict[str, Any] = {}
    if extra_vars:
        try:
            payload["extra_vars"] = json.loads(extra_vars)
        except json.JSONDecodeError:
            typer.echo("Error: --extra-vars must be valid JSON", err=True)
            raise typer.Exit(1) from None
    if limit:
        payload["limit"] = limit
    if inventory:
        payload["inventory"] = _resolve_id(client, "inventories", inventory)
    if not workflow:
        if tags:
            payload["job_tags"] = tags
        if skip_tags:
            payload["skip_tags"] = skip_tags
        if check_mode:
            payload["job_type"] = "check"
        if diff_mode:
            payload["diff_mode"] = True
    if scm_branch:
        payload["scm_branch"] = scm_branch

    try:
        if forks is not None and not workflow:
            original_forks = client.get(f"job_templates/{template_id}").get("forks")
            client.patch(f"job_templates/{template_id}", json={"forks": forks})
            try:
                resp = client.post(f"{ttype}/{template_id}/launch", json=payload)
            finally:
                client.patch(
                    f"job_templates/{template_id}", json={"forks": original_forks or 0}
                )
        else:
            resp = client.post(f"{ttype}/{template_id}/launch", json=payload)
    except Exception as exc:
        if json_output:
            _output(
                {"error": {"type": type(exc).__name__, "message": str(exc)}, "template_id": template_id},
                as_json=True,
            )
            raise typer.Exit(1) from None
        raise
    job_id = resp.get("id") if isinstance(resp, dict) else None

    if not wait or not job_id:
        if not wait and job_id:
            _warn_empty_inventory(client, ttype, template_id)
        _output(resp, as_json=json_output)
        return

    job_type = "workflow_jobs" if workflow else "jobs"
    typer.echo(f"Launched job {job_id}, waiting (timeout={timeout}s)...", err=True)
    start_time = time.monotonic()
    deadline = start_time + timeout
    last_status = "unknown"
    terminal_states = {"successful", "failed", "error", "canceled"}

    consecutive_errors = 0

    while time.monotonic() < deadline:
        time.sleep(poll_interval)
        try:
            job_data = client.get(f"{job_type}/{job_id}")
            consecutive_errors = 0
        except Exception as exc:
            consecutive_errors += 1
            typer.echo(
                f"  Job {job_id}: poll error ({consecutive_errors}/{MAX_CONSECUTIVE_POLL_ERRORS}): {exc}",
                err=True,
            )
            if consecutive_errors >= MAX_CONSECUTIVE_POLL_ERRORS:
                typer.echo(
                    f"  Giving up after {MAX_CONSECUTIVE_POLL_ERRORS} consecutive poll errors",
                    err=True,
                )
                if json_output:
                    _output(
                        {
                            "error": {
                                "type": type(exc).__name__,
                                "message": str(exc),
                            },
                            "job_id": job_id,
                            "last_status": last_status,
                            "consecutive_errors": consecutive_errors,
                        },
                        as_json=True,
                    )
                    raise typer.Exit(1) from None
                raise
            continue
        if not isinstance(job_data, dict):
            consecutive_errors += 1
            typer.echo(f"  Job {job_id}: unexpected response type, retrying...", err=True)
            continue
        last_status = job_data.get("status", "unknown")
        typer.echo(_format_poll_line(job_id, job_data, start_time), err=True)
        if last_status in terminal_states:
            typer.echo(_format_completion_summary(job_data), err=True)
            if last_status == "successful":
                _warn_zero_hosts(job_id, job_data)
            _output(job_data, as_json=json_output)
            if last_status != "successful":
                raise typer.Exit(1)
            return

    typer.echo(f"Timed out after {timeout}s (job_id={job_id}). Last status: {last_status}", err=True)
    if json_output:
        _output(
            {
                "error": {"type": "Timeout", "message": f"Timed out after {timeout}s"},
                "job_id": job_id,
                "last_status": last_status,
            },
            as_json=True,
        )
    raise typer.Exit(2)


@app.command()
def cancel(
    job_id: int = typer.Argument(help="Job ID to cancel"),
    json_output: bool = typer.Option(False, "--json", "-j"),
):
    """Cancel a running job."""
    client = _client()
    resp = client.post(f"jobs/{job_id}/cancel")
    _output(resp, as_json=json_output)
    typer.echo(f"Job {job_id} cancel requested.", err=True)


@app.command()
def relaunch(
    job_id: int = typer.Argument(help="Job ID to relaunch"),
    hosts: str | None = typer.Option(None, "--hosts", help="Host limit for relaunch"),
    on_failed: bool = typer.Option(False, "--on-failed", help="Relaunch only on failed hosts"),
    json_output: bool = typer.Option(False, "--json", "-j"),
):
    """Relaunch a previous job.

    Use --on-failed to automatically relaunch only on hosts that failed.
    """
    client = _client()
    payload: dict[str, Any] = {}
    if on_failed:
        payload["hosts"] = "failed"
    elif hosts:
        payload["hosts"] = hosts
    resp = client.post(f"jobs/{job_id}/relaunch", json=payload)
    _output(resp, as_json=json_output)


@app.command()
def inventories(
    search: str | None = typer.Option(None, "--search", "-s", help="Filter by name"),
    page_size: int = typer.Option(20, "--limit", "-l"),
    fields: str | None = typer.Option(None, "--fields", "-f", help="Comma-separated fields"),
    json_output: bool = typer.Option(False, "--json", "-j"),
):
    """List inventories."""
    client = _client()
    params: dict[str, Any] = {"page_size": page_size}
    if search:
        params["name__icontains"] = search
    resp = client.get("inventories", params=params)
    resp = _apply_fields_filter(resp, fields)
    _output(resp, as_json=json_output)


@app.command()
def projects(
    search: str | None = typer.Option(None, "--search", "-s", help="Filter by name"),
    page_size: int = typer.Option(20, "--limit", "-l"),
    fields: str | None = typer.Option(None, "--fields", "-f", help="Comma-separated fields"),
    json_output: bool = typer.Option(False, "--json", "-j"),
):
    """List projects."""
    client = _client()
    params: dict[str, Any] = {"page_size": page_size}
    if search:
        params["name__icontains"] = search
    resp = client.get("projects", params=params)
    resp = _apply_fields_filter(resp, fields)
    _output(resp, as_json=json_output)


@app.command(name="project-update")
def project_update(
    project_id: int = typer.Argument(help="Project ID"),
    branch: str | None = typer.Option(None, "--branch", "-b", help="Set the project's SCM branch"),
    sync: bool = typer.Option(False, "--sync", help="Trigger an SCM sync after update"),
    wait: bool = typer.Option(False, "--wait", help="Wait for sync to complete"),
    timeout: int = typer.Option(120, "--timeout", help="Timeout in seconds when --wait is used"),
    poll_interval: float = typer.Option(3.0, "--poll-interval", help="Poll interval in seconds"),
    json_output: bool = typer.Option(False, "--json", "-j"),
):
    """Update a project's SCM branch and/or trigger a sync.

    Examples:
        awx-cli project-update 8 --branch feat/my-branch --sync --wait
        awx-cli project-update 8 --sync --wait
        awx-cli project-update 8 --branch main
    """
    if not branch and not sync:
        typer.echo(
            "Error: specify --branch and/or --sync.\n"
            "  awx-cli project-update 8 --branch feat/branch --sync --wait\n"
            "  awx-cli project-update 8 --sync\n"
            "  awx-cli project-update 8 --branch main",
            err=True,
        )
        raise typer.Exit(1)

    client = _client()

    if branch:
        resp = client.patch(f"projects/{project_id}", json={"scm_branch": branch})
        typer.echo(f"Project {project_id}: scm_branch set to '{branch}'", err=True)
        if not sync:
            _output(resp, as_json=json_output)
            return

    if sync:
        resp = client.post(f"projects/{project_id}/update/")
        update_id = resp.get("id") if isinstance(resp, dict) else None

        if not wait or not update_id:
            _output(resp, as_json=json_output)
            return

        typer.echo(f"Project update {update_id} started, waiting (timeout={timeout}s)...", err=True)
        start_time = time.monotonic()
        deadline = start_time + timeout
        terminal_states = {"successful", "failed", "error", "canceled"}
        last_status = "unknown"
        consecutive_errors = 0

        while time.monotonic() < deadline:
            time.sleep(poll_interval)
            try:
                job_data = client.get(f"project_updates/{update_id}")
                consecutive_errors = 0
            except Exception as exc:
                consecutive_errors += 1
                typer.echo(
                    f"  Update {update_id}: poll error ({consecutive_errors}/{MAX_CONSECUTIVE_POLL_ERRORS}): {exc}",
                    err=True,
                )
                if consecutive_errors >= MAX_CONSECUTIVE_POLL_ERRORS:
                    typer.echo(
                        f"  Giving up after {MAX_CONSECUTIVE_POLL_ERRORS} consecutive poll errors",
                        err=True,
                    )
                    if json_output:
                        _output(
                            {
                                "error": {
                                    "type": type(exc).__name__,
                                    "message": str(exc),
                                },
                                "job_id": update_id,
                                "last_status": last_status,
                                "consecutive_errors": consecutive_errors,
                            },
                            as_json=True,
                        )
                        raise typer.Exit(1) from None
                    raise
                continue
            if not isinstance(job_data, dict):
                consecutive_errors += 1
                typer.echo(f"  Update {update_id}: unexpected response type, retrying...", err=True)
                continue
            last_status = job_data.get("status", "unknown")
            elapsed_wall = time.monotonic() - start_time
            typer.echo(f"  Update {update_id}: {last_status} ({elapsed_wall:.0f}s)", err=True)
            if last_status in terminal_states:
                typer.echo(f"FINISHED: {last_status}", err=True)
                _output(job_data, as_json=json_output)
                if last_status != "successful":
                    raise typer.Exit(1)
                return

        typer.echo(
            f"Timed out after {timeout}s (update_id={update_id}). Last status: {last_status}",
            err=True,
        )
        if json_output:
            _output(
                {
                    "error": {"type": "Timeout", "message": f"Timed out after {timeout}s"},
                    "job_id": update_id,
                    "last_status": last_status,
                },
                as_json=True,
            )
        raise typer.Exit(2)


@app.command()
def credentials(
    search: str | None = typer.Option(None, "--search", "-s", help="Filter by name"),
    page_size: int = typer.Option(20, "--limit", "-l"),
    fields: str | None = typer.Option(None, "--fields", "-f", help="Comma-separated fields"),
    json_output: bool = typer.Option(False, "--json", "-j"),
):
    """List credentials."""
    client = _client()
    params: dict[str, Any] = {"page_size": page_size}
    if search:
        params["name__icontains"] = search
    resp = client.get("credentials", params=params)
    resp = _apply_fields_filter(resp, fields)
    _output(resp, as_json=json_output)


@app.command()
def hosts(
    inventory_id: int | None = typer.Argument(None, help="Inventory ID"),
    inventory_opt: int | None = typer.Option(
        None, "--inventory", "-i", help="Inventory ID (alternative to positional arg)"
    ),
    search: str | None = typer.Option(None, "--search", "-s", help="Filter by hostname"),
    limit: int = typer.Option(0, "--limit", "-l", help="Max results (0 = all)"),
    fields: str | None = typer.Option(None, "--fields", "-f", help="Comma-separated fields"),
    json_output: bool = typer.Option(False, "--json", "-j"),
):
    """List hosts in an inventory (auto-paginates).

    INVENTORY_ID can be passed as a positional argument or via --inventory / -i.

    Examples:
        awx-cli hosts 256
        awx-cli hosts --inventory 256
        awx-cli hosts -i 256
        awx-cli hosts 256 --limit 10
    """
    inv = inventory_id or inventory_opt
    if inv is None:
        typer.echo(
            "Error: missing INVENTORY_ID. Pass it as a positional arg or use --inventory / -i.\n"
            "  awx-cli hosts 256\n"
            "  awx-cli hosts --inventory 256",
            err=True,
        )
        raise typer.Exit(1)
    client = _client()
    page_size = min(limit, 200) if limit else 200
    params: dict[str, Any] = {"page_size": page_size}
    if search:
        params["name__icontains"] = search
    resp = _paginate_all(client, f"inventories/{inv}/hosts", params, max_results=limit)
    resp = _apply_fields_filter(resp, fields)
    _output(resp, as_json=json_output)


def _read_reference_hosts(
    reference: str | None,
    reference_stdin: bool,
) -> set[str]:
    """Read reference hosts from --reference (file or comma-separated) or --stdin."""
    if reference_stdin:
        raw = sys.stdin.read()
    elif reference is not None:
        path = Path(reference)
        if path.is_file():
            raw = path.read_text()
        else:
            raw = reference
    else:
        typer.echo("Error: provide --reference or --stdin for reference host list", err=True)
        raise typer.Exit(1)
    hosts: set[str] = set()
    for token in raw.replace(",", "\n").splitlines():
        token = token.strip()
        if token:
            hosts.add(token.strip().lower())
    return hosts


@app.command(name="inventory-audit")
def inventory_audit(
    inventory: str = typer.Argument(help="Inventory ID or name"),
    reference: str | None = typer.Option(
        None, "--reference", "-r", help="Reference host list (file path, or comma-separated hostnames)"
    ),
    reference_stdin: bool = typer.Option(False, "--stdin", help="Read reference hosts from stdin"),
    json_output: bool = typer.Option(False, "--json", "-j"),
) -> None:
    """Audit an AWX inventory against a reference host list.

    Shows hosts that are:
    - In AWX but not in reference (stale)
    - In reference but not in AWX (missing)
    - In both (matched)

    Examples:
        awx-cli inventory-audit 256 --reference host1,host2,host3
        echo "host1\\nhost2" | awx-cli inventory-audit 256 --stdin
        awx-cli inventory-audit "Production" -r hosts.txt
    """
    client = _client()
    inv_id = _resolve_id(client, "inventories", inventory)
    resp = _paginate_all(client, f"inventories/{inv_id}/hosts", {"page_size": 200})
    awx_hosts = {h.get("name", "").lower() for h in resp.get("results", [])}

    ref_hosts = _read_reference_hosts(reference, reference_stdin)

    awx_only = sorted(awx_hosts - ref_hosts)
    ref_only = sorted(ref_hosts - awx_hosts)
    matched = sorted(awx_hosts & ref_hosts)

    result = {
        "inventory_id": inv_id,
        "awx_host_count": len(awx_hosts),
        "reference_host_count": len(ref_hosts),
        "matched_count": len(matched),
        "stale_count": len(awx_only),
        "missing_count": len(ref_only),
        "stale": awx_only,
        "missing": ref_only,
        "matched": matched,
    }

    if json_output:
        _output(result, as_json=True)
        return

    typer.echo(f"Inventory {inv_id}: {len(awx_hosts)} AWX hosts vs {len(ref_hosts)} reference hosts")
    typer.echo(f"  Matched: {len(matched)}  Stale (AWX-only): {len(awx_only)}  Missing (ref-only): {len(ref_only)}")
    typer.echo()
    if awx_only:
        typer.echo("STALE (in AWX, not in reference):")
        for h in awx_only:
            typer.echo(f"  - {h}")
        typer.echo()
    if ref_only:
        typer.echo("MISSING (in reference, not in AWX):")
        for h in ref_only:
            typer.echo(f"  + {h}")
        typer.echo()
    if not awx_only and not ref_only:
        typer.echo("OK: AWX inventory matches reference exactly.")


@app.command()
def ping(
    json_output: bool = typer.Option(False, "--json", "-j"),
):
    """Check AWX connectivity."""
    client = _client()
    resp = client.get("ping")
    _output(resp, as_json=json_output)


@app.command()
def me(
    json_output: bool = typer.Option(False, "--json", "-j"),
):
    """Show current user info."""
    client = _client()
    resp = client.get("me")
    if isinstance(resp, dict) and "results" in resp and resp["results"]:
        resp = resp["results"][0]
    _output(resp, as_json=json_output)


@app.command(name="get")
def get_resource(
    resource_type: str = typer.Argument(
        help="Resource type (e.g. job_templates, jobs, inventories)"
    ),
    resource_id: int = typer.Argument(help="Resource ID"),
    property_path: str | None = typer.Option(
        None, "--property", help="Sub-property path (e.g. survey_spec, variable_data)"
    ),
    fields: str | None = typer.Option(None, "--fields", "-f"),
    json_output: bool = typer.Option(False, "--json", "-j"),
):
    """Get a single resource by type and ID."""
    client = _client()
    endpoint = f"{resource_type}/{resource_id}"
    if property_path:
        endpoint = f"{endpoint}/{property_path}"
    resp = client.get(endpoint)
    resp = _apply_fields_filter(resp, fields)
    _output(resp, as_json=json_output)


@app.command(name="list")
def list_resources(
    resource_type: str = typer.Argument(
        help="Resource type (e.g. job_templates, jobs, inventories, credentials)"
    ),
    filters: str | None = typer.Option(
        None,
        "--filter",
        help=(
            "Filters as key=value pairs, comma-separated. "
            "Examples: status=failed, name__icontains=deploy, created__gt=2026-04-01. "
            "Uses Django-style lookups: __icontains, __gt, __lt, __gte, __lte, __iexact, __in"
        ),
    ),
    fields: str | None = typer.Option(None, "--fields", "-f"),
    page_size: int = typer.Option(20, "--limit", "-l"),
    page: int = typer.Option(1, "--page", "-p"),
    order_by: str | None = typer.Option(None, "--order", "-o"),
    parent_type: str | None = typer.Option(
        None, "--parent-type", help="Parent resource type for nested resources"
    ),
    parent_id: int | None = typer.Option(None, "--parent-id", help="Parent resource ID"),
    json_output: bool = typer.Option(False, "--json", "-j"),
):
    """List any AWX resource type with optional filters (generic fallback)."""
    client = _client()
    params: dict[str, Any] = {"page_size": page_size, "page": page}
    if order_by:
        params["order_by"] = order_by
    if filters:
        for pair in filters.split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                params[k.strip()] = v.strip()

    parts: list[str] = []
    if parent_type and parent_id:
        parts.append(f"{parent_type}/{parent_id}")
    parts.append(resource_type)
    endpoint = "/".join(parts)

    resp = client.get(endpoint, params=params)
    resp = _apply_fields_filter(resp, fields)
    _output(resp, as_json=json_output)


@app.command(name="inventory-sources")
def inventory_sources(
    inventory_id: int = typer.Argument(help="Inventory ID"),
    search: str | None = typer.Option(None, "--search", "-s", help="Filter by name"),
    page_size: int = typer.Option(20, "--limit", "-l"),
    fields: str | None = typer.Option(None, "--fields", "-f", help="Comma-separated fields"),
    json_output: bool = typer.Option(False, "--json", "-j"),
):
    """List inventory sources for an inventory.

    Examples:
        awx-cli inventory-sources 42
        awx-cli inventory-sources 42 --search NetBox
    """
    client = _client()
    params: dict[str, Any] = {"page_size": page_size}
    if search:
        params["name__icontains"] = search
    resp = client.get(f"inventories/{inventory_id}/inventory_sources", params=params)
    resp = _apply_fields_filter(resp, fields)
    _output(resp, as_json=json_output)


@app.command(name="inventory-sync")
def inventory_sync(
    source_id: str = typer.Argument(help="Inventory source ID or name"),
    wait: bool = typer.Option(False, "--wait", help="Wait for sync to complete"),
    timeout: int = typer.Option(120, "--timeout", help="Timeout in seconds when --wait"),
    poll_interval: float = typer.Option(3.0, "--poll-interval", help="Poll interval in seconds"),
    json_output: bool = typer.Option(False, "--json", "-j"),
):
    """Trigger an inventory source sync.

    Examples:
        awx-cli inventory-sync 42
        awx-cli inventory-sync 42 --wait
        awx-cli inventory-sync "NetBox Dynamic" --wait --timeout 60
    """
    client = _client()
    resolved_id = _resolve_id(client, "inventory_sources", source_id)
    resp = client.post(f"inventory_sources/{resolved_id}/update/")
    update_id = resp.get("id") if isinstance(resp, dict) else None

    if not wait or not update_id:
        _output(resp, as_json=json_output)
        return

    typer.echo(
        f"Inventory sync {update_id} started (source={resolved_id}), waiting (timeout={timeout}s)...",
        err=True,
    )
    _poll_until_terminal(
        client,
        "inventory_updates",
        update_id,
        "Sync",
        timeout=timeout,
        poll_interval=poll_interval,
        json_output=json_output,
        error_context={"source_id": resolved_id},
    )


@app.command(name="check-access")
def check_access(
    host: str = typer.Argument(help="Hostname or IP to probe via SSH"),
    timeout: int = typer.Option(5, "--timeout", "-t", min=1, help="SSH connect timeout in seconds"),
    user: str = typer.Option("ansible", "--user", "-u", help="SSH user to test"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """SSH-probe a host to check whether AWX can reach it.

    Preflight check for newly deployed nodes that may not yet have the
    ansible user bootstrapped.  Does NOT require AWX credentials.

    Examples:
        awx-cli check-access gpu-node-001.cloud.together.ai
        awx-cli check-access 10.0.0.42 --user root --timeout 10 --json
    """
    try:
        result = subprocess.run(
            [
                "ssh",
                "-o", f"ConnectTimeout={timeout}",
                # accept-new: auto-trust host keys on first contact so newly deployed
                # nodes are usable immediately without manual known_hosts management.
                "-o", "StrictHostKeyChecking=accept-new",
                "-o", "BatchMode=yes",
                f"{user}@{host}",
                "id",
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 5,
        )
    except subprocess.TimeoutExpired:
        result = None

    reachable = result is not None and result.returncode == 0
    try:
        ipaddress.ip_address(host.strip("[]"))
        host_pattern = host
    except ValueError:
        host_pattern = f"{host.split('.')[0]}*"
    remediation = f'ansible-playbook ansible/prep-awx-access.yaml --limit "{host_pattern}"'

    if json_output:
        payload: dict[str, Any] = {
            "host": host,
            "user": user,
            "reachable": reachable,
        }
        if not reachable:
            payload["remediation"] = remediation
            payload["error"] = result.stderr.strip() if result else "timeout"
        _output(payload, as_json=True)
        raise typer.Exit(0 if reachable else 1)

    if reachable:
        typer.echo(f"OK: {user} user reachable on {host}")
    else:
        error_detail = result.stderr.strip() if result else "timeout"
        typer.echo(f"FAIL: {user} user unreachable on {host}")
        if error_detail:
            typer.echo(f"  Error: {error_detail}")
        typer.echo("")
        typer.echo("Remediation:")
        typer.echo(f"  {remediation}")
        raise typer.Exit(1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
