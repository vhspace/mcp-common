# Redfish MCP - AI Agent Quick Reference

**14 Tools Total:** 8 read, 3 write, 3 agent-coordination
**Python:** 3.13 (3.10+ compatible) | **Package Manager:** uv | **Version:** 1.3.0
**Hardware DB:** JSON-based, versioned, git-friendly
**Firmware:** Complete inventory + online version checking

## Quick Start

### Get Hardware Documentation (NEW! ⭐)
```python
docs = await redfish_get_hardware_docs(
    host="192.168.1.100",
    user="admin",
    password="password"
)
# Returns: specs, BIOS changelog, firmware updates, doc URLs, GPU optimization tips
# Cached for 24 hours for fast repeated access
```

### Get System Information
```python
info = await redfish_get_info(
    host="192.168.1.100",
    user="admin",
    password="password",
    info_types=["system", "boot", "drives"]  # Request what you need
)
```

Available `info_types`:
- `"system"` - Manufacturer, model, serial, BIOS version
- `"boot"` - Boot override settings
- `"bios_current"` - Current BIOS attributes
- `"bios_pending"` - Staged BIOS changes
- `"drives"` - NVMe/drive inventory
- `"all"` - Everything

### Query Specific Settings
```python
# Get specific BIOS attribute
result = await redfish_query(
    host="192.168.1.100",
    user="admin",
    password="password",
    query_type="bios_attribute",
    key="SMT_Enable"
)

# List all BIOS attributes matching filter
result = await redfish_query(
    query_type="list_bios_attributes",
    key="MMIO"  # optional filter
)
```

Query types: `bios_attribute`, `boot_setting`, `power_state`, `health`, `list_nics`, `list_bios_attributes`

### Get Complete Firmware Inventory (NEW! ⭐)
```python
inventory = await redfish_get_firmware_inventory(
    host="192.168.1.100",
    user="admin",
    password="password"
)
# Returns ALL firmware: BIOS, BMC, NICs, GPUs, PSUs, CPLDs, storage controllers
# Categorized and with updateable status
# Tested: Found 41 components on Supermicro PIO-8125GS
```

### Check for Security Advisories (NEW! ⭐)
```python
errata = await redfish_get_vendor_errata(
    host="192.168.1.100",
    user="admin",
    password="password"
)
# Returns security bulletin URLs and CVE tracking links
# Works for: Supermicro, Dell, HPE, Lenovo
```

### Check Online for Latest BIOS (NEW! ⭐)
```python
check = await redfish_check_bios_online(
    host="192.168.1.100",
    user="admin",
    password="password"
)
# Returns Tavily instructions to check vendor website
# Gets real-time latest version info
```

### Compare Two Hosts (NEW! Smart Matching ⭐)
```python
# Smart matching (default) - handles different BIOS firmware versions
diff = await redfish_diff_bios_settings(
    host_a="192.168.1.100",
    host_b="192.168.1.101",
    user="admin",
    password="password",
    smart_match=True,  # default - semantic matching with critical settings
    keys_like="MMIO"  # optional filter
)
# Returns:
# - matched: Semantically matched attributes (e.g., "SMTControl_0037" ↔ "SMTControl")
# - critical_differences: Important settings that differ (SMT, IOMMU, SR-IOV, etc.)
# - summary: Human-readable comparison with counts
#
# AI HINT: smart_match=True handles BIOS firmware quirks where different versions
# use different naming (v1.6: "SMTControl_0037", v3.7: "SMTControl")

# Exact matching (legacy mode)
diff = await redfish_diff_bios_settings(
    host_a="192.168.1.100",
    host_b="192.168.1.101",
    user="admin",
    password="password",
    smart_match=False  # exact key matching only
)
```

### Change Boot Target
```python
result = await redfish_set_nextboot(
    host="192.168.1.100",
    user="admin",
    password="password",
    target="bios",  # or "pxe", "hdd"
    enabled="Once",  # or "Continuous", "Disabled"
    allow_write=True,  # REQUIRED for all writes
    reboot=True  # optional: reboot immediately
)
```

