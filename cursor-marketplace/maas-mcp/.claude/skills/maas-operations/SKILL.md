---
name: maas-operations
description: Use when performing MAAS machine management, BMC credential sync, or config drift auditing via the MAAS MCP server
---

# MAAS Operations Skill

## When to Use

Use this skill when:
- Investigating machine health or configuration in MAAS
- Auditing configuration drift between machines
- Syncing BMC/Redfish credentials with MAAS
- Managing machine power state
- Looking up networking configuration (zones, fabrics, subnets, VLANs)

## Key Principles

1. **system_id over machine_id**: Always prefer `system_id` -- using `machine_id` requires an extra API call to resolve.
2. **Write safety**: All write operations require `allow_write=true`. Always confirm with the user before setting this.
3. **Field projection**: Use the `fields` parameter on list/get calls to reduce token usage.
4. **Multi-instance**: If multiple MAAS instances are configured, ask which one to use.

## Investigation Workflow

1. `maas_get_machine(system_id=...)` -- get full machine details
2. `maas_get_machine_interfaces(system_id=...)` -- check NIC config
3. `maas_get_machine_storage(system_id=...)` -- check storage layout
4. `maas_query_machine_power_state(system_id=...)` -- check power via BMC
5. `maas_list_events(hostname=...)` -- check recent events
6. Summarize anomalies

## Config Drift Workflow

1. `maas_audit_machine_config(system_id=..., baseline_system_id=...)` -- full comparison
2. Or use `maas_audit_nic_config` / `maas_audit_storage_config` for targeted audits
3. Report differences between baseline and target

## BMC Credential Sync Workflow

1. `maas_get_machine_power_parameters(system_id=..., include_secrets=true)` -- read current config
2. `maas_list_bmc_accounts_redfish(bmc_host=..., ...)` -- verify BMC account
3. `maas_set_bmc_account_password_from_maas(system_id=..., new_password=..., allow_write=true)` -- sync password
4. Confirm `redfish_login_verified: true` in response

## Generating an API Key from Username/Password

When only username/password credentials are available (no API key), use the MAAS session
login API to bootstrap access. Common for provider-managed instances (e.g., APLD2).

### CLI Command

```bash
maas-cli create-token \
  --url "${MAAS_URL}" \
  --username "${USERNAME}" \
  --password "${PASSWORD}" \
  --name "agent-token" \
  --json
```

### Flow

1. GET `/MAAS/accounts/login/` to obtain CSRF cookie
2. POST to `/MAAS/accounts/login/` with `username`, `password`, and `csrfmiddlewaretoken` form data + CSRF headers → session cookie
3. POST to `/MAAS/api/2.0/account/?op=create_authorisation_token` with session cookie and refreshed CSRF token
4. Response contains `consumer_key`, `token_key`, `token_secret` → format as `consumer_key:token_key:token_secret`

### Manual Steps (curl)

```bash
# Step 0: Get CSRF token
CSRF=$(curl -sk -c cookies.txt "${MAAS_URL}/accounts/login/" | grep -o 'csrftoken=[^;]*' || true)
CSRF_TOKEN=$(cat cookies.txt | grep csrftoken | awk '{print $NF}')

# Step 1: Login
curl -sk -b cookies.txt -c cookies.txt \
  -H "X-CSRFToken: ${CSRF_TOKEN}" \
  -H "Referer: ${MAAS_URL}/accounts/login/" \
  -d "username=${USERNAME}&password=${PASSWORD}&csrfmiddlewaretoken=${CSRF_TOKEN}" \
  "${MAAS_URL}/accounts/login/"

# Step 2: Create token
CSRF_TOKEN=$(cat cookies.txt | grep csrftoken | awk '{print $NF}')
curl -sk -b cookies.txt \
  -H "X-CSRFToken: ${CSRF_TOKEN}" \
  -H "Referer: ${MAAS_URL}/api/2.0/account/" \
  -d "name=agent-token" \
  "${MAAS_URL}/api/2.0/account/?op=create_authorisation_token"
```

### Agent Bootstrapping

When a site has only username/password credentials:
1. Run `maas-cli create-token` to generate an API key
2. Use the generated key for all subsequent `maas-cli` operations
3. Optionally persist to `.env` as `MAAS_{SITE}_API_KEY`

## Cross-Reference

- Use **NetBox MCP** for device lookups, OOB IP addresses
- Use **Redfish MCP** for direct BMC operations (BIOS, firmware, boot)
- This MAAS MCP handles machine lifecycle and credential management
