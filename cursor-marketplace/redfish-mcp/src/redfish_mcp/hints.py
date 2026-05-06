"""Redfish MCP tool hints for cross-MCP references.

Other MCP servers import this module to get up-to-date Redfish
tool names, CLI commands, and MCP call signatures without
hardcoding strings that go stale on renames.

Usage::

    from redfish_mcp.hints import HINTS as REDFISH

    REDFISH.format_cli("power_state", host="10.0.0.1")
    REDFISH.format_mcp("screenshot", host="10.0.0.1")
    REDFISH.as_agent_hints(host="<oob_ip>")
"""

from mcp_common.hints import HintRegistry, ToolHint

HINTS = HintRegistry(
    server_name="redfish-mcp",
    hints={
        "power_state": ToolHint(
            name="redfish_query",
            description="Check system power state via BMC",
            cli_example="redfish-cli query {host} power_state",
            mcp_example='redfish_query(host="{host}", query_type="power_state")',
            args={"host": "BMC IP (oob_ip from NetBox, NOT primary_ip)"},
        ),
        "health": ToolHint(
            name="redfish_query",
            description="Check system health status via BMC",
            cli_example="redfish-cli health {host}",
            mcp_example='redfish_query(host="{host}", query_type="health")',
            args={"host": "BMC IP (oob_ip from NetBox)"},
        ),
        "screenshot": ToolHint(
            name="redfish_capture_screenshot",
            description="Capture VGA framebuffer from BMC",
            cli_example="redfish-cli screenshot {host} --text-only",
            mcp_example='redfish_capture_screenshot(host="{host}", return_mode="text_only")',
            args={"host": "BMC IP (oob_ip from NetBox)"},
        ),
        "watch_boot": ToolHint(
            name="redfish_watch_screen",
            description="Watch BMC screen by polling screenshots with OCR",
            cli_example="redfish-cli watch {host} --count 20",
            mcp_example='redfish_watch_screen(host="{host}", count=20)',
            args={"host": "BMC IP (oob_ip from NetBox)"},
        ),
        "system_info": ToolHint(
            name="redfish_get_info",
            description="Get system info (model, serial, BIOS, boot config)",
            cli_example="redfish-cli info {host} --types system,boot",
            mcp_example='redfish_get_info(host="{host}", info_types=["system", "boot"])',
            args={"host": "BMC IP (oob_ip from NetBox)"},
        ),
        "firmware_inventory": ToolHint(
            name="redfish_get_firmware_inventory",
            description="Get firmware versions for BIOS, BMC, NICs, GPUs",
            cli_example="redfish-cli firmware {host}",
            mcp_example='redfish_get_firmware_inventory(host="{host}")',
            args={"host": "BMC IP (oob_ip from NetBox)"},
        ),
    },
)
