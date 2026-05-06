---
name: awx-automation
description: Use when launching Ansible jobs, checking job status, triaging failures, managing AWX resources (templates, inventories, credentials, projects), running playbooks, or automating deployments via AWX / Automation Controller.
---

# AWX Automation

Prefer CLI when shell access is available — ~90% fewer tokens.

| Path | When to Use |
|------|-------------|
| **CLI** (`awx-cli`) | Shell access available, token budget matters |
| **MCP** (`awx_launch_and_wait`, etc.) | No shell, sandboxed agent, need JSON schema validation |

## CLI Path

**IMPORTANT:** The CLI wrapper auto-sources `.env` for credentials. Never manually `source`, `export`, or `grep` env vars — just run the command directly.

**Discover flags:** Run `awx-cli <command> --help` — not all commands support the same options.

| Task | Command |
|------|---------|
| List templates | `awx-cli templates` |
| Search templates | `awx-cli templates --search "deploy"` |
| List workflows | `awx-cli workflows` |
| Recent jobs | `awx-cli jobs --limit 10` |
| Failed jobs | `awx-cli jobs --status failed --limit 10` |
| Jobs for template (by ID) | `awx-cli jobs --template 173` |
| Jobs for template (by name) | `awx-cli jobs --template-name "k8s"` |
| Job details | `awx-cli job <JOB_ID>` |
| Job stdout | `awx-cli stdout <JOB_ID>` |
| Stdout errors only | `awx-cli stdout <JOB_ID> --filter errors` |
| Stdout for host | `awx-cli stdout <JOB_ID> --host "gpu*"` |
| Stdout changed only | `awx-cli stdout <JOB_ID> --filter changed --host "node1"` |
| Stdout by play/task | `awx-cli stdout <JOB_ID> --play 1 --task "Configure *"` |
| Failed events | `awx-cli events <JOB_ID> --failed` |
| Events for host | `awx-cli events <JOB_ID> --host "hostname"` |
| Launch (fire-forget) | `awx-cli launch <TEMPLATE_ID>` |
| Launch and wait | `awx-cli launch <TEMPLATE_ID> --wait --timeout 600` |
| Launch with vars | `awx-cli launch <TEMPLATE_ID> -e '{"env":"prod"}' --limit "host1,host2"` |
| Launch workflow | `awx-cli launch <WORKFLOW_ID> --workflow --wait` |
| Cancel a job | `awx-cli cancel <JOB_ID>` |
| Relaunch a job | `awx-cli relaunch <JOB_ID> --hosts "failed-host1"` |
| List inventories | `awx-cli inventories` |
| Hosts in inventory | `awx-cli hosts <INVENTORY_ID>` |
| List projects | `awx-cli projects` |
| List credentials | `awx-cli credentials` |
| Ping AWX | `awx-cli ping` |
| Current user | `awx-cli me` |
| Get any resource | `awx-cli get job_templates <TEMPLATE_ID>` |
| Survey spec | `awx-cli get job_templates <TEMPLATE_ID> --property survey_spec` |
| Generic list | `awx-cli list jobs --filter "status=failed" --order "-created"` |
| JSON output | `awx-cli jobs --json` |
| Fewer fields | `awx-cli templates --fields "id,name,playbook"` |

If `awx-cli` is not on PATH, install with `uvx --from awx-mcp awx-cli` or run from the repo with `uv run awx-cli`.

## MCP Path

| Task | Tool Call |
|------|-----------|
| Launch and wait (preferred) | `awx_launch_and_wait("job_template", <ID>, extra_vars={...}, limit="host1")` |
| Launch fire-and-forget | `awx_launch("job_template", <ID>)` |
| Wait for running job | `awx_wait_for_job(job_id=<ID>, timeout_seconds=600)` |
| Job stdout | `awx_get_job_stdout(job_id=<ID>, format="txt")` |
| Stdout errors only | `awx_get_job_stdout(job_id=<ID>, filter="errors")` |
| Stdout for host | `awx_get_job_stdout(job_id=<ID>, host="gpu*")` |
| Stdout by play/task | `awx_get_job_stdout(job_id=<ID>, play="1", task_filter="Configure *")` |
| List templates | `awx_list_resources("job_templates", filters={"name__icontains": "deploy"}, fields=["id","name","playbook"])` |
| Failed job events | `awx_list_resources("job_events", filters={"failed": "true"}, parent_type="jobs", parent_id=<ID>)` |
| Cancel job | `awx_cancel_job(job_id=<ID>)` |
| Relaunch | `awx_relaunch_job(job_id=<ID>, hosts="failed-host1")` |
| Get resource | `awx_get_resource("job_templates", <ID>, property_path="survey_spec")` |
| Cluster health | `awx_get_cluster_status()` |
| Ping | `awx_ping()` |

