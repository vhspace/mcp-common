# Vendor Credentials Guide

This document explains how to configure credentials for each vendor portal.

## Credential Sources

Vendor API keys are stored in **AWS Secrets Manager** in the netbox-production
EKS account (`943412361556`, `us-west-2`). The RTB (Repair Ticket Bridge)
service uses the same keys via External Secrets Operator.

To retrieve a secret:

```bash
# You need a profile with access to account 943412361556
aws secretsmanager get-secret-value \
  --secret-id <secret-path> --region us-west-2 \
  --query SecretString --output text
```

## IREN (Freshdesk)

IREN uses Freshdesk for support tickets. Two auth modes are supported:

### REST API (preferred)

Uses a Freshdesk API key for direct API access. Provides ISO timestamps,
requester names, conversation threads, pagination, and status filtering.

| Env var | Description | AWS Secret |
|---------|-------------|------------|
| `IREN_FRESHDESK_API_KEY` | Freshdesk API key | `prod/rtb/iren-freshdesk` |
| `IREN_FRESHDESK_URL` | API base URL (default: `https://iren.freshdesk.com`) | ConfigMap |

The API key authenticates via HTTP Basic auth (key as username, `"X"` as password).

**Important**: The portal URL `support.iren.com` does NOT expose the Freshdesk
REST API. The API lives at `iren.freshdesk.com`.

### Browser fallback

When API credentials are not available, the handler falls back to Playwright
browser scraping using portal login credentials:

| Env var | Description |
|---------|-------------|
| `IREN_PORTAL_USERNAME` | Portal login email |
| `IREN_PORTAL_PASSWORD` | Portal login password (NOT a Freshdesk API key) |

Browser mode has limitations: human-readable dates instead of ISO timestamps,
no requester name resolution, and limited pagination.

### Setup

```bash
# Preferred: API key (add to .env or shell profile)
export IREN_FRESHDESK_API_KEY="your-freshdesk-api-key"
export IREN_FRESHDESK_URL="https://iren.freshdesk.com"  # optional, this is the default

# Fallback: portal credentials (for browser scraping)
export IREN_PORTAL_USERNAME="your-email@together.ai"
export IREN_PORTAL_PASSWORD="your-portal-password"
```

## Hypertec / 5C (Jira Service Desk)

Hypertec uses Atlassian Jira Service Management at `hypertec-cloud.atlassian.net`.

### REST API (preferred — not yet implemented, see issue #64)

| Env var | Description | AWS Secret |
|---------|-------------|------------|
| `HYPERTEC_JIRA_EMAIL` | Jira service account email | `prod/rtb/5C-jira` |
| `HYPERTEC_JIRA_API_TOKEN` | Atlassian API token | `prod/rtb/5C-jira` |

### Browser fallback (current)

| Env var | Description |
|---------|-------------|
| `HYPERTEC_PORTAL_USERNAME` | Atlassian portal email |
| `HYPERTEC_PORTAL_PASSWORD` | Atlassian portal password |

### Setup

```bash
export HYPERTEC_PORTAL_USERNAME="your-email@together.ai"
export HYPERTEC_PORTAL_PASSWORD="your-portal-password"
```

## ORI (Atlassian Service Desk)

ORI uses Atlassian Service Desk with browser-based authentication.

| Env var | Description |
|---------|-------------|
| `ORI_PORTAL_USERNAME` | Atlassian portal email |
| `ORI_PORTAL_PASSWORD` | Atlassian portal password |

### Setup

```bash
export ORI_PORTAL_USERNAME="your-email@together.ai"
export ORI_PORTAL_PASSWORD="your-portal-password"
```

## RTB Integration

The [Repair Ticket Bridge](https://rtb.together.ai) (RTB) service automatically
creates vendor tickets when you file repair/node-outage tickets. For IREN nodes,
filing a repair ticket through RTB will auto-create an IREN Freshdesk ticket —
no need for a separate `dc-support-cli create-ticket --vendor iren` call.

RTB uses the same AWS Secrets Manager keys listed above, deployed via External
Secrets Operator in the netbox-production EKS cluster.

## AWS Secret Paths

| Secret path | Contents | Used by |
|-------------|----------|---------|
| `prod/rtb/iren-freshdesk` | `IREN_FRESHDESK_API_KEY` | RTB, dc-support-mcp |
| `prod/rtb/5C-jira` | `5C_JIRA_EMAIL`, `5C_JIRA_API_TOKEN` | RTB, dc-support-mcp |
| `prod/rtb/netbox` | `NETBOX_TOKEN` | RTB |
| `prod/rtb/linear` | `LINEAR_API_KEY`, `LINEAR_TEAM_ID` | RTB |

All secrets are in account `943412361556` (netbox-production), region `us-west-2`.
