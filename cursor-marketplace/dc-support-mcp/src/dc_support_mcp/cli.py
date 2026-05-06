"""
dc-support-cli: Thin CLI wrapper for datacenter vendor support operations.

Provides the same capabilities as dc-support-mcp but via shell commands,
enabling AI agents to use vendor support portals with ~40-90% fewer tokens.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import requests as http_requests
import typer
from dotenv import load_dotenv
from mcp_common.agent_remediation import install_cli_exception_handler
from mcp_common.logging import setup_logging

# Match mcp_server.py: load .env from the package dir and workspace root.
# override=True so .env values win over stale shell env vars.
_pkg_dir = Path(__file__).resolve().parent.parent.parent
load_dotenv(_pkg_dir / ".env", override=True)
load_dotenv(_pkg_dir.parent / ".env", override=True)

app = typer.Typer(
    name="dc-support-cli",
    help="Manage datacenter vendor support tickets (ORI, Hypertec, IREN). Use --help on any subcommand.",
    no_args_is_help=True,
)
install_cli_exception_handler(app, project_repo="vhspace/dc-support-mcp")

VENDORS = ["ori", "hypertec", "iren"]


def _get_handler(vendor: str) -> Any:
    """Lazy-import and return a vendor handler via the registry."""
    from .validation import ValidationError
    from .vendors import HypertecVendorHandler, IrenVendorHandler, OriVendorHandler, VendorRegistry

    registry = VendorRegistry(verbose=False)
    registry.register("ori", OriVendorHandler)
    registry.register("iren", IrenVendorHandler)
    registry.register("hypertec", HypertecVendorHandler)

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


# ── Ticket Commands ─────────────────────────────────────────────────────


@app.command()
def tickets(
    vendor: str = typer.Option("ori", "--vendor", "-v", help="Vendor: ori, hypertec, iren"),
    status: str = typer.Option("open", "--status", "-s", help="open, closed, or all"),
    limit: int = typer.Option(20, "--limit", "-l", help="Max tickets to return"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """List support tickets from a vendor portal."""
    handler = _get_handler(vendor)
    result = handler.list_tickets(status=status, limit=limit)
    if not result:
        if json_output:
            _output({"tickets": [], "count": 0}, as_json=True)
        else:
            typer.echo("No tickets found.")
        return
    if json_output:
        _output({"tickets": result, "count": len(result)}, as_json=True)
    else:
        typer.echo(f"# {len(result)} {status} ticket(s) — {vendor}")
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
        if json_output:
            _output({"error": f"Ticket {ticket_id} not found"}, as_json=True)
        else:
            typer.echo(f"Ticket {ticket_id} not found.", err=True)
        raise typer.Exit(1)
    _output(ticket, as_json=json_output)


@app.command()
def create_service_request(
    summary: str = typer.Option(..., "--summary", help="Short title using provider node name"),
    description: str = typer.Option(
        ..., "--description", help="Issue description (no internal refs)"
    ),
    vendor: str = typer.Option(
        "hypertec", "--vendor", "-v", help="Vendor: hypertec, ori, or iren"
    ),
    support_level: str = typer.Option(
        "Critical", "--support-level", help="Critical/Normal/Question (Hypertec)"
    ),
    reboot_allowed: str = typer.Option(
        "YES", "--reboot-allowed", help="YES/NO/Does not apply (Hypertec)"
    ),
    priority: str = typer.Option(
        "P3", "--priority", "-p", help="P1-P5 (IREN only, default P3)"
    ),
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
            detail = getattr(handler, "last_error", None) or "Unknown error"
            if json_output:
                _output(
                    {"error": "Failed to create IREN ticket", "detail": detail}, as_json=True
                )
            else:
                typer.echo(f"Failed to create IREN ticket: {detail}", err=True)
            raise typer.Exit(1)

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
        detail = getattr(handler, "last_error", None) or "Unknown error"
        if json_output:
            _output({"error": "Failed to create service request", "detail": detail}, as_json=True)
        else:
            typer.echo(f"Failed to create service request: {detail}", err=True)
        raise typer.Exit(1)

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
        typer.echo(f"Failed to add comment to {ticket_id}.", err=True)
        raise typer.Exit(1)
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
        typer.echo(f"Failed to update status of {ticket_id} to {status}.", err=True)
        raise typer.Exit(1)

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
        help="Email of the Linear ticket assignee (overrides on-call lookup)",
    ),
    list_outage_types: bool = typer.Option(
        False, "--list-outage-types", help="Print valid GPU outage types and exit"
    ),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Create an RTB triage ticket (Linear + NetBox + Slack)."""
    from .constants import RTB_OUTAGE_TYPES

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

    rtb_key = os.getenv("RTB_API_KEY")
    if not rtb_key:
        typer.echo("Error: RTB_API_KEY not set", err=True)
        raise typer.Exit(1)

    types_list = [t.strip() for t in issue_types.split(",")] if issue_types else ["GPU issue"]

    try:
        device_resp = http_requests.get(
            f"https://rtb.together.ai/api/v1/device/{device_name}",
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
    from .oncall import get_oncall_email, is_email, linear_assign_ticket

    assignee_email = ""
    if assignee and is_email(assignee):
        assignee_email = assignee
    if not assignee_email and created_by and is_email(created_by):
        assignee_email = created_by
    if not assignee_email:
        assignee_email = get_oncall_email() or ""

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
            "https://rtb.together.ai/api/v1/tickets/triage",
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

    api_key = os.getenv("LINEAR_API_KEY")
    if not api_key:
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
    rtb_key = os.getenv("RTB_API_KEY")
    if not rtb_key:
        typer.echo("Error: RTB_API_KEY not set", err=True)
        raise typer.Exit(1)

    if not device_name and resource_id is None:
        typer.echo("Error: Provide --device or --resource-id", err=True)
        raise typer.Exit(1)

    if resource_id is not None and resource_type not in ("device", "vm"):
        typer.echo("Error: --resource-type must be 'device' or 'vm'", err=True)
        raise typer.Exit(1)

    headers = {"Authorization": f"Bearer {rtb_key}"}

    try:
        if device_name:
            url = f"https://rtb.together.ai/api/v1/nodes/by-name/{device_name}/set-active"
        else:
            url = f"https://rtb.together.ai/api/v1/nodes/{resource_type}/{resource_id}/set-active"

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
        typer.echo("Failed to search knowledge base.", err=True)
        raise typer.Exit(1)
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
        typer.echo(f"Article {article_id} not found.", err=True)
        raise typer.Exit(1)

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


# ── Utility ─────────────────────────────────────────────────────────────


@app.command()
def vendors() -> None:
    """List supported vendors and their credential env vars."""
    info = [
        ("ori", "ORI Industries (Atlassian)", "ORI_PORTAL_USERNAME / ORI_PORTAL_PASSWORD"),
        (
            "hypertec",
            "Hypertec / 5C (Atlassian)",
            "HYPERTEC_PORTAL_USERNAME / HYPERTEC_PORTAL_PASSWORD",
        ),
        ("iren", "IREN (Freshdesk)", "IREN_PORTAL_USERNAME / IREN_PORTAL_PASSWORD"),
    ]
    typer.echo("Supported vendors:")
    for name, desc, env in info:
        configured = "yes" if os.getenv(f"{name.upper()}_PORTAL_USERNAME") else "no"
        typer.echo(f"  {name:<12} {desc:<30} configured={configured}")
        typer.echo(f"               env: {env}")


def main() -> None:
    setup_logging(name="dc-support-cli")
    app()


if __name__ == "__main__":
    main()
