---
name: maas-operations
description: Use when managing bare-metal servers via MAAS, checking machine status, commissioning nodes, or investigating provisioning issues. Triggers on mentions of MAAS, bare-metal, commissioning, deploying machines, or machine lifecycle.
---

# MAAS Operations

**IMPORTANT:** The CLI wrapper auto-sources `.env` for credentials. Never manually `source`, `export`, or `grep` env vars — just run the command directly.

**Discover flags:** Not all commands support the same options. Run `maas-cli <command> --help` to see available flags before using them.

## Common Workflows

### Check Machine Status
1. List machines and filter by status
2. Get detailed machine info including power state
3. Check commissioning/deployment results

### Machine Lifecycle
1. Commission -> Test -> Deploy -> Release
2. Each transition has corresponding MAAS API calls

### Multi-Instance Support
This server supports multiple MAAS instances. Specify the instance when querying.

## Generating an API Key from Username/Password

When only username/password credentials are available (no API key), you can generate one
using the MAAS session login API. This is common for provider-managed MAAS instances
(e.g., APLD2) where admin API key provisioning isn't available.

### Prerequisites

Set these environment variables (or have them in `.env`):
- `MAAS_{SITE}_URL` — e.g., `http://maas-host:5240/MAAS`
- `MAAS_{SITE}_USERNAME`
- `MAAS_{SITE}_PASSWORD`

### CLI Command

```bash
maas-cli create-token \
  --url "${MAAS_URL}" \
  --username "${USERNAME}" \
  --password "${PASSWORD}" \
  --name "agent-token" \
  --json
```

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

The resulting `consumer_key:token_key:token_secret` string is the API key for `maas-cli`.

### Agent Bootstrapping

When an agent encounters a MAAS site with only username/password credentials:
1. Run `maas-cli create-token` to generate an API key
2. Use the generated key for all subsequent `maas-cli` operations
3. Optionally persist the key to `.env` as `MAAS_{SITE}_API_KEY`

## Tips
- MAAS uses OAuth1 authentication
- Machine system_ids are the primary identifiers
- Power parameters contain BMC connection details
