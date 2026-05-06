---
name: gpu-diagnostics
description: Use when investigating GPU health, XID/SXid errors, NCCL failures, ECC issues, NVLink problems, or accepting new/repaired nodes. Triggers on GPU alerts, XID errors, NCCL test failures, ECC reports, NVLink degradation, retired pages, or node acceptance.
---

# GPU Diagnostics

## Available Tools

### MCP tools (gpu-diag-mcp)
- `xid_lookup` — Look up XID error code with severity, category, recommended actions
- `sxid_lookup` — Look up SXid (NVSwitch) error code
- `parse_kernel_xid_logs` — Parse kernel logs for XID, SXid, FBHUB, assertion failures
- `parse_ecc_errors` — Parse nvidia-smi ECC output (CSV or full format)
- `parse_nccl_results` — Parse all_reduce_perf output, detect failure patterns
- `parse_ibdev2netdev` — Parse IB device status, flag down ports
- `parse_nvlink_status` — Parse NVLink status, detect inactive/degraded links
- `parse_nvlink_errors` — Parse NVLink error counters
- `parse_retired_pages` — Parse retired pages, check against H100 baseline
- `diagnose_nccl_failure` — Multi-node NCCL failure analysis: finds root cause node
- `batch_diagnose` — Parse multi-host diagnostic output in one call with per-node severity summary

### CLI (gpu-diag-cli)
- `gpu-diag-cli xid lookup CODE` — Quick XID lookup
- `gpu-diag-cli xid lookup CODE --sxid` — Quick SXid lookup
- `gpu-diag-cli xid list` — List all XID codes
- `gpu-diag-cli parse kernel-logs < logs.txt` — Parse kernel logs from stdin
- `gpu-diag-cli parse ecc --file output.csv` — Parse ECC errors
- `gpu-diag-cli parse nccl --file nccl.log --min-bw 360` — Parse NCCL output
- `gpu-diag-cli parse ib --file ibdev.txt` — Check IB ports
- `gpu-diag-cli parse nvlink --file nvlink.txt` — Parse NVLink status
- `gpu-diag-cli parse retired-pages --file pages.csv` — Check retired pages
- `gpu-diag-cli parse batch --file multi-host.txt` — Parse multi-host diagnostics
- `gpu-diag-cli parse batch --file multi-host.txt --node-type gb200` — Parse GB200 multi-host diagnostics

All CLI commands support `--json` for structured output.

### AWX Templates
- **Template 170**: gpu-diagnostics — Runs comprehensive GPU health checks on a node
- **Template 168**: nccl-test — Runs single-node all_reduce_perf NCCL benchmark

---

## Triage Workflow

Use when responding to GPU alerts (PagerDuty, researcher reports, monitoring).

### 1. Identify the node
```
netbox_search_objects("<hostname>", object_types=["dcim.device"])
netbox_get_object_by_id(object_type="dcim.device", object_id=<id>)
```
Extract cluster membership, rack location, site. Note the node's cluster for NCCL scope.

### 2. Run GPU diagnostics via AWX
```
awx_launch_job(template_id=170, limit="<hostname>")
```
Wait for completion, then retrieve stdout. This collects:
- Kernel logs (XID/SXid/FBHUB)
- ECC errors
- NVLink status + errors
- ibdev2netdev
- Retired pages
- nvidia-smi GPU summary

### 3. Parse results through gpu-diag tools
Feed each section of AWX output to the appropriate parser:
1. `parse_kernel_xid_logs` — Check for XID/SXid errors
2. `parse_ecc_errors` — Check for uncorrectable ECC errors
3. `parse_ibdev2netdev` — **Check IB ports first** (see Key Patterns below)
4. `parse_nvlink_status` / `parse_nvlink_errors` — Check NVLink health
5. `parse_retired_pages` — Check against H100 baseline

For any XID/SXid codes found, look them up:
```
xid_lookup(code=94)
sxid_lookup(code=12028)
```

### 4. Check UFM for correlated fabric issues
```
ufm_check_ports_recent(system="<hostname>", port_numbers=[63,64])
ufm_get_cluster_concerns(lookback_minutes=60)
```
Correlate IB fabric events with GPU errors.

### 5. Batch multi-node diagnostics
When you have combined diagnostic output from multiple nodes (with `--- hostname ---`
headers and `=IB=`, `=ECC=`, `=RETIRED=`, `=KERNEL=`, `=NVLINK=`, `=NCCL=` section markers):
```
batch_diagnose(multi_host_output=combined_text, node_type="h100")
```
Returns per-node severity-ranked summary — critical nodes sort first.

### 6. Multi-node NCCL failure analysis
When NCCL failures span multiple nodes (e.g., training job crash):
```
diagnose_nccl_failure(nodes=["node1", "node2", ...])
```
This cross-references per-node diagnostics to identify the single root cause node.

