from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from pathlib import Path
from typing import Annotated, Any

from mcp.server.experimental.task_context import ServerTaskContext
from pydantic import Field

logger = logging.getLogger("redfish_mcp.server")
from mcp.server.fastmcp import Context
from mcp.shared.experimental.tasks.helpers import MODEL_IMMEDIATE_RESPONSE_KEY, is_terminal
from mcp.types import (
    CallToolResult,
    CreateTaskResult,
    ImageContent,
    TaskMetadata,
    TextContent,
    ToolAnnotations,
)
from mcp_common import get_version, health_resource, setup_logging, suppress_ssl_warnings
from mcp_common.agent_remediation import mcp_remediation_wrapper

from ._util import _json_text
from .bios import discover_bios_settings_url
from .bios_diff import diff_attributes, diff_attributes_smart, get_bios_attributes
from .boot import get_allowable_targets, pick_target
from .chassis_telemetry import collect_power_info, collect_thermal_info
from .dell_recovery import RecoveryResult, run_dell_grub_recovery
from .firmware_inventory import collect_firmware_inventory, get_vendor_errata_urls
from .firmware_update import poll_firmware_task, upload_firmware_image, wait_for_task_completion
from .hardware_docs import (
    HardwareDocsCache,
    get_firmware_update_info,
    get_hardware_docs,
    load_hardware_database,
)
from .helpers import CurlCommandBuilder, ResponseBuilder, SystemFetcher, execution_mode_handler
from .instrumented_fastmcp import InstrumentedFastMCP
from .inventory import collect_drive_inventory
from .jobs import ConcurrencyLimiter
from .kvm.tools import (
    kvm_close as _kvm_close,
)
from .kvm.tools import (
    kvm_screen as _kvm_screen,
)
from .kvm.tools import (
    kvm_sendkey as _kvm_sendkey,
)
from .kvm.tools import (
    kvm_sendkeys as _kvm_sendkeys,
)
from .kvm.tools import (
    kvm_status as _kvm_status,
)
from .kvm.tools import (
    kvm_type_and_read as _kvm_type_and_read,
)
from .manager_info import collect_manager_ethernet, collect_manager_info
from .power_actions import InvalidActionError, PowerAction, resolve_reset_type
from .redfish import RedfishClient, to_abs
from .screen_analysis import analyze_screenshot
from .screen_capture import (
    DellPrivilegeError,
    capture_screen_cgi,
    capture_screen_dell,
    capture_screen_redfish,
    detect_idrac_generation,
    detect_vendor,
    download_dump_redfish,
    is_screenshot_supported,
    vendor_from_manufacturer,
    vendor_from_model,
    vendor_methods,
)
from .screenshot_cache import ScreenshotCache
from .system_inventory import (
    collect_memory_inventory,
    collect_pcie_inventory,
    collect_processor_inventory,
)
from .vision import extract_text_from_screenshot


def _as_call_tool_result(
    structured: dict[str, Any], *, is_error: bool | None = None
) -> CallToolResult:
    ok = bool(structured.get("ok", True))
    err = (not ok) if is_error is None else bool(is_error)
    return CallToolResult(
        content=[TextContent(type="text", text=_json_text(structured))],
        structuredContent=structured,
        isError=err,
    )


async def _create_background_task(
    *,
    ctx: Context,
    ttl_ms: int = 60 * 60 * 1000,
    model_immediate_response: str | None = None,
    work,
) -> CreateTaskResult:
    """Create and spawn an MCP task (CreateTaskResult) without requiring task-augmented invocation."""
    exp = getattr(ctx.request_context, "experimental", None)
    support = getattr(exp, "_task_support", None)
    session = getattr(exp, "_session", None)
    if support is None or session is None:
        raise RuntimeError("Task support not enabled or session unavailable")

    task = await support.store.create_task(TaskMetadata(ttl=ttl_ms))

    task_ctx = ServerTaskContext(
        task=task,
        store=support.store,
        session=session,
        queue=support.queue,
        handler=support.handler,
    )

    async def execute() -> None:
        try:
            result = await work(task_ctx)
            if not is_terminal(task_ctx.task.status):
                await task_ctx.complete(result)
        except Exception as e:
            if not is_terminal(task_ctx.task.status):
                await task_ctx.fail(str(e))

    support.task_group.start_soon(execute)

    meta: dict[str, Any] | None = None
    if model_immediate_response is not None:
        meta = {MODEL_IMMEDIATE_RESPONSE_KEY: model_immediate_response}

    return CreateTaskResult(task=task, **({"_meta": meta} if meta else {}))


