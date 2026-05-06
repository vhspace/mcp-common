# Changelog

## Unreleased

### Added

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
