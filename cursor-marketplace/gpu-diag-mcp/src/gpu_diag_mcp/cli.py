"""
gpu-diag-cli: CLI for GPU XID/SXid diagnostics and parser tools.

Provides the same capabilities as gpu-diag-mcp but via shell commands,
enabling AI agents to use GPU diagnostics with fewer tokens than MCP.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import typer
from mcp_common.agent_remediation import install_cli_exception_handler

from gpu_diag_mcp.parsers import (
    parse_batch,
    parse_ecc_csv,
    parse_ecc_full,
    parse_ibdev2netdev,
    parse_kernel_xid_logs,
    parse_nccl_results,
    parse_nvlink_errors,
    parse_nvlink_status,
    parse_retired_pages,
)
from gpu_diag_mcp.xid_catalog import load_sxid_catalog, load_xid_catalog, sxid_lookup, xid_lookup

app = typer.Typer(
    name="gpu-diag-cli",
    help="GPU XID/SXid diagnostics and parser tools.",
    no_args_is_help=True,
)
install_cli_exception_handler(app, project_repo="vhspace/gpu-diag-mcp")
xid_app = typer.Typer(name="xid", help="XID/SXid knowledge base lookups.", no_args_is_help=True)
parse_app = typer.Typer(name="parse", help="Parse GPU diagnostic output.", no_args_is_help=True)
app.add_typer(xid_app, name="xid")
app.add_typer(parse_app, name="parse")


def _read_input(file: str | None) -> str:
    """Read from file path or stdin."""
    if file:
        with open(file) as fh:
            return fh.read()
    if sys.stdin.isatty():
        typer.echo("Reading from stdin (Ctrl-D to end)...", err=True)
    return sys.stdin.read()


def _output(data: Any, as_json: bool = False) -> None:
    if as_json:
        typer.echo(json.dumps(data, indent=2, default=str))
        return
    if isinstance(data, dict):
        _print_dict(data, indent=0)
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                _print_dict(item, indent=0)
                typer.echo()
            else:
                typer.echo(str(item))
    else:
        typer.echo(str(data))


def _print_dict(d: dict, indent: int = 0) -> None:
    prefix = "  " * indent
    for k, v in d.items():
        if isinstance(v, dict):
            typer.echo(f"{prefix}{k}:")
            _print_dict(v, indent + 1)
        elif isinstance(v, list):
            if not v:
                typer.echo(f"{prefix}{k}: []")
            elif len(v) <= 3 and all(not isinstance(x, (dict, list)) for x in v):
                typer.echo(f"{prefix}{k}: {v}")
            else:
                typer.echo(f"{prefix}{k}: [{len(v)} items]")
                for i, item in enumerate(v):
                    if isinstance(item, dict):
                        typer.echo(f"{prefix}  [{i}]")
                        _print_dict(item, indent + 2)
                    else:
                        typer.echo(f"{prefix}  {item}")
        else:
            typer.echo(f"{prefix}{k}: {v}")


# ---------------------------------------------------------------------------
# xid sub-app
# ---------------------------------------------------------------------------


@xid_app.command()
def lookup(
    code: int = typer.Argument(help="XID or SXid error code to look up"),
    sxid: bool = typer.Option(False, "--sxid", help="Look up an SXid (NVSwitch) code instead"),
    driver: str = typer.Option("590", "--driver", "-d", help="Driver version for XID catalog"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Look up an XID or SXid error code."""
    if sxid:
        result = sxid_lookup(code)
    else:
        result = xid_lookup(code, driver_version=driver)

    if result is None or not result.get("found"):
        typer.echo(f"{'SXid' if sxid else 'XID'} {code}: not found in catalog", err=True)
        raise typer.Exit(1)

    if json_output:
        _output(result, as_json=True)
        return

    typer.echo(f"{'SXid' if sxid else 'XID'} {code}: {result.get('name', '?')}")
    if result.get("severity"):
        typer.echo(f"  severity: {result['severity']}")
    if result.get("category"):
        typer.echo(f"  category: {result['category']}")
    if result.get("description"):
        typer.echo(f"  description: {result['description']}")
    actions = result.get("recommended_actions")
    if actions:
        if isinstance(actions, list):
            typer.echo("  recommended_actions:")
            for a in actions:
                typer.echo(f"    - {a}")
        else:
            typer.echo(f"  recommended_actions: {actions}")


