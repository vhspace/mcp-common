---
name: prefer-cli-over-mcp
description: >-
  Prefer CLI tools over MCP tool calls when both are available. Use when
  interacting with MAAS, NetBox, Redfish, AWX, or any infrastructure service
  that has both a CLI wrapper and an MCP server. Triggers on any MCP tool
  usage for infrastructure operations.
---

# Prefer CLI Over MCP

## Rule

When an infrastructure service has both a CLI tool and an MCP server, **choose the right tool for the job**. CLI tools are preferred for shell pipelines, scripting, and grep-friendly output. For dual-mode migrated MCPs, CLI and MCP return identical data with no token-cost gap.

## Choose Your Path

| Scenario | CLI | MCP | Why |
|---------|-----|-----|-----|
| Shell pipelines and scripting | ✅ Preferred | ❌ Avoid | Natural in bash, easy to loop, no approval prompts |
| Grep-friendly output | ✅ Preferred | ❌ Avoid | Human-readable text, easy to parse with standard tools |
| Interactive exploration | ✅ Good | ✅ Good | Both work well, personal preference |
| Agent workflows (non-interactive) | ✅ Good | ✅ Good | Both return identical data for dual-mode tools |
| Binary data or streaming | ❌ Avoid | ✅ Preferred | CLI can't render images/blobs |

## Dual-Mode Reality

Many vhspace MCPs now use the **dual-mode framework** ([#86](https://github.com/vhspace/mcp-common/issues/86)), where one function definition becomes both a FastMCP tool and a Typer CLI command. For these migrated tools, CLI and MCP return **identical data** with **no token-cost difference**.

### Migrated Services (CLI ≈ MCP)

| Service | CLI | MCP | Status |
|---------|-----|-----|--------|
| NetBox (partial) | `netbox-cli` | `project-0-together-netbox-mcp` | Dual-mode (see [PR #104](https://github.com/vhspace/netbox-mcp/pull/104)) |
| MAAS (planned) | `maas-cli` | `project-0-together-maas-mcp` | Migration planned |
| Redfish (planned) | `redfish-cli` | `project-0-together-redfish-mcp` | Migration planned |

### Legacy Services (CLI > MCP)

| Service | CLI Advantage | MCP Use Case |
|---------|---------------|--------------|
| IPA | Shell access, local config | Sandboxed agents |
| AWX | N/A | Only option (no CLI) |
| UFM | N/A | Only option (no CLI) |

> **Note:** For non-migrated MCPs, CLI calls are still ~90% smaller in tokens than equivalent MCP calls. This gap disappears for dual-mode migrated tools.

## When CLI Is Still Preferred

1. **Shell pipelines and batch operations** - CLI tools integrate naturally with bash:
   ```bash
   # Natural with CLI
   for host in $(cat hosts.txt); do
     netbox-cli lookup-device "$host" --json | jq '.primary_ip'
   done
   ```

2. **Grep-friendly output** - Human-readable text is easier to parse:
   ```bash
   # Easy to filter with CLI
   netbox-cli list-devices --site dfw01 | grep -E "(gpu|cpu)" | awk '{print $1}'
   ```

3. **No approval prompts** - CLI tools run instantly without confirmation:
   ```bash
   # Instant with CLI
   maas-cli list-machines --status ready | wc -l
   ```

## When MCP Makes Sense

1. **Structured data workflows** - When you need to process JSON in code:
   ```python
   # MCP returns structured data directly
   result = await ctx.call_tool("netbox_lookup_device", {"hostname": "sw01"})
   ip = result["primary_ip"]
   ```

2. **Binary data or streaming** - When CLI can't render the output:
   ```python
   # MCP can stream binary data
   screenshot = await ctx.call_tool("redfish_capture_screenshot", {"host": "bmc01"})
   ```

## CLI Discovery

Before using an MCP tool, check if a CLI exists:

```bash
which maas-cli netbox-cli redfish-cli ipa-cli 2>/dev/null
<tool>-cli --help
```

## Batching with CLI

For operations on many nodes, write a Python or shell loop:

```python
for sid in system_ids:
    r = subprocess.run(
        ["maas-cli", "op", sid, "deploy", "-s", "central", "--yes", "--json"],
        capture_output=True, text=True, timeout=60
    )
```

Set env vars first: `export MAAS_CENTRAL_URL="$CENTRAL_MAAS_URL" MAAS_CENTRAL_API_KEY="$CENTRAL_MAAS_API"`

## When MCP Is Required

Use MCP when the CLI doesn't support the operation:
- MAAS interface link/unlink subnet (no CLI command)
- MAAS get machine with `include_secrets=true` (passwords)
- Redfish screenshots and write operations (CLI is read-only)
- All AWX operations (no CLI available)

**IMPORTANT:** Never use the native `ipa` command. Always use `ipa-cli` — it auto-loads credentials and normalizes output. The native `ipa` requires Kerberos `kinit` and is only available on IPA-enrolled hosts.