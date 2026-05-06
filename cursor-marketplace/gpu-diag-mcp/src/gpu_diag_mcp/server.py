"""MCP server for GPU diagnostics.

Exposes tools that accept raw text output from nvidia-smi, journalctl,
ibdev2netdev, NCCL tests, etc. and return structured, severity-ranked
analysis.  All tools are read-only parsers — they never touch hardware.
"""

from __future__ import annotations

import sys
from typing import Annotated, Any

from fastmcp import FastMCP
from mcp_common import (
    HttpAccessTokenAuth,
    add_health_route,
    create_http_app,
    health_resource,
    setup_logging,
    suppress_ssl_warnings,
)
from mcp_common.agent_remediation import mcp_remediation_wrapper
from pydantic import Field

from gpu_diag_mcp import __version__
from gpu_diag_mcp.config import Settings
from gpu_diag_mcp.parsers import ecc, ibstat, kernel_logs, nccl, nvlink, retired_pages
from gpu_diag_mcp.parsers.batch import NODE_HEADER_RE, parse_batch, split_nodes
from gpu_diag_mcp.xid_catalog import sxid_lookup as _sxid_lookup
from gpu_diag_mcp.xid_catalog import xid_lookup as _xid_lookup

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

settings = Settings()
log = setup_logging(level=settings.log_level, json_output=settings.log_json, name="gpu_diag_mcp")

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

mcp = FastMCP("gpu-diag-mcp")

# ---------------------------------------------------------------------------
# Health — HTTP /health endpoint (for K8s probes) via mcp-common
# ---------------------------------------------------------------------------

add_health_route(mcp, "gpu-diag-mcp")

# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


@mcp.resource("health://gpu-diag-mcp")
def health() -> dict[str, Any]:
    """Server health and uptime (MCP resource, not HTTP)."""
    return health_resource(name="gpu-diag-mcp", version=__version__).to_dict()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

_SEVERITY_ORDER = {"ok": 0, "warning": 1, "critical": 2}


@mcp.tool(annotations={"readOnlyHint": True})
@mcp_remediation_wrapper(project_repo="vhspace/gpu-diag-mcp")
def xid_lookup(
    code: Annotated[int, Field(description="NVIDIA GPU XID error code to look up")],
    driver_version: Annotated[
        str,
        Field(description="Driver branch version for the XID catalog (e.g. '590')"),
    ] = "590",
) -> dict[str, Any]:
    """Look up an NVIDIA GPU XID error code in the catalog.

    Returns the catalog entry including description, severity, and
    recommended actions for the given XID code.
    """
    log.info("XID lookup: code=%d driver=%s", code, driver_version)
    return _xid_lookup(code, driver_version=driver_version)


@mcp.tool(annotations={"readOnlyHint": True})
@mcp_remediation_wrapper(project_repo="vhspace/gpu-diag-mcp")
def sxid_lookup(
    code: Annotated[int, Field(description="NVSwitch SXid error code to look up")],
) -> dict[str, Any]:
    """Look up an NVSwitch SXid error code in the catalog.

    Returns the catalog entry including description, severity, and
    recommended actions for the given SXid code.
    """
    log.info("SXid lookup: code=%d", code)
    return _sxid_lookup(code)


@mcp.tool(annotations={"readOnlyHint": True})
@mcp_remediation_wrapper(project_repo="vhspace/gpu-diag-mcp")
def parse_kernel_xid_logs(
    log_text: Annotated[
        str,
        Field(
            description=(
                "Raw kernel log text (journalctl -k or dmesg output) to scan "
                "for XID, SXid, FBHUB, and assertion failure events"
            )
        ),
    ] = "",
    output: Annotated[
        str,
        Field(description="Alias for log_text — pass raw kernel log text here instead"),
    ] = "",
) -> dict[str, Any]:
    """Parse kernel logs for GPU XID errors, NVSwitch SXid errors, FBHUB
    interrupts, and assertion failures.

    Accepts raw journalctl/dmesg output.  Returns structured events grouped
    by type with a severity-ranked summary.
    """
    log_text = log_text or output
    log.info("Parsing kernel XID logs (%d chars)", len(log_text))
    return kernel_logs.parse_kernel_xid_logs(log_text)


