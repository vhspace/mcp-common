"""Parsers for GPU diagnostic output formats."""

from __future__ import annotations

from gpu_diag_mcp.parsers.batch import parse_batch
from gpu_diag_mcp.parsers.ecc import parse_ecc_csv, parse_ecc_full
from gpu_diag_mcp.parsers.ibstat import (
    GB200_ETH_DEVICES,
    GB200_IB_DEVICES,
    H100_ETH_DEVICES,
    H100_IB_DEVICES,
    NODE_TOPOLOGIES,
    parse_ibdev2netdev,
    parse_ibstat,
)
from gpu_diag_mcp.parsers.kernel_logs import parse_kernel_xid_logs
from gpu_diag_mcp.parsers.nccl import parse_nccl_results
from gpu_diag_mcp.parsers.nvlink import parse_nvlink_errors, parse_nvlink_status
from gpu_diag_mcp.parsers.retired_pages import parse_remapped_rows, parse_retired_pages

__all__ = [
    "GB200_ETH_DEVICES",
    "GB200_IB_DEVICES",
    "H100_ETH_DEVICES",
    "H100_IB_DEVICES",
    "NODE_TOPOLOGIES",
    "parse_batch",
    "parse_ecc_csv",
    "parse_ecc_full",
    "parse_ibdev2netdev",
    "parse_ibstat",
    "parse_kernel_xid_logs",
    "parse_nccl_results",
    "parse_nvlink_errors",
    "parse_nvlink_status",
    "parse_remapped_rows",
    "parse_retired_pages",
]
