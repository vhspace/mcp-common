---
name: vendor-support
description: Use when managing datacenter vendor support tickets, creating service requests, GPU triage, alert silencing, or searching vendor knowledge bases. Triggers on vendor tickets, ORI, IREN, Hypertec, service requests, RMA, GPU triage, datacenter support, SUPP-*, HTCSR-*.
---

# DC Vendor Support

Manage support tickets across ORI Industries, Hypertec (5C), and IREN vendor portals. Create triage tickets, silence alerts, and search knowledge bases.

## Choose Your Path

This plugin provides two interfaces. Prefer CLI when shell access is available — it uses ~90% fewer tokens.

| Path | When to Use |
|------|-------------|
| **CLI** (`dc-support-cli`) | Agent has shell access, token budget matters, compact output preferred |
| **MCP** (`get_vendor_ticket`, etc.) | No shell access, sandboxed agent, need structured JSON schema validation |

## CRITICAL: Use Provider Node Names in Vendor Tickets

**NEVER put internal hostnames (e.g. `us-south-3a-r01-05`) in vendor-facing tickets.**

Use the **provider node name** from NetBox `Provider_Machine_ID` field (e.g. `tn1-c1-07-node06`, `dfw01-cpu-04`). Content is auto-sanitized, but always use provider names in summary and description to be safe.

## CLI Path

Requires vendor credentials as env vars (see Credentials section below). Run `dc-support-cli --help` for all commands.

| Task | Command |
|------|---------|
| List open tickets | `dc-support-cli tickets --vendor ori` |
| List closed tickets | `dc-support-cli tickets --vendor hypertec --status closed` |
| Get ticket details | `dc-support-cli get-ticket SUPP-1556 --vendor ori` |
| Create service request | `dc-support-cli create-service-request --vendor hypertec --summary "GPU Missing - tn1-c1-07-node06" --description "3/4 GPUs visible"` |
| Create IREN service request | `dc-support-cli create-service-request --vendor iren --priority P2 --summary "GPU down" --description "Node won't boot"` |
| Add comment | `dc-support-cli comment SUPP-1556 --vendor ori --text "Rebooted, issue persists"` |
| Update ticket status | `dc-support-cli update-ticket SUPP-1556 --vendor ori --status resolved` |
| Create triage ticket | `dc-support-cli triage --device us-south-3a-r07-06 --summary "7/8 GPUs, bus 41:00.0 missing" --assignee user@together.ai` |
| List outage types | `dc-support-cli triage --list-outage-types` |
| List triage tickets | `dc-support-cli triage-list --status open --json` |
| Silence alert | `dc-support-cli silence --instance "us-south-3a-r07-06.cloud.together.ai:.*" --comment "Triage ticket filed"` |
| Search KB | `dc-support-cli kb-search "power distribution" --vendor iren` |
| Get KB article | `dc-support-cli kb-article 12345 --vendor iren` |
| Check auth status | `dc-support-cli auth-status --vendor ori` |
| List vendors | `dc-support-cli vendors` |
| JSON output | Add `--json` to any command |
| Verbose diagnostics | Add `--verbose` / `-V` before the subcommand |

If `dc-support-cli` is not on PATH, install with `uvx --from dc-support-mcp dc-support-cli` or run from the repo with `uv run dc-support-cli`.

## MCP Path

| Task | Tool Call |
|------|-----------|
| List tickets | `list_vendor_tickets(vendor="ori", status="open", limit=20)` |
| Get ticket | `get_vendor_ticket(ticket_id="SUPP-1556", vendor="ori")` |
| Create service request | `create_vendor_service_request(summary="...", description="...", vendor="hypertec")` |
| Add comment | `add_vendor_comment(ticket_id="SUPP-1556", comment="...", vendor="ori")` |
| Update ticket status | `update_vendor_ticket_status(ticket_id="SUPP-1556", status="resolved", vendor="ori")` |
| Create triage ticket | `create_rtb_triage_ticket(device_name="us-south-3a-r07-06", issue_summary="...", assignee="user@together.ai")` |
| List triage tickets | `list_rtb_triage_tickets(status="open", limit=20)` |
| Silence alert | `silence_alert(instance="host.cloud.together.ai:.*", alert_name="GPUFellOffTheBus")` |
| Search KB | `search_vendor_kb(query="power distribution", vendor="iren")` |
| Get KB article | `get_vendor_kb_article(article_id="12345", vendor="iren")` |

