"""Linear ticket assignment and listing for RTB triage tickets.

Provides:
  - is_email()                  -- lightweight email-address check
  - linear_assign_ticket()      -- reassign a Linear ticket by identifier
  - linear_list_issues()        -- list Linear issues with filters
  - linear_attach_url()         -- attach an external URL to a Linear issue
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import requests

from .secrets import maybe_secret

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_email(value: str) -> bool:
    """Return True if *value* looks like an email address."""
    return bool(_EMAIL_RE.match(value))


def linear_assign_ticket(
    issue_identifier: str,
    assignee_email: str,
) -> bool:
    """Reassign a Linear ticket to the user with *assignee_email*.

    Uses two GraphQL calls:
      1. Look up the Linear user ID by email
      2. Update the issue's assignee

    Requires ``LINEAR_API_KEY`` env var.

    Returns True on success, False on any failure.
    """
    api_key = maybe_secret("LINEAR_API_KEY")
    if not api_key:
        logger.debug("LINEAR_API_KEY not set — skipping Linear assignment")
        return False

    headers = {
        "Authorization": api_key,
        "Content-Type": "application/json",
    }
    url = "https://api.linear.app/graphql"

    try:
        user_resp = requests.post(
            url,
            json={
                "query": """
                    query($email: String!) {
                        users(filter: { email: { eq: $email } }) {
                            nodes { id email }
                        }
                    }
                """,
                "variables": {"email": assignee_email},
            },
            headers=headers,
            timeout=10,
        )
        if user_resp.status_code != 200:
            logger.warning("Linear user lookup returned %s", user_resp.status_code)
            return False

        nodes = user_resp.json().get("data", {}).get("users", {}).get("nodes", [])
        if not nodes:
            logger.warning("No Linear user found for %s", assignee_email)
            return False

        user_id = nodes[0]["id"]

        update_resp = requests.post(
            url,
            json={
                "query": """
                    mutation($id: String!, $assigneeId: String!) {
                        issueUpdate(id: $id, input: { assigneeId: $assigneeId }) {
                            success
                            issue { identifier assignee { email } }
                        }
                    }
                """,
                "variables": {"id": issue_identifier, "assigneeId": user_id},
            },
            headers=headers,
            timeout=10,
        )
        if update_resp.status_code != 200:
            logger.warning("Linear issueUpdate returned %s", update_resp.status_code)
            return False

        result = update_resp.json().get("data", {}).get("issueUpdate", {})
        if result.get("success"):
            issue = result.get("issue", {})
            logger.info(
                "Linear ticket %s assigned to %s",
                issue.get("identifier", issue_identifier),
                assignee_email,
            )
            return True

        logger.warning("Linear issueUpdate not successful: %s", update_resp.text[:200])
        return False

    except requests.RequestException as exc:
        logger.warning("Linear assignment failed: %s", exc)
        return False


# ── Linear issue listing ────────────────────────────────────────────────


# Maps user-facing status strings to Linear workflow state categories.
_LINEAR_STATE_FILTERS: dict[str, dict[str, Any]] = {
    "open": {"state": {"type": {"nin": ["completed", "canceled"]}}},
    "closed": {"state": {"type": {"in": ["completed", "canceled"]}}},
    "all": {},
}


def linear_list_issues(
    team_key: str | None = None,
    assignee_email: str | None = None,
    status: str = "open",
    limit: int = 20,
) -> list[dict[str, Any]] | None:
    """List Linear issues, optionally filtered by team, assignee, and status.

    Uses the Linear GraphQL ``issues`` query.  Returns a list of simplified
    issue dicts (id, identifier, title, status, assignee, created, url) or
    ``None`` on failure.

    Requires ``LINEAR_API_KEY`` env var.

    Args:
        team_key: Linear team key to filter by (e.g. "SRE").  If not given,
            falls back to ``RTB_LINEAR_TEAM_KEY`` env var, then lists across
            all teams.
        assignee_email: Filter by assignee email address.
        status: "open" (default), "closed", or "all".
        limit: Maximum number of issues to return (1-50).
    """
    api_key = maybe_secret("LINEAR_API_KEY")
    if not api_key:
        logger.debug("LINEAR_API_KEY not set — cannot list Linear issues")
        return None

    headers = {"Authorization": api_key, "Content-Type": "application/json"}
    url = "https://api.linear.app/graphql"

    issue_filter: dict[str, Any] = {}
    issue_filter.update(_LINEAR_STATE_FILTERS.get(status, _LINEAR_STATE_FILTERS["open"]))

    # Non-secret config: plain env read.
    resolved_team = team_key or os.getenv("RTB_LINEAR_TEAM_KEY", "")
    if resolved_team:
        issue_filter["team"] = {"key": {"eq": resolved_team}}

    if assignee_email:
        issue_filter["assignee"] = {"email": {"eq": assignee_email}}

    limit = max(1, min(limit, 50))

    query = """
        query($filter: IssueFilter, $first: Int) {
            issues(filter: $filter, first: $first, orderBy: createdAt) {
                nodes {
                    id
                    identifier
                    title
                    state { name type }
                    assignee { email displayName }
                    createdAt
                    url
                }
            }
        }
    """

    try:
        resp = requests.post(
            url,
            json={
                "query": query,
                "variables": {"filter": issue_filter, "first": limit},
            },
            headers=headers,
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning("Linear issues query returned %s", resp.status_code)
            return None

        data = resp.json()
        errors = data.get("errors")
        if errors:
            logger.warning("Linear GraphQL errors: %s", errors)
            return None

        nodes = data.get("data", {}).get("issues", {}).get("nodes", [])

        results: list[dict[str, Any]] = []
        for node in nodes:
            state = node.get("state") or {}
            assignee_obj = node.get("assignee") or {}
            results.append(
                {
                    "id": node.get("identifier", node.get("id", "")),
                    "title": node.get("title", ""),
                    "status": state.get("name", "Unknown"),
                    "status_type": state.get("type", ""),
                    "assignee": assignee_obj.get("email", assignee_obj.get("displayName", "")),
                    "created": node.get("createdAt", ""),
                    "url": node.get("url", ""),
                }
            )
        return results

    except requests.RequestException as exc:
        logger.warning("Linear issue listing failed: %s", exc)
        return None


# ── Linear URL attachments ──────────────────────────────────────────────


def linear_attach_url(
    issue_id: str,
    url: str,
    title: str,
    subtitle: str | None = None,
) -> dict[str, Any]:
    """Attach an external URL (e.g. a GitHub PR) to a Linear issue.

    Calls the Linear GraphQL ``attachmentCreate`` mutation, reusing the same
    ``LINEAR_API_KEY`` credential as the RTB triage assignment fallback. Unlike
    the upstream Linear MCP (which only uploads file bytes), this attaches an
    arbitrary URL with a title and optional subtitle.

    Requires the ``LINEAR_API_KEY`` env var.

    Args:
        issue_id: Linear issue id or identifier (e.g. "SRE-1574" or a UUID).
        url: The external URL to attach.
        title: Title shown on the attachment.
        subtitle: Optional subtitle shown beneath the title.

    Returns:
        ``{"ok": True, "attachment": {...}}`` on success, or
        ``{"error": "..."}`` on any failure.
    """
    if not issue_id:
        return {"error": "issue_id is required"}
    if not url:
        return {"error": "url is required"}
    if not title:
        return {"error": "title is required"}

    api_key = maybe_secret("LINEAR_API_KEY")
    if not api_key:
        logger.debug("LINEAR_API_KEY not set — cannot attach URL")
        return {"error": "LINEAR_API_KEY not set"}

    headers = {"Authorization": api_key, "Content-Type": "application/json"}
    endpoint = "https://api.linear.app/graphql"

    attachment_input: dict[str, Any] = {
        "issueId": issue_id,
        "url": url,
        "title": title,
    }
    if subtitle:
        attachment_input["subtitle"] = subtitle

    mutation = """
        mutation($input: AttachmentCreateInput!) {
            attachmentCreate(input: $input) {
                success
                attachment { id url title subtitle }
            }
        }
    """

    try:
        resp = requests.post(
            endpoint,
            json={"query": mutation, "variables": {"input": attachment_input}},
            headers=headers,
            timeout=15,
        )
    except requests.RequestException as exc:
        logger.warning("Linear attachmentCreate request failed: %s", exc)
        return {"error": f"Linear request failed: {exc}"}

    if resp.status_code != 200:
        logger.warning("Linear attachmentCreate returned %s", resp.status_code)
        return {"error": f"Linear returned HTTP {resp.status_code}: {resp.text[:300]}"}

    try:
        data = resp.json()
    except ValueError:
        logger.warning("Linear attachmentCreate returned a non-JSON body")
        return {"error": f"Linear returned a non-JSON body: {resp.text[:300]}"}

    errors = data.get("errors")
    if errors:
        logger.warning("Linear GraphQL errors: %s", errors)
        return {"error": f"Linear GraphQL errors: {errors}"}

    result = data.get("data", {}).get("attachmentCreate", {})
    if not result.get("success"):
        logger.warning("Linear attachmentCreate not successful: %s", resp.text[:300])
        return {"error": "Linear attachmentCreate did not succeed"}

    logger.info("Attached URL to Linear issue %s", issue_id)
    return {"ok": True, "attachment": result.get("attachment", {})}