@mcp.tool(annotations={"readOnlyHint": True})
@mcp_remediation_wrapper(project_repo="vhspace/gpu-diag-mcp")
def parse_ecc_output(
    nvidia_smi_output: Annotated[
        str,
        Field(
            description=(
                "nvidia-smi ECC output — either CSV from "
                "'nvidia-smi --query-gpu=index,ecc.errors.* --format=csv' "
                "or verbose from 'nvidia-smi -q -d ECC'"
            )
        ),
    ] = "",
    format: Annotated[
        str,
        Field(description="Output format: 'csv' for --format=csv, 'full' for -q -d ECC"),
    ] = "csv",
    output: Annotated[
        str,
        Field(description="Alias for nvidia_smi_output — pass ECC output here instead"),
    ] = "",
) -> dict[str, Any]:
    """Parse nvidia-smi ECC error output.

    For CSV format: parses volatile/aggregate correctable and uncorrectable
    counts per GPU.  For full format: additionally extracts SRAM/DRAM
    breakdown, ECC mode, and pending retirement status.

    Flags volatile uncorrectable errors as critical, high aggregate
    correctable counts as warning.
    """
    nvidia_smi_output = nvidia_smi_output or output
    log.info("Parsing ECC output (format=%s, %d chars)", format, len(nvidia_smi_output))
    if format == "full":
        return ecc.parse_ecc_full(nvidia_smi_output)
    return ecc.parse_ecc_csv(nvidia_smi_output)


@mcp.tool(annotations={"readOnlyHint": True})
@mcp_remediation_wrapper(project_repo="vhspace/gpu-diag-mcp")
def parse_nccl_results(
    nccl_output: Annotated[
        str,
        Field(description="Raw output from all_reduce_perf or similar NCCL test"),
    ] = "",
    expected_gpus: Annotated[
        int,
        Field(description="Number of GPUs expected in the NCCL test"),
    ] = 8,
    expected_min_bw: Annotated[
        float,
        Field(description="Minimum acceptable average bus bandwidth in GB/s"),
    ] = 360.0,
    output: Annotated[
        str,
        Field(description="Alias for nccl_output — pass NCCL test output here instead"),
    ] = "",
) -> dict[str, Any]:
    """Parse NCCL collective test (all_reduce_perf) output.

    Extracts average bus bandwidth, per-size data rows, and detects failure
    patterns: bootstrap hang, missing HCAs, OOM, low bandwidth, data
    corruption, and peer-waiting conditions.
    """
    nccl_output = nccl_output or output
    log.info(
        "Parsing NCCL results (%d chars, expected_gpus=%d, min_bw=%.1f)",
        len(nccl_output),
        expected_gpus,
        expected_min_bw,
    )
    return nccl.parse_nccl_results(
        nccl_output, expected_gpus=expected_gpus, expected_min_bw=expected_min_bw
    )


@mcp.tool(annotations={"readOnlyHint": True})
@mcp_remediation_wrapper(project_repo="vhspace/gpu-diag-mcp")
def parse_nvlink_status(
    nvlink_output: Annotated[
        str,
        Field(
            description=(
                "nvidia-smi NVLink output — either 'nvidia-smi nvlink --status' "
                "or 'nvidia-smi nvlink -e' error counters"
            )
        ),
    ] = "",
    check_type: Annotated[
        str,
        Field(description="Check type: 'status' for link status, 'errors' for error counters"),
    ] = "status",
    output: Annotated[
        str,
        Field(description="Alias for nvlink_output — pass NVLink output here instead"),
    ] = "",
) -> dict[str, Any]:
    """Parse NVLink status or error counters from nvidia-smi.

    For status: checks that all 18 links per GPU are active at the
    expected 26.562 GB/s (H100/GB200).  For errors: sums replay,
    recovery, and CRC errors per GPU and flags non-zero counters.
    """
    nvlink_output = nvlink_output or output
    log.info("Parsing NVLink output (check_type=%s, %d chars)", check_type, len(nvlink_output))
    if check_type == "errors":
        return nvlink.parse_nvlink_errors(nvlink_output)
    return nvlink.parse_nvlink_status(nvlink_output)


