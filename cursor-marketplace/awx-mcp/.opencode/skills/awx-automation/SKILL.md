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
| Events task summary | `awx-cli events <JOB_ID> --task "Configure containerd" --summary` |
| All events as JSON | `awx-cli events <JOB_ID> --all --json` |
| Launch (fire-forget) | `awx-cli launch <TEMPLATE_ID>` |
| Launch and wait | `awx-cli launch <TEMPLATE_ID> --wait --timeout 600` |
| Launch with vars | `awx-cli launch <TEMPLATE_ID> -e '{"env":"prod"}' --limit "host1,host2"` |
| Launch branch override | `awx-cli launch <TEMPLATE_ID> --scm-branch "feature/my-branch"` |
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
| Check AWX SSH access | `awx-cli check-access <HOST>` |
| Fewer fields | `awx-cli templates --fields "id,name,playbook"` |

If `awx-cli` is not on PATH, install with `uvx --from awx-mcp awx-cli` or run from the repo with `uv run awx-cli`.

Use `--json` whenever piping to another tool; human output can include headers, summaries, and stderr warnings.

## AWX Organizations (Environments)

AWX uses organizations as environment boundaries. The same job templates and workflows are mirrored into both orgs with per-org defaults applied.

| Organization | Project | SCM Branch | Cleanup | Use Case |
|---|---|---|---|---|
| **Together** | `infra` | `main` (locked) | Enabled — drift deleted | Production jobs |
| **Together-Dev** | `infra-dev` | Prompted on launch | Disabled — UI templates preserved | Branch testing, pre-merge validation |

### Choosing an org

- **Default to `Together`** unless the user explicitly wants to test a feature branch
- Templates share names across orgs — always scope searches by organization when ambiguous
- Prefer the `Together-Dev` copy for branch testing; do not point production templates at feature branches
- `Together-Dev` has `ask_scm_branch_on_launch: true` on all templates via `template_defaults`

### Schedule naming

Schedules are org-prefixed: `<Org> / <Template> / <Schedule Name>`

Example: `Together / AWX / Apply Config / every-24-hours`

### Important behaviors

- **"AWX / Apply Config" from Together-Dev applies BOTH orgs** — launching the reconcile playbook from `Together-Dev` against a feature branch reconfigures both organizations from that branch
- **Cleanup in Together deletes unmanaged resources** — anything not declared in YAML gets removed on reconcile
- **Together-Dev preserves UI-managed templates** — manually created templates and schedules survive reconcile runs
- **`sync-inventories.yaml` is still single-org** — hardcoded to `Together`; inventory sync hasn't been multi-org'd yet

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
| Supported CRUD matrix | `awx_list_supported_resources()` |
| Create resource | `awx_create_resource("schedules", {"name": "Daily", "rrule": "FREQ=DAILY"}, parent_type="job_templates", parent_id=<ID>)` |
| Update resource | `awx_update_resource("projects", <ID>, {"scm_branch": "feature/test"})` |
| Delete resource | `awx_delete_resource("schedules", <ID>, parent_type="job_templates", parent_id=<TEMPLATE_ID>)` |
| Cluster health | `awx_get_cluster_status()` |
| Ping | `awx_ping()` |

Always pass `fields` to reduce MCP response size (~80-90% token savings).

## Managing Templates And Resources

Read and launch with CLI; mutate with MCP or REST only when needed. CLI CRUD for arbitrary resources is not available until #77 lands.

- **Check support first:** `awx_list_supported_resources()` shows what MCP can create/update/delete. Current supported mutations include credentials, schedules, notifications, execution environments, project updates, and job/schedule deletes.
- **Templates are a gap:** job template create/update/delete and relationship attaches are not marked supported in the MCP CRUD matrix. Use declared YAML + AWX Apply Config when possible, or REST for one-off bring-up.
- **Branch testing:** use the `Together-Dev` template copy and `--scm-branch`; keep prod `Together` on configured `main`.
- **Placeholder inventories:** some templates carry a default/placeholder inventory so AWX can save or prompt. For real runs, pass `--inventory <id>` when the template prompts, then verify hosts with `awx-cli hosts <INVENTORY_ID> --json`.

