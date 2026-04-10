---
name: awx-automation
description: Use when launching Ansible jobs, checking job status, triaging failures, managing AWX resources (templates, inventories, credentials, projects), running playbooks, or automating deployments via AWX / Automation Controller.
---

# AWX Automation

AWX / Ansible Automation Controller for job orchestration, playbook execution, and infrastructure automation.

## Choose Your Path

This plugin provides two interfaces. Prefer CLI when shell access is available — it uses ~90% fewer tokens.

| Path | When to Use |
|------|-------------|
| **CLI** (`awx-cli`) | Agent has shell access, token budget matters, compact output preferred |
| **MCP** (`awx_launch_and_wait`, etc.) | No shell access, sandboxed agent, need structured JSON schema validation |

## CLI Path

Requires `AWX_HOST` and `AWX_TOKEN` env vars. Run `awx-cli --help` for all commands.

| Task | Command |
|------|---------|
| List templates | `awx-cli templates` |
| Search templates | `awx-cli templates --search "deploy"` |
| List workflows | `awx-cli workflows` |
| List recent jobs | `awx-cli jobs --limit 10` |
| Failed jobs only | `awx-cli jobs --status failed --limit 10` |
| Job details | `awx-cli job 4353` |
| Job stdout | `awx-cli stdout 4353` |
| Job events | `awx-cli events 4353 --failed` |
| Events for host | `awx-cli events 4353 --host "hostname"` |
| Launch (fire-forget) | `awx-cli launch 174` |
| Launch and wait | `awx-cli launch 174 --wait --timeout 600` |
| Launch with vars | `awx-cli launch 174 -e '{"env":"prod"}' --limit "host1,host2"` |
| Launch workflow | `awx-cli launch 456 --workflow --wait` |
| Cancel a job | `awx-cli cancel 4353` |
| Relaunch a job | `awx-cli relaunch 4353 --hosts "failed-host1"` |
| List inventories | `awx-cli inventories` |
| Hosts in inventory | `awx-cli hosts 64` |
| List projects | `awx-cli projects` |
| List credentials | `awx-cli credentials` |
| Ping AWX | `awx-cli ping` |
| Current user | `awx-cli me` |
| Get any resource | `awx-cli get job_templates 174` |
| Survey spec | `awx-cli get job_templates 174 --property survey_spec` |
| Generic list | `awx-cli list jobs --filter "status=failed" --order "-created"` |
| JSON output | `awx-cli jobs --json` |
| Fewer fields | `awx-cli templates --fields "id,name,playbook"` |

If `awx-cli` is not on PATH, install with `uvx --from awx-mcp awx-cli` or run from the repo with `uv run awx-cli`.

## MCP Path

| Task | Tool Call |
|------|-----------|
| Launch and wait (preferred) | `awx_launch_and_wait("job_template", 174, extra_vars={"env": "prod"}, limit="host1")` |
| Launch fire-and-forget | `awx_launch("job_template", 174)` |
| Wait for running job | `awx_wait_for_job(job_id=4353, timeout_seconds=600)` |
| Job stdout | `awx_get_job_stdout(job_id=4353, format="txt")` |
| List templates | `awx_list_resources("job_templates", filters={"name__icontains": "deploy"}, fields=["id","name","playbook"])` |
| Failed job events | `awx_list_resources("job_events", filters={"failed": "true"}, parent_type="jobs", parent_id=4353)` |
| Cancel job | `awx_cancel_job(job_id=4353)` |
| Relaunch | `awx_relaunch_job(job_id=4353, hosts="failed-host1")` |
| Get resource | `awx_get_resource("job_templates", 174, property_path="survey_spec")` |
| Cluster health | `awx_get_cluster_status()` |
| System metrics | `awx_get_system_metrics()` |
| Ping | `awx_ping()` |

Always pass `fields` to reduce MCP response size (~80-90% token savings).

## Triage Failed Job

Best approach — use structured events, not raw stdout:

**CLI:**
```
awx-cli events 4353 --failed
awx-cli stdout 4353
```

**MCP:**
```
awx_list_resources("job_events", filters={"failed": "true"}, parent_type="jobs", parent_id=4353, page_size=10)
awx_get_job_stdout(job_id=4353, format="txt", limit_chars=20000)
```

## Cross-Tool Workflow

1. **NetBox** → resolve hostname, get FQDN
2. **AWX** → `awx-cli launch 174 --limit "hostname.cloud.together.ai" --wait` or `awx_launch_and_wait("job_template", 174, limit="hostname")`
3. **Redfish** → use oob_ip from NetBox for BMC operations

Hostnames from NetBox can be passed directly as `--limit` / `limit` parameters.

## Guided Prompts (MCP only)

- `triage_failed_job(job_id)` — step-by-step failure investigation
- `launch_deployment(template_name)` — find template, review survey, launch
- `check_cluster_health()` — ping, cluster status, metrics
- `investigate_host(hostname)` — cross-MCP: NetBox lookup then AWX investigation

## Key Gotchas

- **Use `awx_launch_and_wait` / `awx-cli launch --wait`** over manual launch+poll
- **Use job events for failure triage** — structured data is far more efficient than parsing stdout
- **Field selection saves tokens** — always pass `--fields` / `fields`
- **Filters use Django-style lookups** — `name__icontains`, `status`, `created__gt`, etc.
- **`parent_type`/`parent_id`** — required for nested resources (e.g. events under a job)
- **Transient errors retried** — 429, 502, 503, 504 are automatically retried with backoff
- **The server can be flaky** — if a tool call fails, retry once before giving up
