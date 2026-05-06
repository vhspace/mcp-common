# CHANGELOG

## v2.5.1 (2026-03-14)

### Fixes

- CLI `info --types` now supports all 12 info_types (power, thermal, processors, memory, pcie_devices, manager, manager_ethernet)
- CLI `screenshot --method dell` now supported
- Unknown `info_types` return clear error with valid list (previously silently ignored)
- CLI `query` error for MCP-only types hints agent to use `redfish_query` MCP tool
- Stripped `sources` debug arrays from `redfish_get_info` responses (token reduction)
- Default `include_setter_info=False` in `redfish_query` (saves ~200 tokens per call)

## v2.5.0 (2026-03-14)

### Features

- **Hardware inventory modules:** Power/PSU, thermal sensors, processors, memory DIMMs, PCIe devices (GPUs/NICs/NVMe), BMC manager info, BMC network config — works across all 9 fleet vendors (Supermicro, Dell, Lenovo, ASRockRack, CIARA, Inspur, 5C/Hypertec, Iren, Ori Cloud)
- **Tool consolidation (27 → 19):** Extended `redfish_get_info` with 7 new `info_types` (power, thermal, processors, memory, pcie_devices, manager, manager_ethernet). Moved `bmc_log_services` into `redfish_query`. Saves ~2,000 tokens per agent context window.
- **Dell iDRAC screenshot:** New `capture_screen_dell` method via sysmgmt preview API, auto-detected alongside Supermicro methods
- **Screen watch mode:** `redfish_watch_screen` MCP tool and `redfish-cli watch` CLI command for polling screenshots with OCR change detection
- **5 new CLI commands:** `power`, `processors`, `memory`, `pcie`, `manager`, `watch`
- **Live integration tests:** 13 tests validated against real Supermicro BMC at ORI-TX

### Improvements

- Extracted `try_capture()` helper to DRY screenshot fallback logic across MCP/CLI
- Compact absent DIMM entries (3 fields vs 17) for token reduction
- Fixed `clear_bmc_log` Manager path fallback for non-Dell BMCs
- PCIe `by_type` uses device IDs instead of duplicating full entries
- Updated SKILL.md with consolidated tool table and info_types reference

## v2.0.0 (2026-03-02)

### Breaking Changes

- **mcp-common dependency:** Now requires `mcp-common` shared library for logging, health, progress, and version utilities.
- **Stale docs removed:** 16 root and docs/ markdown files removed (duplicates, implementation notes). See `docs/DOCUMENTATION_INDEX.md` for current docs.
- **CHANGELOG managed by semantic-release:** Future versions auto-generated from conventional commits.

### Features

- **MCP Logging:** Tool calls now emit real-time `ctx.info()` / `ctx.warning()` messages to the MCP client (Cursor, Claude) via the logging protocol. Visible during long-running operations.
- **Progress Notifications:** BIOS diff reports progress as it fetches from each host. `ctx.report_progress()` wired through the FastMCP Context.
- **Completions / Autocomplete:** `completion/complete` handler registered for hardware DB resource URI templates (`vendor`, `model`) and prompt arguments (`host`, `host_a`, `host_b`). Autocompletes from hardware DB files and recent hosts in the state store.
- **Health Resource:** New `redfish://health` resource returns server health, version, and state store status.
- **Semantic Release:** `python-semantic-release` configured with `release.yml` workflow. Version tracked in `pyproject.toml`, `__init__.py`, `.cursor-plugin/plugin.json`, and `.claude-plugin/plugin.json`.

### Improvements

- Integrated `mcp-common` for `setup_logging()`, `get_version()`, and `health_resource()`.
- CI hardened: pinned action SHAs, added `mypy` and `uv build` steps.
- README modernized with MCP protocol badge, Claude Code quick start, and cleaner structure.
- `AgentStateStore.recent_hosts()` method for completion support.

## v1.3.0 (2026-02-03)

### Features

- `redfish_get_firmware_inventory` -- complete firmware inventory from UpdateService.
- `redfish_get_vendor_errata` -- security bulletin and CVE tracking.
- `redfish_check_bios_online` -- real-time BIOS version checking via Tavily.
- `firmware_inventory.py` and `firmware_checker.py` modules.

## v1.2.0 (2026-02-03)

### Features

- Hardware database migrated to JSON files (`hardware_db/` directory).
- JSON Schema validation, git-friendly version control for hardware data.

## v1.1.0 (2026-02-03)

### Features

- `redfish_get_hardware_docs` -- hardware documentation with dual-layer caching.
- Migrated from pip to uv package manager.
- Upgraded to Python 3.13.

## v1.0.0 (2026-02-03)

### Breaking Changes

- Tool consolidation: 13 to 8 tools (removed 5 deprecated tools).
- `helpers.py` module with `ResponseBuilder`, `CurlCommandBuilder`, `SystemFetcher`.

## v0.1.0 (2026-02-03)

### Features

- Initial release with 9 tools (4 read, 2 write, 3 job management).
- Safe-by-default write model, async job system, curl rendering mode.