MCP examples for supported resources:

```python
awx_create_resource("credentials", {"name": "temp", "credential_type": 1, "inputs": {...}})
awx_create_resource("schedules", {"name": "Daily", "rrule": "DTSTART:20260527T120000Z RRULE:FREQ=DAILY"}, parent_type="job_templates", parent_id=123)
awx_update_resource("projects", 8, {"scm_branch": "feature/test"})
awx_update_resource("schedules", 456, {"enabled": False}, parent_type="job_templates", parent_id=123)
```

### When To Fall Back To REST API

Use REST for mutation gaps: creating/updating job templates, attaching credentials or instance groups, workflow nodes, or any operation not listed as supported by `awx_list_supported_resources()`.

Do not paste tokens into commands. Use the environment's credential source and keep the header pattern generic:

```bash
curl -sS -X POST \
  -H "Authorization: Bearer ${AWX_TOKEN:?}" \
  -H "Content-Type: application/json" \
  "${AWX_HOST:?}/api/v2/job_templates/" \
  -d '{"name": "Temp Bring-up", "project": <PROJECT_ID>, "inventory": <INVENTORY_ID>, "playbook": "site.yml"}'

curl -sS -X PATCH \
  -H "Authorization: Bearer ${AWX_TOKEN:?}" \
  -H "Content-Type: application/json" \
  "${AWX_HOST:?}/api/v2/job_templates/<TEMPLATE_ID>/" \
  -d '{"forks": 20, "scm_branch": "feature/test", "ask_scm_branch_on_launch": true}'

curl -sS -X POST \
  -H "Authorization: Bearer ${AWX_TOKEN:?}" \
  -H "Content-Type: application/json" \
  "${AWX_HOST:?}/api/v2/job_templates/<TEMPLATE_ID>/credentials/" \
  -d '{"id": <CREDENTIAL_ID>}'

curl -sS -X POST \
  -H "Authorization: Bearer ${AWX_TOKEN:?}" \
  -H "Content-Type: application/json" \
  "${AWX_HOST:?}/api/v2/job_templates/<TEMPLATE_ID>/instance_groups/" \
  -d '{"id": <INSTANCE_GROUP_ID>}'
```

## Fields Vs Property

- `--property` appends an AWX sub-endpoint path: `survey_spec`, `variable_data`, `webhook_key`, `playbooks`, `stdout`.
- `--fields` projects scalar fields from the fetched resource. Use it for `scm_branch`, `allow_override`, IDs, names, and status fields.

```bash
awx-cli get job_templates <ID> --property survey_spec --json
awx-cli get projects <PROJECT_ID> --fields "id,name,scm_branch,allow_override" --json
awx-cli templates --fields "id,name,scm_branch" --json
```

If `--property scm_branch` returns a 404, it is a user error: retry with `--fields scm_branch`, not a remediation workflow.

## SCM Branch Overrides

Use `--scm-branch` for branch-specific launches:

```bash
awx-cli launch <TEMPLATE_ID> --scm-branch "feature/my-branch" --wait --json
awx_launch_and_wait("job_template", <ID>, scm_branch="feature/my-branch")
```

The backing AWX project must have **Allow Branch Override** (`allow_override`) enabled. Check it with:

```bash
awx-cli get projects <PROJECT_ID> --fields "id,name,scm_branch,allow_override" --json
```

If `allow_override` is false, AWX may ignore the requested branch and fall back to the configured project branch.

## Triage Failed Job

Use structured events, not raw stdout:

**CLI:**

```bash
awx-cli events <JOB_ID> --failed
awx-cli stdout <JOB_ID> --filter errors
awx-cli stdout <JOB_ID> --filter errors --host "failing-host*"
```

