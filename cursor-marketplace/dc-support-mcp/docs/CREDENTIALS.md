# dc-support-mcp Credentials

Internal guide for Together engineers configuring `dc-support-mcp` /
`dc-support-cli`.

## TL;DR

- **Set only the secrets for the vendors / features you actually use.** Every
  var below is optional and resolved lazily — an unset var just disables that
  vendor or integration.
- **Any value may be a literal OR an `op://Vault/Item/field` 1Password
  reference.** References are auto-detected and resolved at runtime via the
  mcp-common credential chain, then cached in the Linux kernel keyring (no
  repeated Touch ID, no plaintext secret on disk). `op://` is the recommended
  source for laptops/devcontainers.
- **Check what's configured** (source metadata only — never the values):

  ```bash
  dc-support-cli vendors                 # vendors + internal-ops + source (env vs op://)
  dc-support-cli auth-status -v iren     # per-vendor session + credential source
  mcp-common-doctor                      # debug op:// / keyring resolution
  ```

## All variables (single source of truth)

Tiers: **Vendor** = external support portals; **Internal-ops** = Together
internal services (VPN-gated). The `op://` column shows the canonical 1Password
item in the shared **Together** vault (field name matches the env var). The AWS
column is the canonical value in AWS Secrets Manager (account `943412361556`,
`us-west-2`), which is also what the RTB service consumes via External Secrets.

| Env var | Tier | Used for | When needed | 1Password (`op://` item) | AWS Secret path |
|---------|------|----------|-------------|--------------------------|-----------------|
| `ORI_PORTAL_USERNAME` | Vendor | ORI Atlassian portal login (email) | Any ORI ticket op | `op://Together/ORI Portal/username` | — (personal login) |
| `ORI_PORTAL_PASSWORD` | Vendor | ORI Atlassian portal password | Any ORI ticket op | `op://Together/ORI Portal/password` | — (personal login) |
| `HYPERTEC_PORTAL_USERNAME` | Vendor | Hypertec/5C Atlassian portal login (email) | Any Hypertec ticket op | `op://Together/Hypertec Portal/username` | — (personal login) |
| `HYPERTEC_PORTAL_PASSWORD` | Vendor | Hypertec/5C Atlassian portal password | Any Hypertec ticket op | `op://Together/Hypertec Portal/password` | — (personal login) |
| `IREN_FRESHDESK_API_KEY` | Vendor | IREN Freshdesk REST API (**preferred** path) | IREN ticket ops via API | `op://Together/IREN Freshdesk/IREN_FRESHDESK_API_KEY` | `prod/rtb/iren-freshdesk` |
| `IREN_FRESHDESK_URL` | Vendor | IREN Freshdesk base URL (**non-secret**, default `https://iren.freshdesk.com`) | Only to override the default host | — | ConfigMap |
| `IREN_PORTAL_USERNAME` | Vendor | IREN portal login (email) — browser fallback | IREN ops when no API key | `op://Together/IREN Portal/username` | — (personal login) |
| `IREN_PORTAL_PASSWORD` | Vendor | IREN portal password — browser fallback | IREN ops when no API key | `op://Together/IREN Portal/password` | — (personal login) |
| `RTB_API_KEY` | Internal-ops | Repair Ticket Bridge — GPU triage tickets | `triage` / `create_rtb_triage_ticket` | `op://Together/RTB/RTB_API_KEY` | `prod/rtb/api-key` |
| `RTB_LINEAR_TEAM_KEY` | Internal-ops | Default Linear team key (**non-secret**, e.g. `SRE`) | Scope `triage-list` without `--team` | — | ConfigMap |
| `LINEAR_API_KEY` | Internal-ops | Linear GraphQL — triage list / assignment | `triage-list`, assignee fallback | `op://Together/Linear/LINEAR_API_KEY` | `prod/rtb/linear` |
| `O11Y_GRAFANA_USERNAME` | Internal-ops | Grafana Alertmanager proxy login | `silence` (alert silencing) | `op://Together/O11y Grafana/username` | `prod/rtb/o11y-grafana` |
| `O11Y_GRAFANA_PASSWORD` | Internal-ops | Grafana Alertmanager proxy password | `silence` (alert silencing) | `op://Together/O11y Grafana/password` | `prod/rtb/o11y-grafana` |
| `NETBOX_TOKEN` | Internal-ops | NetBox API — triage-status fallback patch | RTB NetBox fallback | `op://Together/NetBox/NETBOX_TOKEN` | `prod/rtb/netbox` |

