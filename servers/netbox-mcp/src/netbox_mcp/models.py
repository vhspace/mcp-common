"""Output schemas for structured tool responses."""

from typing import Any

from pydantic import BaseModel, Field


class DeviceOOBSummary(BaseModel):
    """Compact OOB-management view of a NetBox device.

    Returned by ``netbox_oob_summary`` to give agents the IPs they
    actually need for cross-MCP workflows (Redfish via ``oob_ip``, SSH
    via ``primary_ip4``) without trawling the full device record.
    """

    id: int = Field(..., description="NetBox device ID.")
    name: str = Field(..., description="NetBox device hostname.")
    status: str | None = Field(None, description="Device status string (e.g. 'active', 'planned').")
    site: str | None = Field(None, description="Site name the device belongs to.")
    primary_ip4: str | None = Field(
        None, description="In-band IPv4 address (no CIDR); use for SSH/applications."
    )
    oob_ip: str | None = Field(
        None,
        description=("Out-of-band management IP (no CIDR); use for BMC/IPMI/Redfish."),
    )
    provider_machine_id: str | None = Field(
        None,
        description=(
            "Vendor / site-operator hostname for this node (custom field Provider_Machine_ID)."
        ),
    )


DEVICE_LOOKUP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "count": {"type": "integer", "description": "Number of matching devices"},
        "results": {
            "type": "array",
            "items": {"type": "object"},
            "description": "Device objects with enriched IP fields",
        },
        "query": {"type": "string", "description": "Original hostname query"},
    },
    "required": ["count", "results", "query"],
}

PAGINATED_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "count": {"type": "integer", "description": "Total matching objects"},
        "next": {
            "type": ["string", "null"],
            "description": "URL for next page, or null",
        },
        "previous": {
            "type": ["string", "null"],
            "description": "URL for previous page, or null",
        },
        "results": {
            "type": "array",
            "items": {"type": "object"},
            "description": "Objects for this page",
        },
    },
    "required": ["count", "results"],
}

DEVICE_UPDATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "device": {
            "type": "object",
            "description": "The updated device record",
        },
        "changes": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List of field changes applied (old → new)",
        },
    },
    "required": ["device", "changes"],
}

SEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Dictionary keyed by object type, each value a list of matching objects",
    "additionalProperties": {
        "type": "array",
        "items": {"type": "object"},
    },
}
