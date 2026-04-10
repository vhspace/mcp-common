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

## Tips
- MAAS uses OAuth1 authentication
- Machine system_ids are the primary identifiers
- Power parameters contain BMC connection details
