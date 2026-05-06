"""NetBox MCP tool hints for cross-MCP references.

Other MCP servers import this module to get up-to-date NetBox
tool names, CLI commands, and MCP call signatures without
hardcoding strings that go stale on renames.

Usage::

    from netbox_mcp.hints import HINTS as NETBOX

    NETBOX.format_cli("device_lookup", hostname="gpu037")
    NETBOX.format_mcp("device_lookup", hostname="gpu037")
    NETBOX.as_agent_hints(hostname="<hostname>")
"""

from mcp_common.hints import HintRegistry, ToolHint

HINTS = HintRegistry(
    server_name="netbox-mcp",
    hints={
        "device_lookup": ToolHint(
            name="netbox_lookup_device",
            description="Look up a device by hostname; returns oob_ip and primary_ip",
            cli_example="netbox-cli lookup {hostname}",
            mcp_example='netbox_lookup_device(hostname="{hostname}")',
            args={
                "hostname": "Device hostname or partial name",
            },
        ),
        "search": ToolHint(
            name="netbox_search_objects",
            description="Search across NetBox object types (devices, IPs, etc.)",
            cli_example='netbox-cli search "{query}"',
            mcp_example='netbox_search_objects(query="{query}")',
            args={
                "query": "Search term (hostname, IP, serial number, etc.)",
            },
        ),
        "get_object": ToolHint(
            name="netbox_get_object_by_id",
            description="Fetch a single NetBox object by type and ID",
            cli_example="netbox-cli get {object_type} {object_id}",
            mcp_example='netbox_get_object_by_id(object_type="{object_type}", object_id={object_id})',
            args={
                "object_type": "NetBox type (e.g. dcim.device, ipam.ipaddress)",
                "object_id": "Numeric object ID",
            },
        ),
        "list_objects": ToolHint(
            name="netbox_get_objects",
            description="List NetBox objects with filters",
            cli_example='netbox-cli list {object_type} --filter "{filters}"',
            mcp_example='netbox_get_objects(object_type="{object_type}", filters={filters})',
            args={
                "object_type": "NetBox type (e.g. dcim.device)",
                "filters": "Filter dict or key=value string",
            },
        ),
    },
)
