# MCP Client Timeout Reference

MCP servers built with mcp-common may be called from multiple AI agent
runtimes. Each runtime imposes its own tool-call timeout. Server authors
should be aware of these limits when designing long-running tools.

## Timeout Matrix (as of May 2026)

| Runtime | Default Timeout | Configurable? | Config Location |
|---------|----------------|---------------|-----------------|
| Cursor 3.2.16+ | None observed (tested to 300s) | N/A | N/A |
| Claude Code 2.1+ | None observed (tested to 300s) | Per-hook (default 600s) | CLAUDE.md hooks |
| OpenCode 1.14+ | **30 seconds** | Yes | `opencode.json` → `mcp.<server>.timeout` (ms) |
| OpenHands 1.15+ | **300 seconds** | Not documented | N/A |

## Recommendations for MCP Server Authors

### Tools that may exceed 30 seconds

If your MCP tool can block for more than 30 seconds (e.g., launching and
waiting for an Ansible job), you should:

1. **Document the expected duration** in the tool description so agents
   can set appropriate expectations.

2. **Generate platform configs with appropriate timeouts.** When using
   `mcp-plugin-gen`, set `timeout_ms` in the `[server]` section of
   `mcp-plugin.toml` so generated `opencode.json` includes the
   `"timeout"` field for long-running servers.

3. **Use `poll_with_progress`** from `mcp_common.progress` for polling
   operations. It handles:
   - Safe progress notifications (won't crash on transport failure)
   - Hard timeout guarantee (always returns, never hangs)
   - Wall-clock elapsed tracking

4. **Consider a fire-and-forget pattern** for operations over 5 minutes:
   return a job ID immediately and provide a separate status-check tool.

### OpenCode timeout configuration

For MCP servers that need more than 30s per tool call, the generated
`opencode.json` should include a timeout override:

```json
{
  "mcp": {
    "my-server": {
      "type": "local",
      "command": ["uvx", "--from", "my-server", "my-server"],
      "timeout": 600000
    }
  }
}
```

The value is in milliseconds. 600000 = 10 minutes.

To have `mcp-plugin-gen` produce this automatically, add to
`mcp-plugin.toml`:

```toml
[server]
command = "uvx"
args = ["--from", "my-server", "my-server"]
timeout_ms = 600000
```

### Future: SEP-1686 Tasks

The MCP specification has accepted SEP-1686 (Tasks), which provides a
protocol-level "fire and check back" pattern. FastMCP 2.14+ implements
it via `@mcp.tool(task=True)`. When all major runtimes support Tasks,
long-running tools should migrate to this pattern.
