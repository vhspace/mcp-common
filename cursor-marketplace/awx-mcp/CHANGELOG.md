# Changelog

## Unreleased

### Added

- **Dual-mode CLI (#89)**: Ten read-only `awx-cli` commands (`ping`, `me`,
  `supported-resources`, `system-metrics`, `system-info`, `cluster-status`,
  `wait-for-job`, `workflow-visualization`, `debug-jt-credentials`,
  `aws-credentials`) are now **synthesized** from the FastMCP tools via
  `mcp_common.dual_mode` (`@dual_mode_tool` + `build_cli_from_mcp`), instead of
  being hand-written — a single source of truth for arguments, types, and help.
  Second pilot of the framework after netbox-mcp.

- **`awx_parse_job_log` MCP tool**: Parses Ansible job stdout into structured data — plays, failures, warnings, PLAY RECAP, per-host stats. Much faster for triage than reading raw stdout.
- **`log-summary` CLI command**: `awx-cli log-summary <job_id>` — structured summary with `--sections` filter and `--json` output.
- **`log_parser` module** (`awx_mcp.log_parser`): Reusable Ansible log parsing with `parse_ansible_log()`, `extract_recap()`, `extract_failures()`, `extract_warnings()`, and `smart_truncate()`.
- **Smart truncation strategies** for `awx_get_job_stdout` (MCP) and `awx-cli stdout` (CLI):
  - `tail` (new default) — last N chars, best for seeing failures and PLAY RECAP
  - `head` — first N chars (previous default behavior)
  - `head_tail` — first 25% + last 75%, see beginning and end
  - `recap_context` — PLAY RECAP section with surrounding context
- **CLI `stdout` improvements**: `--start-line`, `--end-line`, and `--truncation` flags

### Changed

- `awx_get_job_stdout` default truncation changed from `head` to `tail` — failures and PLAY RECAP at end of logs are now shown by default
- `awx_get_job_stdout` response now includes `truncation_strategy` and `original_length` fields
- **`awx-cli` rebuilt on `build_cli_from_mcp`** (#89): the AWX client is now
  initialized via a `before_command` hook (skipped on `--help`), and the
  ~80-line hand-rolled `_poll_until_terminal` was replaced by
  `_wait_for_terminal`, a thin wrapper over `mcp_common.cli.poll_until`. The
  `launch --wait`, `project-update --wait`, and `inventory-sync --wait` flows
  now share that one helper (three duplicated inline poll loops removed). The
  hand-written `ping`/`me` commands were removed (now synthesized). Bumped
  `mcp-common` to `v0.24.0`.

### Fixed

- `require_awx_client` now preserves coroutine functions, so async tools
  (`cluster-status`, `system-info`, `wait-for-job`) drive correctly under both
  FastMCP and the synthesized CLI (the sync wrapper previously returned an
  un-awaited coroutine).

## 0.2.0

### Added

- **MCP Prompts**: 4 guided workflow prompts (`triage_failed_job`, `launch_deployment`, `check_cluster_health`, `investigate_host`)
- **MCP Resources**: Static `awx://resource-capabilities`, health check at `health://awx`, job status template at `awx://jobs/{job_id}`
- **Resource notifications**: `notifications/resources/updated` sent during job polling for subscription-based monitoring
- **MCP logging/progress**: Long-running tools (`awx_launch_and_wait`, `awx_wait_for_job`) now report progress via MCP `notifications/progress`
- **Context logging**: `awx_get_system_info` and `awx_get_cluster_status` send `notifications/message` with progress updates
- **mcp-common integration**: Shared utilities for progress polling, health checks, version introspection, and structured logging
- **Release workflow**: GitHub Actions CI/CD for PyPI publishing on tag push

### Changed

- `awx_launch_and_wait`, `awx_wait_for_job`, `awx_get_system_info`, `awx_get_cluster_status` converted to `async def` with `Context` parameter
- Poll loops replaced with `mcp_common.poll_with_progress` (DRY)
- Logging uses `mcp_common.setup_logging` instead of custom `configure_logging`
- Version introspection uses `mcp_common.get_version`

### Removed

- `CONSOLIDATION_ANALYSIS.md` (historical, moved to git history)
- `MCP_CONFIG.md` (content folded into README)

## 0.1.0

Initial release with 33 tools for AWX/Automation Controller management.