Always pass `fields` to reduce MCP response size (~80-90% token savings).

## Triage Failed Job

Use structured events, not raw stdout:

**CLI:**
```
awx-cli events <JOB_ID> --failed
awx-cli stdout <JOB_ID> --filter errors
awx-cli stdout <JOB_ID> --filter errors --host "failing-host*"
```

**MCP:**
```
awx_list_resources("job_events", filters={"failed": "true"}, parent_type="jobs", parent_id=<JOB_ID>, page_size=10)
awx_get_job_stdout(job_id=<JOB_ID>, filter="errors")
awx_get_job_stdout(job_id=<JOB_ID>, filter="errors", host="failing-host*")
```

If the error is `Permission denied (publickey)`, the node likely needs bootstrapping — see **Freshly Deployed Nodes (Bootstrap)** below.

## Cross-Tool Workflow

1. **NetBox** → resolve hostname (returns FQDN)
2. **AWX** → use short hostname in `--limit` (strip `.cloud.together.ai`). Verify with `awx-cli hosts <INVENTORY_ID>` if unsure.
3. **Redfish** → use oob_ip from NetBox for BMC operations

## Freshly Deployed Nodes (Bootstrap)

MAAS-deployed machines only have the `ubuntu` user. AWX connects as the `ansible` user (credential id 3, `ansible-service-account`), so **AWX jobs will fail with `Permission denied (publickey)` on new nodes** until the `ansible` user is bootstrapped.

**Before launching any AWX job template against a freshly deployed node**, run `prep-awx-access.yaml` from a local ansible host:

```bash
cd infra/ansible
ansible-playbook -i "HOSTNAME," prep-awx-access.yaml
```

For multiple nodes or inventory-based targeting:

```bash
CLUSTER_NAME="XYZ" ansible-playbook prep-awx-access.yaml --limit "node1,node2"
```

The playbook automatically tries your personal SSH user first and falls back to `ubuntu` — no extra flags needed. It applies the `awx-ansible-user` role which creates the `ansible` account and installs the authorized_keys.

**Only proceed to AWX job templates (e.g. template 472, `prep-ori-gpu-node.yaml`) after this completes successfully.**

## Guided Prompts (MCP only)

- `triage_failed_job(job_id)` — step-by-step failure investigation
- `launch_deployment(template_name)` — find template, review survey, launch
- `check_cluster_health()` — ping, cluster status, metrics
- `investigate_host(hostname)` — cross-MCP: NetBox lookup then AWX investigation

## Key Gotchas

- **AWX inventories use short hostnames** — NetBox returns FQDNs like `host.cloud.together.ai` but AWX inventories have just `host`. Strip the domain or run `awx-cli hosts <ID>` to check. Using a FQDN in `--limit` silently matches zero hosts.
- **Use `awx_launch_and_wait` / `awx-cli launch --wait`** over manual launch+poll
- **Use job events for failure triage** — structured data beats parsing stdout
- **Field selection saves tokens (MCP)** — pass `fields` to MCP tools; CLI `--fields` only works on `templates`, `workflows`, `jobs`, and `list`
- **Filters use Django-style lookups** — `name__icontains`, `status`, `created__gt`, etc.
- **`parent_type`/`parent_id`** — required for nested resources (e.g. events under a job)
- **Transient errors retried** — 429, 502, 503, 504 are automatically retried with backoff
- **Freshly deployed nodes need bootstrap first** — MAAS nodes only have `ubuntu`; run `prep-awx-access.yaml` before any AWX template (see "Freshly Deployed Nodes" above)
- **The server can be flaky** — if a tool call fails, retry once before giving up