@mcp.tool(annotations={"readOnlyHint": True})
@mcp_remediation_wrapper(project_repo="vhspace/gpu-diag-mcp")
def parse_ib_status(
    ibdev2netdev_output: Annotated[
        str,
        Field(
            description=(
                "Output of 'ibdev2netdev' showing IB device-to-interface mapping "
                "and link state. This is the single most important network health "
                "check — a single IB port down causes cascading NCCL failures "
                "across all nodes in the communicator group."
            )
        ),
    ] = "",
    ibstat_output: Annotated[
        str,
        Field(
            description=(
                "Optional output of 'ibstat' for detailed HCA info including "
                "firmware, GUID, rate, and physical state"
            )
        ),
    ] = "",
    node_type: Annotated[
        str,
        Field(
            description=(
                "Node GPU topology: 'h100' (8 IB + 2 ETH, default) or "
                "'gb200' (4 IB + 1 ETH). Determines expected IB device set."
            )
        ),
    ] = "h100",
    output: Annotated[
        str,
        Field(description="Alias for ibdev2netdev_output — pass ibdev2netdev output here instead"),
    ] = "",
) -> dict[str, Any]:
    """Parse InfiniBand device status from ibdev2netdev and optionally ibstat.

    **CRITICAL**: Any IB port in a non-Up state causes cascading NCCL
    failures across every node sharing the same NCCL communicator group.
    A single node with 1 IB port down will cause multi-node NCCL
    all_reduce_perf to fail on ALL participating nodes — not just the
    node with the bad port.  Always check IB status FIRST when triaging
    multi-node NCCL failures.

    H100 (default): expects 8 IB devices + 2 Ethernet.
    GB200: expects 4 IB devices + 1 Ethernet.
    """
    ibdev2netdev_output = ibdev2netdev_output or output
    topo = ibstat.NODE_TOPOLOGIES.get(node_type.lower(), ibstat.NODE_TOPOLOGIES["h100"])
    log.info(
        "Parsing IB status (%d chars ibdev2netdev, topology=%s)",
        len(ibdev2netdev_output),
        node_type,
    )
    result: dict[str, Any] = {
        "ibdev2netdev": ibstat.parse_ibdev2netdev(
            ibdev2netdev_output,
            expected_ib_devices=topo["ib"],
            expected_eth_devices=topo["eth"],
        ),
    }

    if ibstat_output and ibstat_output.strip():
        log.info("Parsing ibstat (%d chars)", len(ibstat_output))
        result["ibstat"] = ibstat.parse_ibstat(ibstat_output)

    severities = [result["ibdev2netdev"]["severity"]]
    if "ibstat" in result:
        severities.append(result["ibstat"]["severity"])
    result["severity"] = max(severities, key=lambda s: _SEVERITY_ORDER.get(s, 0))

    return result


