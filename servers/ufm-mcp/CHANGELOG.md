# Changelog

All notable changes to `ufm-mcp` are documented here.

## [Unreleased]

### Changed

- **Migrated into the `togethercomputer/mcp-common` monorepo** (`servers/ufm-mcp`).
  - Adopted the shared dual-mode framework: every MCP tool is registered with
    `@dual_mode_tool` and the `ufm-cli` app is built via
    `build_cli_from_mcp(...)`. All tool names, CLI commands/flags, multi-site
    (`site=`) support, the `ufm-mcp` / `ufm-cli` entry points, and the `ufm-mcp`
    distribution name are preserved.
  - Repointed `mcp-common` at `togethercomputer/mcp-common`
    (`tag = "mcp-common-v0.38.0"`) and the agent-remediation / plugin metadata at
    the monorepo repository + `servers/ufm-mcp` subdirectory.

Prior release history lived in the standalone `vhspace/ufm-mcp` repository.