**MCP:**

```python
awx_list_resources("job_events", filters={"failed": "true"}, parent_type="jobs", parent_id=<JOB_ID>, page_size=10)
awx_get_job_stdout(job_id=<JOB_ID>, filter="errors")
awx_get_job_stdout(job_id=<JOB_ID>, filter="errors", host="failing-host*")
```

If the error is `Permission denied (publickey)`, the node likely needs bootstrapping — see **Freshly Deployed Nodes (Bootstrap)** below.

## Events Triage At Scale

Prefer `awx-cli events` before reading huge stdout:

```bash
awx-cli events <JOB_ID> --task "Configure containerd" --summary
awx-cli events <JOB_ID> --all --json
awx-cli events <JOB_ID> --event-type runner_on_failed --all --json
awx-cli events <JOB_ID> --changed --host "gpu" --all --json
awx-cli events <JOB_ID> --task-exact "Configure containerd /scratch" --summary --json
```

- Default mode is one page; use `--all` for full-job triage and `--max` as the safety cap.
- `--summary` aggregates latest terminal runner event per host and is best for large fan-out jobs.
- Combine `--task`, `--event-type`, `--changed`, `--failed`, and `--host` to shrink results before JSON piping.

## Cross-Tool Workflow

1. **NetBox** → resolve hostname (returns FQDN)
2. **AWX** → use short hostname in `--limit` (strip `.cloud.together.ai`). Verify with `awx-cli hosts <INVENTORY_ID>` if unsure.
3. **Redfish** → use oob_ip from NetBox for BMC operations

## Freshly Deployed Nodes (Bootstrap)

MAAS-deployed machines only have the `ubuntu` user. AWX connects as the `ansible` user (credential id 3, `ansible-service-account`), so **AWX jobs will fail with `Permission denied (publickey)` on new nodes** until the `ansible` user is bootstrapped.

**First, check whether AWX can already reach the node** (no AWX credentials required):

```bash
awx-cli check-access <HOSTNAME>
```

If `check-access` reports **OK**, skip straight to AWX job templates. If it reports **FAIL**, run `prep-awx-access.yaml` from a local ansible host:

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
- **Use job events for failure triage** — `awx-cli events --summary` and structured event filters beat parsing stdout
- **Use JSON for pipelines** — add `--json` before piping to `jq`, scripts, or another agent
- **Field selection saves tokens** — pass `fields` to MCP tools; in CLI use `--fields` for scalar fields and `--property` only for AWX sub-endpoints
- **Filters use Django-style lookups** — `name__icontains`, `status`, `created__gt`, etc.
- **`parent_type`/`parent_id`** — required for nested resources (e.g. events under a job)
- **Transient errors retried** — 429, 502, 503, 504 are automatically retried with backoff
- **4xx errors are usually user errors** — bad IDs, unsupported sub-endpoints, and validation failures are concise now; fix the command/payload instead of treating them as incident remediation
- **Effective inventory warnings are scoped** — if `--inventory` is supplied, empty-inventory warnings refer to that override; otherwise they refer to the template default. A zero-host success still means inventory or `--limit` matched nothing.
- **SCM branch overrides require project support** — check project `allow_override`; if false, AWX can fall back to the configured branch
- **CLI CRUD is not here yet** — use MCP supported CRUD or REST mutation gaps until #77 lands
- **Freshly deployed nodes need bootstrap first** — MAAS nodes only have `ubuntu`; run `prep-awx-access.yaml` before any AWX template (see "Freshly Deployed Nodes" above)
- **The server can be flaky** — if a tool call fails, retry once before giving up
- **Two orgs, same template names** — `Together` (prod/main) and `Together-Dev` (dev/branch) both contain identical template names. Default to `Together` for production operations. Use `Together-Dev` only when the user wants to test a branch.
