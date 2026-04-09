# Private Claude Marketplace Migration

This guide rolls out `mcp-plugin-gen` private marketplace artifacts across MCP repos.

## What changed in `mcp-common`

- `mcp-plugin.toml` supports optional `[marketplace]` metadata.
- `mcp-plugin-gen generate .` now also emits `.claude-plugin/registry-entry.json`.
- `mcp-plugin-gen registry-entry <repo_root>` emits only the registry entry.
- `mcp-plugin-gen aggregate-marketplace <entries_dir> <output_file>` creates a deterministic aggregated marketplace file.
- `mcp-plugin-gen check .` now treats `.claude-plugin/registry-entry.json` as a generated artifact.

## Template checklist (apply in every MCP repo)

- [ ] Upgrade `mcp-common` to a release that includes private marketplace support.
- [ ] Repin `.pre-commit-config.yaml` `mcp-plugin-gen` hook to that release.
- [ ] Add optional `[marketplace]` section to `mcp-plugin.toml` if you need tags/categories:

```toml
[marketplace]
categories = ["infrastructure", "operations"]
tags = ["mcp", "private", "claude"]
```

- [ ] Run generation:

```bash
uv run mcp-plugin-gen generate .
```

- [ ] Verify `.claude-plugin/registry-entry.json` exists and is committed.
- [ ] Verify sync checks pass:

```bash
uv run mcp-plugin-gen check .
```

- [ ] Validate deterministic aggregation in CI or release tooling:

```bash
uv run mcp-plugin-gen aggregate-marketplace ./.claude-plugin ./dist/marketplace.json
```

## Downstream rollout checklist (existing MCP repos)

- [ ] `awx-mcp`
- [ ] `dc-support-mcp`
- [ ] `gpu-diag-mcp`
- [ ] `ipa-mcp`
- [ ] `maas-mcp`
- [ ] `netbox-mcp`
- [ ] `redfish-mcp`
- [ ] `ufm-mcp`
- [ ] `weka-mcp`
