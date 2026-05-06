# CHANGELOG

## v1.0.0 (2026-03-02)

### Features

- **MCP draft standard features**: MCP client logging, structured content with output schemas, parameterized resource templates, elicitation confirmations for write operations, and argument completions for prompts
- **14 MCP tools** for MAAS machine management, BMC/Redfish operations, networking, and config drift auditing
- **Multi-instance support**: Manage multiple MAAS servers from a single MCP server
- **BMC credential sync**: Full workflow for syncing passwords between MAAS and Redfish BMCs
- **Config drift auditing**: Compare NIC, storage, and BIOS configurations across machines
- **3 MCP prompts**: `investigate_machine`, `audit_drift`, `sync_bmc_credentials`
- **Resource templates**: Browse machines and events as MCP resources (`maas://{instance}/machines`, `maas://{instance}/machine/{system_id}`)
- **Safety controls**: All write operations gated behind `allow_write=true` with `destructiveHint` annotations
- **Dual transport**: stdio for local use, Streamable HTTP for remote/K8s deployment with Bearer token auth
- **Kubernetes manifests**: Ready-to-deploy K8s configuration in `deploy/k8s/`

### Infrastructure

- Integrated `mcp-common` shared utilities for testing
- Added `python-semantic-release` for automated versioning
- CI pipeline with lint, typecheck, and test stages
- Docker support with health checks

---

## v0.1.0 (2026-02-04)

- Initial development release