> The exact 1Password vault/item names live in the shared **Together** vault;
> confirm the precise item path with the SRE team if a reference fails to
> resolve. The field name always matches the env var.

## Per-vendor setup

Each value can be a literal or an `op://` reference — substitute as you prefer.

### ORI Industries (Atlassian Service Desk)

Browser-authenticated portal (cookie-cached, ~13x speedup after first auth).

```bash
export ORI_PORTAL_USERNAME="you@together.ai"
export ORI_PORTAL_PASSWORD="op://Together/ORI Portal/password"
```

### Hypertec / 5C (Atlassian Service Desk)

Browser-authenticated portal. **REST ticket creation works** today via portal
form automation — no separate Jira API token is required for the supported
operations.

```bash
export HYPERTEC_PORTAL_USERNAME="you@together.ai"
export HYPERTEC_PORTAL_PASSWORD="op://Together/Hypertec Portal/password"
```

### IREN (Freshdesk)

The **Freshdesk REST API is the primary path** — it gives ISO timestamps,
requester names, conversation threads, pagination, and status filtering. The
API key authenticates via HTTP Basic auth (key as username, `"X"` as password).

```bash
# Preferred: REST API
export IREN_FRESHDESK_API_KEY="op://Together/IREN Freshdesk/IREN_FRESHDESK_API_KEY"
# Optional override (non-secret); defaults to https://iren.freshdesk.com
# export IREN_FRESHDESK_URL="https://iren.freshdesk.com"
```

> The portal host `support.iren.com` does **not** expose the REST API — the API
> lives at `iren.freshdesk.com`.

Browser fallback (used only when no API key is set): set `IREN_PORTAL_USERNAME`
/ `IREN_PORTAL_PASSWORD`. Browser mode has fewer features (human-readable dates,
no requester resolution, limited pagination).

`dc-support-cli vendors` reports IREN as `configured=yes` when **either** the
Freshdesk API key **or** the portal pair is present, and shows which mode is
active.

## Internal-ops (VPN-gated)

These integrations talk to Together-internal services and require the corporate
VPN. They power the GPU-triage workflow and alert silencing.

```bash
export RTB_API_KEY="op://Together/RTB/RTB_API_KEY"           # triage tickets
export LINEAR_API_KEY="op://Together/Linear/LINEAR_API_KEY"  # triage list / assign
export O11Y_GRAFANA_USERNAME="op://Together/O11y Grafana/username"
export O11Y_GRAFANA_PASSWORD="op://Together/O11y Grafana/password"
export NETBOX_TOKEN="op://Together/NetBox/NETBOX_TOKEN"       # triage-status fallback
# Optional, non-secret:
# export RTB_LINEAR_TEAM_KEY="SRE"
```

RTB itself consumes the same AWS Secrets Manager values (via External Secrets
Operator in the netbox-production EKS cluster), so a triage filed through RTB
can auto-create the downstream Linear/NetBox/vendor records.

## Secret sources

You can supply any secret three ways, in increasing order of preference:

1. **Literal value** — plain string in your shell/`.env`. Fine for quick local
   use; avoid committing it anywhere.
2. **`op://` 1Password reference** (recommended) — e.g.
   `op://Together/RTB/RTB_API_KEY`. Resolved at runtime via the `op` CLI and
   cached in the kernel keyring. One-time setup:
   [`credential-chain-setup.md`](../../../docs/credential-chain-setup.md) and,
   for devcontainers, [`DEVCONTAINER_1PASSWORD.md`](../../../DEVCONTAINER_1PASSWORD.md).
   (Both live in the mcp-common repo root — not duplicated here.)
3. **AWS Secrets Manager** — canonical stored values in account `943412361556`,
   region `us-west-2` (see the table above). Retrieve with a profile that has
   access:

   ```bash
   aws secretsmanager get-secret-value \
     --secret-id prod/rtb/iren-freshdesk --region us-west-2 \
     --query SecretString --output text
   ```

   You can paste the retrieved value as a literal, or (better) store it in the
   shared 1Password vault and reference it via `op://`.

## Security rules

- **Never commit secret values** (literals or resolved tokens) to git, configs,
  or PRs. Prefer `op://` references so plaintext never lands on disk.
- **Only source metadata is logged.** `dc-support-cli` and the resolver emit the
  *source* (`env` vs `op://`) and candidate name — never the secret value.
- **Agents receive tool results only**, never the environment or resolved
  secrets. Keep it that way: don't echo secret values into tool output.
- Use `mcp-common-doctor` (output is value-free and safe to paste to an agent)
  to debug resolution problems.