@mcp.tool(annotations={"readOnlyHint": True})
@mcp_remediation_wrapper(project_repo="vhspace/gpu-diag-mcp")
def parse_retired_pages(
    retired_pages_output: Annotated[
        str,
        Field(
            description=(
                "CSV output from 'nvidia-smi --query-retired-pages="
                "gpu_uuid,retired_pages.address,retired_pages.cause --format=csv'"
            )
        ),
    ] = "",
    remapped_rows_output: Annotated[
        str,
        Field(
            description=(
                "Optional CSV output from 'nvidia-smi --query-remapped-rows="
                "gpu_uuid,remapped_rows.correctable,remapped_rows.uncorrectable,"
                "remapped_rows.pending,remapped_rows.failure --format=csv'"
            )
        ),
    ] = "",
    expected_gpu_count: Annotated[
        int | None,
        Field(
            description=(
                "Expected number of GPUs for baseline calculation. "
                "H100 = 8 (baseline 16 pages), GB200 = 4 (baseline 8 pages). "
                "When omitted, auto-detected from data."
            )
        ),
    ] = None,
    output: Annotated[
        str,
        Field(description="Alias for retired_pages_output — pass retired pages CSV here instead"),
    ] = "",
) -> dict[str, Any]:
    """Parse retired pages and optionally remapped rows from nvidia-smi.

    Normal baseline is 2 retired pages per GPU (1 SBE + 1 DBE):
    H100 (8 GPUs) = 16 total, GB200 (4 GPUs) = 8 total.
    Anything above baseline is flagged as warning; excessive DBE counts
    are critical.  Remapped row failures or pending remaps are also flagged.
    """
    retired_pages_output = retired_pages_output or output
    log.info(
        "Parsing retired pages (%d chars, expected_gpus=%s)",
        len(retired_pages_output),
        expected_gpu_count,
    )
    result: dict[str, Any] = {
        "retired_pages": retired_pages.parse_retired_pages(
            retired_pages_output, expected_gpu_count=expected_gpu_count
        ),
    }

    if remapped_rows_output and remapped_rows_output.strip():
        log.info("Parsing remapped rows (%d chars)", len(remapped_rows_output))
        result["remapped_rows"] = retired_pages.parse_remapped_rows(remapped_rows_output)

    severities = [result["retired_pages"]["severity"]]
    if "remapped_rows" in result:
        severities.append(result["remapped_rows"]["severity"])
    result["severity"] = max(severities, key=lambda s: _SEVERITY_ORDER.get(s, 0))

    return result


_NODE_HEADER_RE = NODE_HEADER_RE


@mcp.tool(annotations={"readOnlyHint": True})
@mcp_remediation_wrapper(project_repo="vhspace/gpu-diag-mcp")
def batch_diagnose(
    multi_host_output: Annotated[
        str,
        Field(
            description=(
                "Multi-host diagnostic output with node headers "
                "(--- hostname ---) and section markers "
                "(=IB=, =ECC=, =RETIRED=, =KERNEL=, =NVLINK=, =NCCL=)"
            )
        ),
    ] = "",
    node_type: Annotated[
        str,
        Field(
            description=(
                "Node GPU topology: 'h100' (8 IB + 2 ETH, default) or "
                "'gb200' (4 IB + 1 ETH). Determines IB device expectations."
            )
        ),
    ] = "h100",
    expected_gpu_count: Annotated[
        int | None,
        Field(
            description=(
                "Expected GPU count for retired pages baseline. "
                "H100 = 8, GB200 = 4. Auto-detected when omitted."
            )
        ),
    ] = None,
    expected_min_bw: Annotated[
        float,
        Field(description="Minimum acceptable NCCL bus bandwidth in GB/s"),
    ] = 360.0,
    output: Annotated[
        str,
        Field(description="Alias for multi_host_output — pass multi-host output here instead"),
    ] = "",
) -> dict[str, Any]:
    """Parse diagnostic output from multiple hosts in one call.

    Input format example::

        --- hostname1 ---
        =IB=
        mlx5_0 port 1 ==> ibs3 (Up)
        ...
        =ECC=
        index, ecc.errors.uncorrected.volatile.total
        0, 0
        ...
        =RETIRED=
        gpu_uuid, retired_pages.address, retired_pages.cause
        ...
        --- hostname2 ---
        =IB=
        ...

    Accepts node headers (--- hostname ---) and section markers
    (=IB=, =ECC=, =RETIRED=, =KERNEL=, =NVLINK=, =NCCL=).
    Routes each section to the appropriate parser and returns a
    per-node severity-ranked summary.
    """
    multi_host_output = multi_host_output or output
    log.info("Batch diagnose (%d chars, node_type=%s)", len(multi_host_output), node_type)
    return parse_batch(
        multi_host_output,
        node_type=node_type,
        expected_gpu_count=expected_gpu_count,
        expected_min_bw=expected_min_bw,
    )