## Common Workflows

### GPU Triage → Vendor Ticket → Silence

1. **Triage**: `dc-support-cli triage --device us-south-3a-r07-06 --summary "7/8 GPUs visible, bus 41:00.0 missing after reboot"`
2. **Vendor ticket**: `dc-support-cli create-service-request --vendor hypertec --summary "GPU Missing - tn1-c1-07-node06" --description "..."`
3. **Silence**: `dc-support-cli silence --instance "us-south-3a-r07-06.cloud.together.ai:.*" --comment "Triage SRE-1574 filed"`

### Check Ticket Status

```
dc-support-cli get-ticket HTCSR-3391 --vendor hypertec
dc-support-cli get-ticket SUPP-1556 --vendor ori --json
```

## Vendor-Specific Notes

### ORI Industries (Atlassian Service Desk)
- Ticket IDs: `SUPP-NNNN`
- Full CRUD: list, get, create, comment, resolve/close
- Browser-based ticket creation (~15-20s)
- Status updates use Jira Service Desk transitions API (resolved, closed)

### Hypertec / 5C (Atlassian Service Desk)
- Ticket IDs: `HTCSR-NNNN`
- Service requests via REST API
- Extra fields: support level (Critical/Normal/Question), reboot allowed (YES/NO)

### IREN (Freshdesk)
- Numeric ticket IDs
- Full CRUD: list, get, create ticket, add comment/note, resolve/close
- KB search and article retrieval supported
- REST API via Freshdesk API key (no browser auth needed for writes)

## Credentials

All from environment variables — no hardcoded secrets.

| Vendor | Env Vars |
|--------|----------|
| ORI | `ORI_PORTAL_USERNAME`, `ORI_PORTAL_PASSWORD` |
| Hypertec | `HYPERTEC_PORTAL_USERNAME`, `HYPERTEC_PORTAL_PASSWORD` |
| IREN | `IREN_PORTAL_USERNAME`, `IREN_PORTAL_PASSWORD` |
| RTB (triage) | `RTB_API_KEY` |
| Linear (triage list) | `LINEAR_API_KEY` |
| Alertmanager | `O11Y_GRAFANA_USERNAME`, `O11Y_GRAFANA_PASSWORD` |
| NetBox (fallback) | `NETBOX_TOKEN` |

Set only the vendor(s) you need. Handlers are lazily initialized on first use.

## Cross-Tool Workflow

- **NetBox** → get provider node name (`Provider_Machine_ID` custom field) + device details
- **Redfish** → hardware diagnostics before filing vendor ticket
- **PagerDuty** → correlate alerts with vendor tickets
- **UFM** → InfiniBand fabric issues before escalating to vendor

## Provider_Machine_ID Workflow

When triaging ORI vendor tickets, the vendor uses **provider node names** (e.g. `GPU-39`, `gpu068`) that differ from NetBox device names. To find the correct `--device` for triage:

1. Look up the provider name in NetBox's `Provider_Machine_ID` custom field
2. Use the NetBox device name (not the provider name) for `--device`
3. Use the provider name in vendor-facing ticket summaries/descriptions

Example mapping:

| Provider Name (ORI) | NetBox Device Name |
|---|---|
| gpu039 | `gpu039` |
| gpu068 | `research-common-h100-068` |
| dfw01-cpu-03 | `research-common-h100-hn1` |

## Key Gotchas

- Content is auto-sanitized (internal hostnames, Linear IDs, Slack links stripped) — but always prefer provider node names
- **Auth cooldown (all vendors):** A per-process 5-min cooldown prevents account lockout after a failed browser login. This cooldown is per-process only — it does not block other MCP/CLI processes. Rapid calls reuse existing cookies instead of re-authenticating.
- **Auth error format:** When auth fails, MCP tools return `{"error": "Auth failure for <vendor>: <details>", "remediation": "..."}`. If you see this, wait 5 minutes and retry, or run `dc-support-cli auth-status --vendor <vendor>` to check session state.
- `--json` flag on any CLI command for machine-readable output
- `--verbose` / `-V` (before the subcommand) enables auth/API diagnostics on stderr — useful when debugging 401s or cookie issues
- Use `auth-status` to check cookie age, expiry, and session validity before filing tickets
- Triage command requires `RTB_API_KEY`; silence requires `O11Y_GRAFANA_USERNAME`/`O11Y_GRAFANA_PASSWORD`
