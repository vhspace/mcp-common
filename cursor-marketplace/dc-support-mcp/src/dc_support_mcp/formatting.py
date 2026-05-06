"""
Formatting utilities for dc-support-mcp.

Provides:
  - markdown_to_wiki()          -- convert Markdown to Atlassian wiki markup
  - sanitize_for_vendor()       -- strip internal refs before sending to vendors
  - format_gpu_triage_summary() -- structured GPU diagnostic text for RTB tickets
"""

from __future__ import annotations

import logging
import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import requests

logger = logging.getLogger(__name__)


# ── Markdown → Atlassian Wiki Markup ────────────────────────────────────

_FENCED_CODE_RE = re.compile(
    r"^```(\w*)\n(.*?)^```",
    re.MULTILINE | re.DOTALL,
)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_INLINE_CODE_RE = re.compile(r"`([^`]+?)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_HR_RE = re.compile(r"^-{3,}\s*$", re.MULTILINE)
_NUMBERED_LIST_RE = re.compile(r"^(\s*)\d+\.\s+", re.MULTILINE)
_BULLET_DASH_RE = re.compile(r"^(\s*)- ", re.MULTILINE)
_BLOCKQUOTE_RE = re.compile(r"^>\s?(.*)$", re.MULTILINE)


def markdown_to_wiki(text: str) -> str:
    """Convert common Markdown patterns to Atlassian wiki markup.

    Handles headings, bold, inline code, fenced code blocks, links,
    bullet/numbered lists, blockquotes, and horizontal rules.

    Plain text and text already in wiki format pass through safely.
    """
    if not text:
        return text

    # Fenced code blocks first (before inline transforms touch backticks)
    def _replace_code_fence(m: re.Match[str]) -> str:
        lang = m.group(1)
        code = m.group(2).rstrip("\n")
        if lang:
            return f"{{code:{lang}}}\n{code}\n{{code}}"
        return f"{{code}}\n{code}\n{{code}}"

    out = _FENCED_CODE_RE.sub(_replace_code_fence, text)

    # Protect existing {code} blocks from further transforms
    code_blocks: list[str] = []

    def _stash_code(m: re.Match[str]) -> str:
        code_blocks.append(m.group(0))
        return f"\x00CODE{len(code_blocks) - 1}\x00"

    out = re.sub(
        r"\{code(?::\w+)?\}.*?\{code\}",
        _stash_code,
        out,
        flags=re.DOTALL,
    )

    # Headings: ## Title -> h2. Title
    def _replace_heading(m: re.Match[str]) -> str:
        level = len(m.group(1))
        return f"h{level}. {m.group(2)}"

    out = _HEADING_RE.sub(_replace_heading, out)

    # Bold: **text** -> *text*
    out = _BOLD_RE.sub(r"*\1*", out)

    # Inline code: `code` -> {{code}}
    out = _INLINE_CODE_RE.sub(r"{{\1}}", out)

    # Links: [text](url) -> [text|url]
    out = _LINK_RE.sub(r"[\1|\2]", out)

    # Horizontal rules: --- -> ----
    out = _HR_RE.sub("----", out)

    # Numbered lists: 1. item -> # item
    out = _NUMBERED_LIST_RE.sub(r"\1# ", out)

    # Bullet lists with dashes: - item -> * item
    out = _BULLET_DASH_RE.sub(r"\1* ", out)

    # Blockquotes: > text -> {quote}text{quote}
    # Collect consecutive blockquote lines into a single {quote} block
    lines = out.split("\n")
    result: list[str] = []
    in_quote = False
    for line in lines:
        bq = _BLOCKQUOTE_RE.match(line)
        if bq:
            if not in_quote:
                result.append("{quote}")
                in_quote = True
            result.append(bq.group(1))
        else:
            if in_quote:
                result.append("{quote}")
                in_quote = False
            result.append(line)
    if in_quote:
        result.append("{quote}")
    out = "\n".join(result)

    # Restore stashed code blocks
    for i, block in enumerate(code_blocks):
        out = out.replace(f"\x00CODE{i}\x00", block)

    return out


# ── Structured GPU Triage Summary ───────────────────────────────────────


def format_gpu_triage_summary(
    node: str,
    gpus_visible: int,
    gpus_expected: int,
    failed_bus_ids: list[str],
    error_type: str,
    dmesg_excerpt: str = "",
    reboot_attempted: bool = False,
    prior_ticket: str = "",
) -> str:
    """Format GPU diagnostic data into a structured triage summary.

    Produces clean markdown suitable for Linear (via RTB) that also
    converts well to Atlassian wiki markup via ``markdown_to_wiki``.
    """
    missing = gpus_expected - gpus_visible
    bus_str = ", ".join(failed_bus_ids) if failed_bus_ids else "unknown"

    lines = [
        f"**GPU Status:** {gpus_visible}/{gpus_expected} GPUs visible ({missing} missing)",
        f"**Failed PCIe Bus:** {bus_str}",
        f"**Error:** {error_type}",
    ]

    if reboot_attempted:
        lines.append("**Reboot attempted:** Yes -- GPU did not recover")
    else:
        lines.append("**Reboot attempted:** No")

    if dmesg_excerpt:
        lines.append("")
        lines.append("**dmesg excerpt:**")
        for dline in dmesg_excerpt.strip().splitlines():
            lines.append(f"> {dline}")

    if prior_ticket:
        lines.append("")
        lines.append(f"**Prior ticket:** {prior_ticket}")

    lines.append("")
    lines.append(
        "**Action needed:** Provider hands-on repair"
        " -- PCIe hardware failure persists across reboots"
    )

    return "\n".join(lines)


# ── Vendor-facing content sanitizer ─────────────────────────────────────

# Internal Linear ticket prefixes that should never reach vendor portals
_LINEAR_ID_RE = re.compile(r"\b(?:SRE|ENG|NS|SELL|BECCA|TCL|NETENG)-\d+\b")

# Internal hostnames: us-south-3a-r01-05.cloud.together.ai or us-south-3a-r01-05
_INTERNAL_HOST_RE = re.compile(
    r"\b[a-z]{2}-(?:south|north|east|west|central)-\d+[a-z]?"
    r"-r\d{1,2}-\d{1,2}"
    r"(?:\.cloud\.together\.ai)?\b"
)

# together.ai internal URLs (Linear, Slack, Grafana, Notion, etc.)
_INTERNAL_URL_RE = re.compile(
    r"https?://(?:"
    r"linear\.app/together-ai|"
    r"togetherai\.(?:pagerduty|slack)\.com|"
    r"monitoring(?:-\w+)?\.(?:internal\.)?together\.ai|"
    r"argocd[^/]*\.together\.ai|"
    r"www\.notion\.so/together-docs"
    r")\S*"
)

# Slack channel references
_SLACK_CHANNEL_RE = re.compile(r"#[a-z][a-z0-9_-]{2,}")

# @mentions of internal users
_AT_MENTION_RE = re.compile(r"@\w+@together\.ai\b")


def sanitize_for_vendor(text: str) -> str:
    """Strip internal references from text before sending to a vendor portal.

    Removes:
      - Linear ticket IDs (SRE-xxx, ENG-xxx, NS-xxx, etc.)
      - Internal hostnames (us-south-3a-r01-05 style)
      - Internal URLs (Linear, Slack, PagerDuty, Grafana, Notion)
      - Slack channel references (#channel-name)
      - @user@together.ai mentions

    Replacements use "[internal ref]" so the text stays readable
    without leaking specifics.
    """
    if not text:
        return text

    out = _INTERNAL_URL_RE.sub("[internal link]", text)
    out = _LINEAR_ID_RE.sub("[internal ticket]", out)
    out = _INTERNAL_HOST_RE.sub("[internal hostname]", out)
    out = _AT_MENTION_RE.sub("[internal contact]", out)
    out = _SLACK_CHANNEL_RE.sub("[internal channel]", out)

    return out


# ── RTB triage payload helpers ──────────────────────────────────────────


def build_rtb_triage_payload(
    device_id: int | str,
    issue_summary: str,
    issue_types: list[str],
    gpu_outage_type: str = "GPU - Missing",
    customer_impacting: bool = False,
    created_by: str = "",
    assignee_email: str = "",
) -> dict[str, Any]:
    """Build the JSON payload for ``POST /api/v1/tickets/triage``.

    Centralizes the summary-enrichment and ``created_by`` logic shared
    by both the MCP tool and CLI command.

    ``assignee_email`` is passed through to RTB so it can assign the
    resulting Linear ticket to the correct on-call engineer instead of
    defaulting to a hardcoded user.
    """
    enriched_summary = issue_summary
    if created_by:
        enriched_summary = f"{issue_summary}\n\nCreated by: {created_by}"

    payload: dict[str, Any] = {
        "device_id": int(device_id),
        "issue_types": issue_types,
        "issue_summary": enriched_summary,
        "gpu_outage_type": gpu_outage_type,
        "customer_impacting": customer_impacting,
    }
    if created_by:
        payload["created_by"] = created_by
    if assignee_email:
        payload["assignee_email"] = assignee_email

    return payload


# ── NetBox triage-status fallback ───────────────────────────────────────

_NETBOX_URL = "https://i.together.ai"


def netbox_ensure_triage_status(
    device_id: int | str,
    linear_ticket: str = "",
) -> bool:
    """Set a NetBox device to ``triage`` status (and link a Linear ticket).

    Intended as a fallback when RTB reports ``netbox_updated: false``.
    Uses the NETBOX_TOKEN environment variable for auth.

    Returns True on success, False on failure.
    """
    token = os.getenv("NETBOX_TOKEN")
    if not token:
        logger.warning("NETBOX_TOKEN not set -- cannot update NetBox")
        return False

    patch_url = f"{_NETBOX_URL}/api/dcim/devices/{device_id}/"
    payload: dict[str, object] = {"status": "triage"}
    if linear_ticket:
        payload["custom_fields"] = {"Linear": linear_ticket}

    try:
        resp = requests.patch(
            patch_url,
            json=payload,
            headers={
                "Authorization": f"Token {token}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )
        if resp.status_code == 200:
            logger.info(
                "NetBox device %s updated: status=triage, linear=%s",
                device_id,
                linear_ticket or "(none)",
            )
            return True

        logger.warning(
            "NetBox update failed for device %s: %s %s",
            device_id,
            resp.status_code,
            resp.text[:200],
        )
        return False

    except requests.RequestException as exc:
        logger.warning("NetBox update request failed: %s", exc)
        return False


# ── Alertmanager silence creation ───────────────────────────────────────


def alertmanager_create_silence(
    instance_pattern: str,
    alert_name: str = "GPUFellOffTheBus",
    duration_hours: int = 168,
    comment: str = "",
    created_by: str = "dc-support-mcp",
) -> dict[str, Any] | None:
    """Create an Alertmanager silence via the Grafana proxy.

    Args:
        instance_pattern: Instance regex (e.g. ``us-south-3a-r01-05.cloud.together.ai:.*``)
        alert_name: Alert name to silence (exact match)
        duration_hours: How long the silence lasts (default 7 days)
        comment: Human-readable reason for the silence
        created_by: Creator label on the silence

    Returns:
        Dict with ``silence_id`` and ``expires_at`` on success, None on failure.
    """
    from .constants import (
        GRAFANA_AM_DATASOURCE_UID,
        GRAFANA_AM_PROXY_BASE,
    )

    username = os.getenv("O11Y_GRAFANA_USERNAME")
    password = os.getenv("O11Y_GRAFANA_PASSWORD")
    if not username or not password:
        logger.warning("O11Y_GRAFANA_USERNAME/PASSWORD not set -- cannot create silence")
        return None

    now = datetime.now(tz=UTC)
    ends_at = now + timedelta(hours=duration_hours)

    payload = {
        "matchers": [
            {
                "name": "alertname",
                "value": alert_name,
                "isRegex": False,
                "isEqual": True,
            },
            {
                "name": "instance",
                "value": instance_pattern,
                "isRegex": True,
                "isEqual": True,
            },
        ],
        "startsAt": now.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "endsAt": ends_at.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "createdBy": created_by,
        "comment": comment or f"Silenced by {created_by}",
    }

    url = f"{GRAFANA_AM_PROXY_BASE}/{GRAFANA_AM_DATASOURCE_UID}/api/v2/silences"

    try:
        resp = requests.post(
            url,
            json=payload,
            auth=(username, password),
            timeout=10,
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            silence_id = data.get("silenceID", data.get("id", ""))
            logger.info(
                "Created Alertmanager silence %s for %s (expires %s)",
                silence_id,
                instance_pattern,
                ends_at.isoformat(),
            )
            return {
                "silence_id": silence_id,
                "expires_at": ends_at.isoformat(),
                "matchers": {
                    "alertname": alert_name,
                    "instance": instance_pattern,
                },
            }

        logger.warning(
            "Failed to create silence: %s %s",
            resp.status_code,
            resp.text[:200],
        )
        return None

    except requests.RequestException as exc:
        logger.warning("Alertmanager silence request failed: %s", exc)
        return None