@xid_app.command("list")
def list_codes(
    sxid: bool = typer.Option(False, "--sxid", help="List SXid codes instead of XID codes"),
    driver: str = typer.Option("590", "--driver", "-d", help="Driver version for XID catalog"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """List all known XID or SXid codes."""
    if sxid:
        catalog = load_sxid_catalog()
        label = "SXid"
    else:
        catalog = load_xid_catalog(driver_version=driver)
        label = "XID"

    if json_output:
        _output(
            {
                "type": label,
                "count": len(catalog),
                "codes": [
                    {
                        "code": code,
                        "name": entry.get("name", "?"),
                        "severity": entry.get("severity", "?"),
                    }
                    for code, entry in sorted(catalog.items())
                ],
            },
            as_json=True,
        )
        return

    typer.echo(f"# {len(catalog)} {label} code(s)")
    for code, entry in sorted(catalog.items()):
        name = entry.get("name", "?")
        sev = entry.get("severity", "?")
        typer.echo(f"  {code:>5d}  [{sev}]  {name}")


# ---------------------------------------------------------------------------
# parse sub-app
# ---------------------------------------------------------------------------


@parse_app.command("kernel-logs")
def parse_kernel_logs_cmd(
    file: str | None = typer.Option(
        None, "--file", "-f", help="Path to log file (reads stdin if omitted)"
    ),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Parse kernel log text for XID, SXid, FBHUB, and assertion events."""
    text = _read_input(file)
    result = parse_kernel_xid_logs(text)

    if json_output:
        _output(result, as_json=True)
        return

    summary = result.get("summary", {})
    typer.echo(f"severity: {result.get('severity', '?')}")
    typer.echo(
        f"XID events: {summary.get('total_xid', 0)}  codes={summary.get('unique_xid_codes', [])}"
    )
    typer.echo(
        f"SXid events: {summary.get('total_sxid', 0)}  codes={summary.get('unique_sxid_codes', [])}"
    )
    typer.echo(
        f"FBHUB events: {summary.get('total_fbhub', 0)}  boot_time={summary.get('is_boot_time_fbhub', False)}"
    )
    typer.echo(f"Assert failures: {len(result.get('assert_failures', []))}")

    for ev in result.get("xid_events", [])[:20]:
        typer.echo(
            f"  XID {ev['xid_code']}  pci={ev['pci_bus']}  ts={ev['timestamp']}  gpu={ev.get('gpu_index', '?')}"
        )
    for ev in result.get("sxid_events", [])[:20]:
        typer.echo(f"  SXid {ev['sxid_code']}  pci={ev['pci_bus']}  ts={ev['timestamp']}")
    for ev in result.get("fbhub_events", [])[:10]:
        typer.echo(f"  FBHUB gpu={ev['gpu_index']}  ts={ev['timestamp']}  {ev['message'][:80]}")


@parse_app.command("ecc")
def parse_ecc_cmd(
    file: str | None = typer.Option(None, "--file", "-f", help="Path to ECC output file"),
    format: str = typer.Option("csv", "--format", help="Input format: csv or full"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Parse nvidia-smi ECC error output."""
    text = _read_input(file)
    if format == "full":
        result = parse_ecc_full(text)
    else:
        result = parse_ecc_csv(text)

    if json_output:
        _output(result, as_json=True)
        return

    summary = result.get("summary", {})
    typer.echo(f"severity: {result.get('severity', '?')}")
    typer.echo(f"total correctable: {summary.get('total_correctable', 0)}")
    typer.echo(f"total uncorrectable: {summary.get('total_uncorrectable', 0)}")
    typer.echo(f"volatile uncorrectable: {summary.get('any_volatile_uncorrectable', False)}")
    typer.echo(f"aggregate uncorrectable: {summary.get('any_aggregate_uncorrectable', False)}")
    typer.echo(f"high aggregate correctable: {summary.get('high_aggregate_correctable', False)}")

    for gpu in result.get("gpus", []):
        idx = gpu.get("index", "?")
        if format == "csv":
            typer.echo(
                f"  GPU {idx}: vol_corr={gpu.get('volatile_correctable', 0)} "
                f"vol_uncorr={gpu.get('volatile_uncorrectable', 0)} "
                f"agg_corr={gpu.get('aggregate_correctable', 0)} "
                f"agg_uncorr={gpu.get('aggregate_uncorrectable', 0)}"
            )
        else:
            vol = gpu.get("volatile", {})
            agg = gpu.get("aggregate", {})
            typer.echo(
                f"  GPU {idx}: ecc={gpu.get('ecc_mode_current', '?')} "
                f"vol_sram_u={vol.get('sram_uncorrectable', 0)} "
                f"vol_dram_u={vol.get('dram_uncorrectable', 0)} "
                f"agg_sram_u={agg.get('sram_uncorrectable', 0)} "
                f"agg_dram_u={agg.get('dram_uncorrectable', 0)}"
            )


@parse_app.command("nccl")
def parse_nccl_cmd(
    file: str | None = typer.Option(None, "--file", "-f", help="Path to NCCL test output"),
    expected_gpus: int = typer.Option(8, "--expected-gpus", "-g", help="Expected GPU count"),
    min_bw: float = typer.Option(
        360.0, "--min-bw", "-b", help="Minimum acceptable bus bandwidth (GB/s)"
    ),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Parse NCCL all_reduce_perf test output."""
    text = _read_input(file)
    result = parse_nccl_results(text, expected_gpus=expected_gpus, expected_min_bw=min_bw)

    if json_output:
        _output(result, as_json=True)
        return

    typer.echo(f"severity: {result.get('severity', '?')}")
    typer.echo(f"success: {result.get('success', False)}")
    typer.echo(
        f"avg bus bandwidth: {result.get('avg_busbw', 'N/A')} GB/s (min={result.get('expected_min_bw')})"
    )
    typer.echo(f"init complete: {result.get('init_complete', False)}")
    typer.echo(f"data rows: {len(result.get('data_rows', []))}")
    typer.echo(f"wrong answers: {result.get('wrong_count', 0)}")

    failures = result.get("failures", [])
    if failures:
        typer.echo(f"failures: {', '.join(failures)}")
    if result.get("waiting_for"):
        typer.echo(f"waiting for peers: {result['waiting_for']}")


@parse_app.command("ib")
def parse_ib_cmd(
    file: str | None = typer.Option(None, "--file", "-f", help="Path to ibdev2netdev output"),
    node_type: str = typer.Option(
        "h100",
        "--node-type",
        "-t",
        help="Node topology: h100 (8 IB + 2 ETH) or gb200 (4 IB + 1 ETH)",
    ),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Parse ibdev2netdev output. Flag any ports that are Down."""
    from gpu_diag_mcp.parsers.ibstat import NODE_TOPOLOGIES

    text = _read_input(file)
    topo = NODE_TOPOLOGIES.get(node_type.lower(), NODE_TOPOLOGIES["h100"])
    result = parse_ibdev2netdev(
        text, expected_ib_devices=topo["ib"], expected_eth_devices=topo["eth"]
    )

    if json_output:
        _output(result, as_json=True)
        return

    typer.echo(f"severity: {result.get('severity', '?')}")
    typer.echo(f"ports up: {result.get('ports_up_count', 0)}")
    typer.echo(f"all IB up: {result.get('all_ib_up', False)}")

    down = result.get("ports_down", [])
    if down:
        typer.echo(f"PORTS DOWN: {', '.join(down)}")
    missing_ib = result.get("missing_ib_devices", [])
    if missing_ib:
        typer.echo(f"MISSING IB: {', '.join(missing_ib)}")
    missing_eth = result.get("missing_eth_devices", [])
    if missing_eth:
        typer.echo(f"MISSING ETH: {', '.join(missing_eth)}")

    for dev in result.get("devices", []):
        state = dev["state"]
        marker = " *** DOWN ***" if state.lower() != "up" else ""
        typer.echo(f"  {dev['device']} port {dev['port']} => {dev['interface']} ({state}){marker}")


@parse_app.command("nvlink")
def parse_nvlink_cmd(
    file: str | None = typer.Option(
        None, "--file", "-f", help="Path to nvlink status or error output"
    ),
    errors: bool = typer.Option(
        False, "--errors", "-e", help="Parse error counters instead of status"
    ),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Parse nvidia-smi NVLink status or error output."""
    text = _read_input(file)
    if errors:
        result = parse_nvlink_errors(text)
    else:
        result = parse_nvlink_status(text)

    if json_output:
        _output(result, as_json=True)
        return

    typer.echo(f"severity: {result.get('severity', '?')}")
    typer.echo(f"total GPUs: {result.get('total_gpus', 0)}")

    if errors:
        typer.echo(f"total errors: {result.get('total_errors', 0)}")
        typer.echo(f"any errors: {result.get('any_errors', False)}")
        for gpu in result.get("gpus", []):
            if gpu.get("has_errors"):
                typer.echo(f"  GPU {gpu['index']}: {gpu['total_errors']} errors")
                for link_id, errs in gpu.get("link_errors", {}).items():
                    if sum(errs.values()) > 0:
                        typer.echo(f"    link {link_id}: {errs}")
    else:
        for gpu in result.get("gpus", []):
            inactive = gpu.get("inactive_links", [])
            degraded = gpu.get("degraded_links", [])
            active = gpu.get("active_link_count", 0)
            expected = gpu.get("expected_links", 18)
            status = "OK" if not gpu.get("has_issue") else "ISSUE"
            typer.echo(f"  GPU {gpu['index']}: {active}/{expected} links [{status}]")
            if inactive:
                typer.echo(f"    inactive: {inactive}")
            if degraded:
                typer.echo(f"    degraded speed: {degraded}")


@parse_app.command("retired-pages")
def parse_retired_pages_cmd(
    file: str | None = typer.Option(None, "--file", "-f", help="Path to retired pages output"),
    expected_gpus: int | None = typer.Option(
        None,
        "--expected-gpus",
        "-g",
        help="Expected GPU count for baseline (H100=8, GB200=4). Auto-detected if omitted.",
    ),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Parse nvidia-smi retired pages output."""
    text = _read_input(file)
    result = parse_retired_pages(text, expected_gpu_count=expected_gpus)

    if json_output:
        _output(result, as_json=True)
        return

    summary = result.get("summary", {})
    typer.echo(f"severity: {result.get('severity', '?')}")
    typer.echo(f"total retired: {summary.get('total_retired', 0)}")
    typer.echo(f"single-bit ECC: {summary.get('total_single_bit', 0)}")
    typer.echo(f"double-bit ECC: {summary.get('total_double_bit', 0)}")
    baseline = summary.get("normal_baseline", "?")
    typer.echo(f"normal baseline ({baseline} = 2/GPU): {summary.get('is_normal_baseline', False)}")
    typer.echo(f"GPUs: {summary.get('gpu_count', 0)}")

    for gpu in result.get("gpus", []):
        typer.echo(
            f"  {gpu['gpu_uuid'][:20]}...  "
            f"sbe={gpu['single_bit_ecc']}  dbe={gpu['double_bit_ecc']}  total={gpu['total']}"
        )


@parse_app.command("batch")
def parse_batch_cmd(
    file: str | None = typer.Option(
        None, "--file", "-f", help="Path to multi-host diagnostic output"
    ),
    node_type: str = typer.Option("h100", "--node-type", "-t", help="Node topology: h100 or gb200"),
    expected_gpus: int | None = typer.Option(
        None, "--expected-gpus", "-g", help="Expected GPU count for baseline"
    ),
    min_bw: float = typer.Option(
        360.0, "--min-bw", "-b", help="Minimum acceptable NCCL bus bandwidth (GB/s)"
    ),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Parse multi-host diagnostic output. Reads from file or stdin."""
    text = _read_input(file)
    result = parse_batch(
        text,
        node_type=node_type,
        expected_gpu_count=expected_gpus,
        expected_min_bw=min_bw,
    )

    if json_output:
        _output(result, as_json=True)
        return

    summary = result.get("summary", {})
    total = summary.get("total_nodes", 0)
    ok_count = summary.get("ok", 0)
    crit_count = summary.get("critical", 0)
    warn_count = summary.get("warning", 0)

    typer.echo(
        f"# {total} nodes scanned ({ok_count} ok, {crit_count} critical, {warn_count} warning)"
    )
    typer.echo()

    crit_nodes = [n for n in result.get("nodes", []) if n["overall_severity"] == "critical"]
    warn_nodes = [n for n in result.get("nodes", []) if n["overall_severity"] == "warning"]
    ok_nodes = [n for n in result.get("nodes", []) if n["overall_severity"] == "ok"]

    if crit_nodes:
        typer.echo("CRITICAL:")
        for n in crit_nodes:
            issues = _summarize_node_issues(n)
            typer.echo(f"  {n['node']}: {issues}")
        typer.echo()

    if warn_nodes:
        typer.echo("WARNING:")
        for n in warn_nodes:
            issues = _summarize_node_issues(n)
            typer.echo(f"  {n['node']}: {issues}")
        typer.echo()

    if ok_nodes:
        names = ", ".join(n["node"] for n in ok_nodes)
        typer.echo(f"OK ({len(ok_nodes)} nodes):")
        typer.echo(f"  {names}")


def _summarize_node_issues(node: dict[str, Any]) -> str:
    """Build a short issue description from a node's checks."""
    parts: list[str] = []
    checks = node.get("checks", {})

    ib = checks.get("ib", {})
    if ib.get("severity") == "critical":
        down = ib.get("ports_down", [])
        if down:
            parts.append(f"IB port {', '.join(down)} DOWN")
        else:
            parts.append("IB issues")

    ecc_check = checks.get("ecc", {})
    if ecc_check.get("severity") in ("critical", "warning"):
        parts.append(f"ECC uncorrectable={ecc_check.get('total_uncorrectable', 0)}")

    kernel = checks.get("kernel_logs", {})
    if kernel.get("severity") in ("critical", "warning"):
        codes = kernel.get("unique_xid_codes", [])
        parts.append(f"XID {','.join(str(c) for c in codes)}" if codes else "kernel log issues")

    nvl = checks.get("nvlink", {})
    if nvl.get("severity") in ("critical", "warning"):
        parts.append("NVLink issues")

    rp = checks.get("retired_pages", {})
    if rp.get("severity") in ("critical", "warning"):
        parts.append(f"retired_pages={rp.get('total_retired', 0)}")

    nccl_check = checks.get("nccl", {})
    if nccl_check.get("severity") in ("critical", "warning"):
        bw = nccl_check.get("avg_busbw")
        parts.append(f"NCCL bw={bw}" if bw is not None else "NCCL failures")

    return "; ".join(parts) if parts else "unknown issue"


def main() -> None:
    app()


if __name__ == "__main__":
    main()