@mcp.tool(annotations={"readOnlyHint": True})
@mcp_remediation_wrapper(project_repo="vhspace/gpu-diag-mcp")
def diagnose_nccl_failure(
    failing_nodes_ib_status: Annotated[
        str,
        Field(
            description=(
                "ibdev2netdev output from multiple nodes that failed multi-node "
                "NCCL, separated by node headers (e.g. '--- node1 ---' or "
                "'### hostname'). Each section contains that node's ibdev2netdev "
                "output."
            )
        ),
    ] = "",
    output: Annotated[
        str,
        Field(
            description="Alias for failing_nodes_ib_status — pass multi-node IB output here instead"
        ),
    ] = "",
) -> dict[str, Any]:
    """Given ibdev2netdev output from multiple nodes that failed multi-node
    NCCL, identify the root cause node.

    A single node with 1 IB port down can cascade failure to all nodes in
    the same NCCL communicator group.  This tool parses per-node IB status,
    identifies which node(s) have ports down, and explains the cascade
    pattern so operators know which specific node to pull for repair.
    """
    failing_nodes_ib_status = failing_nodes_ib_status or output
    log.info(
        "Diagnosing NCCL failure from multi-node IB status (%d chars)",
        len(failing_nodes_ib_status),
    )

    nodes = split_nodes(failing_nodes_ib_status)
    if not nodes:
        nodes["unknown"] = failing_nodes_ib_status

    per_node: list[dict[str, Any]] = []
    root_cause_nodes: list[str] = []

    for node_name, ib_text in nodes.items():
        parsed = ibstat.parse_ibdev2netdev(ib_text)
        entry: dict[str, Any] = {"node": node_name, **parsed}
        per_node.append(entry)
        if not parsed["all_ib_up"]:
            root_cause_nodes.append(node_name)

    if root_cause_nodes:
        explanation = (
            f"Node(s) with IB ports down: {', '.join(root_cause_nodes)}. "
            "When any node in an NCCL communicator group has an IB port down, "
            "the collective operation stalls or fails on ALL participating nodes — "
            "not just the degraded one. Fix or replace the node(s) with ports down "
            "to restore the entire group."
        )
        severity = "critical"
    else:
        explanation = (
            "All nodes show all IB ports up. The NCCL failure may be caused by "
            "transient fabric issues, switch-side problems, or a non-IB root cause "
            "(check XID errors, ECC, NVLink status, or NCCL debug logs)."
        )
        severity = "warning"

    return {
        "root_cause_nodes": root_cause_nodes,
        "explanation": explanation,
        "per_node": per_node,
        "total_nodes": len(per_node),
        "severity": severity,
    }


