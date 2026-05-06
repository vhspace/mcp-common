# AWX MCP Tool Examples

Practical examples and common workflows for the AWX MCP server tools.

## Most Useful Tools

1. **`awx_launch_and_wait`** — Launch a job and wait for completion in one call
2. **`awx_list_resources`** with `parent_type="jobs"` and filters — Debug job failures fast
3. **`awx_get_job_stdout`** — Get job output/logs
4. **`awx_list_resources("job_templates")`** — Find templates by name

## Launch + Wait (Single Call)

The most common workflow — launch a job and get the result:

```python
result = awx_launch_and_wait(
    "job_template", <TEMPLATE_ID>,
    extra_vars={"env": "prod"},
    limit="host1,host2",
    timeout_seconds=600,
)
# Returns final job status: {"id": ..., "status": "successful", "elapsed": 45.2, ...}

if result.get("status") == "failed":
    stdout = awx_get_job_stdout(job_id=result["id"])
```

If you only need fire-and-forget, use `awx_launch` instead.

## Debugging Job Failures

### Quick Failure Triage

Use `awx_list_resources` with the `job_events` resource type to get structured failure data
instead of parsing 50KB+ of stdout:

```python
# Step 1: Get only the failures (not 50KB of stdout!)
failures = awx_list_resources(
    "job_events",
    filters={"failed": "true"},
    parent_type="jobs",
    parent_id=<JOB_ID>,
    page_size=10,
)
# Returns structured data: host_name, task, error message for each failure

# Step 2: If you need full context, get stdout
stdout = awx_get_job_stdout(job_id=<JOB_ID>, format="txt")
```

### Useful Event Filters

```python
# Failed events on a specific host:
awx_list_resources("job_events", filters={"failed": "true", "host": "<HOST>"}, parent_type="jobs", parent_id=<JOB_ID>)

# Events that made changes:
awx_list_resources("job_events", filters={"changed": "true"}, parent_type="jobs", parent_id=<JOB_ID>)

# Task pattern matching:
awx_list_resources("job_events", filters={"task__icontains": "setup", "failed": "true"}, parent_type="jobs", parent_id=<JOB_ID>)
```

## Finding Jobs by Commit Hash

```python
# Step 1: Find template ID by name
templates = awx_list_resources("job_templates", filters={"name__icontains": "forge"})
template_id = templates["results"][0]["id"]

# Step 2: List recent jobs with commit hashes
jobs = awx_list_resources(
    "jobs",
    filters={"job_template": template_id},
    fields=["id", "scm_revision", "status", "created"],
    order_by="-id",
    page_size=20,
)

# Step 3: Filter by status if needed
failed_jobs = awx_list_resources(
    "jobs",
    filters={"job_template": template_id, "status": "failed"},
    fields=["id", "scm_revision", "created"],
    order_by="-id",
    page_size=10,
)
```

## Job Output

```python
# Default: shows the TAIL of the log (failures + PLAY RECAP are at the end)
awx_get_job_stdout(job_id=<JOB_ID>)

# See beginning and end of a long log:
awx_get_job_stdout(job_id=<JOB_ID>, truncation_strategy="head_tail", limit_chars=50000)

# Jump directly to PLAY RECAP with context:
awx_get_job_stdout(job_id=<JOB_ID>, truncation_strategy="recap_context")

# Old behavior — first N chars:
awx_get_job_stdout(job_id=<JOB_ID>, truncation_strategy="head")

# Returns: {
#   "job_id": <JOB_ID>,
#   "format": "txt",
#   "truncated": true,
#   "truncation_strategy": "tail",
#   "limit_chars": 20000,
#   "original_length": 85000,
#   "content": "...PLAY RECAP ****\nhost1 : ok=5 changed=2..."
# }
```

Truncation strategies: `tail` (default, best for triage), `head`, `head_tail`, `recap_context`.
Available formats: `txt` (default), `ansi`, `json`, `html`.

