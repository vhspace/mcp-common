"""
dc-support-cli: Thin CLI wrapper for datacenter vendor support operations.

Provides the same capabilities as dc-support-mcp but via shell commands,
enabling AI agents to use vendor support portals with ~40-90% fewer tokens.
"""

from __future__ import annotations

import json
from typing import Any, NoReturn

import requests as http_requests
import typer
from mcp_common.dual_mode import build_cli_from_mcp
from mcp_common.logging import setup_logging

from .mcp_server import mcp
from .secrets import maybe_secret, portal_source, secret_configured, secret_source

# Build the CLI app from the FastMCP server. Every dc-support tool is registered
# ``mcp_only=True`` (see mcp_server.py), so ``build_cli_from_mcp`` synthesizes no
# commands here — it supplies the shared scaffolding (``no_args_is_help``,
# ``SuggestingTyperGroup`` typo suggestions, ``install_cli_exception_handler``)
# and, via ``package_name``, a free ``--version`` flag reporting the installed
# dc-support-mcp version. The hand-written ``@app.command()`` definitions below
# remain the source of truth for the CLI surface: bespoke exit codes, the
# auth-aware failure surface (issue #87), and CLI-only flags the synthesized path
# can't reproduce (these tools return ``{"error": ...}`` dicts rather than raising).
app = build_cli_from_mcp(
    mcp,
    project_repo="togethercomputer/mcp-common",
    name="dc-support-cli",
    help="Manage datacenter vendor support tickets (ORI, Hypertec, IREN). Use --help on any subcommand.",
    package_name="dc-support-mcp",
)

VENDORS = ["ori", "hypertec", "iren"]

_HANDLER_CLASSES_CACHE: dict[str, type] | None = None


def _handler_classes() -> dict[str, type]:
    """Vendor key → handler class registry (lazily imported, then cached).

    Single source of truth for both ``_get_handler`` (which constructs via
    the ``VendorRegistry`` and triggers browser auth) and
    ``_build_inspection_handler`` (which constructs directly for passive
    ``auth-status`` checks).
    """
    global _HANDLER_CLASSES_CACHE
    if _HANDLER_CLASSES_CACHE is None:
        from .vendors import HypertecVendorHandler, IrenVendorHandler, OriVendorHandler

        _HANDLER_CLASSES_CACHE = {
            "ori": OriVendorHandler,
            "iren": IrenVendorHandler,
            "hypertec": HypertecVendorHandler,
        }
    return _HANDLER_CLASSES_CACHE


def _get_handler(vendor: str) -> Any:
    """Lazy-import and return a vendor handler via the registry."""
    from .validation import ValidationError
    from .vendors import VendorRegistry

    registry = VendorRegistry(verbose=False)
    for key, cls in _handler_classes().items():
        registry.register(key, cls)

    try:
        return registry.get_handler(vendor)
    except (ValidationError, Exception) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from e


def _output(data: object, as_json: bool = False) -> None:
    """Print output — compact text by default, JSON with --json."""
    if as_json:
        typer.echo(json.dumps(data, indent=2, default=str))
        return

    if isinstance(data, dict):
        if "error" in data:
            typer.echo(f"Error: {data['error']}", err=True)
            raise typer.Exit(1)
        for k, v in data.items():
            if isinstance(v, dict):
                typer.echo(f"  {k}:")
                for dk, dv in v.items():
                    typer.echo(f"    {dk}: {dv}")
            elif isinstance(v, list) and len(v) > 3:
                typer.echo(f"  {k}: [{len(v)} items]")
            else:
                typer.echo(f"  {k}: {v}")
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                _format_ticket_line(item)
            else:
                typer.echo(item)
    else:
        typer.echo(data)


def _format_ticket_line(ticket: dict[str, Any]) -> None:
    """One-line compact summary of a ticket."""
    tid = ticket.get("id", "?")
    summary = ticket.get("summary", "")
    status = ticket.get("status", "?")
    assignee = ticket.get("assignee", "")
    if len(summary) > 70:
        summary = summary[:67] + "..."
    parts = [f"[{tid}]", f"status={status}"]
    if assignee and assignee != "Unknown" and assignee != "Unassigned":
        parts.append(f"assignee={assignee}")
    parts.append(summary)
    typer.echo("  ".join(parts))


_AUTH_ERROR_TOKENS: tuple[str, ...] = ("auth", "cooldown", "login")


def _is_auth_error(message: object | None) -> bool:
    """True if *message* looks like an auth/cooldown/login error.

    Case-insensitive substring match — used to decide between the
    auth-flavored exit-2 path and the generic exit-1 path in
    ``_exit_with_handler_failure`` (see issue #87).
    """
    if not isinstance(message, str) or not message:
        return False
    lower = message.lower()
    return any(token in lower for token in _AUTH_ERROR_TOKENS)