### Change BIOS Settings
```python
result = await redfish_set_bios_attributes(
    host="192.168.1.100",
    user="admin",
    password="password",
    attributes={
        "Re_SizeBARSupport_00B2": "Enabled",
        "Above4GDecoding_00B1": "Enabled"
    },
    allow_write=True,  # REQUIRED
    reboot=True  # optional: apply immediately
)
```

### KVM console (screen capture)

Capture a screenshot from a Supermicro BMC via `redfish_kvm_screen`:

```python
result = await redfish_kvm_screen(
    host="192.168.196.1",
    user="ADMIN",
    password=os.environ["REDFISH_PASSWORD"],
    mode="image",
)
if result["ok"]:
    png = base64.b64decode(result["png_b64"])
    Path("/tmp/kvm.png").write_bytes(png)
```

First call on a cold daemon takes 15–30 seconds (Java + Xvfb + x11vnc
warmup, JAR download on first-ever use). Subsequent calls against the
same BMC reuse the open session and typically return in under 2 seconds.

Currently only `mode="image"` is supported in phase 2. Text/analysis modes
arrive in a follow-up.

### Monitor Tasks (MCP)

Write operations default to `async_mode=True` and return an MCP `CreateTaskResult`
with a `taskId`. Use MCP task endpoints to poll/cancel:

- `tasks/get` (status)
- `tasks/result` (final payload)
- `tasks/cancel` (cancel)

## All Tools

| Tool | Purpose |
|------|---------|
| `redfish_get_info` | Get system information (unified) |
| `redfish_list_bmc_users` | List BMC/IPMI user accounts |
| `redfish_query` | Query specific settings |
| `redfish_diff_bios_settings` | Compare BIOS between hosts |
| `redfish_get_firmware_inventory` | Get ALL firmware versions (BIOS, BMC, NICs, GPUs, PSUs, CPLDs) |
| `redfish_get_hardware_docs` | Get hardware specs, BIOS info, firmware updates, docs (cached) |
| `redfish_check_bios_online` | Check vendor website for latest BIOS (uses Tavily) |
| `redfish_get_vendor_errata` | Get security bulletins and CVE links |
| `redfish_set_nextboot` | Change boot settings |
| `redfish_set_bios_attributes` | Change BIOS settings |
| `redfish_update_firmware` | Upload/apply firmware via Redfish UpdateService |
| `redfish_agent_report_observation` | Store an observation about a host (local SQLite) |
| `redfish_agent_list_observations` | Retrieve stored observations about a host |
| `redfish_agent_get_host_stats` | Per-host call statistics (recent calls/errors) |

## Agent Coordination: Observations + Hinting

### Store an Observation (Reusable Knowledge)

Use this when you learned something durable about a host (vendor quirks, credentials source, failure mode, etc.).

```python
await redfish_agent_report_observation(
    host="192.168.1.100",
    kind="bmc",
    summary="BMC times out if >1 concurrent request",
    details={"mitigation": "keep per-host concurrency at 1"},
    tags=["triage", "timeouts"],
    confidence=0.8,
    ttl_hours=72,
)
```

### Read Existing Observations

```python
await redfish_agent_list_observations(host="192.168.1.100", limit=20)
```

### Hinting (Together LLM, Feature-Flagged)

The MCP can emit **sparse hints** (only when likely helpful) in the MCP response `_meta`.

Server-side env vars:
- `REDFISH_HINTING_ENABLED=1`
- `TOGETHER_INFERENCE_KEY=...`
- `REDFISH_HINTING_MODEL` (default: `Qwen/Qwen3-235B-A22B-Instruct-2507-tput`)

### Per-call agent context (`_meta`)

Agents may attach optional context to *any* tool call via MCP request `_meta` under:
- `together.ai/redfish-mcp`

This is intended for “possible interesting things other agents found” so the MCP can decide whether to prompt for an observation.

## Common Patterns