### 7. Remediation
- **IB port down**: Taint the node, file vendor ticket via dc-support MCP
- **XID hardware errors**: Taint node with `gpu-diag/unhealthy=true:NoSchedule`, file vendor ticket
- **Transient XID 94+137+SXid 12028**: No action needed if during active workloads (see baselines)
- **Elevated retired pages**: Monitor; taint if > 2× baseline (H100: 32, GB200: 16)
- **NVLink inactive**: Taint node, needs physical inspection

Taint pattern:
```
kubectl taint nodes <node> gpu-diag/unhealthy=true:NoSchedule
```

---

## Acceptance Workflow

Use when onboarding new nodes or verifying post-repair nodes.

### Gate 1: IB Port Check (CRITICAL — do this first)
```
awx_launch_job(template_id=170, limit="<hostname>")
# parse ibdev2netdev section — pass topology for GB200 nodes
parse_ibdev2netdev(text)                                                        # H100 default
parse_ibdev2netdev(text, expected_ib_devices=GB200_IB_DEVICES, expected_eth_devices=GB200_ETH_DEVICES)  # GB200
```
**STOP if any IB port is down.** 1 IB port down will cascade to NCCL failure. Fix IB first.

### Gate 2: GPU Health
Parse the diagnostics output:
- **GPU count**: Must match expected count for the platform (H100 = 8, GB200 = 4)
- **ECC**: No uncorrectable errors
- **NVLink**: All 18 links active per GPU at 26.562 GB/s
- **Retired pages**: ≤ normal baseline (2 per GPU: H100 = 16, GB200 = 8)
- **Kernel logs**: No XID errors (FBHUB at boot is normal — 1 per GPU)

### Gate 3: Single-Node NCCL Test
```
awx_launch_job(template_id=168, limit="<hostname>")
# parse NCCL output — set expected_gpus to match the platform
parse_nccl_results(text, expected_gpus=8, expected_min_bw=360.0)   # H100
parse_nccl_results(text, expected_gpus=4, expected_min_bw=360.0)   # GB200
```
Must achieve ≥ 360 GB/s average bus bandwidth with zero wrong answers.

### Gate 4: UFM Port Health
```
ufm_check_ports_recent(system="<hostname>", port_numbers=[63,64])
```
Verify no high-BER or degraded ports in UFM.

### Acceptance Report
Summarize all gates as pass/fail with key metrics.

**H100 (8-GPU) example:**
- IB: 8/8 IB up, 2/2 ETH up
- GPUs: 8 detected, ECC clean, NVLink 18/18 per GPU
- Retired pages: 16 (normal baseline = 2 × 8 GPUs)
- NCCL: X GB/s avg bus bandwidth (pass/fail vs 360)
- UFM: no port concerns

**GB200 (4-GPU) example:**
- IB: 4/4 IB up, 1/1 ETH up
- GPUs: 4 detected, ECC clean, NVLink 18/18 per GPU
- Retired pages: 8 (normal baseline = 2 × 4 GPUs)
- NCCL: X GB/s avg bus bandwidth (pass/fail vs 360)
- UFM: no port concerns

---

## Key Patterns (Baselines by Platform)

### Platform GPU Counts
| Platform | GPUs | IB Devices | ETH Devices | Retired Pages Baseline |
|----------|------|------------|-------------|----------------------|
| H100     | 8    | 8          | 2           | 16 (2 per GPU)       |
| GB200    | 4    | 4          | 1           | 8 (2 per GPU)        |

### Normal / Transient — No Action Required
| Signal | Explanation |
|--------|-------------|
| XID 94 + XID 137 + SXid 12028 during workloads | GPU preemption cascade, normal under load |
| 2 retired pages per GPU (1 SBE + 1 DBE) | Factory-baseline retired pages (H100: 16, GB200: 8) |
| FBHUB interrupts at boot (1 per GPU, within 10s) | Normal boot-time initialization |

### Critical — Requires Action
| Signal | Action |
|--------|--------|
| Any IB port down | Fix IB before anything else; cascades to NCCL failures |
| XID 48 (DBE) | Hardware ECC failure — taint + vendor ticket |
| XID 79 (GPU fallen off bus) | Physical issue — taint + vendor ticket |
| Uncorrectable ECC errors | Taint node, investigate GPU hardware |
| Retired pages > 2× baseline (H100: >32, GB200: >16) | Taint node, escalate |
| NVLink inactive links | Physical inspection required |
| NCCL avg busbw < 360 GB/s | Investigate IB ports, NVLink, GPU thermals |

### The #1 Rule
**IB port status is the first diagnostic check for any NCCL failure.**
1 down IB port on 1 node causes N NCCL failures across the training group.
Always rule out IB before investigating GPU/NVLink.