@mcp.tool(annotations={"readOnlyHint": True})
@mcp_remediation_wrapper(project_repo="vhspace/gpu-diag-mcp")
def generate_diagnostic_report(
    kernel_log_result: Annotated[
        str,
        Field(
            description=("JSON string from parse_kernel_xid_logs, or empty string if not collected")
        ),
    ] = "",
    ecc_result: Annotated[
        str,
        Field(description="JSON string from parse_ecc_output, or empty string if not collected"),
    ] = "",
    nccl_result: Annotated[
        str,
        Field(description="JSON string from parse_nccl_results, or empty string if not collected"),
    ] = "",
    nvlink_result: Annotated[
        str,
        Field(description="JSON string from parse_nvlink_status, or empty string if not collected"),
    ] = "",
    ib_result: Annotated[
        str,
        Field(description="JSON string from parse_ib_status, or empty string if not collected"),
    ] = "",
    retired_pages_result: Annotated[
        str,
        Field(description="JSON string from parse_retired_pages, or empty string if not collected"),
    ] = "",
) -> dict[str, Any]:
    """Generate a consolidated, severity-ranked diagnostic report from
    multiple GPU health check results.

    Accepts JSON strings (as returned by the other tools) for each check
    category.  Produces a unified report with findings sorted by severity
    (critical first) and actionable next-step recommendations.
    """
    import json

    log.info("Generating consolidated diagnostic report")

    checks: list[dict[str, Any]] = []

    check_map = {
        "kernel_logs": kernel_log_result,
        "ecc": ecc_result,
        "nccl": nccl_result,
        "nvlink": nvlink_result,
        "ib_status": ib_result,
        "retired_pages": retired_pages_result,
    }

    for name, raw in check_map.items():
        if not raw or not raw.strip():
            checks.append({"check": name, "status": "not_collected", "severity": "unknown"})
            continue
        try:
            data = json.loads(raw)
            sev = data.get("severity", "unknown")
            checks.append({"check": name, "status": "collected", "severity": sev, "data": data})
        except json.JSONDecodeError:
            checks.append({"check": name, "status": "parse_error", "severity": "unknown"})

    collected = [c for c in checks if c["status"] == "collected"]
    collected.sort(key=lambda c: _SEVERITY_ORDER.get(c["severity"], -1), reverse=True)

    critical = [c for c in collected if c["severity"] == "critical"]
    warning = [c for c in collected if c["severity"] == "warning"]
    ok = [c for c in collected if c["severity"] == "ok"]
    not_collected = [c for c in checks if c["status"] != "collected"]

    overall = "ok"
    if critical:
        overall = "critical"
    elif warning:
        overall = "warning"

    recommendations: list[str] = []
    for c in critical:
        recommendations.append(f"[CRITICAL] {c['check']}: investigate immediately")
    for c in warning:
        recommendations.append(f"[WARNING] {c['check']}: review for degradation")
    if not_collected:
        names = [c["check"] for c in not_collected]
        recommendations.append(f"Collect missing checks: {', '.join(names)}")

    if any(c["check"] == "ib_status" and c["severity"] == "critical" for c in checks):
        recommendations.insert(
            0,
            "[PRIORITY] IB port(s) down — this is the most common root cause of "
            "multi-node NCCL failures. Fix IB first before other checks.",
        )

    return {
        "overall_severity": overall,
        "summary": {
            "critical_count": len(critical),
            "warning_count": len(warning),
            "ok_count": len(ok),
            "not_collected_count": len(not_collected),
        },
        "checks": checks,
        "recommendations": recommendations,
    }


# ---------------------------------------------------------------------------
# ASGI app factory (for uvicorn / K8s deployment)
# ---------------------------------------------------------------------------


def create_app() -> Any:
    """Create an ASGI application for production HTTP deployment.

    Usage::

        uvicorn gpu_diag_mcp.server:create_app --factory --host 0.0.0.0 --port 8000
    """
    token = (
        settings.mcp_http_access_token.get_secret_value()
        if settings.mcp_http_access_token
        else None
    )
    return create_http_app(
        mcp, path="/mcp", auth_token=token, stateless_http=settings.stateless_http
    )


# ---------------------------------------------------------------------------
# CLI entry point (stdio or HTTP)
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point: ``gpu-diag-mcp`` command."""
    suppress_ssl_warnings()
    log.info("Starting gpu-diag-mcp v%s (log_level=%s)", __version__, settings.log_level)

    try:
        if settings.transport == "stdio":
            log.info("Starting stdio transport")
            mcp.run(transport="stdio")
        elif settings.transport == "http":
            if settings.mcp_http_access_token:
                mcp.add_middleware(
                    HttpAccessTokenAuth(settings.mcp_http_access_token.get_secret_value())
                )
            log.info("Starting HTTP transport on %s:%s", settings.host, settings.port)
            mcp.run(transport="http", host=settings.host, port=settings.port)
    except Exception as e:
        log.error("Failed to start MCP server: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