def create_mcp_app():
    """Create an MCP server app with comprehensive Redfish tools.

    Returns:
        tuple: (mcp_app, tools_dict) where tools_dict contains callable tool functions for testing
    """

    mcp = InstrumentedFastMCP("redfish-mcp")
    agent = mcp.agent_controller
    # Conservative defaults: BMCs can be fragile under concurrency.
    limiter = ConcurrencyLimiter(global_limit=16, per_key_limit=1)
    docs_cache = HardwareDocsCache()
    screenshot_cache = ScreenshotCache(enabled=False)

    # Enable MCP task support (tasks/get, tasks/result, tasks/list, tasks/cancel).
    # Note: FastMCP doesn't surface this API; we access the low-level server.
    with suppress(Exception):
        mcp._mcp_server.experimental.enable_tasks()

    # Store tool references for testing
    tools = {}
    _wrap = mcp_remediation_wrapper(project_repo="vhspace/redfish-mcp")

    def _client(
        host: str, user: str, password: str, verify_tls: bool, timeout_s: int
    ) -> RedfishClient:
        logger.debug(
            "Creating RedfishClient for host=%s verify_tls=%s timeout=%ds",
            host,
            verify_tls,
            timeout_s,
        )
        return RedfishClient(
            host=host, user=user, password=password, verify_tls=verify_tls, timeout_s=timeout_s
        )

    async def _to_thread(fn, /, *args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)

    def _curl(verify_tls: bool) -> CurlCommandBuilder:
        """Get a curl command builder with the specified TLS verification setting."""
        return CurlCommandBuilder(verify_tls=verify_tls)

    # ==================== Agent Hinting / Observation Store ====================

    async def redfish_agent_report_observation(
        host: str,
        kind: str,
        summary: str,
        details: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        confidence: float | None = None,
        ttl_hours: int | None = 72,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Store an agent observation about a host for later reuse.

        Notes:
        - This is local MCP state (SQLite), not a Redfish write.
        - Avoid secrets in `details`.
        """
        reporter_id = None
        if ctx is not None:
            try:
                reporter_id = ctx.client_id
            except Exception:
                reporter_id = None

        rec = agent.report_observation(
            host=host,
            kind=kind,
            summary=summary,
            details=details,
            tags=tags,
            confidence=confidence,
            reporter_id=reporter_id,
            ttl_hours=ttl_hours,
        )
        if not rec.get("ok"):
            return ResponseBuilder.error(str(rec.get("error", "failed to store observation")))
        return ResponseBuilder.success(host=host, **rec)

    tools["redfish_agent_report_observation"] = redfish_agent_report_observation
    mcp.tool(
        annotations=ToolAnnotations(
            title="Report Agent Observation",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
        )
    )(_wrap(redfish_agent_report_observation))

    async def redfish_agent_list_observations(
        host: str,
        limit: int = 20,
        include_expired: bool = False,
    ) -> dict[str, Any]:
        """List stored agent observations for a host."""
        rec = agent.list_observations(host=host, limit=limit, include_expired=include_expired)
        if not rec.get("ok"):
            return ResponseBuilder.error(str(rec.get("error", "failed to list observations")))
        return ResponseBuilder.success(**rec)

    tools["redfish_agent_list_observations"] = redfish_agent_list_observations
    mcp.tool(
        annotations=ToolAnnotations(
            title="List Agent Observations",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
        )
    )(_wrap(redfish_agent_list_observations))

    async def redfish_agent_get_host_stats(
        host: str,
        window_minutes: int = 60,
    ) -> dict[str, Any]:
        """Get recent tool-call statistics for a host."""
        rec = agent.get_host_stats(host=host, window_minutes=window_minutes)
        if not rec.get("ok"):
            return ResponseBuilder.error(str(rec.get("error", "failed to get stats")))
        return ResponseBuilder.success(**rec)

    tools["redfish_agent_get_host_stats"] = redfish_agent_get_host_stats
    mcp.tool(
        annotations=ToolAnnotations(
            title="Get Host Tool-Call Stats",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
        )
    )(_wrap(redfish_agent_get_host_stats))

    # ==================== Read Operations ====================

    async def redfish_diff_bios_settings(
        host_a: str,
        host_b: str,
        user: str,
        password: str,
        verify_tls: bool = False,
        timeout_s: int = 30,
        keys_like: str | None = None,
        only_diff: bool = True,
        smart_match: bool = True,
        execution_mode: str = "execute",
    ) -> dict[str, Any]:
        """Diff BIOS Attributes between two machines (read-only).

        Args:
            host_a: First host IP/hostname
            host_b: Second host IP/hostname
            user: Redfish username
            password: Redfish password
            verify_tls: Verify TLS certificates (default: False)
            timeout_s: Request timeout in seconds (default: 30)
            keys_like: Filter attributes by name substring (optional)
            only_diff: Return only differences (default: True)
            smart_match: Use semantic matching for different BIOS versions (default: True)
                AI HINT: When True, handles BIOS firmware quirks where different versions
                use different naming (e.g., "SMTControl_0037" vs "SMTControl").
                Automatically highlights critical settings and provides summary.
            execution_mode: "execute" or "render_curl" (default: "execute")

        If execution_mode == "render_curl", returns equivalent curl commands and does not execute.
        """
        if execution_mode == "render_curl":
            curl = _curl(verify_tls)
            return execution_mode_handler(
                verify_tls,
                [
                    "# For each host, fetch the BIOS object and compare Bios.Attributes:",
                    curl.get("/redfish/v1/Systems"),
                    "# Then GET the discovered system member @odata.id + '/Bios' for host A and host B.",
                ],
            )

        c1 = _client(host_a, user, password, verify_tls, timeout_s)
        c2 = _client(host_b, user, password, verify_tls, timeout_s)

        ep1 = await _to_thread(c1.discover_system)
        ep2 = await _to_thread(c2.discover_system)

        ctx: Context = mcp.get_context()
        await ctx.report_progress(progress=1, total=4, message=f"Fetching BIOS from {host_a}")
        a, a_url, a_err = await _to_thread(get_bios_attributes, c1, ep1)
        await ctx.report_progress(progress=2, total=4, message=f"Fetching BIOS from {host_b}")
        b, b_url, b_err = await _to_thread(get_bios_attributes, c2, ep2)
        await ctx.report_progress(progress=3, total=4, message="Diffing attributes")

        if a_err or b_err or not a or not b:
            return ResponseBuilder.error(
                "Failed to get BIOS attributes from one or both hosts",
                host_a=host_a,
                host_b=host_b,
                bios_url_a=a_url,
                bios_url_b=b_url,
                error_a=a_err,
                error_b=b_err,
            )

        # Choose diff algorithm based on smart_match
        if smart_match:
            d = diff_attributes_smart(a, b, keys_like=keys_like)
            # For smart mode, we filter based on showing only differences/critical
            if only_diff:
                # Keep matched entries only if they differ
                d["matched"] = [m for m in d["matched"] if not m["values_match"]]
                d["counts"]["matched"] = len(d["matched"])
        else:
            d = diff_attributes(a, b, keys_like=keys_like)
            if only_diff:
                d.pop("same", None)
                d.get("counts", {}).pop("same", None)

        await ctx.report_progress(progress=4, total=4, message="Diff complete")
        return ResponseBuilder.success(
            host_a=host_a,
            host_b=host_b,
            bios_url_a=a_url,
            bios_url_b=b_url,
            diff=d,
        )

    tools["redfish_diff_bios_settings"] = redfish_diff_bios_settings
    mcp.tool(
        annotations=ToolAnnotations(
            title="Diff BIOS Settings",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
        )
    )(_wrap(redfish_diff_bios_settings))

    async def redfish_get_info(
        host: str,
        user: str,
        password: str,
        info_types: list[str] | None = None,
        verify_tls: bool = False,
        timeout_s: int = 30,
        execution_mode: str = "execute",
    ) -> dict[str, Any]:
        """Get comprehensive system information with configurable options.

        A unified info retrieval tool that can fetch multiple types of information
        in a single call. Much more efficient than calling multiple separate tools.

        Args:
            info_types: List of info types to retrieve. Options:
                - "system" - Basic system info (manufacturer, model, serial, BIOS version)
                - "boot" - Current boot override settings and allowable targets
                - "bios_current" - Current BIOS attributes (can be large!)
                - "bios_pending" - Pending BIOS changes awaiting reboot
                - "drives" - NVMe/drive inventory
                - "power" - Power supply status, consumption, and voltage readings
                - "thermal" - Temperature sensors and fan speeds
                - "processors" - CPU inventory (model, cores, threads, speed)
                - "memory" - Memory DIMM inventory with capacity summary
                - "pcie_devices" - PCIe device inventory (GPUs, NICs, NVMe)
                - "manager" - BMC/Manager details and network service config
                - "manager_ethernet" - BMC network interface configuration (IPs, MAC, DHCP)
                - "all" - Everything (equivalent to all options)
                Default: ["system", "boot"] if not specified

        Returns comprehensive information based on requested types.

        If execution_mode == \"render_curl\", returns equivalent curl commands and does not execute.
        """
        if execution_mode == "render_curl":
            curl = _curl(verify_tls)
            return execution_mode_handler(
                verify_tls,
                [
                    "# Get system information:",
                    curl.get("/redfish/v1/Systems"),
                    curl.get("/redfish/v1/Systems/1"),
                ],
            )

        valid_types = {
            "system",
            "boot",
            "bios_current",
            "bios_pending",
            "drives",
            "power",
            "thermal",
            "processors",
            "memory",
            "pcie_devices",
            "manager",
            "manager_ethernet",
            "all",
        }

        if not info_types:
            info_types = ["system", "boot"]

        unknown = [t for t in info_types if t not in valid_types]
        if unknown:
            return ResponseBuilder.error(
                f"Unknown info_type(s): {', '.join(unknown)}",
                valid_types=sorted(valid_types - {"all"}),
            )

        if "all" in info_types:
            info_types = [
                "system",
                "boot",
                "bios_current",
                "bios_pending",
                "drives",
                "power",
                "thermal",
                "processors",
                "memory",
                "pcie_devices",
                "manager",
                "manager_ethernet",
            ]

        c = _client(host, user, password, verify_tls, timeout_s)
        ep = await _to_thread(c.discover_system)

        result: dict[str, Any] = {
            "ok": True,
            "host": host,
            "system_url": ep.system_url,
            "info_types": info_types,
        }

        # Get system info (needed for most operations)
        fetcher = SystemFetcher(c, ep)
        system, err_response = fetcher.get_system_or_error_response(host)
        if err_response:
            err_response["info_types"] = info_types
            return err_response

        # System info (basic details + BIOS version)
        if "system" in info_types:
            result["system"] = {
                "Id": system.get("Id"),
                "Name": system.get("Name"),
                "Manufacturer": system.get("Manufacturer"),
                "Model": system.get("Model"),
                "SerialNumber": system.get("SerialNumber"),
                "BiosVersion": system.get("BiosVersion"),
                "PowerState": system.get("PowerState"),
                "Status": system.get("Status"),
            }

        # Boot override settings
        if "boot" in info_types:
            boot = system.get("Boot") or {}
            allowable = get_allowable_targets(system)
            result["boot"] = {
                "BootSourceOverrideEnabled": boot.get("BootSourceOverrideEnabled"),
                "BootSourceOverrideTarget": boot.get("BootSourceOverrideTarget"),
                "BootSourceOverrideMode": boot.get("BootSourceOverrideMode"),
                "AllowableTargets": allowable,
            }

        # Current BIOS attributes
        if "bios_current" in info_types:
            current, current_url, current_err = await _to_thread(get_bios_attributes, c, ep)
            if current_err:
                result["bios_current"] = {"error": current_err, "url": current_url}
            else:
                result["bios_current"] = {
                    "url": current_url,
                    "attributes": current,
                    "count": len(current) if current else 0,
                }

        # Pending BIOS changes
        if "bios_pending" in info_types:
            current, _, _ = await _to_thread(get_bios_attributes, c, ep)
            settings_url, _bios_url, _ = await _to_thread(discover_bios_settings_url, c, ep)

            if not settings_url or not current:
                result["bios_pending"] = {
                    "has_pending": False,
                    "note": "No pending settings object found or BIOS not accessible",
                }
            else:
                pending, pending_err = await _to_thread(c.get_json_maybe, settings_url)
                if pending_err or not pending:
                    result["bios_pending"] = {
                        "has_pending": False,
                        "error": pending_err,
                    }
                else:
                    pending_attrs = pending.get("Attributes", {})
                    if pending_attrs and isinstance(pending_attrs, dict):
                        d = diff_attributes(current, pending_attrs)
                        has_changes = (
                            len(d.get("only_a", [])) > 0
                            or len(d.get("only_b", [])) > 0
                            or len(d.get("different", [])) > 0
                        )
                        d.pop("same", None)
                        d.get("counts", {}).pop("same", None)
                        result["bios_pending"] = {
                            "has_pending": has_changes,
                            "url": settings_url,
                            "changes": d if has_changes else {},
                        }
                    else:
                        result["bios_pending"] = {"has_pending": False}

        # Drive inventory
        if "drives" in info_types:
            inv = await _to_thread(collect_drive_inventory, c, ep, nvme_only=True)
            result["drives"] = {
                "count": inv.get("count", 0),
                "drives": inv.get("drives", []),
            }

        def _compact(d: dict[str, Any]) -> dict[str, Any]:
            """Strip debug/provenance keys to reduce token count in aggregated responses."""
            d.pop("sources", None)
            if not d.get("errors"):
                d.pop("errors", None)
            return d

        if "power" in info_types:
            power = await _to_thread(collect_power_info, c)
            result["power"] = _compact(power)

        if "thermal" in info_types:
            thermal = await _to_thread(collect_thermal_info, c)
            result["thermal"] = _compact(thermal)

        if "processors" in info_types:
            proc = await _to_thread(collect_processor_inventory, c, ep)
            result["processors"] = _compact(proc)

        if "memory" in info_types:
            mem = await _to_thread(collect_memory_inventory, c, ep)
            result["memory"] = _compact(mem)

        if "pcie_devices" in info_types:
            pcie = await _to_thread(collect_pcie_inventory, c, ep)
            result["pcie_devices"] = _compact(pcie)

        if "manager" in info_types:
            mgr = await _to_thread(collect_manager_info, c)
            result["manager_info"] = _compact(mgr)

        if "manager_ethernet" in info_types:
            mgr_eth = await _to_thread(collect_manager_ethernet, c)
            result["manager_ethernet"] = _compact(mgr_eth)

        for key in list(result.keys()):
            if isinstance(result[key], dict):
                result[key].pop("sources", None)
                if not result[key].get("errors"):
                    result[key].pop("errors", None)

        return result

    tools["redfish_get_info"] = redfish_get_info
    mcp.tool(
        annotations=ToolAnnotations(
            title="Get System Information",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
        )
    )(_wrap(redfish_get_info))

    async def redfish_list_bmc_users(
        host: str,
        user: str,
        password: str,
        verify_tls: bool = False,
        timeout_s: int = 30,
        execution_mode: str = "execute",
    ) -> dict[str, Any]:
        """List BMC users from AccountService/Accounts.

        Returns account details for each user visible via Redfish.
        """
        if execution_mode == "render_curl":
            curl = _curl(verify_tls)
            return execution_mode_handler(
                verify_tls,
                [
                    "# Get account service and account collection:",
                    curl.get("/redfish/v1/AccountService"),
                    curl.get("/redfish/v1/AccountService/Accounts"),
                    "# Then GET each member @odata.id for full account details",
                ],
            )

        c = _client(host, user, password, verify_tls, timeout_s)
        account_service_url = f"{c.base_url}/redfish/v1/AccountService"
        account_service, account_service_err = await _to_thread(
            c.get_json_maybe, account_service_url
        )
        if account_service_err or not account_service:
            return ResponseBuilder.error(
                "Failed to fetch AccountService",
                host=host,
                account_service_url=account_service_url,
                error=account_service_err,
            )

        accounts_obj = account_service.get("Accounts")
        accounts_rel = (
            accounts_obj.get("@odata.id") if isinstance(accounts_obj, dict) else None
        ) or "/redfish/v1/AccountService/Accounts"
        accounts_url = to_abs(c.base_url, str(accounts_rel))
        account_collection, collection_err = await _to_thread(c.get_json_maybe, accounts_url)
        if collection_err or not account_collection:
            return ResponseBuilder.error(
                "Failed to fetch account collection",
                host=host,
                account_collection_url=accounts_url,
                error=collection_err,
            )

        members = account_collection.get("Members")
        if not isinstance(members, list):
            members = []

        users: list[dict[str, Any]] = []
        failed_members: list[dict[str, str]] = []
        for member in members:
            if not isinstance(member, dict):
                continue
            member_odata = member.get("@odata.id")
            if not isinstance(member_odata, str):
                continue
            member_url = to_abs(c.base_url, member_odata)
            account, account_err = await _to_thread(c.get_json_maybe, member_url)
            if account_err or not account:
                failed_members.append({"url": member_url, "error": account_err or "unknown error"})
                continue

            users.append(
                {
                    "id": account.get("Id"),
                    "username": account.get("UserName"),
                    "role_id": account.get("RoleId"),
                    "enabled": account.get("Enabled"),
                    "locked": account.get("Locked"),
                    "url": member_url,
                }
            )

        return ResponseBuilder.success(
            host=host,
            account_service_url=account_service_url,
            account_collection_url=accounts_url,
            count=len(users),
            users=users,
            failed_member_count=len(failed_members),
            failed_members=failed_members,
        )

    tools["redfish_list_bmc_users"] = redfish_list_bmc_users
    mcp.tool(
        annotations=ToolAnnotations(
            title="List BMC Users",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
        )
    )(_wrap(redfish_list_bmc_users))

    async def redfish_query(
        host: str,
        user: str,
        password: str,
        query_type: str,
        key: str | None = None,
        verify_tls: bool = False,
        timeout_s: int = 30,
        include_setter_info: bool = False,
        execution_mode: str = "execute",
    ) -> dict[str, Any]:
        """Query specific settings and get information about how to modify them.

        A flexible query tool for probing individual settings without fetching entire objects.

        Query Types:
        - "bios_attribute" - Check a specific BIOS attribute (e.g., SMT_Enable, Re_SizeBARSupport_00B2)
        - "boot_setting" - Check boot override settings (key: "enabled", "target", or "mode")
        - "power_state" - Check system power state
        - "health" - Check system health status
        - "nic_pxe" - Check if a NIC has PXE enabled (requires key: NIC ID)
        - "list_nics" - List available NICs with their configuration
        - "list_bios_attributes" - List all available BIOS attributes (with optional filter)
        - "bmc_log_services" - List available BMC log services (SEL, Lifecycle, etc.)

        Args:
            query_type: Type of query (see above)
            key: Specific key/attribute to query (required for some query types)
            include_setter_info: If True, include information about how to modify this setting

        Returns query result with current value and optional setter information.

        If execution_mode == \"render_curl\", returns equivalent curl commands and does not execute.
        """
        if execution_mode == "render_curl":
            curl = _curl(verify_tls)
            cmds = [
                f"# Query type: {query_type}",
                curl.get("/redfish/v1/Systems"),
                curl.get("/redfish/v1/Systems/1"),
            ]
            if query_type == "bios_attribute":
                cmds.append(curl.get("/redfish/v1/Systems/1/Bios"))
            elif query_type == "list_nics":
                cmds.append(curl.get("/redfish/v1/Systems/1/NetworkInterfaces"))
            return execution_mode_handler(verify_tls, cmds)

        c = _client(host, user, password, verify_tls, timeout_s)
        ep = await _to_thread(c.discover_system)
        fetcher = SystemFetcher(c, ep)
        system, err_response = fetcher.get_system_or_error_response(host)

        if err_response:
            err_response["query_type"] = query_type
            return err_response

        result: dict[str, Any] = {
            "ok": True,
            "host": host,
            "query_type": query_type,
            "key": key,
        }

        # ==================== BIOS Attribute Query ====================
        if query_type == "bios_attribute":
            if not key:
                return ResponseBuilder.error("key parameter required for bios_attribute query")

            attrs, _bios_url, err = await _to_thread(get_bios_attributes, c, ep)
            if err or not attrs:
                result["ok"] = False
                result["error"] = err or "Failed to get BIOS attributes"
                return result

            if key in attrs:
                result["found"] = True
                result["current_value"] = attrs[key]
            else:
                result["found"] = False
                result["error"] = f"BIOS attribute '{key}' not found"
                # Try to find similar keys
                similar = [k for k in attrs if key.lower() in k.lower()][:10]
                if similar:
                    result["similar_keys"] = similar

            if include_setter_info and result.get("found"):
                settings_url, _, _ = await _to_thread(discover_bios_settings_url, c, ep)
                result["setter_info"] = {
                    "tool": "redfish_set_bios_attributes",
                    "writable": settings_url is not None,
                    "example": {
                        "tool": "redfish_set_bios_attributes",
                        "arguments": {
                            "host": host,
                            "attributes": {key: "<new_value>"},
                            "allow_write": True,
                        },
                    }
                    if settings_url
                    else None,
                    "note": "Changes require reboot to apply"
                    if settings_url
                    else "BIOS not writable via Redfish",
                }

        # ==================== Boot Setting Query ====================
        elif query_type == "boot_setting":
            boot = system.get("Boot") or {}
            valid_keys = ["enabled", "target", "mode"]

            if key and key not in valid_keys:
                return ResponseBuilder.error(
                    f"Invalid boot key '{key}'. Must be one of: {valid_keys}"
                )

            mapping = {
                "enabled": "BootSourceOverrideEnabled",
                "target": "BootSourceOverrideTarget",
                "mode": "BootSourceOverrideMode",
            }

            if key:
                redfish_key = mapping[key]
                result["found"] = True
                result["current_value"] = boot.get(redfish_key)

                if include_setter_info:
                    result["setter_info"] = {
                        "tool": "redfish_set_nextboot",
                        "writable": True,
                        "example": {
                            "tool": "redfish_set_nextboot",
                            "arguments": {
                                "host": host,
                                key: "<new_value>",
                                "allow_write": True,
                            },
                        },
                        "note": "Use target='bios', 'pxe', 'hdd', etc. Enabled can be 'Once', 'Continuous', or 'Disabled'",
                    }
                    if key == "target":
                        allowable = get_allowable_targets(system)
                        if allowable:
                            result["setter_info"]["allowable_values"] = allowable
            else:
                # Return all boot settings
                result["current_values"] = {
                    "enabled": boot.get("BootSourceOverrideEnabled"),
                    "target": boot.get("BootSourceOverrideTarget"),
                    "mode": boot.get("BootSourceOverrideMode"),
                }
                allowable = get_allowable_targets(system)
                if allowable:
                    result["allowable_targets"] = allowable

        # ==================== Power State Query ====================
        elif query_type == "power_state":
            result["found"] = True
            result["current_value"] = system.get("PowerState")
            if include_setter_info:
                result["setter_info"] = {
                    "note": "Power control not yet implemented. Use redfish_set_power (future tool) or manual reboot.",
                    "future_tool": "redfish_set_power",
                }

        # ==================== Health Status Query ====================
        elif query_type == "health":
            status = system.get("Status") or {}
            result["found"] = True
            result["current_value"] = {
                "State": status.get("State"),
                "Health": status.get("Health"),
                "HealthRollup": status.get("HealthRollup"),
            }
            if include_setter_info:
                result["setter_info"] = {
                    "note": "Health status is read-only. Cannot be directly modified.",
                }

        # ==================== List NICs Query ====================
        elif query_type == "list_nics":
            nics_url = f"{ep.system_url}/NetworkInterfaces"
            nics_coll, nics_err = await _to_thread(c.get_json_maybe, nics_url)

            if nics_err:
                # Try alternate path
                eth_url = f"{ep.system_url}/EthernetInterfaces"
                nics_coll, nics_err = await _to_thread(c.get_json_maybe, eth_url)
                nics_url = eth_url

            if nics_err:
                result["found"] = False
                result["error"] = f"Could not get NICs: {nics_err}"
            else:
                members = (nics_coll or {}).get("Members", [])
                nic_list = []
                for m in members[:20]:  # Limit to first 20
                    if isinstance(m, dict) and "@odata.id" in m:
                        nic_url = to_abs(c.base_url, m["@odata.id"])
                        nic, nic_err = await _to_thread(c.get_json_maybe, nic_url)
                        if nic and not nic_err:
                            nic_list.append(
                                {
                                    "Id": nic.get("Id"),
                                    "Name": nic.get("Name"),
                                    "Status": nic.get("Status"),
                                    "MACAddress": nic.get("MACAddress"),
                                    "LinkStatus": nic.get("LinkStatus"),
                                    "SpeedMbps": nic.get("SpeedMbps"),
                                    "url": nic_url,
                                }
                            )
                result["found"] = True
                result["nics"] = nic_list
                result["count"] = len(nic_list)
                result["collection_url"] = nics_url

        # ==================== NIC PXE Query ====================
        elif query_type == "nic_pxe":
            if not key:
                return ResponseBuilder.error("key parameter required for nic_pxe query (NIC ID)")

            # Try to find the NIC
            for base_path in ["/NetworkInterfaces", "/EthernetInterfaces"]:
                nic_url = f"{ep.system_url}{base_path}/{key}"
                nic, nic_err = await _to_thread(c.get_json_maybe, nic_url)
                if not nic_err and nic:
                    result["found"] = True
                    result["nic_url"] = nic_url
                    result["nic_info"] = {
                        "Id": nic.get("Id"),
                        "Name": nic.get("Name"),
                        "MACAddress": nic.get("MACAddress"),
                    }
                    # Check for PXE-related fields
                    result["pxe_info"] = {
                        "NetDevFuncType": nic.get("NetDevFuncType"),
                        "BootMode": nic.get("BootMode"),
                        "Enabled": nic.get("Enabled"),
                        "note": "PXE enablement varies by firmware. Check BootMode or NetDevFuncType fields.",
                    }
                    break

            if not result.get("found"):
                result["found"] = False
                result["error"] = (
                    f"NIC '{key}' not found. Try query_type='list_nics' to see available NICs."
                )

        # ==================== List BIOS Attributes ====================
        elif query_type == "list_bios_attributes":
            attrs, _bios_url, err = await _to_thread(get_bios_attributes, c, ep)
            if err or not attrs:
                result["ok"] = False
                result["error"] = err or "Failed to get BIOS attributes"
            else:
                # If key provided, use it as a filter
                if key:
                    filtered = {k: v for k, v in attrs.items() if key.lower() in k.lower()}
                    result["attributes"] = filtered
                    result["count"] = len(filtered)
                    result["filter"] = key
                else:
                    # Return all keys (values can be large)
                    result["attribute_keys"] = sorted(attrs.keys())
                    result["count"] = len(attrs)
                    result["note"] = (
                        "Use key parameter to filter, or use info_types=['bios_current'] for full attributes"
                    )

                if include_setter_info:
                    settings_url, _, _ = await _to_thread(discover_bios_settings_url, c, ep)
                    result["setter_info"] = {
                        "tool": "redfish_set_bios_attributes",
                        "writable": settings_url is not None,
                        "note": "Use redfish_set_bios_attributes to modify BIOS attributes",
                    }

        elif query_type == "bmc_log_services":
            from redfish_mcp.cli import _enumerate_all_log_services

            available = await _to_thread(_enumerate_all_log_services, c)
            services = [{"name": n, "url": u} for n, u in available]

            result["found"] = True
            result["log_services"] = services
            result["count"] = len(services)

        else:
            return ResponseBuilder.error(
                f"Unknown query_type: {query_type}",
                supported_types=[
                    "bios_attribute",
                    "boot_setting",
                    "power_state",
                    "health",
                    "list_nics",
                    "nic_pxe",
                    "list_bios_attributes",
                    "bmc_log_services",
                ],
            )

        return result

    tools["redfish_query"] = redfish_query
    mcp.tool(
        annotations=ToolAnnotations(
            title="Query Redfish Settings",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
        )
    )(_wrap(redfish_query))

    async def redfish_get_hardware_docs(
        host: str,
        user: str,
        password: str,
        include_firmware_check: bool = True,
        include_bios_recommendations: bool = True,
        check_online: bool = False,
        verify_tls: bool = False,
        timeout_s: int = 15,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """Get hardware-specific documentation and firmware update information.

        Fetches documentation for the detected hardware including:
        - Hardware specifications and details
        - BIOS version information and changelogs
        - Firmware update availability (database or online)
        - GPU optimization recommendations
        - Documentation URLs (manuals, support pages)

        Data is cached for 24 hours to avoid repeated lookups.

        Args:
            include_firmware_check: Check for available firmware updates
            include_bios_recommendations: Include BIOS optimization recommendations
            check_online: Check vendor website for latest BIOS (requires Tavily MCP, slower)
            use_cache: Use cached documentation (24 hour TTL)
        """
        # Get system info to identify hardware
        c = _client(host, user, password, verify_tls, timeout_s)
        ep = await _to_thread(c.discover_system)
        fetcher = SystemFetcher(c, ep)
        system, err_response = fetcher.get_system_or_error_response(host)

        if err_response:
            return err_response

        manufacturer = system.get("Manufacturer")
        model = system.get("Model")
        bios_version = system.get("BiosVersion")
        serial = system.get("SerialNumber")

        # Get documentation (with caching)
        cache = docs_cache if use_cache else None
        docs = await _to_thread(get_hardware_docs, manufacturer, model, bios_version, serial, cache)

        # Build result without duplicating keys
        result = {**docs, "host": host}
        if not docs.get("manufacturer"):
            result["manufacturer"] = manufacturer
        if not docs.get("model"):
            result["model"] = model
        if not docs.get("bios_version"):
            result["bios_version"] = bios_version
        if not docs.get("serial_number"):
            result["serial_number"] = serial

        # Add firmware update check if requested
        if include_firmware_check and docs.get("matched"):
            firmware_info = await _to_thread(
                get_firmware_update_info, manufacturer, model, bios_version
            )
            result["firmware_updates"] = firmware_info

        # Include BIOS recommendations if requested
        if not include_bios_recommendations:
            result.pop("gpu_optimization", None)
            result.get("bios_info", {}).pop("recommended_settings", None)

        return result

    tools["redfish_get_hardware_docs"] = redfish_get_hardware_docs
    mcp.tool(
        annotations=ToolAnnotations(
            title="Get Hardware Documentation",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
        )
    )(_wrap(redfish_get_hardware_docs))

    async def redfish_check_bios_online(
        host: str,
        user: str,
        password: str,
        verify_tls: bool = False,
        timeout_s: int = 15,
    ) -> dict[str, Any]:
        """Check vendor website for latest BIOS version (requires Tavily MCP).

        This tool checks the vendor's website for the absolute latest BIOS version
        available online, not just what's in our database.

        Returns information on how to perform the check with Tavily MCP since
        cross-MCP calls are not directly supported.

        For Supermicro systems, this checks their download center for the latest BIOS.
        """
        # Get system info
        c = _client(host, user, password, verify_tls, timeout_s)
        ep = await _to_thread(c.discover_system)
        fetcher = SystemFetcher(c, ep)
        system, err_response = fetcher.get_system_or_error_response(host)

        if err_response:
            return err_response

        manufacturer = system.get("Manufacturer")
        model = system.get("Model")
        current_bios = system.get("BiosVersion")

        # Import here to avoid loading if not needed
        from .firmware_checker import get_motherboard_from_model

        motherboard = get_motherboard_from_model(model) if model else None

        # Construct instructions for using Tavily
        if manufacturer == "Supermicro" and motherboard:
            download_url = f"https://www.supermicro.com/en/support/resources/downloadcenter/firmware/MBD-{motherboard}/BIOS"

            return ResponseBuilder.success(
                host=host,
                manufacturer=manufacturer,
                model=model,
                motherboard=motherboard,
                current_bios=current_bios,
                check_method="tavily_extract",
                instructions={
                    "step1": "Use Tavily MCP to extract content from the download page",
                    "tavily_call": {
                        "tool": "tavily_extract",
                        "arguments": {
                            "urls": [download_url],
                            "query": "BIOS version revision release",
                            "extract_depth": "advanced",
                        },
                    },
                    "step2": "Look for 'BIOS Revision: X.Xa' in the extracted content",
                    "step3": "Compare extracted version with current_bios",
                },
                download_url=download_url,
                note="This tool provides instructions for manual checking. Automated checking requires Tavily MCP integration.",
            )

        return ResponseBuilder.error(
            f"Online BIOS checking not implemented for {manufacturer} {model}",
            note="Currently supports Supermicro only. Add support for other vendors in firmware_checker.py",
        )

    tools["redfish_check_bios_online"] = redfish_check_bios_online
    mcp.tool(
        annotations=ToolAnnotations(
            title="Check BIOS Version Online",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
        )
    )(_wrap(redfish_check_bios_online))

    async def redfish_get_firmware_inventory(
        host: str,
        user: str,
        password: str,
        verify_tls: bool = False,
        timeout_s: int = 30,
        execution_mode: str = "execute",
    ) -> dict[str, Any]:
        """Get comprehensive firmware inventory for all hardware components.

        Queries Redfish UpdateService/FirmwareInventory to get firmware versions for:
        - BIOS, BMC, storage controllers, NICs, PSUs, CPLDs, PCIe devices, etc.

        Returns firmware version for every component visible to BMC, categorized by type.
        """
        if execution_mode == "render_curl":
            curl = _curl(verify_tls)
            return execution_mode_handler(
                verify_tls,
                [
                    "# Get firmware inventory collection:",
                    curl.get("/redfish/v1/UpdateService/FirmwareInventory"),
                    "# Then GET each member @odata.id for detailed version info",
                ],
            )

        c = _client(host, user, password, verify_tls, timeout_s)
        ep = await _to_thread(c.discover_system)

        # Collect firmware inventory
        inventory = await _to_thread(collect_firmware_inventory, c, ep)

        return ResponseBuilder.success(host=host, **inventory)

    tools["redfish_get_firmware_inventory"] = redfish_get_firmware_inventory
    mcp.tool(
        annotations=ToolAnnotations(
            title="Get Firmware Inventory",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
        )
    )(_wrap(redfish_get_firmware_inventory))

    async def redfish_get_vendor_errata(
        host: str,
        user: str,
        password: str,
        verify_tls: bool = False,
        timeout_s: int = 15,
    ) -> dict[str, Any]:
        """Get vendor errata and security bulletin URLs.

        Returns links to security advisories, CVE bulletins, and errata pages
        for the detected hardware vendor (Supermicro, Dell, HPE, Lenovo).

        Useful for checking if your firmware versions have known vulnerabilities.
        """
        # Get system info to identify vendor
        c = _client(host, user, password, verify_tls, timeout_s)
        ep = await _to_thread(c.discover_system)
        fetcher = SystemFetcher(c, ep)
        system, err_response = fetcher.get_system_or_error_response(host)

        if err_response:
            return err_response

        manufacturer = system.get("Manufacturer")

        # Get errata URLs
        errata_info = await _to_thread(get_vendor_errata_urls, manufacturer)

        return ResponseBuilder.success(
            host=host,
            manufacturer=manufacturer,
            model=system.get("Model"),
            **errata_info,
        )

    tools["redfish_get_vendor_errata"] = redfish_get_vendor_errata
    mcp.tool(
        annotations=ToolAnnotations(
            title="Get Vendor Errata URLs",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
        )
    )(_wrap(redfish_get_vendor_errata))

    # ==================== Screen Capture ====================

    async def redfish_capture_screenshot(
        host: str,
        user: str,
        password: str,
        method: str = "auto",
        return_mode: str = "image",
        force: bool = False,
        verify_tls: bool = False,
        timeout_s: int = 30,
        max_analysis_tokens: int | None = None,
        execution_mode: str = "execute",
    ) -> CallToolResult:
        """Capture the VGA framebuffer from a BMC (Supermicro, Dell iDRAC, or compatible).

        Returns the screenshot as an inline image that agents can see directly,
        alongside structured metadata. The BMC produces a 1024x768 JPEG.

        **Token-saving features:**

        Screenshots are cached per-host. When ``force=False`` (default) and the
        screen content hasn't changed since the last capture, only a compact
        ``"no_change"`` status is returned — no image bytes, saving thousands
        of tokens. Set ``force=True`` to always receive the full image.

        ``return_mode`` controls what is returned:
          - ``"image"`` (default): inline image + metadata. If cached and
            unchanged, returns ``"no_change"`` status instead (unless ``force``).
          - ``"text_only"``: OCR the screen via Together AI vision model and
            return extracted text (much cheaper than an inline image).
          - ``"both"``: return both the inline image and OCR text.
          - ``"summary"``: LLM-analyzed one-line summary + screen_type (~50 tokens).
          - ``"analysis"``: structured extraction with errors, boot_stage, key_values (~200 tokens).
          - ``"diagnosis"``: full diagnosis with suggested_actions and severity (~350 tokens).

        Capture methods tried in ``"auto"`` order:
          1. Redfish OEM DumpService (Supermicro BMC fw 4.0+)
          2. CGI CapturePreview (Supermicro older fw)
          3. Dell iDRAC sysmgmt/OEM screenshot (iDRAC 9+)

        Args:
            method: Capture method — "auto", "redfish", "cgi", or "dell".
            return_mode: What to return — "image", "text_only", "both", "summary", "analysis", or "diagnosis".
            force: Always return full content even if screenshot is unchanged.
            max_analysis_tokens: Override default max tokens for analysis modes.
            execution_mode: "execute" or "render_curl".
        """
        if execution_mode == "render_curl":
            curl = _curl(verify_tls)
            return _as_call_tool_result(
                execution_mode_handler(
                    verify_tls,
                    [
                        "# Step 1: Create screen capture",
                        curl.post(
                            "/redfish/v1/Oem/Supermicro/DumpService/Actions/OemDumpService.Collect",
                            json.dumps({"DumpType": "ScreenCapture", "ActionType": "Create"}),
                        ),
                        "# Step 2: Download the capture (returns JPEG bytes)",
                        curl.post(
                            "/redfish/v1/Oem/Supermicro/DumpService/Actions/OemDumpService.Collect",
                            json.dumps({"DumpType": "ScreenCapture", "ActionType": "Download"}),
                        )
                        + " --output screenshot.jpg",
                    ],
                )
            )

        import base64

        valid_methods = ("auto", "redfish", "cgi", "dell", "ami")
        if method not in valid_methods:
            return _as_call_tool_result(
                ResponseBuilder.error(
                    f"Invalid method '{method}'; use {', '.join(repr(m) for m in valid_methods)}",
                    host=host,
                )
            )

        valid_return_modes = ("image", "text_only", "both", "summary", "analysis", "diagnosis")
        if return_mode not in valid_return_modes:
            return _as_call_tool_result(
                ResponseBuilder.error(
                    f"Invalid return_mode '{return_mode}'; use {', '.join(repr(m) for m in valid_return_modes)}",
                    host=host,
                )
            )

        power_state: str | None = None
        model_vendor_hint: str | None = None
        try:
            c = _client(host, user, password, verify_tls, timeout_s)
            ep = c.discover_system()
            system = c.get_json(ep.system_url)
            power_state = system.get("PowerState")
            model_vendor_hint = (
                vendor_from_model(system.get("Model", ""))
                or vendor_from_manufacturer(system.get("Manufacturer", ""))
            )
        except Exception:
            pass

        if power_state and power_state.lower() == "off":
            return _as_call_tool_result(
                ResponseBuilder.error(
                    "Cannot capture screenshot: host is powered off (PowerState=Off). "
                    "Power on the system first.",
                    host=host,
                    power_state="Off",
                )
            )

        img_bytes: bytes | None = None
        mime_type: str = "image/jpeg"
        method_used: str = method
        errors: list[str] = []

        vendor = "unknown"
        idrac_gen = "unknown"
        if method == "auto":
            if model_vendor_hint and model_vendor_hint != "unknown":
                vendor = model_vendor_hint
            else:
                try:
                    c_detect = _client(host, user, password, verify_tls, timeout_s)
                    vendor = await _to_thread(detect_vendor, c_detect)
                except Exception:
                    pass
            if vendor == "dell":
                try:
                    c_dell = _client(host, user, password, verify_tls, timeout_s)
                    idrac_gen = await _to_thread(detect_idrac_generation, c_dell)
                except Exception:
                    pass
            methods_to_try = vendor_methods(vendor)
        else:
            methods_to_try = [method]

        for try_method in methods_to_try:
            try:
                if try_method == "redfish":
                    c = _client(host, user, password, verify_tls, timeout_s)
                    img_bytes, mime_type = await _to_thread(capture_screen_redfish, c)
                elif try_method == "cgi":
                    img_bytes, mime_type = await _to_thread(
                        capture_screen_cgi, host, user, password, verify_tls, timeout_s
                    )
                elif try_method == "dell":
                    img_bytes, mime_type = await _to_thread(
                        capture_screen_dell,
                        host,
                        user,
                        password,
                        verify_tls,
                        timeout_s,
                        idrac_generation=idrac_gen,
                    )
                elif try_method == "ami":
                    from redfish_mcp.kvm.backends.playwright_ami import capture_screen_ami

                    img_bytes, mime_type = await capture_screen_ami(
                        host, user, password, timeout_s=timeout_s
                    )
                method_used = try_method
                break
            except DellPrivilegeError:
                raise
            except Exception as e:
                errors.append(f"{try_method}: {e}")
                if method != "auto":
                    return _as_call_tool_result(
                        ResponseBuilder.error(
                            f"{try_method.title()} capture failed: {e}",
                            host=host,
                        )
                    )
                logger.info("%s capture failed for %s: %s", try_method, host, e)

        if img_bytes is None:
            if not is_screenshot_supported(vendor):
                return _as_call_tool_result(
                    ResponseBuilder.error(
                        f"Screenshot capture is not supported for this BMC (detected vendor: {vendor})",
                        host=host,
                        vendor=vendor,
                    )
                )
            detail = "; ".join(errors) if errors else "no capture methods matched"
            vendor_info = f" (detected vendor: {vendor})" if vendor != "unknown" else ""
            return _as_call_tool_result(
                ResponseBuilder.error(
                    f"All screenshot methods failed{vendor_info} — {detail}",
                    host=host,
                    vendor=vendor,
                    methods_tried=methods_to_try,
                )
            )

        # ---- Cache-based change detection ----
        changed = screenshot_cache.has_changed(host, img_bytes)
        cached_entry = screenshot_cache.store(host, img_bytes, mime_type, method_used)

        if not changed and not force:
            meta = {
                "ok": True,
                "host": host,
                "status": "no_change",
                "method_used": method_used,
                "mime_type": mime_type,
                "size_bytes": len(img_bytes),
                "dimensions": "1024x768",
                "sha256": cached_entry.sha256[:16],
                "hint": (
                    "Screen content unchanged since last capture. "
                    "Use force=true to receive the image anyway, "
                    "or return_mode='text_only' to get OCR text."
                ),
            }
            if cached_entry.ocr_text:
                meta["cached_ocr_text"] = cached_entry.ocr_text
            return CallToolResult(
                content=[TextContent(type="text", text=_json_text(meta))],
                isError=False,
            )

        # ---- Notify subscribers if screenshot changed ----
        if changed:
            try:
                from pydantic import AnyUrl

                ctx: Context = mcp.get_context()
                uri = AnyUrl(f"screenshot://{host}")
                await ctx.session.send_resource_updated(uri)
            except Exception:
                pass

        # ---- LLM analysis modes (summary / analysis / diagnosis) ----
        if return_mode in ("summary", "analysis", "diagnosis"):
            cached_analysis = screenshot_cache.get_analysis(host, return_mode)
            if cached_analysis and not changed and not force:
                screen_data = cached_analysis
            else:
                try:
                    screen_data = await _to_thread(
                        analyze_screenshot,
                        img_bytes,
                        mime_type,
                        return_mode,
                        max_tokens=max_analysis_tokens,
                    )
                    screenshot_cache.set_analysis(host, return_mode, screen_data)
                except Exception as e:
                    logger.warning("Screen analysis failed for %s: %s", host, e)
                    screen_data = {
                        "summary": f"Analysis failed: {e}",
                        "screen_type": "unknown",
                        "is_interactive": False,
                        "needs_attention": False,
                        "_error": str(e),
                    }

            meta = {
                "ok": True,
                "host": host,
                "status": "changed" if changed else "unchanged",
                "method_used": method_used,
                "sha256": cached_entry.sha256[:16],
                "return_mode": return_mode,
                "screen": screen_data,
            }
            return CallToolResult(
                content=[TextContent(type="text", text=_json_text(meta))],
                isError=False,
            )

        # ---- OCR text extraction (for text_only / both modes) ----
        ocr_text: str | None = None
        if return_mode in ("text_only", "both"):
            try:
                ocr_text = await _to_thread(extract_text_from_screenshot, img_bytes, mime_type)
                screenshot_cache.set_ocr_text(host, ocr_text)
            except Exception as e:
                logger.warning("OCR extraction failed for %s: %s", host, e)
                ocr_text = f"[OCR failed: {e}]"

        # ---- Build response based on return_mode ----
        b64 = base64.b64encode(img_bytes).decode("ascii")
        meta = {
            "ok": True,
            "host": host,
            "status": "changed" if changed else "unchanged",
            "method_used": method_used,
            "mime_type": mime_type,
            "size_bytes": len(img_bytes),
            "dimensions": "1024x768",
            "sha256": cached_entry.sha256[:16],
            "return_mode": return_mode,
        }

        content: list[TextContent | ImageContent] = []

        if return_mode == "text_only":
            meta["ocr_text"] = ocr_text or ""
            content.append(TextContent(type="text", text=_json_text(meta)))
        elif return_mode == "both":
            meta["ocr_text"] = ocr_text or ""
            content.append(ImageContent(type="image", data=b64, mimeType=mime_type))
            content.append(TextContent(type="text", text=_json_text(meta)))
        else:
            content.append(ImageContent(type="image", data=b64, mimeType=mime_type))
            content.append(TextContent(type="text", text=_json_text(meta)))

        return CallToolResult(content=content, isError=False)

    tools["redfish_capture_screenshot"] = redfish_capture_screenshot
    mcp.tool(
        annotations=ToolAnnotations(
            title="Capture VGA Screenshot",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
        )
    )(_wrap(redfish_capture_screenshot))

    async def redfish_watch_screen(
        host: str,
        user: str,
        password: str,
        interval_s: int = 5,
        max_captures: int = 12,
        method: str = "auto",
        analysis_mode: str = "none",
        stop_when: str = "none",
        verify_tls: bool = False,
        timeout_s: int = 30,
    ) -> dict[str, Any]:
        """Watch a BMC screen by polling screenshots with change detection.

        Captures screenshots at regular intervals. For changed frames,
        extracts information via OCR or LLM analysis depending on
        ``analysis_mode``.

        Args:
            interval_s: Seconds between captures (default 5, min 2).
            max_captures: Maximum number of captures (default 12, max 60).
            method: Capture method — "auto", "redfish", "cgi", or "dell".
            analysis_mode: "none" for raw OCR, or "summary"/"analysis"/"diagnosis" for LLM analysis.
            stop_when: Early termination — "none", "login_prompt", "error", "interactive", or "stable".
        """
        valid_analysis = ("none", "summary", "analysis", "diagnosis")
        if analysis_mode not in valid_analysis:
            return ResponseBuilder.error(
                f"Invalid analysis_mode '{analysis_mode}'; use {', '.join(valid_analysis)}",
                host=host,
            )

        valid_stops = ("none", "login_prompt", "error", "interactive", "stable")
        if stop_when not in valid_stops:
            return ResponseBuilder.error(
                f"Invalid stop_when '{stop_when}'; use {', '.join(valid_stops)}",
                host=host,
            )

        if stop_when in ("login_prompt", "error", "interactive") and analysis_mode == "none":
            analysis_mode = "summary"

        interval_s = max(interval_s, 2)
        max_captures = min(max(max_captures, 1), 60)

        vendor = "unknown"
        idrac_gen = "unknown"
        if method == "auto":
            try:
                c_detect = _client(host, user, password, verify_tls, timeout_s)
                vendor = await _to_thread(detect_vendor, c_detect)
                if vendor == "dell":
                    idrac_gen = await _to_thread(detect_idrac_generation, c_detect)
            except Exception:
                pass
            methods_to_try = vendor_methods(vendor)
        else:
            methods_to_try = [method]

        timeline: list[dict[str, Any]] = []
        boot_progression: list[str] = []
        last_hash: str | None = None
        stable_count = 0
        stopped_reason: str | None = None

        for i in range(max_captures):
            if i > 0:
                await asyncio.sleep(interval_s)

            img_bytes: bytes | None = None
            mime_type: str = "image/jpeg"
            capture_error: str | None = None

            for try_method in methods_to_try:
                try:
                    if try_method == "redfish":
                        c = _client(host, user, password, verify_tls, timeout_s)
                        img_bytes, mime_type = await _to_thread(capture_screen_redfish, c)
                    elif try_method == "cgi":
                        img_bytes, mime_type = await _to_thread(
                            capture_screen_cgi, host, user, password, verify_tls, timeout_s
                        )
                    elif try_method == "dell":
                        img_bytes, mime_type = await _to_thread(
                            capture_screen_dell,
                            host,
                            user,
                            password,
                            verify_tls,
                            timeout_s,
                            idrac_generation=idrac_gen,
                        )
                    elif try_method == "ami":
                        from redfish_mcp.kvm.backends.playwright_ami import capture_screen_ami

                        img_bytes, mime_type = await capture_screen_ami(
                            host, user, password, timeout_s=timeout_s
                        )
                    break
                except DellPrivilegeError:
                    raise
                except Exception as e:
                    capture_error = str(e)
                    continue

            if img_bytes is None:
                timeline.append({"frame": i, "error": capture_error or "capture failed"})
                continue

            import hashlib

            frame_hash = hashlib.sha256(img_bytes).hexdigest()[:16]
            changed = frame_hash != last_hash

            entry: dict[str, Any] = {
                "frame": i,
                "changed": changed,
                "hash": frame_hash,
            }

            if changed:
                stable_count = 0
                if analysis_mode != "none":
                    try:
                        screen_data = await _to_thread(
                            analyze_screenshot,
                            img_bytes,
                            mime_type,
                            analysis_mode,
                        )
                        entry["screen"] = screen_data
                        stage = screen_data.get("boot_stage")
                        if stage and (not boot_progression or boot_progression[-1] != stage):
                            boot_progression.append(stage)
                    except Exception as e:
                        entry["analysis_error"] = str(e)
                else:
                    try:
                        ocr_text = await _to_thread(
                            extract_text_from_screenshot, img_bytes, mime_type
                        )
                        entry["ocr_text"] = ocr_text
                    except Exception as e:
                        entry["ocr_error"] = str(e)
                last_hash = frame_hash
            else:
                stable_count += 1

            timeline.append(entry)

            if stop_when != "none" and changed:
                screen = entry.get("screen", {})
                if stop_when == "login_prompt" and screen.get("screen_type") == "login_prompt":
                    stopped_reason = "login_prompt detected"
                    break
                elif stop_when == "error" and screen.get("needs_attention"):
                    stopped_reason = "error/attention detected"
                    break
                elif stop_when == "interactive" and screen.get("is_interactive"):
                    stopped_reason = "interactive screen detected"
                    break
            if stop_when == "stable" and stable_count >= 3:
                stopped_reason = "screen stable (3 unchanged frames)"
                break

        changes = [t for t in timeline if t.get("changed")]
        last_changed = changes[-1] if changes else None

        result = ResponseBuilder.success(
            host=host,
            total_captures=len(timeline),
            changes_detected=len(changes),
        )
        if analysis_mode != "none":
            result["analysis_mode"] = analysis_mode
            result["boot_progression"] = boot_progression
            if last_changed and "screen" in last_changed:
                result["final_state"] = last_changed["screen"]
        if stopped_reason:
            result["stopped_reason"] = stopped_reason
        result["timeline"] = timeline
        return result

    tools["redfish_watch_screen"] = redfish_watch_screen
    mcp.tool(
        annotations=ToolAnnotations(
            title="Watch Screen (Polling OCR)",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
        )
    )(_wrap(redfish_watch_screen))

    async def redfish_capture_video(
        host: str,
        user: str,
        password: str,
        capture_type: str = "video",
        output_dir: str = "/tmp/bmc-capture",
        verify_tls: bool = False,
        timeout_s: int = 60,
    ) -> dict[str, Any]:
        """Download a video or crash-screen recording from a Supermicro BMC.

        The BMC can record POST/boot video and OS crash screens (if enabled
        in the DumpService settings). This tool downloads those recordings.

        Args:
            capture_type: What to download.
                - "video": POST/boot video recording (VideoCapture)
                - "crash_screen": OS crash screen capture (CrashScreenCapture)
            output_dir: Directory to save the file (default /tmp/bmc-capture)
        """
        import os

        dump_type_map = {
            "video": "VideoCapture",
            "crash_screen": "CrashScreenCapture",
        }
        dump_type = dump_type_map.get(capture_type)
        if not dump_type:
            return ResponseBuilder.error(
                f"Invalid capture_type '{capture_type}'; use 'video' or 'crash_screen'"
            )

        c = _client(host, user, password, verify_tls, timeout_s)
        try:
            data, ct = await _to_thread(download_dump_redfish, c, dump_type)
        except Exception as e:
            return ResponseBuilder.error(
                f"DumpService download failed: {e}",
                host=host,
                capture_type=capture_type,
            )

        if len(data) < 64:
            return ResponseBuilder.error(
                "BMC returned empty or trivially small capture; recording may not be enabled",
                host=host,
                capture_type=capture_type,
                size_bytes=len(data),
            )

        os.makedirs(output_dir, exist_ok=True)
        ext = "bin"
        if "video" in ct or dump_type == "VideoCapture":
            ext = "avi"
        elif "image" in ct:
            ext = "jpg"
        filename = f"{host.replace('.', '_')}_{capture_type}.{ext}"
        path = os.path.join(output_dir, filename)

        with open(path, "wb") as f:
            f.write(data)

        return ResponseBuilder.success(
            host=host,
            capture_type=capture_type,
            dump_type=dump_type,
            file_path=path,
            size_bytes=len(data),
            content_type=ct,
        )

    tools["redfish_capture_video"] = redfish_capture_video
    mcp.tool(
        annotations=ToolAnnotations(
            title="Download BMC Video/Crash Capture",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
        )
    )(_wrap(redfish_capture_video))

    # ==================== Write Operations ====================

    def _run_set_nextboot(
        *,
        host: str,
        user: str,
        password: str,
        verify_tls: bool,
        timeout_s: int,
        target: str,
        enabled: str,
        reboot: bool,
        reset_type: str,
    ) -> dict[str, Any]:
        logger.info(
            "set_nextboot host=%s target=%s enabled=%s reboot=%s", host, target, enabled, reboot
        )
        c = _client(host, user, password, verify_tls, timeout_s)
        ep = c.discover_system()
        system = c.get_json(ep.system_url)
        allowable = get_allowable_targets(system)
        chosen_target, attempted = pick_target(target, allowable)
        payload_boot: dict[str, Any] = {
            "BootSourceOverrideEnabled": enabled,
            "BootSourceOverrideTarget": chosen_target,
        }
        current_mode = (
            (system.get("Boot") or {}).get("BootSourceOverrideMode")
            if isinstance(system, dict)
            else None
        )
        if isinstance(current_mode, str) and current_mode:
            payload_boot["BootSourceOverrideMode"] = current_mode
        payload = {"Boot": payload_boot}
        resp = c.patch_json(ep.system_url, payload)
        if resp.status_code >= 400:
            return ResponseBuilder.error(resp.text[:2000], payload=payload)

        result = ResponseBuilder.success(
            host=host,
            system_url=ep.system_url,
            chosen_target=chosen_target,
            attempted_targets=attempted,
        )

        if reboot:
            post = c.post_json(ep.reset_url, {"ResetType": reset_type})
            if post.status_code >= 400:
                result["reboot_ok"] = False
                result["reboot_error"] = post.text[:2000]
            else:
                result["reboot_ok"] = True
                result["reset_type"] = reset_type
        return result

    async def redfish_set_nextboot(
        host: str,
        user: str,
        password: str,
        target: str = "bios",
        enabled: str = "Once",
        reboot: bool = False,
        reset_type: str = "ForceRestart",
        verify_tls: bool = False,
        timeout_s: int = 30,
        allow_write: bool = False,
        async_mode: bool = True,
        execution_mode: str = "execute",
        ctx: Context | None = None,
    ) -> dict[str, Any] | CreateTaskResult:
        """Set next-boot override.

        Safe-by-default: requires allow_write=true.
        By default runs as an MCP task (returns CreateTaskResult) to avoid long-running calls holding the MCP request.
        """
        if not allow_write:
            return ResponseBuilder.error("Refusing write operation without allow_write=true")

        if execution_mode == "render_curl":
            curl = _curl(verify_tls)
            payload = json.dumps(
                {
                    "Boot": {
                        "BootSourceOverrideEnabled": enabled,
                        "BootSourceOverrideTarget": target,
                    }
                }
            )
            cmds: list[str] = [
                "# Discover system member:",
                curl.get("/redfish/v1/Systems"),
                "# PATCH the system resource (example uses /redfish/v1/Systems/1):",
                curl.patch("/redfish/v1/Systems/1", payload),
            ]
            if reboot:
                cmds.append("# Optionally reboot:")
                cmds.append(
                    curl.post(
                        "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
                        json.dumps({"ResetType": reset_type}),
                    )
                )
            return execution_mode_handler(verify_tls, cmds)

        if async_mode and ctx is not None:

            async def work(task: ServerTaskContext) -> CallToolResult:
                await task.update_status("Setting next-boot override...")

                async def _run():
                    return await _to_thread(
                        _run_set_nextboot,
                        host=host,
                        user=user,
                        password=password,
                        verify_tls=verify_tls,
                        timeout_s=timeout_s,
                        target=target,
                        enabled=enabled,
                        reboot=reboot,
                        reset_type=reset_type,
                    )

                rec = await limiter.run(key=host, fn=_run)
                return _as_call_tool_result(
                    rec if isinstance(rec, dict) else {"ok": True, "result": rec}
                )

            return await _create_background_task(
                ctx=ctx,
                model_immediate_response="Queued: setting next-boot override",
                work=work,
            )

        # Fallback: run synchronously (used in unit tests / when ctx isn't available).
        return await _to_thread(
            _run_set_nextboot,
            host=host,
            user=user,
            password=password,
            verify_tls=verify_tls,
            timeout_s=timeout_s,
            target=target,
            enabled=enabled,
            reboot=reboot,
            reset_type=reset_type,
        )

    tools["redfish_set_nextboot"] = redfish_set_nextboot
    mcp.tool(
        annotations=ToolAnnotations(
            title="Set Next Boot Override",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
        )
    )(_wrap(redfish_set_nextboot))

    # ==================== Supermicro Fixed Boot Order ====================

    async def redfish_get_fixed_boot_order(
        host: str,
        user: str,
        password: str,
        verify_tls: bool = False,
        timeout_s: int = 30,
    ) -> dict[str, Any]:
        """Get the persistent UEFI fixed boot order from a Supermicro BMC.

        Reads the OEM endpoint at /redfish/v1/Systems/{id}/Oem/Supermicro/FixedBootOrder
        (system member ID is discovered dynamically).
        Only available on Supermicro hardware.

        Args:
            host: BMC IP or hostname (use oob_ip from NetBox).
        """
        from .supermicro_boot_order import get_fixed_boot_order, is_supermicro

        def _run() -> dict[str, Any]:
            c = _client(host, user, password, verify_tls, timeout_s)
            if not is_supermicro(c):
                return ResponseBuilder.error(
                    "Not a Supermicro BMC — OEM endpoint not found", host=host
                )
            data, _etag, err = get_fixed_boot_order(c)
            if err:
                return ResponseBuilder.error(err, host=host)
            return ResponseBuilder.success(host=host, fixed_boot_order=data)

        return await _to_thread(_run)

    tools["redfish_get_fixed_boot_order"] = redfish_get_fixed_boot_order
    mcp.tool(
        annotations=ToolAnnotations(
            title="Get Supermicro Fixed Boot Order",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
        )
    )(_wrap(redfish_get_fixed_boot_order))

    def _run_set_fixed_boot_order(
        *,
        host: str,
        user: str,
        password: str,
        verify_tls: bool,
        timeout_s: int,
        boot_order: dict[str, Any],
    ) -> dict[str, Any]:
        from .supermicro_boot_order import is_supermicro, set_fixed_boot_order

        c = _client(host, user, password, verify_tls, timeout_s)
        if not is_supermicro(c):
            return ResponseBuilder.error("Not a Supermicro BMC — OEM endpoint not found", host=host)
        result = set_fixed_boot_order(c, boot_order)
        result["host"] = host
        return result

    async def redfish_set_fixed_boot_order(
        host: str,
        user: str,
        password: str,
        boot_order: Annotated[
            dict[str, Any],
            Field(description="Boot order payload to PATCH to the FixedBootOrder endpoint"),
        ],
        verify_tls: bool = False,
        timeout_s: int = 30,
        allow_write: bool = False,
        async_mode: bool = True,
        execution_mode: str = "execute",
        ctx: Context | None = None,
    ) -> dict[str, Any] | CreateTaskResult:
        """Set the persistent UEFI fixed boot order on a Supermicro BMC.

        PATCHes /redfish/v1/Systems/{id}/Oem/Supermicro/FixedBootOrder with the
        provided boot_order payload. Automatically fetches the ETag and sends
        If-Match. Returns 202 Accepted on success; a system reset is required
        to apply the new order.

        Safe-by-default: requires allow_write=true.
        Only available on Supermicro hardware.

        Args:
            host: BMC IP or hostname (use oob_ip from NetBox).
            boot_order: The boot order object to write. Get the current order
                first with redfish_get_fixed_boot_order, modify it, then pass
                it here.
        """
        if not allow_write:
            return ResponseBuilder.error("Refusing write operation without allow_write=true")

        if execution_mode == "render_curl":
            curl = _curl(verify_tls)
            fbo_path = "/redfish/v1/Systems/$SYSTEM_ID/Oem/Supermicro/FixedBootOrder"
            cmds: list[str] = [
                "# Discover system member first: GET /redfish/v1/Systems/ and note the @odata.id",
                "# Then GET current boot order + ETag (replace $SYSTEM_ID, e.g. 1 or Self):",
                curl.get(fbo_path),
                "# PATCH with If-Match (replace $ETAG with value from GET):",
                f'{curl._base()} -X PATCH -H "Content-Type: application/json"'
                f' -H "If-Match: $ETAG"'
                f" -u $REDFISH_USER:$REDFISH_PASSWORD"
                f" https://$HOST{fbo_path}"
                f" -d '{json.dumps(boot_order)}'",
            ]
            return execution_mode_handler(verify_tls, cmds)

        if async_mode and ctx is not None:

            async def work(task: ServerTaskContext) -> CallToolResult:
                await task.update_status("Setting Supermicro fixed boot order...")

                async def _run():
                    return await _to_thread(
                        _run_set_fixed_boot_order,
                        host=host,
                        user=user,
                        password=password,
                        verify_tls=verify_tls,
                        timeout_s=timeout_s,
                        boot_order=boot_order,
                    )

                rec = await limiter.run(key=host, fn=_run)
                return _as_call_tool_result(
                    rec if isinstance(rec, dict) else {"ok": True, "result": rec}
                )

            return await _create_background_task(
                ctx=ctx,
                model_immediate_response="Queued: setting Supermicro fixed boot order",
                work=work,
            )

        return await _to_thread(
            _run_set_fixed_boot_order,
            host=host,
            user=user,
            password=password,
            verify_tls=verify_tls,
            timeout_s=timeout_s,
            boot_order=boot_order,
        )

    tools["redfish_set_fixed_boot_order"] = redfish_set_fixed_boot_order
    mcp.tool(
        annotations=ToolAnnotations(
            title="Set Supermicro Fixed Boot Order",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
        )
    )(_wrap(redfish_set_fixed_boot_order))

    def _run_power_control(
        *,
        host: str,
        user: str,
        password: str,
        verify_tls: bool,
        timeout_s: int,
        action: str,
    ) -> dict[str, Any]:
        try:
            canonical, reset_type = resolve_reset_type(action)
        except InvalidActionError as exc:
            return ResponseBuilder.error(
                f"Invalid action '{action}': {exc.message}",
                host=host,
            )
        logger.info("power_control host=%s action=%s reset_type=%s", host, canonical, reset_type)
        c = _client(host, user, password, verify_tls, timeout_s)
        ep = c.discover_system()
        system = c.get_json(ep.system_url)
        current_power = system.get("PowerState", "Unknown")
        resp = c.post_json(ep.reset_url, {"ResetType": reset_type})
        if resp.status_code >= 400:
            return ResponseBuilder.error(
                f"Power control failed: {resp.text[:500]}",
                host=host,
                action=canonical,
                reset_type=reset_type,
                prior_power_state=current_power,
            )
        return ResponseBuilder.success(
            host=host,
            action=canonical,
            reset_type=reset_type,
            prior_power_state=current_power,
        )

    async def redfish_power_control(
        host: str,
        user: str,
        password: str,
        action: Annotated[
            PowerAction,
            Field(description="Power action to perform."),
        ],
        allow_write: bool = False,
        verify_tls: bool = False,
        timeout_s: int = 30,
        execution_mode: str = "execute",
    ) -> dict[str, Any]:
        """Control server power state. Safe-by-default: requires allow_write=true."""
        if not allow_write:
            return ResponseBuilder.error("Refusing write operation without allow_write=true")

        if execution_mode == "render_curl":
            try:
                _canonical, reset_type = resolve_reset_type(action)
            except InvalidActionError as exc:
                return ResponseBuilder.error(
                    f"Invalid action '{action}': {exc.message}",
                )
            curl = _curl(verify_tls)
            cmds: list[str] = [
                "# Discover system member:",
                curl.get("/redfish/v1/Systems"),
                "# POST reset action:",
                curl.post(
                    "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
                    json.dumps({"ResetType": reset_type}),
                ),
            ]
            return execution_mode_handler(verify_tls, cmds)

        return await _to_thread(
            _run_power_control,
            host=host,
            user=user,
            password=password,
            verify_tls=verify_tls,
            timeout_s=timeout_s,
            action=action,
        )

    tools["redfish_power_control"] = redfish_power_control
    mcp.tool(
        annotations=ToolAnnotations(
            title="Power Control",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
        )
    )(_wrap(redfish_power_control))

    def _run_set_bios_attributes(
        *,
        host: str,
        user: str,
        password: str,
        verify_tls: bool,
        timeout_s: int,
        attributes: dict[str, Any],
        reboot: bool,
        reset_type: str,
    ) -> dict[str, Any]:
        logger.info(
            "set_bios_attributes host=%s attrs=%s reboot=%s",
            host,
            list(attributes.keys()),
            reboot,
        )
        c = _client(host, user, password, verify_tls, timeout_s)
        ep = c.discover_system()

        settings_url, bios_url, bios = discover_bios_settings_url(c, ep)
        if not settings_url:
            return ResponseBuilder.error(
                "Could not discover writable BIOS settings object. Firmware may not support BIOS writes via Redfish.",
                bios_url=bios_url,
            )

        current_attrs = (bios or {}).get("Attributes") if isinstance(bios, dict) else None
        current_values = {}
        if isinstance(current_attrs, dict):
            for k in attributes:
                if k in current_attrs:
                    current_values[k] = current_attrs[k]

        payload = {"Attributes": attributes}

        # Some BMCs require If-Match with the BIOS resource ETag
        headers: dict[str, str] = {"Content-Type": "application/json"}
        etag = (bios or {}).get("@odata.etag") if isinstance(bios, dict) else None
        if isinstance(etag, str) and etag.strip():
            headers["If-Match"] = etag

        resp = c.session.patch(
            settings_url, headers=headers, data=json.dumps(payload), timeout=c.timeout_s
        )
        if resp.status_code >= 400:
            return ResponseBuilder.error(resp.text[:2000], settings_url=settings_url)

        result = ResponseBuilder.success(
            host=host,
            bios_url=bios_url,
            settings_url=settings_url,
            current_values=current_values,
            staged_attributes=attributes,
            note="BIOS settings staged; may require reboot to apply",
        )

        if reboot:
            post = c.post_json(ep.reset_url, {"ResetType": reset_type})
            if post.status_code >= 400:
                result["reboot_ok"] = False
                result["reboot_error"] = post.text[:2000]
            else:
                result["reboot_ok"] = True
                result["reset_type"] = reset_type

        return result

    async def redfish_set_bios_attributes(
        host: str,
        user: str,
        password: str,
        attributes: dict[str, Any],
        reboot: bool = False,
        reset_type: str = "ForceRestart",
        verify_tls: bool = False,
        timeout_s: int = 30,
        allow_write: bool = False,
        async_mode: bool = True,
        execution_mode: str = "execute",
        ctx: Context | None = None,
    ) -> dict[str, Any] | CreateTaskResult:
        """Stage BIOS attribute changes.

        Safe-by-default: requires allow_write=true.
        Changes are typically staged and require reboot to apply.
        By default runs as an async job (returns job_id).

        Example attributes:
          {"Re_SizeBARSupport_00B2": "Enabled", "Above4GDecoding_00B1": "Enabled"}
        """
        if not allow_write:
            return ResponseBuilder.error("Refusing write operation without allow_write=true")

        if not attributes:
            return ResponseBuilder.error("No attributes provided")

        if execution_mode == "render_curl":
            curl = _curl(verify_tls)
            payload = json.dumps({"Attributes": attributes})
            cmds: list[str] = [
                "# Discover BIOS settings URL:",
                curl.get("/redfish/v1/Systems"),
                curl.get("/redfish/v1/Systems/1/Bios"),
                '# Look for "@Redfish.Settings".SettingsObject."@odata.id" in the BIOS response',
                "# PATCH the settings URL (example uses /redfish/v1/Systems/1/Bios/Settings):",
                curl.patch("/redfish/v1/Systems/1/Bios/Settings", payload),
            ]
            if reboot:
                cmds.append("# Optionally reboot:")
                cmds.append(
                    curl.post(
                        "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
                        json.dumps({"ResetType": reset_type}),
                    )
                )
            return execution_mode_handler(verify_tls, cmds)

        if async_mode and ctx is not None:

            async def work(task: ServerTaskContext) -> CallToolResult:
                await task.update_status("Staging BIOS attributes...")

                async def _run():
                    return await _to_thread(
                        _run_set_bios_attributes,
                        host=host,
                        user=user,
                        password=password,
                        verify_tls=verify_tls,
                        timeout_s=timeout_s,
                        attributes=attributes,
                        reboot=reboot,
                        reset_type=reset_type,
                    )

                rec = await limiter.run(key=host, fn=_run)
                return _as_call_tool_result(
                    rec if isinstance(rec, dict) else {"ok": True, "result": rec}
                )

            return await _create_background_task(
                ctx=ctx,
                model_immediate_response="Queued: staging BIOS attributes",
                work=work,
            )

        # Fallback: run synchronously (used in unit tests / when ctx isn't available).
        return await _to_thread(
            _run_set_bios_attributes,
            host=host,
            user=user,
            password=password,
            verify_tls=verify_tls,
            timeout_s=timeout_s,
            attributes=attributes,
            reboot=reboot,
            reset_type=reset_type,
        )

    tools["redfish_set_bios_attributes"] = redfish_set_bios_attributes
    mcp.tool(
        annotations=ToolAnnotations(
            title="Set BIOS Attributes",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
        )
    )(_wrap(redfish_set_bios_attributes))

    def _run_update_firmware(
        *,
        host: str,
        user: str,
        password: str,
        image_path: str,
        verify_tls: bool,
        timeout_s: int,
        request_timeout_s: int,
        targets: list[str] | None,
        apply_time: str,
        update_parameters_oem: dict[str, Any] | None,
        preserve_bmc_settings: bool,
        allow_non_preserving_update: bool,
        wait_for_completion: bool,
        task_timeout_s: int,
        poll_interval_s: int,
    ) -> dict[str, Any]:
        logger.info(
            "update_firmware host=%s image=%s apply_time=%s preserve=%s",
            host,
            image_path,
            apply_time,
            preserve_bmc_settings,
        )
        if not preserve_bmc_settings and not allow_non_preserving_update:
            return ResponseBuilder.error(
                "Refusing potentially destructive firmware update without allow_non_preserving_update=true",
                preserve_bmc_settings=preserve_bmc_settings,
                hint=(
                    "Set preserve_bmc_settings=true (recommended), or explicitly acknowledge risk with "
                    "allow_non_preserving_update=true."
                ),
            )

        c = _client(host, user, password, verify_tls, timeout_s)
        upload = upload_firmware_image(
            c,
            image_path=image_path,
            targets=targets,
            apply_time=apply_time,
            update_parameters_oem=update_parameters_oem,
            request_timeout_s=request_timeout_s,
        )
        if not upload.get("ok"):
            return ResponseBuilder.error(
                "Firmware upload failed",
                host=host,
                image_path=image_path,
                preserve_bmc_settings=preserve_bmc_settings,
                allow_non_preserving_update=allow_non_preserving_update,
                **upload,
            )

        result = ResponseBuilder.success(
            host=host,
            image_path=image_path,
            preserve_bmc_settings=preserve_bmc_settings,
            allow_non_preserving_update=allow_non_preserving_update,
            note=(
                "preserve_bmc_settings is an MCP safety policy. Vendor-specific preserve behavior depends on "
                "firmware support and optional update_parameters_oem values."
            ),
            **upload,
        )

        task_url = upload.get("task_url")
        if wait_for_completion and isinstance(task_url, str) and task_url:
            task_result = wait_for_task_completion(
                c,
                task_url=task_url,
                timeout_s=task_timeout_s,
                poll_interval_s=poll_interval_s,
            )
            result["task_result"] = task_result
            if not task_result.get("ok"):
                result["ok"] = False
                result["error"] = "Firmware task did not complete successfully"
        elif wait_for_completion and not task_url:
            result["warning"] = (
                "No task URL found in upload response; cannot wait for completion. "
                "Verify update state through UpdateService/Tasks."
            )

        return result

    async def redfish_update_firmware(
        host: str,
        user: str,
        password: str,
        image_path: str,
        targets: list[str] | None = None,
        apply_time: str = "Immediate",
        update_parameters_oem: dict[str, Any] | None = None,
        preserve_bmc_settings: bool = True,
        allow_non_preserving_update: bool = False,
        wait_for_completion: bool = True,
        task_timeout_s: int = 3600,
        poll_interval_s: int = 10,
        verify_tls: bool = False,
        timeout_s: int = 30,
        request_timeout_s: int = 300,
        allow_write: bool = False,
        async_mode: bool = True,
        execution_mode: str = "execute",
        ctx: Context | None = None,
    ) -> dict[str, Any] | CreateTaskResult:
        """Upload and apply firmware via Redfish UpdateService.

        Safe-by-default: requires allow_write=true.
        Also requires explicit acknowledgement for non-preserving update attempts.
        """
        if not allow_write:
            return ResponseBuilder.error("Refusing write operation without allow_write=true")

        if execution_mode == "render_curl":
            curl_base = "curl -sS" if verify_tls else "curl -sSk"
            oem_line = ""
            if update_parameters_oem is not None:
                oem_line = f', "Oem": {json.dumps(update_parameters_oem)}'
            update_params = (
                f'{{"@Redfish.OperationApplyTime":"{apply_time}"'
                + (f', "Targets": {json.dumps(targets)}' if targets else "")
                + oem_line
                + "}"
            )
            return execution_mode_handler(
                verify_tls,
                [
                    "# Multipart upload to UpdateService/upload:",
                    (
                        f'{curl_base} -u "$REDFISH_USER:$REDFISH_PASSWORD" '
                        "-X POST "
                        '-F "UpdateFile=@/path/to/firmware.bin" '
                        f"-F 'UpdateParameters={update_params}' "
                        '"https://$REDFISH_IP/redfish/v1/UpdateService/upload"'
                    ),
                    (
                        "# Safety policy: keep preserve_bmc_settings=true unless you explicitly set "
                        "allow_non_preserving_update=true."
                    ),
                ],
            )

        if async_mode and ctx is not None:

            async def work(task: ServerTaskContext) -> CallToolResult:
                await task.update_status("Uploading firmware image...")

                async def _run():
                    rec = await _to_thread(
                        _run_update_firmware,
                        host=host,
                        user=user,
                        password=password,
                        image_path=image_path,
                        verify_tls=verify_tls,
                        timeout_s=timeout_s,
                        request_timeout_s=request_timeout_s,
                        targets=targets,
                        apply_time=apply_time,
                        update_parameters_oem=update_parameters_oem,
                        preserve_bmc_settings=preserve_bmc_settings,
                        allow_non_preserving_update=allow_non_preserving_update,
                        wait_for_completion=False,
                        task_timeout_s=task_timeout_s,
                        poll_interval_s=poll_interval_s,
                    )
                    if not isinstance(rec, dict) or not rec.get("ok"):
                        return rec

                    fw_task_url = rec.get("task_url")
                    if wait_for_completion and isinstance(fw_task_url, str) and fw_task_url:
                        c = _client(host, user, password, verify_tls, timeout_s)
                        poll_result = await poll_firmware_task(
                            ctx,
                            c,
                            fw_task_url,
                            timeout_s=task_timeout_s,
                            poll_interval_s=poll_interval_s,
                        )
                        rec["task_result"] = poll_result
                        if not poll_result.get("ok"):
                            rec["ok"] = False
                            rec["error"] = "Firmware task did not complete successfully"
                    elif wait_for_completion and not fw_task_url:
                        rec["warning"] = (
                            "No task URL found in upload response; cannot wait for completion. "
                            "Verify update state through UpdateService/Tasks."
                        )
                    return rec

                result = await limiter.run(key=host, fn=_run)
                return _as_call_tool_result(
                    result if isinstance(result, dict) else {"ok": True, "result": result}
                )

            return await _create_background_task(
                ctx=ctx,
                model_immediate_response="Queued: firmware update in progress",
                work=work,
            )

        return await _to_thread(
            _run_update_firmware,
            host=host,
            user=user,
            password=password,
            image_path=image_path,
            verify_tls=verify_tls,
            timeout_s=timeout_s,
            request_timeout_s=request_timeout_s,
            targets=targets,
            apply_time=apply_time,
            update_parameters_oem=update_parameters_oem,
            preserve_bmc_settings=preserve_bmc_settings,
            allow_non_preserving_update=allow_non_preserving_update,
            wait_for_completion=wait_for_completion,
            task_timeout_s=task_timeout_s,
            poll_interval_s=poll_interval_s,
        )

    tools["redfish_update_firmware"] = redfish_update_firmware
    mcp.tool(
        annotations=ToolAnnotations(
            title="Update Firmware",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=True,
        )
    )(_wrap(redfish_update_firmware))

    # ==================== BMC / iDRAC Log & Lifecycle Tools ====================

    async def redfish_get_bmc_logs(
        host: str,
        user: str,
        password: str,
        log_service: str = "",
        date_filter: str | None = None,
        severity_filter: str | None = None,
        limit: int = 50,
        verify_tls: bool = False,
        timeout_s: int = 30,
        execution_mode: str = "execute",
    ) -> dict[str, Any]:
        """Read BMC log entries (SEL, Lifecycle Log, Fault List, Supermicro MEL, etc.).

        Retrieves entries from BMC log services via the Redfish Managers
        LogServices API. Automatically discovers available log services
        on both Dell iDRAC and Supermicro BMCs. Useful for post-incident
        triage to find hardware events (PCIe errors, thermal, power, NIC
        link changes) that the host OS may not record.

        Args:
            log_service: Which log to read (auto-detected if empty). Common values:
                - "Sel" -- IPMI System Event Log (hardware sensors)
                - "Lclog" -- Lifecycle Controller Log (firmware, config, login audit)
                - "FaultList" -- Active faults
                - "Log1" -- Supermicro Maintenance Event Log
            date_filter: Optional ISO-8601 date prefix to filter entries
                (e.g. "2026-03-04" returns only entries from that day).
            severity_filter: Optional severity to filter on
                (e.g. "Warning", "Critical", "OK").
            limit: Max entries to return (default 50, max 500).
        """
        if execution_mode == "render_curl":
            svc = log_service or "Sel"
            curl = _curl(verify_tls)
            return execution_mode_handler(
                verify_tls,
                [
                    "# List available log services on first manager:",
                    curl.get("/redfish/v1/Managers"),
                    f"# Read {svc} entries (adjust manager ID and service for your BMC):",
                    curl.get(
                        f"/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/{svc}/Entries?$top={limit}"
                    ),
                ],
            )

        c = _client(host, user, password, verify_tls, timeout_s)
        limit = min(max(limit, 1), 500)

        from redfish_mcp.cli import _discover_log_service

        try:
            entries_url, resolved_service = await _to_thread(
                _discover_log_service,
                c,
                log_service or None,
            )
        except RuntimeError as e:
            return ResponseBuilder.error(str(e), host=host)

        url_with_query = f"{entries_url}?$top={limit}"
        data, err = await _to_thread(c.get_json_maybe, url_with_query)
        if err or not data:
            return ResponseBuilder.error(
                f"Failed to read {resolved_service} entries: {err}",
                host=host,
            )

        raw_entries = data.get("Members", [])

        # Sort newest-first client-side (not all BMCs support $orderby)
        raw_entries.sort(key=lambda e: e.get("Created", ""), reverse=True)

        entries: list[dict[str, Any]] = []
        for entry in raw_entries:
            created = entry.get("Created", "")
            severity = entry.get("Severity", "")

            if date_filter and not created.startswith(date_filter):
                continue
            if severity_filter and severity.lower() != severity_filter.lower():
                continue

            entries.append(
                {
                    "id": entry.get("Id"),
                    "created": created,
                    "severity": severity,
                    "message": entry.get("Message", ""),
                    "message_id": entry.get("MessageId", ""),
                    "sensor_type": entry.get("SensorType"),
                    "entry_code": entry.get("EntryCode"),
                    "category": (entry.get("Oem", {}).get("Dell", {}).get("Category")),
                }
            )

        return ResponseBuilder.success(
            host=host,
            log_service=resolved_service,
            total_fetched=len(raw_entries),
            filtered_count=len(entries),
            date_filter=date_filter,
            severity_filter=severity_filter,
            entries=entries,
        )

    tools["redfish_get_bmc_logs"] = redfish_get_bmc_logs
    mcp.tool(
        annotations=ToolAnnotations(
            title="Get BMC / iDRAC Logs",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
        )
    )(_wrap(redfish_get_bmc_logs))

    async def redfish_clear_bmc_log(
        host: str,
        user: str,
        password: str,
        log_service: str = "Sel",
        verify_tls: bool = False,
        timeout_s: int = 30,
        execution_mode: str = "execute",
    ) -> dict[str, Any]:
        """Clear a BMC log service (e.g. SEL).

        Posts a ClearLog action to the specified log service.
        This is a destructive operation -- log entries cannot be recovered.

        Args:
            log_service: Which log to clear (default "Sel"). Use
                redfish_query with query_type="bmc_log_services" to list
                available services.
        """
        if execution_mode == "render_curl":
            curl = _curl(verify_tls)
            return execution_mode_handler(
                verify_tls,
                [
                    f"# Clear {log_service} log:",
                    curl.post(
                        f"/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/{log_service}/Actions/LogService.ClearLog",
                        "{}",
                    ),
                ],
            )

        c = _client(host, user, password, verify_tls, timeout_s)

        from redfish_mcp.cli import _discover_log_service

        try:
            entries_url, resolved_service = await _to_thread(
                _discover_log_service,
                c,
                log_service or None,
            )
        except RuntimeError as e:
            return ResponseBuilder.error(str(e), host=host)

        svc_base_url = entries_url.removesuffix("/Entries")
        clear_url = f"{svc_base_url}/Actions/LogService.ClearLog"
        resp = await _to_thread(c.post_json, clear_url, {})

        if resp.status_code >= 400:
            return ResponseBuilder.error(
                f"ClearLog failed: {resp.status_code} {resp.text[:500]}",
                host=host,
                log_service=resolved_service,
            )

        return ResponseBuilder.success(
            host=host,
            log_service=resolved_service,
            message=f"{resolved_service} log cleared successfully",
        )

    tools["redfish_clear_bmc_log"] = redfish_clear_bmc_log
    mcp.tool(
        annotations=ToolAnnotations(
            title="Clear BMC Log",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=True,
        )
    )(_wrap(redfish_clear_bmc_log))

    # ==================== Dell GRUB Recovery ====================

    async def redfish_dell_grub_recovery(
        host: str,
        user: str,
        password: str,
        service_name: str,
        kernel_version: str,
        root_uuid: str,
        additional_kernel_params: str = "pci=realloc=off",
        verify_tls: bool = False,
        boot_wait_s: int = 300,
        re_enable_pxe: bool = True,
        allow_write: bool = False,
        async_mode: bool = True,
        ctx: Context | None = None,
    ) -> dict[str, Any] | CreateTaskResult:
        """Recover a Dell server by disabling a systemd service via GRUB.

        For Dell servers where the OS is unreachable because a systemd service
        (e.g., disable_acs.service) crashes the NIC or causes other hardware
        failures at boot. This tool automates a full recovery cycle:

          1. Configures BIOS serial console redirection for iDRAC SOL
          2. Temporarily disables PXE to force disk boot
          3. Power cycles and connects to Serial Over LAN via racadm SSH
          4. Catches GRUB menu, enters command-line mode
          5. Boots with systemd.mask=<service> (prevents service from running)
             + systemd.run="systemctl disable <service>" (permanent fix)
             + systemd.run_success_action=reboot (auto-reboots when done)
          6. Verifies BMC health after the self-healing reboot
          7. Re-enables PXE boot

        Requires: sshpass installed on the MCP host, pexpect Python package.
        Dell-specific: uses racadm CLI, iDRAC SOL, Dell BIOS attribute names.

        Args:
            service_name: The systemd service to disable (e.g., "disable_acs.service")
            kernel_version: Linux kernel version on the target (e.g., "6.8.0-101-generic")
            root_uuid: Root filesystem UUID (from /etc/fstab or GRUB config)
            additional_kernel_params: Extra kernel parameters to append
            boot_wait_s: Seconds to wait for the boot+disable+reboot cycle (default 300)
            re_enable_pxe: Re-enable PXE boot after recovery (default True)

        Safe-by-default: requires allow_write=true.
        Long-running operation (5-10 minutes). Runs as an async MCP task.
        """
        if not allow_write:
            return ResponseBuilder.error(
                "Refusing write operation without allow_write=true. "
                "This tool power cycles the server and modifies BIOS settings."
            )

        if async_mode and ctx is not None:

            async def work(task: ServerTaskContext) -> CallToolResult:
                await task.update_status(f"Starting Dell GRUB recovery: disabling {service_name}")

                async def _run() -> RecoveryResult:
                    return await _to_thread(
                        run_dell_grub_recovery,
                        host=host,
                        user=user,
                        password=password,
                        service_name=service_name,
                        kernel_version=kernel_version,
                        root_uuid=root_uuid,
                        additional_kernel_params=additional_kernel_params,
                        verify_tls=verify_tls,
                        boot_wait_s=boot_wait_s,
                        re_enable_pxe=re_enable_pxe,
                    )

                rec = await limiter.run(key=host, fn=_run)
                d = rec.to_dict() if isinstance(rec, RecoveryResult) else {"ok": True}
                return _as_call_tool_result(d)

            return await _create_background_task(
                ctx=ctx,
                ttl_ms=20 * 60 * 1000,  # 20 min TTL
                model_immediate_response=f"Queued: Dell GRUB recovery for {service_name}",
                work=work,
            )

        result = await _to_thread(
            run_dell_grub_recovery,
            host=host,
            user=user,
            password=password,
            service_name=service_name,
            kernel_version=kernel_version,
            root_uuid=root_uuid,
            additional_kernel_params=additional_kernel_params,
            verify_tls=verify_tls,
            boot_wait_s=boot_wait_s,
            re_enable_pxe=re_enable_pxe,
        )
        return result.to_dict() if isinstance(result, RecoveryResult) else {"ok": True}

    tools["redfish_dell_grub_recovery"] = redfish_dell_grub_recovery
    mcp.tool(
        annotations=ToolAnnotations(
            title="Dell GRUB Recovery (Disable Systemd Service)",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=True,
        )
    )(_wrap(redfish_dell_grub_recovery))

    # ==================== KVM Tool Stubs ====================

    tools["redfish_kvm_screen"] = _kvm_screen
    tools["redfish_kvm_sendkey"] = _kvm_sendkey
    tools["redfish_kvm_sendkeys"] = _kvm_sendkeys
    tools["redfish_kvm_type_and_read"] = _kvm_type_and_read
    tools["redfish_kvm_close"] = _kvm_close
    tools["redfish_kvm_status"] = _kvm_status

    mcp.tool(
        annotations=ToolAnnotations(
            title="KVM: capture screen",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
        )
    )(_wrap(_kvm_screen))
    mcp.tool(
        annotations=ToolAnnotations(
            title="KVM: send single key",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
        )
    )(_wrap(_kvm_sendkey))
    mcp.tool(
        annotations=ToolAnnotations(
            title="KVM: send keystrokes",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
        )
    )(_wrap(_kvm_sendkeys))
    mcp.tool(
        annotations=ToolAnnotations(
            title="KVM: send keys and read screen",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
        )
    )(_wrap(_kvm_type_and_read))
    mcp.tool(
        annotations=ToolAnnotations(
            title="KVM: close session",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
        )
    )(_wrap(_kvm_close))
    mcp.tool(
        annotations=ToolAnnotations(
            title="KVM: status",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
        )
    )(_wrap(_kvm_status))

    # ==================== MCP Resources ====================

    @mcp.resource("redfish://hardware-db/list")
    async def list_hardware_db() -> str:
        """List available hardware profiles in the database."""
        db = load_hardware_database()
        profiles = []
        for model_key, data in sorted(db.items()):
            hw = data.get("hardware", {})
            vendor = hw.get("vendor", "unknown")
            model = hw.get("model", model_key)
            profiles.append(f"{vendor}/{model}")
        return json.dumps({"profiles": profiles, "count": len(profiles)}, indent=2)

    @mcp.resource("redfish://hardware-db/{vendor}/{model}")
    async def get_hardware_profile(vendor: str, model: str) -> str:
        """Get hardware profile details for a specific vendor/model."""
        db = load_hardware_database()
        for _key, data in db.items():
            hw = data.get("hardware", {})
            if (
                hw.get("vendor", "").lower() == vendor.lower()
                and hw.get("model", "").lower() == model.lower()
            ):
                return json.dumps(data, indent=2, default=str)
        return json.dumps({"error": f"Profile not found: {vendor}/{model}"})

    # ==================== MCP Prompts ====================

    @mcp.prompt()
    async def investigate_host(host: str) -> str:
        """Investigate a host's health, firmware, and configuration status."""
        return (
            f"Please investigate host {host} by running these steps:\n"
            f"1. Get system info: redfish_get_info(host='{host}', info_types=['system', 'boot'])\n"
            f"2. Check health: redfish_query(host='{host}', query_type='health')\n"
            f"3. Get firmware versions: redfish_get_firmware_inventory(host='{host}')\n"
            f"4. Check hardware docs for known issues: redfish_get_hardware_docs(host='{host}')\n"
            f"5. Store findings: redfish_agent_report_observation(host='{host}', kind='health_check', summary='...')\n"
            f"\nProvide credentials when prompted. Use oob_ip from NetBox if hostname needs resolution."
        )

    @mcp.prompt()
    async def compare_hosts(host_a: str, host_b: str) -> str:
        """Compare BIOS and firmware between two hosts."""
        return (
            f"Please compare hosts {host_a} and {host_b}:\n"
            f"1. Diff BIOS settings: redfish_diff_bios_settings(host_a='{host_a}', host_b='{host_b}', smart_match=True)\n"
            f"2. Get firmware for each: redfish_get_firmware_inventory on both hosts\n"
            f"3. Summarize differences and flag any concerning discrepancies\n"
            f"\nProvide credentials when prompted."
        )

    @mcp.prompt()
    async def prepare_firmware_update(host: str) -> str:
        """Pre-flight check before firmware update."""
        return (
            f"Before updating firmware on {host}, verify:\n"
            f"1. Current firmware: redfish_get_firmware_inventory(host='{host}')\n"
            f"2. Hardware docs: redfish_get_hardware_docs(host='{host}', include_firmware_check=True)\n"
            f"3. Vendor errata: redfish_get_vendor_errata(host='{host}')\n"
            f"4. Check BIOS online: redfish_check_bios_online(host='{host}')\n"
            f"5. Review current BIOS: redfish_get_info(host='{host}', info_types=['bios_current'])\n"
            f"\nDo NOT proceed with update without explicit user confirmation."
        )

    # ==================== Screenshot Resources ====================

    @mcp.resource("screenshot://{host}")
    async def get_screenshot_resource(host: str) -> str:
        """Get the cached screenshot for a host.

        Returns cached OCR text and metadata if available.
        Agents can subscribe to this resource to receive
        ``notifications/resources/updated`` when the screen changes.
        """
        entry = screenshot_cache.get(host)
        if entry is None:
            return json.dumps(
                {"ok": False, "host": host, "error": "No cached screenshot. Capture one first."},
                indent=2,
            )
        result: dict[str, Any] = {
            "ok": True,
            "host": entry.host,
            "sha256": entry.sha256[:16],
            "mime_type": entry.mime_type,
            "size_bytes": len(entry.image_bytes),
            "method_used": entry.method_used,
        }
        if entry.ocr_text:
            result["ocr_text"] = entry.ocr_text
        return json.dumps(result, indent=2)

    # ==================== Health Resource ====================

    @mcp.resource("redfish://health")
    async def health() -> str:
        """Health check for the redfish-mcp server."""
        version = get_version("redfish-mcp")
        store_ok = agent._store is not None
        result = health_resource("redfish-mcp", version, checks={"state_store": store_ok})
        return json.dumps(result.to_dict(), indent=2)

    # ==================== Completions ====================

    from mcp.types import (
        Completion,
        CompletionArgument,
        CompletionContext,
        PromptReference,
        ResourceTemplateReference,
    )

    def _hardware_db_vendors() -> list[str]:
        db_dir = Path(__file__).parent.parent.parent / "hardware_db"
        if not db_dir.is_dir():
            return []
        return sorted(d.name for d in db_dir.iterdir() if d.is_dir() and not d.name.startswith("."))

    def _hardware_db_models(vendor: str) -> list[str]:
        db_dir = Path(__file__).parent.parent.parent / "hardware_db" / vendor
        if not db_dir.is_dir():
            return []
        return sorted(p.stem for p in db_dir.glob("*.json") if not p.name.startswith("_"))

    @mcp.completion()  # type: ignore[misc]
    async def handle_completion(
        ref: PromptReference | ResourceTemplateReference,
        argument: CompletionArgument,
        context: CompletionContext | None = None,
    ) -> Completion | None:
        """Provide autocomplete for prompts and resource URI templates."""
        prefix = argument.value.lower()

        if isinstance(ref, ResourceTemplateReference):
            resolved = (context.arguments or {}) if context else {}
            if argument.name == "vendor":
                matches = [v for v in _hardware_db_vendors() if v.lower().startswith(prefix)]
                return Completion(values=matches[:100])
            if argument.name == "model":
                vendor = resolved.get("vendor", "")
                matches = [m for m in _hardware_db_models(vendor) if m.lower().startswith(prefix)]
                return Completion(values=matches[:100])

        if isinstance(ref, PromptReference):
            if argument.name in ("host", "host_a", "host_b") and agent._store is not None:
                hosts = agent._store.recent_hosts(limit=50)
                matches = [h for h in hosts if h.lower().startswith(prefix)]
                return Completion(values=matches[:100])

        return None

    return mcp, tools


def main() -> None:  # pragma: no cover
    suppress_ssl_warnings()
    setup_logging(level="INFO", name="redfish_mcp")
    app, _ = create_mcp_app()
    version = get_version("redfish-mcp")
    logger.info("Starting redfish-mcp server v%s", version)
    app.run()