## Parsed Job Log (Structured Triage)

For large logs, use `awx_parse_job_log` instead of reading raw stdout — it extracts
structured data (plays, failures, warnings, per-host stats) in one call:

```python
# Full parse — plays, failures, warnings, recap, host stats:
parsed = awx_parse_job_log(job_id=<JOB_ID>)
# Returns: {
#   "job_id": <JOB_ID>,
#   "overall_result": "failed",
#   "has_failures": true,
#   "total_lines": 1523,
#   "plays": ["Preflight checks", "Install packages"],
#   "total_tasks": 42,
#   "failed_tasks": [
#       {"host": "gpu103", "task": "Configure mlxconfig", "module": "FAILED",
#        "message": "mlxconfig: command not found"}
#   ],
#   "warnings": ["Host 'gpu103' had errors"],
#   "host_stats": [
#       {"host": "gpu103", "ok": 12, "changed": 3, "failed": 1, ...}
#   ],
#   "recap_text": "PLAY RECAP ****\ngpu103 : ok=12 ..."
# }

# Just failures:
awx_parse_job_log(job_id=<JOB_ID>, sections=["failures"])

# Just the recap:
awx_parse_job_log(job_id=<JOB_ID>, sections=["recap"])

# Failures + warnings:
awx_parse_job_log(job_id=<JOB_ID>, sections=["failures", "warnings"])
```

### CLI equivalents

```bash
# Tail of log (default — shows end with failures/recap):
awx-cli stdout <JOB_ID>

# Head+tail view:
awx-cli stdout <JOB_ID> --truncation head_tail --limit-chars 50000

# Structured summary:
awx-cli log-summary <JOB_ID>

# Just failures:
awx-cli log-summary <JOB_ID> --sections failures

# JSON output for piping:
awx-cli log-summary <JOB_ID> --json
```

## Credentials

```python
# Search by name
awx_list_resources("credentials", filters={"name__icontains": "netbox"}, fields=["id", "name", "kind"])

# Get credential details
awx_get_resource("credentials", <CREDENTIAL_ID>)

# Find AWS-like credentials
awx_list_aws_like_credentials()
```

## Cross-MCP Integration with NetBox

> **AWX inventories use short hostnames**, not FQDNs. NetBox returns FQDNs like
> `b65c909e-41.cloud.together.ai`, but AWX inventories have just `b65c909e-41`.
> Using the FQDN in `--limit` silently matches zero hosts.

```python
# 1. Look up host in NetBox MCP → get FQDN "b65c909e-41.cloud.together.ai"
# 2. Check AWX inventory to confirm the host name format:
awx_list_resources("hosts", parent_type="inventories", parent_id=<INVENTORY_ID>, filters={"name__icontains": "b65c909e"})
# 3. Use the SHORT hostname (strip .cloud.together.ai) as the limit parameter:
awx_launch_and_wait("job_template", <TEMPLATE_ID>, limit="b65c909e-41")
```

## Pagination & Field Selection

All list operations return paginated responses:

```json
{
  "count": 150,
  "next": "/api/v2/jobs/?page=2",
  "previous": null,
  "results": [...]
}
```

Use `fields` to reduce token usage:

```python
# Full objects (expensive):
awx_list_resources("job_templates")

# Only what you need (efficient):
awx_list_resources("job_templates", fields=["id", "name", "playbook"], page_size=50)
```

Use `order_by` with `-` prefix for descending:

```python
awx_list_resources("jobs", filters={"status": "failed"}, order_by="-created", page_size=5)
```

## Available Event Types

- `runner_on_failed` — Task failure on a host
- `runner_on_ok` — Task success on a host
- `runner_on_skipped` — Task skipped on a host
- `runner_on_unreachable` — Host unreachable
- `playbook_on_task_start` — Task started
- `playbook_on_play_start` — Play started
- `playbook_on_stats` — Final statistics
