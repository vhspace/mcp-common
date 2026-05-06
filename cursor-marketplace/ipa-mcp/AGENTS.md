# ipa-mcp

FreeIPA MCP server for user/host group management, HBAC, and sudo rules

## CLI: `ipa-cli`

Run `ipa-cli --help` for all commands.
Install: `uvx --from git+https://github.com/vhspace/ipa-mcp@v1.1.1 ipa-cli`

## MCP Server

```bash
uvx --from git+https://github.com/vhspace/ipa-mcp@v1.1.1 ipa-mcp
```

### Required env vars

- `IPA_HOST`: ${IPA_HOST}
- `IPA_USERNAME`: ${IPA_USERNAME}
- `IPA_PASSWORD`: ${IPA_PASSWORD}
- `IPA_VERIFY_SSL`: ${IPA_VERIFY_SSL:-false}