def _exit_with_handler_failure(
    handler: Any,
    vendor: str,
    fallback_message: str,
    json_output: bool,
    *,
    detail_when_missing: str | None = None,
) -> NoReturn:
    """Print an error using ``handler.last_error`` and exit.

    Behaviour (issue #87):
      - auth-flavored ``last_error`` → exit 2 with "Auth failed for <vendor>: …"
      - other ``last_error`` → exit 1 with "<fallback>: <last_error>"
      - no ``last_error`` → exit 1 with bare ``fallback_message``

    ``detail_when_missing`` is used to populate the JSON ``detail`` key
    when ``last_error`` is unset — kept for callers (like
    ``create-service-request``) that previously emitted ``"Unknown error"``.
    """
    raw_last_error = getattr(handler, "last_error", None)
    last_err: str | None = raw_last_error if isinstance(raw_last_error, str) else None

    if _is_auth_error(last_err):
        assert last_err is not None
        msg = f"Auth failed for {vendor}: {last_err}"
        if json_output:
            _output({"error": msg, "vendor": vendor}, as_json=True)
        else:
            typer.echo(msg, err=True)
        raise typer.Exit(2)

    detail = last_err or detail_when_missing
    if detail:
        text_msg = f"{fallback_message}: {detail}"
    else:
        text_msg = fallback_message

    if json_output:
        out: dict[str, Any] = {"error": fallback_message}
        if detail:
            out["detail"] = detail
        _output(out, as_json=True)
    else:
        typer.echo(text_msg, err=True)
    raise typer.Exit(1)


# ── Ticket Commands ─────────────────────────────────────────────────────