### Get Hardware Documentation and Optimization Tips
```python
# Get comprehensive hardware docs (cached for 24 hours)
docs = await redfish_get_hardware_docs(
    host="192.168.1.100",
    user="admin",
    password="password"
)

# Check what you get:
print(f"Hardware: {docs['hardware_info']['description']}")
print(f"GPU Slots: {docs['hardware_info']['gpu_slots']}")
print(f"BIOS: {docs['bios_info']['current_version']}")
print(f"Is Latest: {docs['bios_info']['is_latest']}")
print(f"Manual: {docs['documentation']['manual']}")

# Apply recommended settings if BIOS not optimal
if docs['bios_info'].get('recommended_settings'):
    await redfish_set_bios_attributes(
        host="192.168.1.100",
        attributes=docs['bios_info']['recommended_settings'],
        allow_write=True
    )
```

### Configure Host for GPU Workload
```python
# 1. Check current state
info = await redfish_get_info(
    host="192.168.1.100",
    info_types=["system", "bios_current"]
)

# 2. Apply GPU-optimized BIOS settings
task = await redfish_set_bios_attributes(
    host="192.168.1.100",
    attributes={
        "Above4GDecoding_00B1": "Enabled",
        "Re_SizeBARSupport_00B2": "Enabled",
        "PCIeARISupport_00B7": "Enabled"
    },
    allow_write=True,
    async_mode=True
)

# 3. Poll the task via MCP tasks/get and tasks/result (see "Monitor Tasks" above).
```

### Verify Fleet Configuration
```python
baseline = await redfish_get_info(
    host="192.168.1.100",  # golden host
    info_types=["bios_current"]
)

for host in fleet:
    diff = await redfish_diff_bios_settings(
        host_a="192.168.1.100",
        host_b=host,
        user="admin",
        password="password",
        only_diff=True
    )
    if diff["diff"]["different"]:
        print(f"{host}: {len(diff['diff']['different'])} differences")
```

### Debug Boot Issues
```python
# Get comprehensive diagnostic info
info = await redfish_get_info(
    host="192.168.1.100",
    info_types=["all"],
    timeout_s=60
)

print(f"System: {info['system']['Manufacturer']} {info['system']['Model']}")
print(f"Boot: {info['boot']['BootSourceOverrideTarget']}")
print(f"Power: {info['system']['PowerState']}")
print(f"Health: {info['system']['Status']['Health']}")
print(f"Drives: {info['drives']['count']}")
```

## Best Practices

✅ **DO:**
- Use `info_types=["system", "boot"]` to get multiple things in one call
- Always set `allow_write=True` for write operations
- Filter BIOS queries with `keys_like` parameter
- Set appropriate `timeout_s` for slow operations

❌ **DON'T:**
- Make multiple calls when one will do
- Forget `allow_write=True` on write operations
- Assume BIOS changes apply immediately (they require reboot)
- Query all BIOS attributes without filtering (use `keys_like`)

## Response Format

All tools return consistent format:

```python
# Success
{"ok": True, "host": "...", ...data...}

# Error
{"ok": False, "error": "message", ...context...}
```

Always check `result["ok"]` before accessing data.

## Curl Mode

Get equivalent curl commands instead of executing:

```python
result = await redfish_get_info(
    host="192.168.1.100",
    execution_mode="render_curl"
)
# Returns: {"ok": True, "execution_mode": "render_curl", "curl": [...]}
```

## Common Parameters

All tools accept:
- `host` (str, required) - IP or hostname
- `user` (str, required) - Username
- `password` (str, required) - Password
- `verify_tls` (bool, default=False) - Verify TLS certs
- `timeout_s` (int, default varies) - Request timeout
- `execution_mode` (str, default="execute") - Set to "render_curl" for curl commands

Write operations also accept:
- `allow_write` (bool, REQUIRED) - Must be True
- `async_mode` (bool, default=True) - Return MCP task (CreateTaskResult) or wait
- `reboot` (bool, default=False) - Reboot after change
