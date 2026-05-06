"""MCP Server for DC Support portals using FastMCP."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, cast

import requests as http_requests
from dotenv import load_dotenv
from fastmcp import FastMCP
from mcp_common.agent_remediation import mcp_remediation_wrapper
from mcp_common.logging import setup_logging

from .formatting import (
    alertmanager_create_silence,
    build_rtb_triage_payload,
    netbox_ensure_triage_status,
)
from .oncall import get_oncall_email, is_email, linear_assign_ticket
from .validation import ValidationError
from .vendor_handler import VendorHandler
from .vendors import HypertecVendorHandler, IrenVendorHandler, OriVendorHandler, VendorRegistry

# Load .env from the MCP directory and the parent (workspace root) for
# centralised credential management, matching the pattern used by other MCPs.
# override=True ensures .env file values take precedence over stale env vars
# inherited from parent processes.
_mcp_dir = Path(__file__).resolve().parent.parent.parent
load_dotenv(_mcp_dir / ".env", override=True)
load_dotenv(_mcp_dir.parent / ".env", override=True)

logger = logging.getLogger(__name__)

mcp = FastMCP("dc-support-mcp")

_registry = VendorRegistry(verbose=False)
_registry.register("ori", OriVendorHandler)
_registry.register("iren", IrenVendorHandler)
_registry.register("hypertec", HypertecVendorHandler)


def _get_handler(vendor: str) -> VendorHandler:
    """Get a vendor handler, raising ValueError for unknown vendors."""
    try:
        return cast(VendorHandler, _registry.get_handler(vendor))
    except (ValidationError, Exception) as e:
        raise ValueError(str(e)) from e


def _auth_error_or(handler: VendorHandler, vendor: str, fallback: dict[str, Any]) -> dict[str, Any]:
    """Return auth error with remediation if the handler recorded one, otherwise *fallback*."""
    err = handler.last_error
    if err:
        return {
            "error": f"Auth failure for {vendor}: {err}",
            "remediation": (
                f"Session expired or auth cooldown active. Wait 5 minutes and retry, "
                f"or run `dc-support-cli auth-status --vendor {vendor}` to check."
            ),
        }
    return fallback


@mcp.tool()
@mcp_remediation_wrapper(project_repo="vhspace/dc-support-mcp")
def get_vendor_ticket(ticket_id: str, vendor: str = "ori") -> dict[str, Any]:
    """Fetch a support ticket with full details (summary, status, assignee, comments).

    Use this to check ticket status, read the conversation thread, or
    get context before adding a comment.

    Args:
        ticket_id: Ticket identifier (e.g. SUPP-1556 for ORI, HTCSR-3391 for Hypertec, or numeric ID for IREN)
        vendor: "ori" (ORI Industries), "hypertec" (Hypertec/5C), or "iren" (IREN / Freshdesk)
    """
    handler = _get_handler(vendor)
    handler.last_error = None
    ticket = handler.get_ticket(ticket_id)
    if not ticket:
        return _auth_error_or(handler, vendor, {"error": f"Ticket {ticket_id} not found"})
    return ticket


@mcp.tool()
@mcp_remediation_wrapper(project_repo="vhspace/dc-support-mcp")
def add_vendor_comment(
    ticket_id: str,
    comment: str,
    vendor: str = "ori",
    public: bool = True,
) -> dict[str, Any]:
    """Post a comment on a vendor support ticket.

    Supported for ORI, Hypertec (both Atlassian), and IREN (Freshdesk).
    For IREN, public=False creates a private internal note.

    Args:
        ticket_id: Ticket identifier (e.g. SUPP-1552, HTCSR-3391, or numeric ID for IREN)
        comment: Comment text to post
        vendor: "ori", "hypertec", or "iren"
        public: True for customer-visible comment, False for internal note
    """
    handler = _get_handler(vendor)
    handler.last_error = None
    result = handler.add_comment(ticket_id, comment, public=public)
    if not result:
        return _auth_error_or(handler, vendor, {"error": f"Failed to add comment to {ticket_id}"})
    return {"ok": True, "ticket_id": ticket_id, "comment_preview": comment[:100]}


_STATUS_LABELS = ("resolved", "closed")


@mcp.tool()
def update_vendor_ticket_status(
    ticket_id: str,
    status: str,
    vendor: str = "ori",
) -> dict[str, Any]:
    """Update the status of a vendor support ticket (resolve or close).

    Supported for:
      - vendor="ori" / "hypertec" (Atlassian Service Desk transitions)
      - vendor="iren" (Freshdesk status codes)

    Args:
        ticket_id: Ticket identifier (e.g. SUPP-1556 for ORI, numeric for IREN)
        status: Target status — "resolved" or "closed"
        vendor: "ori", "hypertec", or "iren"
    """
    status_lower = status.lower()
    if status_lower not in _STATUS_LABELS:
        return {"error": f"Unknown status '{status}'. Use: {', '.join(_STATUS_LABELS)}"}

    handler = _get_handler(vendor)
    handler.last_error = None

    if not hasattr(handler, "update_ticket_status"):
        return {"error": f"Vendor {vendor} does not support status updates"}

    if vendor == "iren":
        from .vendors.iren import FRESHDESK_STATUS_MAP

        status_code = FRESHDESK_STATUS_MAP.get(status_lower)
        if status_code is None:
            return {"error": f"Unknown IREN status '{status}'"}
        result = handler.update_ticket_status(ticket_id, status_code)
    else:
        result = handler.update_ticket_status(ticket_id, status_lower)

    if not result:
        return _auth_error_or(
            handler, vendor, {"error": f"Failed to update status of {ticket_id} to {status}"}
        )
    return dict(result)


@mcp.tool()
@mcp_remediation_wrapper(project_repo="vhspace/dc-support-mcp")
def list_vendor_tickets(
    vendor: str = "ori",
    status: str = "open",
    limit: int = 20,
) -> dict[str, Any]:
    """List support tickets from a vendor portal, filtered by status.

    Returns a summary list (id, summary, status, url). Use get_vendor_ticket
    for full ticket details including comments.

    Args:
        vendor: "ori", "hypertec", or "iren"
        status: "open", "closed", or "all"
        limit: Max tickets to return (1-100, default 20)
    """
    handler = _get_handler(vendor)
    handler.last_error = None
    tickets = handler.list_tickets(status=status, limit=limit)
    if not tickets:
        return _auth_error_or(handler, vendor, {"tickets": [], "count": 0, "status": status})
    return {"tickets": tickets, "count": len(tickets), "status": status}


@mcp.tool()
@mcp_remediation_wrapper(project_repo="vhspace/dc-support-mcp")
def create_vendor_ticket(
    summary: str,
    description: str,
    cause: str = "",
    vendor: str = "ori",
    priority: str = "P3",
    urgency: str = "Moderate",
    impact: str = "Medium",
) -> dict[str, Any]:
    """Create a support ticket on a vendor portal.

    Supported vendors:
      - **ori**: ORI Infrastructure Support via ProForma form (browser
        automation, ~15-20s). Uses priority/urgency/impact dropdowns.
      - **iren**: IREN Freshdesk via REST API (~2s). Uses priority
        only (P1-P5 or Freshdesk 1-4). Cause is appended to
        description. Urgency/impact are ignored.

    Args:
        summary: Short title (e.g. "dfw01-cpu-04: won't power on")
        description: Detailed issue description with investigation steps
        cause: What caused the issue (required by ORI; appended to description for IREN)
        vendor: "ori" or "iren"
        priority: P1-P5 (default P3). For IREN maps to Freshdesk 1-4.
        urgency: Critical/High/Moderate/Low/Lowest (ORI only, default Moderate)
        impact: Highest/High/Medium/Low/Lowest (ORI only, default Medium)
    """
    handler = _get_handler(vendor)
    handler.last_error = None
    result = handler.create_ticket(
        summary=summary,
        description=description,
        cause=cause,
        priority=priority,
        urgency=urgency,
        impact=impact,
    )
    if not result:
        return _auth_error_or(
            handler,
            vendor,
            {"error": "Failed to create ticket. Check credentials and portal access."},
        )
    return {"ok": True, **result}


@mcp.tool()
@mcp_remediation_wrapper(project_repo="vhspace/dc-support-mcp")
def create_vendor_service_request(
    summary: str,
    description: str,
    vendor: str = "hypertec",
    support_level: str = "Critical",
    reboot_allowed: str = "YES",
    priority: str = "P3",
) -> dict[str, Any]:
    """Create a support ticket on a vendor portal.

    The summary and description are automatically sanitized to remove
    internal references (Linear tickets, internal hostnames, customer
    names, Slack links, etc.) before submission.

    Use provider node names (from NetBox Provider_Machine_ID), not
    internal hostnames.

    Supported vendors:
      - **hypertec** / **ori**: Atlassian Service Desk REST API.
      - **iren**: Freshdesk REST API (priority maps from P1-P5;
        support_level and reboot_allowed are ignored).

    Args:
        summary: Short title using provider node name (e.g. "GPU Missing - tn1-c1-07-node06 - 3/4 GPUs")
        description: Issue description with provider node name, IP, error details. No internal refs.
        vendor: "hypertec", "ori", or "iren"
        support_level: "Critical", "Normal", or "Question" (Hypertec only)
        reboot_allowed: "YES", "NO", or "Does not apply" (Hypertec only)
        priority: P1-P5 (IREN only, default P3)
    """
    from .vendors.atlassian_base import AtlassianServiceDeskHandler
    from .vendors.iren import IrenVendorHandler

    handler = _get_handler(vendor)
    handler.last_error = None

    # IREN / Freshdesk path
    if isinstance(handler, IrenVendorHandler):
        result = handler.create_ticket(
            summary=summary,
            description=description,
            priority=priority,
        )
        if not result:
            return _auth_error_or(
                handler,
                vendor,
                {"error": "Failed to create IREN ticket. Check credentials and portal access."},
            )
        return {
            "ok": True,
            "ticket_id": result.get("id", ""),
            "url": result.get("url", ""),
            "vendor": vendor,
        }

    # Atlassian path
    if not isinstance(handler, AtlassianServiceDeskHandler):
        return {"error": f"Vendor {vendor} does not support service desk requests"}

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
        return _auth_error_or(
            handler,
            vendor,
            {"error": "Failed to create service request. Check credentials and portal access."},
        )

    ticket_key = result.get("issueKey", "")
    portal_url = f"{handler.BASE_URL}/servicedesk/customer/portal/{handler.PORTAL_ID}/{ticket_key}"
    return {
        "ok": True,
        "ticket_id": ticket_key,
        "url": portal_url,
        "vendor": vendor,
    }


@mcp.tool()
@mcp_remediation_wrapper(project_repo="vhspace/dc-support-mcp")
def create_rtb_triage_ticket(
    device_name: str,
    issue_summary: str,
    issue_types: list[str] | None = None,
    gpu_outage_type: str = "GPU - Missing",
    customer_impacting: bool = False,
    created_by: str = "",
    assignee: str = "",
) -> dict[str, Any]:
    """Create an internal triage ticket via the Repair Ticket Bridge (RTB).

    RTB creates a Linear ticket, updates NetBox status, and posts to Slack.

    The ticket assignee is resolved with this priority:
      1. Explicit ``assignee`` email (if provided)
      2. ``created_by`` (if it's an email)
      3. Current PagerDuty on-call engineer

    If RTB doesn't honor the assignee, a fallback Linear API call reassigns
    the ticket.

    If RTB fails to update NetBox, this tool patches NetBox directly as a
    fallback.

    Requires RTB_API_KEY environment variable.  Optional: PAGERDUTY_USER_API_KEY
    for on-call lookup, LINEAR_API_KEY for assignment fallback.

    Args:
        device_name: NetBox device name (e.g. "us-south-3a-r07-06")
        issue_summary: Structured issue description (use format_gpu_triage_summary style)
        issue_types: List of issue types (default: ["GPU issue"])
        gpu_outage_type: GPU outage sub-type label (default: "GPU - Missing").
            Valid values: Node Down, Node Not in Cluster, Memory Error,
            GPU - ECC errors, GPU - Missing, GPU - Thermal,
            GPU - Misconfiguration, GPU - Baseboard, GPU - Replaced,
            GPU - NIC replaced, GPU - NVSwitch, Network - Optics Cleaning,
            Network - Unspecified, Network - Cable/Fiber, Network - Transceiver,
            Network - Inband, Network - Config, Filesystem, Storage, SSD,
            NCCL Error, Reboot only, BIOS/BMC/PLX/Retimer Firmware, Other
        customer_impacting: If true, sets priority to Urgent
        created_by: Email or name of the person/on-call who triggered creation
        assignee: Email of the Linear ticket assignee (overrides on-call lookup)
    """
    from .validation import ValidationError as _ValErr
    from .validation import validate_gpu_outage_type

    try:
        gpu_outage_type = validate_gpu_outage_type(gpu_outage_type)
    except _ValErr as exc:
        return {"error": str(exc)}

    rtb_key = os.getenv("RTB_API_KEY")
    if not rtb_key:
        return {"error": "RTB_API_KEY not set"}

    if issue_types is None:
        issue_types = ["GPU issue"]

    assignee_email = ""
    if assignee and is_email(assignee):
        assignee_email = assignee
    if not assignee_email and created_by and is_email(created_by):
        assignee_email = created_by
    if not assignee_email:
        assignee_email = get_oncall_email() or ""

    try:
        device_resp = http_requests.get(
            f"https://rtb.together.ai/api/v1/device/{device_name}",
            headers={"Authorization": f"Bearer {rtb_key}"},
            timeout=10,
        )
        if device_resp.status_code != 200:
            return {"error": f"Device {device_name} not found in RTB"}
        device_data = device_resp.json()
        device_id = device_data["id"]
    except (http_requests.RequestException, KeyError) as e:
        return {"error": f"RTB device lookup failed: {e}"}

    payload = build_rtb_triage_payload(
        device_id=device_id,
        issue_summary=issue_summary,
        issue_types=issue_types,
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
        return {"error": f"RTB request failed: {e}"}

    if resp.status_code != 201:
        return {"error": f"RTB returned {resp.status_code}: {resp.text[:300]}"}

    data = resp.json()
    ticket = data.get("ticket", {})
    netbox_ok = data.get("netbox_updated", False)

    if not netbox_ok:
        linear_id = ticket.get("id", "")
        netbox_ok = netbox_ensure_triage_status(device_id, linear_id)

    ticket_id = ticket.get("id", "")
    instance_fqdn = f"{device_name}.cloud.together.ai"

    linear_assigned = False
    if assignee_email and ticket_id:
        linear_assigned = linear_assign_ticket(ticket_id, assignee_email)

    return {
        "ok": True,
        "ticket_id": ticket_id,
        "title": ticket.get("title", ""),
        "url": ticket.get("url", ""),
        "assignee": assignee_email or None,
        "linear_assigned": linear_assigned,
        "netbox_updated": netbox_ok,
        "device_id": device_id,
        "next_steps": {
            "silence_alert": {
                "action": "Call silence_alert to stop this node from paging",
                "instance": f"{instance_fqdn}:.*",
                "alert_name": "GPUFellOffTheBus",
                "suggested_comment": (
                    f"Triage ticket {ticket_id} filed. Node cordoned, awaiting provider repair."
                ),
            }
        },
    }


@mcp.tool()
@mcp_remediation_wrapper(project_repo="vhspace/dc-support-mcp")
def list_rtb_triage_tickets(
    status: str = "open",
    assignee: str = "",
    team_key: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    """List internal triage tickets from Linear.

    Returns triage tickets created via create_rtb_triage_ticket (or the CLI
    ``triage`` command).  Useful for checking which nodes already have triage
    tickets or verifying tickets were filed after bulk operations.

    Requires LINEAR_API_KEY environment variable.

    Args:
        status: "open" (default), "closed", or "all"
        assignee: Filter by assignee email (optional)
        team_key: Linear team key, e.g. "SRE" (optional; falls back to
            RTB_LINEAR_TEAM_KEY env var, then lists across all teams)
        limit: Max tickets to return (1-50, default 20)
    """
    from .oncall import linear_list_issues

    api_key = os.getenv("LINEAR_API_KEY")
    if not api_key:
        return {"error": "LINEAR_API_KEY not set"}

    if status not in ("open", "closed", "all"):
        return {"error": f"Unknown status '{status}'. Use: open, closed, all"}

    tickets = linear_list_issues(
        team_key=team_key or None,
        assignee_email=assignee or None,
        status=status,
        limit=limit,
    )

    if tickets is None:
        return {"error": "Failed to query Linear. Check LINEAR_API_KEY."}

    return {"tickets": tickets, "count": len(tickets), "status": status}


@mcp.tool()
@mcp_remediation_wrapper(project_repo="vhspace/dc-support-mcp")
def silence_alert(
    instance: str,
    alert_name: str = "GPUFellOffTheBus",
    duration_hours: int = 168,
    comment: str = "",
) -> dict[str, Any]:
    """Create an Alertmanager silence for a specific node/alert combination.

    Typically called after create_rtb_triage_ticket, using the values from
    its next_steps.silence_alert response. Silences the alert for the
    specified duration (default 7 days) so known-broken nodes stop paging.

    Args:
        instance: Instance regex pattern (e.g. "us-south-3a-r01-05.cloud.together.ai:.*")
        alert_name: Alert name to silence (default: "GPUFellOffTheBus")
        duration_hours: Silence duration in hours (default: 168 = 7 days)
        comment: Reason for the silence (e.g. "Triage ticket SRE-1574 filed")
    """
    result = alertmanager_create_silence(
        instance_pattern=instance,
        alert_name=alert_name,
        duration_hours=duration_hours,
        comment=comment,
    )
    if not result:
        return {
            "error": "Failed to create silence. Check O11Y_GRAFANA_USERNAME/PASSWORD env vars.",
        }
    return {
        "ok": True,
        **result,
    }


@mcp.tool()
@mcp_remediation_wrapper(project_repo="vhspace/dc-support-mcp")
def set_node_active(
    device_name: str = "",
    resource_id: int | None = None,
    resource_type: str = "device",
) -> dict[str, Any]:
    """Reset a node's NetBox status to Active and clear its Linear ticket via RTB.

    Call this after a node has been repaired and returned to service. The RTB
    API sets the node's status to Active in NetBox and clears the attached
    Linear ticket field, eliminating the manual cleanup step.

    Provide EITHER device_name (looks up by name) OR resource_id + resource_type.

    Requires RTB_API_KEY environment variable.

    Args:
        device_name: NetBox device name or provider machine ID (e.g. "us-south-3a-r07-06")
        resource_id: NetBox numeric resource ID (alternative to device_name)
        resource_type: "device" (default) or "vm" — only used with resource_id
    """
    rtb_key = os.getenv("RTB_API_KEY")
    if not rtb_key:
        return {"error": "RTB_API_KEY not set"}

    if not device_name and resource_id is None:
        return {"error": "Provide either device_name or resource_id"}

    if resource_id is not None and resource_type not in ("device", "vm"):
        return {"error": "resource_type must be 'device' or 'vm'"}

    headers = {"Authorization": f"Bearer {rtb_key}"}

    try:
        if device_name:
            url = f"https://rtb.together.ai/api/v1/nodes/by-name/{device_name}/set-active"
        else:
            url = f"https://rtb.together.ai/api/v1/nodes/{resource_type}/{resource_id}/set-active"

        resp = http_requests.post(url, headers=headers, timeout=15)
    except http_requests.RequestException as e:
        return {"error": f"RTB request failed: {e}"}

    if resp.status_code != 200:
        try:
            body = resp.json()
            detail = body.get("error", resp.text[:300])
        except Exception:
            detail = resp.text[:300]
        return {"error": f"RTB returned {resp.status_code}: {detail}"}

    identifier = device_name or f"{resource_type}/{resource_id}"
    return {
        "ok": True,
        "device_name": identifier,
        "message": f"Node {identifier} set to Active in NetBox, Linear ticket cleared.",
    }


@mcp.tool()
@mcp_remediation_wrapper(project_repo="vhspace/dc-support-mcp")
def search_vendor_kb(
    query: str,
    vendor: str = "iren",
    limit: int = 10,
) -> dict[str, Any]:
    """Search vendor knowledge base articles by keyword (cached, fast).

    Currently only supported for vendor="iren". Results are title-matched
    against a 24h-cached article index.

    Args:
        query: Search keywords
        vendor: Currently only "iren" is supported
        limit: Max articles to return (1-50, default 10)
    """
    handler = _get_handler(vendor)
    handler.last_error = None
    if not hasattr(handler, "search_knowledge_base"):
        return {"error": f"Vendor {vendor} does not support knowledge base search"}
    articles = handler.search_knowledge_base(query, limit=limit)
    if articles is None:
        return _auth_error_or(handler, vendor, {"error": "Failed to search knowledge base"})
    return {"articles": articles, "count": len(articles), "query": query}


@mcp.tool()
@mcp_remediation_wrapper(project_repo="vhspace/dc-support-mcp")
def get_vendor_kb_article(
    article_id: str,
    vendor: str = "iren",
) -> dict[str, Any]:
    """Get a knowledge base article with full content, metadata, and attachments.

    Accepts a numeric article ID or a full Freshdesk article URL.
    Fetches directly via REST API first (works for any article, not just
    cached ones), then falls back to the cached index + browser scrape.

    The response includes an ``attachments`` list with name, URL,
    content_type, and size for each attached file.

    Currently only supported for vendor="iren".

    Args:
        article_id: KB article ID (numeric string) or full article URL
        vendor: Currently only "iren" is supported
    """
    handler = _get_handler(vendor)
    handler.last_error = None
    if not hasattr(handler, "get_kb_article"):
        return {"error": f"Vendor {vendor} does not support knowledge base articles"}
    article = handler.get_kb_article(article_id)
    if not article:
        return _auth_error_or(handler, vendor, {"error": f"Article {article_id} not found"})
    return cast(dict[str, Any], article)


def main() -> None:
    setup_logging(name="dc-support-mcp")
    mcp.run()


if __name__ == "__main__":
    main()