@app.command()
def tickets(
    vendor: str = typer.Option("ori", "--vendor", "-v", help="Vendor: ori, hypertec, iren"),
    status: str = typer.Option("open", "--status", "-s", help="open, closed, or all"),
    limit: int = typer.Option(20, "--limit", "-l", help="Max tickets to return"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """List support tickets from a vendor portal.

    Results are capped at --limit. The listing is not guaranteed complete:
    check whether more exist before assuming you've seen everything.
      - JSON: inspect "has_more" (true = raise --limit for the rest) and
        "total" (the real backend total when known, else null).
      - Text: a "(more available — raise --limit)" hint is appended to the
        header when the result is truncated.
    Atlassian vendors (ori, hypertec) report a real total; IREN does not.

    Examples:
        dc-support-cli tickets --vendor ori
        dc-support-cli tickets --vendor hypertec --status all --limit 100
        dc-support-cli tickets --vendor iren --status closed --json
    """
    handler = _get_handler(vendor)
    result = handler.list_tickets(status=status, limit=limit)
    if not result:
        if getattr(handler, "last_error", None):
            _exit_with_handler_failure(
                handler,
                vendor,
                "Failed to list tickets",
                json_output,
            )
        if json_output:
            _output({"tickets": [], "count": 0}, as_json=True)
        else:
            typer.echo("No tickets found.")
        return
    signal = handler.list_more_signal()
    has_more = bool(signal.get("has_more", False))
    total = signal.get("total")
    if json_output:
        _output(
            {
                "tickets": result,
                "count": len(result),
                "has_more": has_more,
                "total": total,
            },
            as_json=True,
        )
    else:
        header = f"# {len(result)} {status} ticket(s) — {vendor}"
        if has_more:
            header += " (more available — raise --limit)"
        typer.echo(header)
        _output(result)


@app.command()
def get_ticket(
    ticket_id: str = typer.Argument(help="Ticket ID (e.g. SUPP-1556, HTCSR-3391, or numeric)"),
    vendor: str = typer.Option("ori", "--vendor", "-v", help="Vendor: ori, hypertec, iren"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Fetch a single ticket with full details and comments."""
    handler = _get_handler(vendor)
    ticket = handler.get_ticket(ticket_id)
    if not ticket:
        _exit_with_handler_failure(
            handler,
            vendor,
            f"Ticket {ticket_id} not found",
            json_output,
        )
    _output(ticket, as_json=json_output)


@app.command()
def create_service_request(
    summary: str = typer.Option(..., "--summary", help="Short title using provider node name"),
    description: str = typer.Option(
        ..., "--description", help="Issue description (no internal refs)"
    ),
    vendor: str = typer.Option("hypertec", "--vendor", "-v", help="Vendor: hypertec, ori, or iren"),
    support_level: str = typer.Option(
        "Critical", "--support-level", help="Critical/Normal/Question (Hypertec)"
    ),
    reboot_allowed: str = typer.Option(
        "YES", "--reboot-allowed", help="YES/NO/Does not apply (Hypertec)"
    ),
    priority: str = typer.Option("P3", "--priority", "-p", help="P1-P5 (IREN only, default P3)"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Create a service request on a vendor portal.

    Supported vendors:
      - hypertec / ori: Atlassian Service Desk REST API.
      - iren: Freshdesk REST API (priority maps from P1-P5;
        support_level and reboot_allowed are ignored).

    Content is auto-sanitized to strip internal references before submission.
    Use provider node names (from NetBox Provider_Machine_ID), NOT internal hostnames.
    """
    from .vendors.atlassian_base import AtlassianServiceDeskHandler
    from .vendors.iren import IrenVendorHandler

    handler = _get_handler(vendor)

    if isinstance(handler, IrenVendorHandler):
        result = handler.create_ticket(
            summary=summary,
            description=description,
            priority=priority,
        )
        if not result:
            _exit_with_handler_failure(
                handler,
                vendor,
                "Failed to create IREN ticket",
                json_output,
                detail_when_missing="Unknown error",
            )

        _output(
            {
                "ok": True,
                "ticket_id": result.get("id", ""),
                "url": result.get("url", ""),
                "vendor": vendor,
            },
            as_json=json_output,
        )
        return

    if not isinstance(handler, AtlassianServiceDeskHandler):
        typer.echo(f"Error: Vendor '{vendor}' does not support service desk requests", err=True)
        raise typer.Exit(1)

    extra_fields: dict[str, Any] = {}
    if vendor == "hypertec":
        extra_fields["customfield_10078"] = {"value": support_level}
        extra_fields["customfield_10133"] = [{"value": reboot_allowed}]

    request_type_id = getattr(handler, "INFRA_REQUEST_TYPE_ID", "7")
    result = handler.create_service_desk_request(
        summary=summary,
        description=description,
        request_type_id=str(request_type_id),
        extra_fields=extra_fields,
    )
    if not result:
        _exit_with_handler_failure(
            handler,
            vendor,
            "Failed to create service request",
            json_output,
            detail_when_missing="Unknown error",
        )

    ticket_key = result.get("issueKey", "")
    portal_url = f"{handler.BASE_URL}/servicedesk/customer/portal/{handler.PORTAL_ID}/{ticket_key}"
    _output(
        {"ok": True, "ticket_id": ticket_key, "url": portal_url, "vendor": vendor},
        as_json=json_output,
    )


@app.command()
def comment(
    ticket_id: str = typer.Argument(help="Ticket ID (e.g. SUPP-1556, HTCSR-3391)"),
    text: str = typer.Option(..., "--text", "-t", help="Comment text to post"),
    vendor: str = typer.Option("ori", "--vendor", "-v", help="Vendor: ori, hypertec"),
    public: bool = typer.Option(True, "--public/--internal", help="Public or internal note"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Add a comment to a vendor support ticket."""
    handler = _get_handler(vendor)
    result = handler.add_comment(ticket_id, text, public=public)
    if not result:
        _exit_with_handler_failure(
            handler,
            vendor,
            f"Failed to add comment to {ticket_id}",
            json_output,
        )
    _output(
        {"ok": True, "ticket_id": ticket_id, "comment_preview": text[:100]}, as_json=json_output
    )


@app.command()
def update_ticket(
    ticket_id: str = typer.Argument(help="Ticket ID (e.g. SUPP-1556, HTCSR-3391, or numeric)"),
    status: str = typer.Option(..., "--status", "-s", help="Target status: resolved or closed"),
    vendor: str = typer.Option("ori", "--vendor", "-v", help="Vendor: ori, hypertec, iren"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Update the status of a vendor support ticket (resolve or close)."""
    valid_statuses = ("resolved", "closed")
    status_lower = status.lower()
    if status_lower not in valid_statuses:
        typer.echo(f"Error: Unknown status '{status}'. Use: {', '.join(valid_statuses)}", err=True)
        raise typer.Exit(1)

    handler = _get_handler(vendor)

    if not hasattr(handler, "update_ticket_status"):
        typer.echo(f"Error: Vendor '{vendor}' does not support status updates", err=True)
        raise typer.Exit(1)

    if vendor == "iren":
        from .vendors.iren import FRESHDESK_STATUS_MAP

        status_code = FRESHDESK_STATUS_MAP.get(status_lower)
        if status_code is None:
            typer.echo(f"Error: Unknown IREN status '{status}'", err=True)
            raise typer.Exit(1)
        result = handler.update_ticket_status(ticket_id, status_code)
    else:
        result = handler.update_ticket_status(ticket_id, status_lower)

    if not result:
        _exit_with_handler_failure(
            handler,
            vendor,
            f"Failed to update status of {ticket_id} to {status}",
            json_output,
        )

    _output(dict(result), as_json=json_output)


# ── Triage Commands ─────────────────────────────────────────────────────


@app.command()
def triage(
    device_name: str = typer.Option(
        "", "--device", "-d", help="NetBox device name (e.g. us-south-3a-r07-06)"
    ),
    issue_summary: str = typer.Option("", "--summary", help="Structured issue description"),
    issue_types: str | None = typer.Option(
        None, "--issue-types", help="Comma-separated issue types (default: GPU issue)"
    ),
    gpu_outage_type: str = typer.Option(
        "GPU - Missing", "--gpu-outage-type", help="GPU outage sub-type (use --list-outage-types)"
    ),
    customer_impacting: bool = typer.Option(
        False, "--customer-impacting", help="Set priority to Urgent"
    ),
    created_by: str = typer.Option(
        "", "--created-by", help="Email or name of person/on-call who triggered creation"
    ),
    assignee: str = typer.Option(
        "",
        "--assignee",
        "-a",
        help="Email of the Linear ticket assignee (falls back to --created-by)",
    ),
    list_outage_types: bool = typer.Option(
        False, "--list-outage-types", help="Print valid GPU outage types and exit"
    ),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Create an RTB triage ticket (Linear + NetBox + Slack)."""
    from .constants import RTB_OUTAGE_TYPES, rtb_base_url

    if list_outage_types:
        if json_output:
            _output({"outage_types": list(RTB_OUTAGE_TYPES)}, as_json=True)
        else:
            typer.echo("Valid RTB GPU outage types:")
            for t in RTB_OUTAGE_TYPES:
                typer.echo(f"  {t}")
        return

    if not device_name:
        typer.echo("Error: --device is required (unless using --list-outage-types)", err=True)
        raise typer.Exit(1)
    if not issue_summary:
        typer.echo("Error: --summary is required (unless using --list-outage-types)", err=True)
        raise typer.Exit(1)

    from .validation import ValidationError, validate_gpu_outage_type

    try:
        gpu_outage_type = validate_gpu_outage_type(gpu_outage_type)
    except ValidationError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from e

    rtb_key = maybe_secret("RTB_API_KEY")
    if not rtb_key:
        typer.echo("Error: RTB_API_KEY not set", err=True)
        raise typer.Exit(1)

    rtb_base = rtb_base_url()

    types_list = [t.strip() for t in issue_types.split(",")] if issue_types else ["GPU issue"]

    try:
        device_resp = http_requests.get(
            f"{rtb_base}/api/v1/device/{device_name}",
            headers={"Authorization": f"Bearer {rtb_key}"},
            timeout=10,
        )
        if device_resp.status_code != 200:
            typer.echo(f"Error: Device {device_name} not found in RTB", err=True)
            raise typer.Exit(1)
        device_data = device_resp.json()
        device_id = device_data["id"]
    except http_requests.RequestException as e:
        typer.echo(f"Error: RTB device lookup failed: {e}", err=True)
        raise typer.Exit(1) from e

    from .formatting import build_rtb_triage_payload
    from .oncall import is_email, linear_assign_ticket

    assignee_email = ""
    if assignee and is_email(assignee):
        assignee_email = assignee
    if not assignee_email and created_by and is_email(created_by):
        assignee_email = created_by

    payload = build_rtb_triage_payload(
        device_id=device_id,
        issue_summary=issue_summary,
        issue_types=types_list,
        gpu_outage_type=gpu_outage_type,
        customer_impacting=customer_impacting,
        created_by=created_by,
        assignee_email=assignee_email,
    )

    try:
        resp = http_requests.post(
            f"{rtb_base}/api/v1/tickets/triage",
            headers={
                "Authorization": f"Bearer {rtb_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
    except http_requests.RequestException as e:
        typer.echo(f"Error: RTB request failed: {e}", err=True)
        raise typer.Exit(1) from e

    if resp.status_code != 201:
        typer.echo(f"Error: RTB returned {resp.status_code}: {resp.text[:300]}", err=True)
        raise typer.Exit(1)

    data = resp.json()
    ticket = data.get("ticket", {})
    netbox_ok = data.get("netbox_updated", False)

    if not netbox_ok:
        from .formatting import netbox_ensure_triage_status

        linear_id = ticket.get("id", "")
        netbox_ok = netbox_ensure_triage_status(device_id, linear_id)

    ticket_id = ticket.get("id", "")

    linear_assigned = False
    if assignee_email and ticket_id:
        linear_assigned = linear_assign_ticket(ticket_id, assignee_email)

    result = {
        "ok": True,
        "ticket_id": ticket_id,
        "title": ticket.get("title", ""),
        "url": ticket.get("url", ""),
        "assignee": assignee_email or None,
        "linear_assigned": linear_assigned,
        "netbox_updated": netbox_ok,
        "device_id": device_id,
    }
    _output(result, as_json=json_output)


@app.command()
def triage_list(
    status: str = typer.Option("open", "--status", "-s", help="Filter: open, closed, or all"),
    assignee: str = typer.Option("", "--assignee", "-a", help="Filter by assignee email"),
    team: str = typer.Option(
        "",
        "--team",
        "-t",
        help="Linear team key (e.g. SRE). Falls back to RTB_LINEAR_TEAM_KEY env var.",
    ),
    limit: int = typer.Option(20, "--limit", "-l", help="Max tickets to return (1-50)"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """List RTB triage tickets from Linear.

    Shows internal triage tickets created via the triage command. Requires
    LINEAR_API_KEY. Use --team to scope to a specific Linear team, or set
    RTB_LINEAR_TEAM_KEY in your environment.
    """
    from .oncall import linear_list_issues

    if not secret_configured("LINEAR_API_KEY"):
        typer.echo("Error: LINEAR_API_KEY not set", err=True)
        raise typer.Exit(1)

    if status not in ("open", "closed", "all"):
        typer.echo(f"Error: Unknown status '{status}'. Use: open, closed, all", err=True)
        raise typer.Exit(1)

    tickets = linear_list_issues(
        team_key=team or None,
        assignee_email=assignee or None,
        status=status,
        limit=limit,
    )

    if tickets is None:
        typer.echo("Error: Failed to query Linear. Check LINEAR_API_KEY.", err=True)
        raise typer.Exit(1)

    if not tickets:
        if json_output:
            _output({"tickets": [], "count": 0, "status": status}, as_json=True)
        else:
            typer.echo("No triage tickets found.")
        return

    if json_output:
        _output({"tickets": tickets, "count": len(tickets), "status": status}, as_json=True)
    else:
        typer.echo(f"# {len(tickets)} {status} triage ticket(s)")
        for t in tickets:
            tid = t.get("id", "?")
            title = t.get("title", "")
            tst = t.get("status", "?")
            tassignee = t.get("assignee", "")
            if len(title) > 60:
                title = title[:57] + "..."
            parts = [f"[{tid}]", f"status={tst}"]
            if tassignee:
                parts.append(f"assignee={tassignee}")
            parts.append(title)
            typer.echo("  ".join(parts))


# ── Linear: Attach URL ──────────────────────────────────────────────────


@app.command()
def linear_attach_url(
    issue_id: str = typer.Argument(help="Linear issue id or identifier (e.g. SRE-1574)"),
    url: str = typer.Option(..., "--url", "-u", help="External URL to attach (e.g. a GitHub PR)"),
    title: str = typer.Option(..., "--title", "-t", help="Title shown on the attachment"),
    subtitle: str = typer.Option(
        "", "--subtitle", "-s", help="Optional subtitle shown beneath the title"
    ),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Attach an external URL to a Linear issue.

    Calls the Linear GraphQL attachmentCreate mutation, reusing LINEAR_API_KEY.
    Use this to link a PR, dashboard, doc, or any external resource onto a
    Linear issue.
    """
    from .oncall import linear_attach_url as _linear_attach_url

    if not secret_configured("LINEAR_API_KEY"):
        typer.echo("Error: LINEAR_API_KEY not set", err=True)
        raise typer.Exit(1)

    result = _linear_attach_url(
        issue_id=issue_id,
        url=url,
        title=title,
        subtitle=subtitle or None,
    )
    _output(result, as_json=json_output)


# ── RTB: Set Node Active ────────────────────────────────────────────────


@app.command()
def set_active(
    device_name: str = typer.Option(
        "", "--device", "-d", help="NetBox device name or provider machine ID"
    ),
    resource_id: int | None = typer.Option(
        None, "--resource-id", help="NetBox numeric resource ID"
    ),
    resource_type: str = typer.Option(
        "device", "--resource-type", help="'device' (default) or 'vm'"
    ),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Reset a node's NetBox status to Active after repair (via RTB API)."""
    from .constants import rtb_base_url

    rtb_key = maybe_secret("RTB_API_KEY")
    if not rtb_key:
        typer.echo("Error: RTB_API_KEY not set", err=True)
        raise typer.Exit(1)

    rtb_base = rtb_base_url()

    if not device_name and resource_id is None:
        typer.echo("Error: Provide --device or --resource-id", err=True)
        raise typer.Exit(1)

    if resource_id is not None and resource_type not in ("device", "vm"):
        typer.echo("Error: --resource-type must be 'device' or 'vm'", err=True)
        raise typer.Exit(1)

    headers = {"Authorization": f"Bearer {rtb_key}"}

    try:
        if device_name:
            url = f"{rtb_base}/api/v1/nodes/by-name/{device_name}/set-active"
        else:
            url = f"{rtb_base}/api/v1/nodes/{resource_type}/{resource_id}/set-active"

        resp = http_requests.post(url, headers=headers, timeout=15)
    except http_requests.RequestException as e:
        typer.echo(f"Error: RTB request failed: {e}", err=True)
        raise typer.Exit(1) from e

    if resp.status_code != 200:
        try:
            body = resp.json()
            detail = body.get("error", resp.text[:300])
        except Exception:
            detail = resp.text[:300]
        typer.echo(f"Error: RTB returned {resp.status_code}: {detail}", err=True)
        raise typer.Exit(1)

    identifier = device_name or f"{resource_type}/{resource_id}"
    result = {
        "ok": True,
        "device_name": identifier,
        "message": f"Node {identifier} set to Active in NetBox, Linear ticket cleared.",
    }
    _output(result, as_json=json_output)


# ── Alert Silencing ─────────────────────────────────────────────────────


@app.command()
def silence(
    instance: str = typer.Option(
        ..., "--instance", "-i", help="Instance regex (e.g. host.cloud.together.ai:.*)"
    ),
    alert_name: str = typer.Option(
        "GPUFellOffTheBus", "--alert-name", "-a", help="Alert name to silence"
    ),
    duration_hours: int = typer.Option(
        168, "--duration", help="Silence duration in hours (default 168 = 7d)"
    ),
    comment_text: str = typer.Option("", "--comment", "-c", help="Reason for the silence"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Create an Alertmanager silence for a node/alert combination."""
    from .formatting import alertmanager_create_silence

    result = alertmanager_create_silence(
        instance_pattern=instance,
        alert_name=alert_name,
        duration_hours=duration_hours,
        comment=comment_text,
    )
    if not result:
        typer.echo(
            "Error: Failed to create silence. Check O11Y_GRAFANA_USERNAME/PASSWORD.", err=True
        )
        raise typer.Exit(1)
    _output({"ok": True, **result}, as_json=json_output)


# ── Knowledge Base ──────────────────────────────────────────────────────


@app.command()
def kb_search(
    query: str = typer.Argument(help="Search keywords"),
    vendor: str = typer.Option("iren", "--vendor", "-v", help="Vendor (currently only iren)"),
    limit: int = typer.Option(10, "--limit", "-l", help="Max articles (1-50)"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Search vendor knowledge base articles."""
    handler = _get_handler(vendor)
    if not hasattr(handler, "search_knowledge_base"):
        typer.echo(f"Error: Vendor '{vendor}' does not support KB search", err=True)
        raise typer.Exit(1)
    articles = handler.search_knowledge_base(query, limit=limit)
    if articles is None:
        _exit_with_handler_failure(
            handler,
            vendor,
            "Failed to search knowledge base",
            json_output,
        )
    if json_output:
        _output({"articles": articles, "count": len(articles), "query": query}, as_json=True)
    else:
        typer.echo(f"# {len(articles)} article(s) matching '{query}'")
        for a in articles:
            aid = a.get("id", "?")
            title = a.get("title", "?")
            typer.echo(f"  [{aid}] {title}")


@app.command()
def kb_article(
    article_id: str = typer.Argument(help="KB article ID (numeric) or full URL"),
    vendor: str = typer.Option("iren", "--vendor", "-v", help="Vendor (currently only iren)"),
    download_attachments: bool = typer.Option(
        False, "--download-attachments", help="Download article attachments to current directory"
    ),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Get a knowledge base article with full content.

    Accepts a numeric ID or a full Freshdesk article URL.
    Use --download-attachments to save attached files locally.
    """
    handler = _get_handler(vendor)
    if not hasattr(handler, "get_kb_article"):
        typer.echo(f"Error: Vendor '{vendor}' does not support KB articles", err=True)
        raise typer.Exit(1)
    article = handler.get_kb_article(article_id)
    if not article:
        _exit_with_handler_failure(
            handler,
            vendor,
            f"Article {article_id} not found",
            json_output,
        )

    if download_attachments:
        attachments = article.get("attachments", [])
        if not attachments:
            typer.echo("No attachments to download.", err=True)
        else:
            for att in attachments:
                _download_attachment(att)

    _output(article, as_json=json_output)


def _download_attachment(att: dict[str, Any]) -> None:
    """Download a single attachment to the current directory."""
    import requests as _req

    url = att.get("url", "")
    name = att.get("name", "") or url.rsplit("/", 1)[-1]
    if not url:
        return
    try:
        resp = _req.get(url, timeout=30)
        if resp.status_code == 200:
            from pathlib import Path

            dest = Path(name)
            dest.write_bytes(resp.content)
            typer.echo(f"  Downloaded: {dest} ({len(resp.content)} bytes)")
        else:
            typer.echo(f"  Failed to download {name}: HTTP {resp.status_code}", err=True)
    except Exception as e:
        typer.echo(f"  Failed to download {name}: {e}", err=True)


# ── Auth diagnostics ────────────────────────────────────────────────────


def _build_inspection_handler(vendor: str) -> Any:
    """Build a handler for passive ``auth-status`` inspection.

    Bypasses the registry (which would trigger browser auth on stale
    cookies in the Atlassian constructor).  Constructed with
    ``use_cached_cookies=False`` so the constructor stays quiet; cookies
    are loaded manually afterwards so ``_probe_session`` (where
    supported) can validate them against the portal.

    Missing credentials are tolerated — we use empty strings so the
    handler still constructs.  No write operation will succeed without
    creds, but ``auth-status`` only needs to read cookie state.
    """
    from .validation import ValidationError

    handler_classes = _handler_classes()
    vkey = vendor.lower()
    if vkey not in handler_classes:
        raise ValidationError(
            f"Vendor '{vendor}' not registered. Available: {', '.join(handler_classes.keys())}"
        )

    # Secrets resolved via the credential chain (literal or op:// ref); missing
    # values fall back to "" so the inspection handler still constructs.
    env_prefix = vkey.upper()
    username = maybe_secret(f"{env_prefix}_PORTAL_USERNAME") or ""
    password = maybe_secret(f"{env_prefix}_PORTAL_PASSWORD") or ""

    handler = handler_classes[vkey](
        email=username,
        password=password,
        use_cached_cookies=False,
        verbose=False,
    )

    loader = getattr(handler, "_load_cookies", None)
    if callable(loader):
        try:
            loader()
        except Exception:
            pass
    return handler


@app.command()
def auth_status(
    vendor: str = typer.Option("ori", "--vendor", "-v", help="Vendor: ori, hypertec, iren"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Report vendor session health: cookie age, cooldown, probe, last error.

    Exit code is 0 when the session looks usable, 1 otherwise.  The
    in-process cooldown is always 0 for a fresh invocation — cooldown
    is per-process (see issue #54), so a fresh shell may still succeed
    even when another process is in cooldown.

    Examples:
        dc-support-cli auth-status --vendor ori
        dc-support-cli auth-status --vendor iren --json
    """
    from datetime import datetime
    from pathlib import Path

    from .constants import COOKIE_MAX_AGE

    try:
        handler = _build_inspection_handler(vendor)
    except Exception as exc:
        if json_output:
            _output({"error": str(exc), "vendor": vendor}, as_json=True)
        else:
            typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    cookie_file = getattr(handler, "cookie_file", None)
    cookie_path = str(cookie_file) if cookie_file is not None else None

    cookie_exists = False
    cookie_mtime: datetime | None = None

    if isinstance(cookie_file, Path) or (
        cookie_file is not None and hasattr(cookie_file, "exists")
    ):
        try:
            cookie_exists = bool(cookie_file.exists())
        except Exception:
            cookie_exists = False
        if cookie_exists:
            try:
                stat = cookie_file.stat()
                cookie_mtime = datetime.fromtimestamp(stat.st_mtime)
            except Exception:
                cookie_mtime = None

    # Both timing values come from the handler's helpers (issue #90).  Fall
    # back to ``None`` / ``0`` if a handler doesn't expose them — the test
    # suite uses bare ``MagicMock`` instances for some legacy fixtures.
    cookie_age_method = getattr(handler, "cookie_age_seconds", None)
    cookie_age_seconds: int | None
    if callable(cookie_age_method):
        try:
            cookie_age_seconds = cookie_age_method()
        except Exception:
            cookie_age_seconds = None
    else:
        cookie_age_seconds = None

    cooldown_method = getattr(handler, "cooldown_remaining_seconds", None)
    cooldown_remaining_seconds: int = 0
    if callable(cooldown_method):
        try:
            cooldown_remaining_seconds = int(cooldown_method())
        except Exception:
            cooldown_remaining_seconds = 0

    cookie_max_age_seconds = int(COOKIE_MAX_AGE.total_seconds())
    cookie_fresh = (
        cookie_exists
        and cookie_age_seconds is not None
        and cookie_age_seconds < cookie_max_age_seconds
    )

    probe = getattr(handler, "_probe_session", None)
    probe_supported = callable(probe)
    probe_ok: bool | None = None
    probe_error: str | None = None
    if probe_supported and cookie_exists:
        assert probe is not None  # narrowed by `callable(probe)` above
        try:
            probe_ok = bool(probe())
        except Exception as exc:
            probe_ok = False
            probe_error = f"Probe failed: {type(exc).__name__}: {exc}"
    elif probe_supported and not cookie_exists:
        probe_ok = False

    raw_last_error = getattr(handler, "last_error", None)
    handler_last_error = raw_last_error if isinstance(raw_last_error, str) else None
    last_error = handler_last_error or probe_error

    # IREN doesn't expose ``_probe_session`` — it uses Freshdesk API-key
    # auth for writes and browser cookies only for portal scraping.  In
    # that case we accept ``probe_supported=False`` and lean on cookie
    # state + cooldown for the ``usable`` verdict.
    usable = (
        cookie_fresh
        and cooldown_remaining_seconds == 0
        and (not probe_supported or probe_ok is True)
    )

    # Audit-safe credential metadata (source only — never the secret values).
    credential_source = portal_source(vendor)
    is_iren = vendor.lower() == "iren"
    freshdesk_api_source = secret_source("IREN_FRESHDESK_API_KEY") if is_iren else None

    # IREN's primary write path is the Freshdesk REST API; a configured API key
    # makes the vendor usable even without fresh portal cookies.
    if is_iren and freshdesk_api_source is not None:
        usable = True

    data: dict[str, Any] = {
        "vendor": vendor,
        "cookie_file": cookie_path,
        "cookie_exists": cookie_exists,
        "cookie_mtime": cookie_mtime.isoformat() if cookie_mtime else None,
        "cookie_age_seconds": cookie_age_seconds,
        "cookie_max_age_seconds": cookie_max_age_seconds,
        "cookie_fresh": cookie_fresh,
        "cooldown_remaining_seconds": cooldown_remaining_seconds,
        "probe_supported": probe_supported,
        "probe_ok": probe_ok,
        "credential_source": credential_source,
        "last_error": last_error,
        "usable": usable,
    }
    if is_iren:
        data["freshdesk_api_configured"] = freshdesk_api_source is not None
        data["freshdesk_api_source"] = freshdesk_api_source

    if json_output:
        _output(data, as_json=True)
    else:
        typer.echo(f"vendor: {vendor}")
        typer.echo(f"  cookie_file: {cookie_path}")
        typer.echo(f"  cookie_exists: {cookie_exists}")
        if cookie_mtime is not None:
            typer.echo(f"  cookie_mtime: {cookie_mtime.isoformat()}")
        if cookie_age_seconds is not None:
            typer.echo(f"  cookie_age: {cookie_age_seconds}s / {cookie_max_age_seconds}s max")
        typer.echo(f"  cookie_fresh: {cookie_fresh}")
        typer.echo(f"  cooldown_remaining: {cooldown_remaining_seconds}s")
        typer.echo(f"  probe_supported: {probe_supported}")
        typer.echo(f"  probe_ok: {probe_ok}")
        typer.echo(f"  credential_source: {credential_source or '-'}")
        if is_iren:
            typer.echo(f"  freshdesk_api_configured: {freshdesk_api_source is not None}")
        if last_error:
            typer.echo(f"  last_error: {last_error}")
        typer.echo(f"  usable: {usable}")

    raise typer.Exit(0 if usable else 1)


# ── Utility ─────────────────────────────────────────────────────────────


def _integration_status(env_vars: list[str]) -> tuple[str, str]:
    """Return (configured, source) for a set of env vars, values never read.

    Configured is ``"yes"`` only when *every* var is set.  Source is ``op://``
    if any var is a 1Password reference, else ``env`` (``-`` when unconfigured).
    """
    sources = [secret_source(v) for v in env_vars]
    if any(s is None for s in sources):
        return "no", "-"
    return "yes", ("op://" if "op://" in sources else "env")


@app.command()
def vendors() -> None:
    """List vendors + internal-ops integrations with credential status.

    Only credential *source* metadata is shown (``env`` vs ``op://``); secret
    values are never read or printed.  Any value may be a literal or an
    ``op://Vault/Item/field`` 1Password reference — see ``docs/CREDENTIALS.md``.
    """
    typer.echo("Vendors:")
    for name, desc in (
        ("ori", "ORI Industries (Atlassian)"),
        ("hypertec", "Hypertec / 5C (Atlassian)"),
    ):
        src = portal_source(name)
        configured = "yes" if src else "no"
        typer.echo(f"  {name:<10} {desc:<32} configured={configured}  source={src or '-'}")
        typer.echo(
            f"             env: {name.upper()}_PORTAL_USERNAME / {name.upper()}_PORTAL_PASSWORD"
        )

    # IREN is configured when EITHER the Freshdesk API key OR portal creds exist.
    iren_portal_src = portal_source("iren")
    iren_api_src = secret_source("IREN_FRESHDESK_API_KEY")
    modes: list[str] = []
    if iren_api_src:
        modes.append(f"freshdesk-api ({iren_api_src})")
    if iren_portal_src:
        modes.append(f"portal ({iren_portal_src})")
    iren_configured = "yes" if modes else "no"
    typer.echo(
        f"  {'iren':<10} {'IREN (Freshdesk API + browser)':<32} "
        f"configured={iren_configured}  mode={', '.join(modes) if modes else '-'}"
    )
    typer.echo(
        "             env: IREN_FRESHDESK_API_KEY | IREN_PORTAL_USERNAME / IREN_PORTAL_PASSWORD"
    )

    typer.echo("")
    typer.echo("Internal-ops integrations (VPN-gated):")
    for label, env_vars in (
        ("RTB", ["RTB_API_KEY"]),
        ("NetBox", ["NETBOX_TOKEN"]),
        ("Grafana", ["O11Y_GRAFANA_USERNAME", "O11Y_GRAFANA_PASSWORD"]),
        ("Linear", ["LINEAR_API_KEY"]),
    ):
        configured, src = _integration_status(env_vars)
        typer.echo(f"  {label:<10} configured={configured}  source={src}")
        typer.echo(f"             env: {' / '.join(env_vars)}")


def main() -> None:
    from mcp_common.env import load_env

    load_env()
    setup_logging(name="dc-support-cli")
    app()


if __name__ == "__main__":
    main()
