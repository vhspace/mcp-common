"""On-call lookup and Linear ticket assignment for RTB triage tickets.

Provides:
  - get_oncall_email()          -- resolve current PagerDuty on-call engineer
  - linear_assign_ticket()      -- reassign a Linear ticket by identifier
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import requests

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

DEFAULT_ESCALATION_POLICY_ID = "PRV4MLZ"  # "infra" policy


def is_email(value: str) -> bool:
    """Return True if *value* looks like an email address."""
    return bool(_EMAIL_RE.match(value))


def get_oncall_email(
    escalation_policy_id: str | None = None,
    escalation_level: int = 1,
) -> str | None:
    """Look up the current PagerDuty on-call engineer's email.

    Uses the PagerDuty REST API (``/oncalls``) filtered to a single
    escalation policy and level.  Returns the first non-placeholder
    user's email, or ``None`` on any failure.

    Requires ``PAGERDUTY_USER_API_KEY`` env var.
    """
    api_key = os.getenv("PAGERDUTY_USER_API_KEY")
    if not api_key:
        logger.debug("PAGERDUTY_USER_API_KEY not set — skipping on-call lookup")
        return None

    policy_id = (
        escalation_policy_id
        or os.getenv("PAGERDUTY_ESCALATION_POLICY_ID")
        or DEFAULT_ESCALATION_POLICY_ID
    )
    api_host = os.getenv("PAGERDUTY_API_HOST", "https://api.pagerduty.com")

    try:
        resp = requests.get(
            f"{api_host}/oncalls",
            params={
                "escalation_policy_ids[]": policy_id,
                "earliest": "true",
            },
            headers={
                "Authorization": f"Token token={api_key}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning("PagerDuty oncalls returned %s: %s", resp.status_code, resp.text[:200])
            return None

        oncalls: list[dict[str, Any]] = resp.json().get("oncalls", [])

        for oc in oncalls:
            if oc.get("escalation_level") != escalation_level:
                continue
            user = oc.get("user", {})
            email = user.get("email") or user.get("summary", "")
            if email and is_email(email) and "placeholder" not in email.lower():
                logger.info("PagerDuty on-call (level %d): %s", escalation_level, email)
                return str(email)

        logger.info("No on-call found at level %d for policy %s", escalation_level, policy_id)
        return None

    except requests.RequestException as exc:
        logger.warning("PagerDuty on-call lookup failed: %s", exc)
        return None


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
    api_key = os.getenv("LINEAR_API_KEY")
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
    api_key = os.getenv("LINEAR_API_KEY")
    if not api_key:
        logger.debug("LINEAR_API_KEY not set — cannot list Linear issues")
        return None

    headers = {"Authorization": api_key, "Content-Type": "application/json"}
    url = "https://api.linear.app/graphql"

    issue_filter: dict[str, Any] = {}
    issue_filter.update(_LINEAR_STATE_FILTERS.get(status, _LINEAR_STATE_FILTERS["open"]))

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
            results.append({
                "id": node.get("identifier", node.get("id", "")),
                "title": node.get("title", ""),
                "status": state.get("name", "Unknown"),
                "status_type": state.get("type", ""),
                "assignee": assignee_obj.get("email", assignee_obj.get("displayName", "")),
                "created": node.get("createdAt", ""),
                "url": node.get("url", ""),
            })
        return results

    except requests.RequestException as exc:
        logger.warning("Linear issue listing failed: %s", exc)
        return None
