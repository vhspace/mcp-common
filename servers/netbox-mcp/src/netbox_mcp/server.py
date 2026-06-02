import argparse
import ipaddress as _ipaddress
import json
import logging
import threading
import time
from typing import Annotated, Any, Literal

import requests
import typer
from fastmcp import Context, FastMCP
from fastmcp.tools.tool import ToolAnnotations  # type: ignore[attr-defined]
from mcp.types import (
    Completion,
    CompletionArgument,
    CompletionContext,
    PromptReference,
    ResourceTemplateReference,
)
from mcp_common import HttpAccessTokenAuth, add_health_route, create_http_app, suppress_ssl_warnings
from mcp_common.agent_remediation import mcp_remediation_wrapper
from mcp_common.credential_chain import CachedResolver, CredentialChain, EnvResolver
from mcp_common.dual_mode import dual_mode_tool, tool_cli_subcommands
from mcp_common.health import health_resource
from mcp_common.logging import setup_logging
from pydantic import Field

from netbox_mcp.config import Settings, suppress_noisy_loggers
from netbox_mcp.models import (
    DEVICE_LOOKUP_SCHEMA,
    DEVICE_UPDATE_SCHEMA,
    PAGINATED_SCHEMA,
    SEARCH_SCHEMA,
    DeviceOOBSummary,
)
from netbox_mcp.netbox_client import NetBoxRestClient
from netbox_mcp.netbox_types import NETBOX_OBJECT_TYPES

logger = logging.getLogger(__name__)

DEFAULT_SEARCH_TYPES = [
    "dcim.device",
    "dcim.site",
    "ipam.ipaddress",
    "dcim.interface",
    "dcim.rack",
    "ipam.vlan",
    "circuits.circuit",
    "virtualization.virtualmachine",
]

VALID_FILTER_SUFFIXES = frozenset(
    {
        "n",
        "ic",
        "nic",
        "isw",
        "nisw",
        "iew",
        "niew",
        "ie",
        "nie",
        "empty",
        "regex",
        "iregex",
        "lt",
        "lte",
        "gt",
        "gte",
        "in",
    }
)

OBJECT_TYPES_LIST = "\n".join(f"- {t}" for t in sorted(NETBOX_OBJECT_TYPES.keys()))

_READ_ONLY = {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": True}
_WRITE = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": True,
    "openWorldHint": True,
}

VALID_DEVICE_STATUSES = frozenset(
    {"active", "planned", "staged", "failed", "inventory", "decommissioning", "offline"}
)

# JSON-schema enums DERIVED from the authoritative constants above (never
# hand-listed) so the schema the model sees lists exactly the values the server
# already accepts — tightening argument construction for small/fast models
# (netbox-mcp#126; evidence in #121) WITHOUT narrowing accepted inputs. Runtime
# validation is unchanged (``_validate_object_type`` / the explicit status check
# in ``netbox_update_device``); these only enrich the published tool JSON-schema.
#
# fastmcp 3.x / mcp-common expose no tool-level ``strict`` flag, so argument
# reliability comes from these enums plus fastmcp's default
# ``additionalProperties: false`` on each tool's top-level parameter object
# (inspect forwards ``enum``/``strict`` to Together; the ``filters`` dict stays
# open by design — it accepts arbitrary NetBox field lookups).
_OBJECT_TYPE_ENUM: list[str] = sorted(NETBOX_OBJECT_TYPES)
_DEVICE_STATUS_ENUM: list[str] = sorted(VALID_DEVICE_STATUSES)

# Reusable parameter types carrying the enum in their JSON schema. ``Annotated``
# flattens when nested, so these compose with ``typer.Argument(...)`` on the
# dual-mode tools, e.g. ``Annotated[ObjectTypeParam, typer.Argument(...)]``.
ObjectTypeParam = Annotated[str, Field(json_schema_extra={"enum": _OBJECT_TYPE_ENUM})]
DeviceStatusParam = Annotated[str, Field(json_schema_extra={"enum": _DEVICE_STATUS_ENUM})]

ESSENTIAL_DEVICE_FIELDS = {
    "id",
    "name",
    "status",
    "serial",
    "site",
    "rack",
    "position",
    "device_role",
    "role",
    "device_type",
    "cluster",
    "primary_ip4",
    "primary_ip6",
    "oob_ip",
    "primary_ip4_address",
    "primary_ip6_address",
    "oob_ip_address",
    "provider_machine_id",
    "custom_fields",
    "tags",
}

_NESTED_NAME_KEYS = frozenset({"site", "rack", "device_type", "device_role", "role", "cluster"})


def _trim_device(device: dict[str, Any]) -> dict[str, Any]:
    """Trim a device dict to essential fields for reduced token usage."""
    trimmed: dict[str, Any] = {}
    for key in ESSENTIAL_DEVICE_FIELDS:
        if key not in device:
            continue
        val = device[key]
        if isinstance(val, dict) and key in _NESTED_NAME_KEYS:
            trimmed[key] = {
                "id": val.get("id"),
                "name": val.get("name", val.get("model", val.get("display"))),
            }
        elif key == "custom_fields" and isinstance(val, dict):
            provider_id = val.get("Provider_Machine_ID")
            if provider_id:
                trimmed[key] = {"Provider_Machine_ID": provider_id}
        elif key == "status" and isinstance(val, dict):
            trimmed[key] = val.get("value", val)
        else:
            trimmed[key] = val
    return trimmed


mcp = FastMCP("NetBox")
netbox: NetBoxRestClient | None = None
vpn_monitor: "VPNMonitor | None" = None


# ---------------------------------------------------------------------------
# VPN connectivity monitor (Issue #13)
# ---------------------------------------------------------------------------

VPN_CHECK_INTERVAL = 300  # seconds between background checks


class VPNMonitor:
    """Periodic background monitor for VPN connectivity.

    Cloudflare blocks write operations (PATCH/PUT/DELETE) from non-VPN
    environments.  This monitor probes the API periodically and caches
    the result so callers can fail fast with a clear message.
    """

    def __init__(self, client: NetBoxRestClient, interval: int = VPN_CHECK_INTERVAL):
        self._client = client
        self._interval = interval
        self._connected: bool | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_check: float = 0

    def start(self) -> None:
        """Run the initial check synchronously, then start the background loop."""
        self._check()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="vpn-monitor")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _check(self) -> None:
        was = self._connected
        now = self._client.check_vpn()
        with self._lock:
            self._connected = now
            self._last_check = time.monotonic()
        if was is True and not now:
            logger.warning("VPN connection lost — write operations will be blocked")
        elif was is False and now:
            logger.info("VPN connection restored — write operations available")

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            self._check()

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return bool(self._connected)

    def require_vpn(self) -> None:
        """Raise ``ValueError`` with an agent-friendly message if VPN is down."""
        if not self.is_connected:
            raise ValueError(
                "VPN not connected — write operations require VPN access to NetBox. "
                "Connect to the VPN and retry."
            )


# ---------------------------------------------------------------------------
# Health endpoint (via mcp-common)
# ---------------------------------------------------------------------------


async def _netbox_health_check() -> dict[str, Any]:
    """Readiness check: verify NetBox API and VPN connectivity."""
    checks: dict[str, Any] = {}
    if netbox is not None:
        try:
            netbox.get("status")
            checks["netbox_api"] = {"status": "ok"}
        except Exception:
            checks["netbox_api"] = {"status": "error"}
    if vpn_monitor is not None:
        connected = vpn_monitor.is_connected
        checks["vpn"] = {
            "status": "ok" if connected else "degraded",
            "detail": "Write operations available"
            if connected
            else "Write operations blocked — VPN not connected",
        }
    return checks


add_health_route(mcp, "netbox-mcp", health_check_fn=_netbox_health_check)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _ensure_client() -> NetBoxRestClient:
    """Return the initialized NetBox client or raise a clear error."""
    if netbox is None:
        raise RuntimeError(
            "NetBox client not initialized. "
            "Ensure the server is started via main() before calling tools."
        )
    return netbox


def _validate_object_type(object_type: str) -> None:
    """Raise ValueError if object_type is not in the supported mapping."""
    if object_type not in NETBOX_OBJECT_TYPES:
        raise ValueError(
            f"Invalid object_type '{object_type}'. Must be one of:\n{OBJECT_TYPES_LIST}"
        )


def _endpoint_for_type(object_type: str) -> str:
    """Return the API endpoint path for a given object type."""
    return NETBOX_OBJECT_TYPES[object_type]["endpoint"]


def _serialize_filters(filters: dict[str, Any]) -> dict[str, Any]:
    """Normalize list-valued filters for the NetBox REST API.

    NetBox 4.x silently ignores ``__in`` suffixes (e.g. ``id__in``).
    The correct way to filter by multiple values is repeated keys on
    the base field name: ``?id=1&id=2&id=3``.  The ``requests`` library
    produces this naturally when a param value is a Python list.

    This helper strips the ``__in`` suffix so agents can write the
    intuitive ``{"id__in": [1, 2, 3]}`` and get the right query.
    """
    serialized: dict[str, Any] = {}
    for key, value in filters.items():
        if isinstance(value, list) and key.endswith("__in"):
            serialized[key[: -len("__in")]] = value
        else:
            serialized[key] = value
    return serialized


def _build_field_params(fields: list[str] | None = None, brief: bool = False) -> dict[str, str]:
    """Build query params for field filtering and brief mode."""
    params: dict[str, str] = {}
    if fields:
        params["fields"] = ",".join(fields)
    if brief:
        params["brief"] = "1"
    return params


def _is_ip_address(s: str) -> bool:
    """Return True if *s* is a valid IPv4 or IPv6 address."""
    try:
        _ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


def _extract_ip_address(ip_field: dict[str, Any] | None) -> str | None:
    """Extract bare IP (no CIDR suffix) from a NetBox nested IP object.

    NetBox returns IPs as ``{"id": 1, "address": "10.0.0.1/24", ...}``.
    This helper returns just ``"10.0.0.1"``.
    """
    if ip_field and isinstance(ip_field, dict):
        addr = ip_field.get("address", "")
        return addr.split("/")[0] if addr else None
    return None


def _netbox_api_call(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Wrap a NetBox API call with user-friendly error handling."""
    try:
        return fn(*args, **kwargs)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"

        from netbox_mcp.netbox_client import _is_cloudflare_block

        if exc.response is not None and _is_cloudflare_block(exc.response):
            if vpn_monitor is not None:
                vpn_monitor.require_vpn()
            raise ValueError(
                "VPN not connected — write operations require VPN access to NetBox. "
                "Connect to the VPN and retry."
            ) from None

        detail = ""
        if exc.response is not None:
            try:
                detail = f": {exc.response.json().get('detail', exc.response.text[:200])}"
            except Exception:
                detail = f": {exc.response.text[:200]}"
        raise ValueError(f"NetBox API returned HTTP {status}{detail}") from None
    except requests.ConnectionError:
        raise ValueError(
            "Could not connect to NetBox. Check NETBOX_URL and network connectivity."
        ) from None
    except requests.Timeout:
        raise ValueError("NetBox API request timed out. The server may be overloaded.") from None


def validate_filters(filters: dict[str, Any]) -> None:
    """Validate that filters don't use multi-hop relationship traversal."""
    for filter_name in filters:
        if filter_name in ("limit", "offset", "fields", "q"):
            continue
        if "__" not in filter_name:
            continue

        parts = filter_name.split("__")
        if len(parts) == 2 and parts[-1] in VALID_FILTER_SUFFIXES:
            continue
        if len(parts) >= 2:
            raise ValueError(
                f"Invalid filter '{filter_name}': Multi-hop relationship "
                f"traversal or invalid lookup suffix not supported. "
                f"Use direct field filters like 'site_id' or two-step queries."
            )


def _parse_cli_args() -> dict[str, Any]:
    """Parse command-line arguments for configuration overrides."""
    parser = argparse.ArgumentParser(
        description="NetBox MCP Server - Model Context Protocol server for NetBox",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument("--netbox-url", type=str, help="Base URL of the NetBox instance")
    parser.add_argument("--netbox-token", type=str, help="API token for NetBox authentication")
    parser.add_argument(
        "--transport", type=str, choices=["stdio", "http"], help="MCP transport protocol"
    )
    parser.add_argument("--host", type=str, help="Host address for HTTP server")
    parser.add_argument("--port", type=int, help="Port for HTTP server")

    ssl_group = parser.add_mutually_exclusive_group()
    ssl_group.add_argument("--verify-ssl", action="store_true", dest="verify_ssl", default=None)
    ssl_group.add_argument("--no-verify-ssl", action="store_false", dest="verify_ssl")

    parser.add_argument(
        "--log-level", type=str, choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    )

    args = parser.parse_args()
    return {k: v for k, v in vars(args).items() if v is not None}


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

FIELDS_DESCRIPTION = """Optional list of specific fields to return.
**IMPORTANT: ALWAYS USE THIS PARAMETER TO MINIMIZE TOKEN USAGE.**
- None or [] = returns all fields (NOT RECOMMENDED)
- ['id', 'name'] = returns only specified fields (RECOMMENDED)
Uses NetBox's native field filtering via ?fields= parameter."""

GET_OBJECTS_DESCRIPTION = f"""Query/list NetBox objects of ONE type by filters (devices, sites, IPs, clusters, VLANs, etc.).

For a single device by hostname / provider-ID / IP, prefer netbox_lookup_device.

FILTERS (exact field lookups, AND-combined):
  Direct:   {{'site_id': 1, 'status': 'active'}}
  Suffix:   {{'name__ic': 'switch', 'id__in': [1, 2, 3], 'vid__gte': 100}}
  Suffixes: n, ic, nic, isw, nisw, iew, niew, ie, nie, empty, regex, iregex, lt, lte, gt, gte, in
  No multi-hop ({{'device__site_id': 1}}) — do two steps instead.

Filter by NAME → resolve to *_id FIRST. A bare cluster=/site= text filter does
icontains and over-matches sibling clusters/sites (returns thousands). Two-step:
  # exact devices in a cluster: resolve the cluster id, then filter by cluster_id
  c = netbox_get_objects('virtualization.cluster', {{'name': 'cartesia5'}})
  netbox_get_objects('dcim.device', {{'cluster_id': c['results'][0]['id'], 'status': 'active'}})
  # devices at a site
  s = netbox_get_objects('dcim.site', {{'name': 'NYC'}})
  netbox_get_objects('dcim.device', {{'site_id': s['results'][0]['id']}})

COUNTING / LARGE RESULTS: results are paginated (max 100 per page). The response
'count' is the FULL total — report THAT for "how many" / cluster-size questions, not
len(results) (a single page). To enumerate every member, page with offset until you
have collected 'count' rows.

Device status values: {", ".join(sorted(VALID_DEVICE_STATUSES))}

Returns a paginated dict: count, next, previous, results.

Valid object_type values:
{OBJECT_TYPES_LIST}
"""


# NOTE: kept on plain ``@mcp.tool`` (NOT ``@dual_mode_tool``) on purpose.
# Two independent framework constraints block CLI synthesis here:
#   1. ``filters`` is a top-level ``dict`` param — Typer cannot render a bare
#      ``dict`` as a CLI option (togethercomputer/mcp-common#111). Forcing it through
#      ``@dual_mode_tool`` crashes the CLI at build time.
#   2. ``ordering: str | list[str] | None`` is a non-Optional union, which the
#      ``@dual_mode_tool`` decorator rejects outright at decoration time (Typer
#      cannot map scalar-or-list unions; see mcp-common edge cases / #110).
# The CLI surface for filtered listing is provided by the hand-written
# ``list``/``devices``/``sites``/``clusters``/``ips`` commands in ``cli.py``.
@mcp.tool(
    annotations=ToolAnnotations(title="Get NetBox Objects", **_READ_ONLY),
    description=GET_OBJECTS_DESCRIPTION,
    tags={"query", "dcim", "ipam"},
    output_schema=PAGINATED_SCHEMA,
)
@mcp_remediation_wrapper(project_repo="togethercomputer/mcp-common", logger=logger)
def netbox_get_objects(
    object_type: ObjectTypeParam,
    filters: dict[str, Any],
    fields: list[str] | None = None,
    brief: bool = False,
    limit: Annotated[int, Field(default=20, ge=1, le=100)] = 20,
    offset: Annotated[int, Field(default=0, ge=0)] = 0,
    ordering: str | list[str] | None = None,
) -> dict[str, Any]:
    """Get objects from NetBox based on their type and filters."""
    _validate_object_type(object_type)
    validate_filters(filters)
    client = _ensure_client()

    params = _serialize_filters(filters)
    params["limit"] = limit
    params["offset"] = offset
    params.update(_build_field_params(fields, brief))

    if ordering:
        if isinstance(ordering, list):
            ordering = ",".join(ordering)
        if ordering.strip():
            params["ordering"] = ordering

    result: dict[str, Any] = _netbox_api_call(
        client.get, _endpoint_for_type(object_type), params=params
    )
    return result


@dual_mode_tool(
    mcp,
    name="netbox_get_object_by_id",
    cli_name="get-object-by-id",
    annotations=ToolAnnotations(title="Get NetBox Object by ID", **_READ_ONLY),
    tags={"query", "dcim", "ipam"},
)
@mcp_remediation_wrapper(project_repo="togethercomputer/mcp-common", logger=logger)
def netbox_get_object_by_id(
    object_type: Annotated[
        ObjectTypeParam, typer.Argument(help="NetBox object type (e.g. 'dcim.device').")
    ],
    object_id: Annotated[int, typer.Argument(help="Numeric object ID.")],
    fields: list[str] | None = None,
    brief: bool = False,
) -> dict[str, Any]:
    """Get a single NetBox object by its numeric ID.

    Use this when you already have ONE object's ID from a previous query.
    For 2+ IDs, use netbox_get_objects_by_ids (one batched call) instead of a loop.
    For device lookups by hostname, prefer netbox_lookup_device instead.

    Args:
        object_type: NetBox object type (e.g. "dcim.device", "ipam.ipaddress")
        object_id: The numeric ID of the object
        fields: Specific fields to return (RECOMMENDED to minimize token usage).
                e.g. ['id', 'name', 'status', 'primary_ip4', 'oob_ip']
        brief: Minimal representation without nested related objects.

    Returns:
        Object dict (complete or with only requested fields)
    """
    _validate_object_type(object_type)
    client = _ensure_client()
    params = _build_field_params(fields, brief)
    result: dict[str, Any] = _netbox_api_call(
        client.get, _endpoint_for_type(object_type), id=object_id, params=params
    )
    return result


BATCH_FETCH_PAGE_SIZE = 100

# Full MCP tool description, kept in a module constant so migrating to
# ``@dual_mode_tool`` (which otherwise defaults the description to the first
# docstring line) preserves the original rich description verbatim while the
# concise function docstring drives the synthesized CLI command's help.
GET_OBJECTS_BY_IDS_DESCRIPTION = """Batch-fetch objects by numeric ID in ONE call — use whenever you have 2+ IDs.

Prefer this over calling netbox_get_object_by_id in a loop. Pages internally and
returns every requested object.
  Example: netbox_get_objects_by_ids("dcim.device", ids=[101, 102, 103])

Args:
    object_type: NetBox object type (e.g. "dcim.device", "virtualization.cluster")
    ids: List of numeric IDs to fetch
    fields: Specific fields to return (RECOMMENDED to minimize token usage)
    brief: Minimal representation without nested related objects

Returns:
    Dict with 'count' and 'results' containing all matched objects."""


@dual_mode_tool(
    mcp,
    name="netbox_get_objects_by_ids",
    cli_name="get-objects-by-ids",
    annotations=ToolAnnotations(title="Get NetBox Objects by IDs", **_READ_ONLY),
    description=GET_OBJECTS_BY_IDS_DESCRIPTION,
    tags={"query", "dcim", "ipam"},
)
@mcp_remediation_wrapper(project_repo="togethercomputer/mcp-common", logger=logger)
def netbox_get_objects_by_ids(
    object_type: Annotated[
        ObjectTypeParam, typer.Argument(help="NetBox object type (e.g. 'dcim.device').")
    ],
    ids: Annotated[list[int], typer.Argument(help="Numeric object IDs to fetch.")],
    fields: list[str] | None = None,
    brief: bool = False,
) -> dict[str, Any]:
    """Fetch multiple NetBox objects by their IDs in a single call."""
    _validate_object_type(object_type)
    client = _ensure_client()

    if not ids:
        return {"count": 0, "results": []}

    unique_ids = list(dict.fromkeys(ids))
    field_params = _build_field_params(fields, brief)
    endpoint = _endpoint_for_type(object_type)
    all_results: list[dict[str, Any]] = []

    for i in range(0, len(unique_ids), BATCH_FETCH_PAGE_SIZE):
        chunk = unique_ids[i : i + BATCH_FETCH_PAGE_SIZE]
        params: dict[str, Any] = _serialize_filters({"id__in": chunk})
        params["limit"] = len(chunk)
        params.update(field_params)

        response: dict[str, Any] = _netbox_api_call(client.get, endpoint, params=params)
        all_results.extend(response.get("results", []))

    return {"count": len(all_results), "results": all_results}


CHANGELOGS_DESCRIPTION = """Get audit trail / change history from NetBox.

Use this to find who changed what and when. Useful for investigating
recent modifications to devices, IPs, sites, or any NetBox object.

Common filters:
  - user / user_id: Who made the change
  - changed_object_type_id: ContentType ID of the changed object
  - changed_object_id: ID of the specific changed object
  - action: 'create', 'update', or 'delete'
  - time_before / time_after: ISO 8601 timestamps (e.g. '2025-01-01T00:00:00Z')
  - q: Search term in object representation

Returns paginated response dict with count, next, previous, results.
"""


# Registered through the dual-mode framework for consistency with the other
# read tools, but kept ``mcp_only=True``: ``filters`` is a top-level ``dict``
# param that Typer cannot render as a CLI option (togethercomputer/mcp-common#111), so
# the framework cannot synthesize a CLI command here. The CLI surface is the
# hand-written ``changelogs`` command in ``cli.py`` (``--filter key=value``).
@dual_mode_tool(
    mcp,
    name="netbox_get_changelogs",
    mcp_only=True,
    # Derived CLI name would be ``get-changelogs``; the real hand-written CLI
    # command (cli.py) is ``changelogs``. Declare the alias so
    # ``tool_cli_subcommands(mcp)`` maps this tool to its actual subcommand for
    # ``cli_tool_use_scorer`` (netbox-mcp#125). ``mcp_only`` => no CLI command
    # is synthesized; ``cli_aliases`` are scoring equivalences only.
    cli_aliases=("changelogs",),
    annotations=ToolAnnotations(title="Get NetBox Changelogs", **_READ_ONLY),
    description=CHANGELOGS_DESCRIPTION,
    tags={"query", "audit"},
    output_schema=PAGINATED_SCHEMA,
)
@mcp_remediation_wrapper(project_repo="togethercomputer/mcp-common", logger=logger)
def netbox_get_changelogs(
    filters: dict[str, Any],
    fields: list[str] | None = None,
    limit: Annotated[int, Field(default=20, ge=1, le=100)] = 20,
    offset: Annotated[int, Field(default=0, ge=0)] = 0,
) -> dict[str, Any]:
    """Get object change records (changelogs) from NetBox."""
    client = _ensure_client()
    params = _serialize_filters(filters)
    params["limit"] = limit
    params["offset"] = offset
    if fields:
        params["fields"] = ",".join(fields)
    result: dict[str, Any] = _netbox_api_call(client.get, "core/object-changes", params=params)
    return result


LOOKUP_DEVICE_DESCRIPTION = """Resolve ONE device — by hostname, provider/vendor machine ID, OR IP address — to its details + IPs in a single call.

Use this FIRST for any single-device question (not just hostnames): it searches by
name, then falls back to the Provider_Machine_ID custom field, then to an IPAM lookup.
  - hostname:           netbox_lookup_device("f30409c5-342")
  - provider/vendor ID: netbox_lookup_device("gpu001c")   # a vendor name, NOT a Together hostname → Provider_Machine_ID
  - IP address:         netbox_lookup_device("10.0.0.5")   # answers "which device has IP X?" via reverse IPAM lookup
Don't give up just because the query is not a Together hostname. Pass site= to
disambiguate when a provider ID / short name can repeat across sites.

Returns device details including:
  - primary_ip4 / primary_ip6: in-band IPs (SSH, applications)
  - oob_ip: out-of-band management IP (BMC/IPMI/Redfish)
  - primary_ip4_address, oob_ip_address: bare IPs without CIDR
  - provider_machine_id: the vendor/site-operator hostname for this node
    (this is what "vendor name" means here, NOT device_type.manufacturer)

CRITICAL — IP field usage for cross-MCP workflows:
  - Redfish MCP:  use oob_ip_address (NOT primary_ip) for BMC access
  - MAAS / AWX:   use the device name or primary_ip
"""


@dual_mode_tool(
    mcp,
    name="netbox_lookup_device",
    cli_name="lookup-device",
    annotations=ToolAnnotations(title="Lookup Device by Hostname", **_READ_ONLY),
    description=LOOKUP_DEVICE_DESCRIPTION,
    tags={"query", "dcim"},
    output_schema=DEVICE_LOOKUP_SCHEMA,
)
@mcp_remediation_wrapper(project_repo="togethercomputer/mcp-common", logger=logger)
def netbox_lookup_device(
    hostname: Annotated[
        str, typer.Argument(help="Device hostname, provider machine ID, or IP address.")
    ],
    fields: list[str] | None = None,
    site: str | None = None,
) -> dict[str, Any]:
    """Look up a device by hostname and return its details with all IP addresses."""
    client = _ensure_client()

    site_id: int | None = None
    if site:
        resp = _netbox_api_call(client.get, "dcim/sites", params={"name": site, "limit": 1})
        site_results = resp.get("results", [])
        if site_results and site_results[0].get("name") == site:
            site_id = site_results[0]["id"]
        else:
            return {
                "count": 0,
                "results": [],
                "query": hostname,
                "_hint": f"Site '{site}' not found in NetBox. Use netbox_search_objects to find valid site names.",
            }

    params: dict[str, Any] = {"name__ic": hostname, "limit": 5}
    if site_id is not None:
        params["site_id"] = site_id
    if fields:
        params["fields"] = ",".join(fields)

    response = _netbox_api_call(client.get, "dcim/devices", params=params)
    devices = response.get("results", [])

    if not devices:
        fallback_params: dict[str, Any] = {"cf_Provider_Machine_ID": hostname, "limit": 5}
        if site_id is not None:
            fallback_params["site_id"] = site_id
        if fields:
            fallback_params["fields"] = ",".join(fields)
        response = _netbox_api_call(client.get, "dcim/devices", params=fallback_params)
        if response.get("count", 0) <= 50:
            devices = response.get("results", [])

    if not devices and _is_ip_address(hostname):
        ip_resp = _netbox_api_call(
            client.get, "ipam/ip-addresses", params={"address": hostname, "limit": 5}
        )
        ip_results = ip_resp.get("results", []) if isinstance(ip_resp, dict) else []
        device_ids_seen: set[int] = set()
        for ip_obj in ip_results:
            assigned = ip_obj.get("assigned_object") or {}
            dev_ref = assigned.get("device") or {}
            dev_id = dev_ref.get("id")
            if dev_id and dev_id not in device_ids_seen:
                device_ids_seen.add(dev_id)
                device = _netbox_api_call(client.get, "dcim/devices", id=dev_id)
                if isinstance(device, dict) and device.get("id"):
                    devices.append(device)

    if not devices:
        return {"count": 0, "results": [], "query": hostname}

    for device in devices:
        device["primary_ip4_address"] = _extract_ip_address(device.get("primary_ip4"))
        device["primary_ip6_address"] = _extract_ip_address(device.get("primary_ip6"))
        device["oob_ip_address"] = _extract_ip_address(device.get("oob_ip"))
        provider_id = device.get("custom_fields", {}).get("Provider_Machine_ID")
        if provider_id:
            device["provider_machine_id"] = provider_id

    if not fields:
        devices = [_trim_device(d) for d in devices]

    result: dict[str, Any] = {"count": len(devices), "results": devices, "query": hostname}
    if len(devices) > 1:
        result["_hint"] = (
            f"Multiple devices matched '{hostname}'. Use site= to narrow, "
            "or verify the correct device by checking site/cluster fields."
        )
    return result


# ---------------------------------------------------------------------------
# Demo tool: returns a Pydantic model so the dual-mode framework's
# Pydantic-aware JSON encoding gets exercised end-to-end. Also covers the
# ``Literal[...]`` parameter mapping in the @dual_mode_tool framework.
# ---------------------------------------------------------------------------

OOB_SUMMARY_DESCRIPTION = """Compact BMC/out-of-band summary for ONE device — use for "what's the BMC/OOB IP of X?".

Example: netbox_oob_summary("f30409c5-342"). Returns a structured ``DeviceOOBSummary``
with just the cross-MCP fields (no Redfish call, no generic object dump):

  - ``oob_ip``: BMC/IPMI/Redfish address
  - ``primary_ip4``: SSH/application address
  - ``provider_machine_id``: vendor/site-operator hostname

Pass ``status_filter`` to require a specific NetBox status (the call fails if it does
not match) — handy for guarding write workflows against stale/decommissioned devices.
Pass ``site=`` to disambiguate. Read-only.
"""


@dual_mode_tool(
    mcp,
    name="netbox_oob_summary",
    cli_name="oob-summary",
    annotations=ToolAnnotations(title="Device OOB Summary", **_READ_ONLY),
    description=OOB_SUMMARY_DESCRIPTION,
    tags={"query", "dcim"},
)
@mcp_remediation_wrapper(project_repo="togethercomputer/mcp-common", logger=logger)
def netbox_oob_summary(
    hostname: Annotated[str, typer.Argument(help="Device hostname to summarize.")],
    status_filter: Literal[
        "active", "planned", "staged", "failed", "inventory", "decommissioning", "offline"
    ]
    | None = None,
    site: str | None = None,
) -> DeviceOOBSummary:
    """Return a compact OOB-management summary for a NetBox device."""
    lookup = netbox_lookup_device(hostname=hostname, site=site)
    devices = lookup.get("results", []) if isinstance(lookup, dict) else []
    if not devices:
        raise ValueError(f"No device found matching '{hostname}'.")
    if len(devices) > 1:
        names = ", ".join(d.get("name", "?") for d in devices[:5])
        raise ValueError(
            f"Multiple devices matched '{hostname}': {names}. "
            "Pass site= to disambiguate or use an exact hostname."
        )
    device = devices[0]

    status_val = device.get("status")
    if isinstance(status_val, dict):
        status_val = status_val.get("value", status_val.get("label"))

    if status_filter is not None and status_val != status_filter:
        raise ValueError(
            f"Device '{device.get('name', hostname)}' status is "
            f"{status_val!r}, expected {status_filter!r}."
        )

    site_field = device.get("site")
    site_name = site_field.get("name") if isinstance(site_field, dict) else site_field

    return DeviceOOBSummary(
        id=device["id"],
        name=device.get("name") or "",
        status=status_val if isinstance(status_val, str) else None,
        site=site_name if isinstance(site_name, str) else None,
        primary_ip4=device.get("primary_ip4_address"),
        oob_ip=device.get("oob_ip_address"),
        provider_machine_id=device.get("provider_machine_id"),
    )


UPDATE_DEVICE_DESCRIPTION = f"""Update a device's status or cluster assignment in NetBox.

This is a WRITE operation that modifies data in NetBox. Requires VPN connectivity.

The device can be specified by hostname (case-insensitive partial match) or numeric ID.
At least one field (status or cluster) must be provided.

Valid status values: {", ".join(sorted(VALID_DEVICE_STATUSES))}

Safety:
  - Device must exist before patching (looked up first)
  - At least one field to update is required
  - Returns the full updated device record and a summary of changes

Returns dict with 'device' (updated record) and 'changes' (list of old → new).
"""


@mcp.tool(
    annotations=ToolAnnotations(title="Update Device Status", **_WRITE),
    description=UPDATE_DEVICE_DESCRIPTION,
    tags={"write", "dcim"},
    output_schema=DEVICE_UPDATE_SCHEMA,
)
@mcp_remediation_wrapper(project_repo="togethercomputer/mcp-common", logger=logger)
def netbox_update_device(
    device: str,
    status: DeviceStatusParam | None = None,
    cluster: str | None = None,
) -> dict[str, Any]:
    """Update a device's status or cluster assignment."""
    if status is None and cluster is None:
        raise ValueError("At least one of 'status' or 'cluster' must be provided.")

    if status is not None and status not in VALID_DEVICE_STATUSES:
        raise ValueError(
            f"Invalid status '{status}'. Valid values: {', '.join(sorted(VALID_DEVICE_STATUSES))}"
        )

    client = _ensure_client()

    if vpn_monitor is not None:
        vpn_monitor.require_vpn()

    if device.isdigit():
        device_obj = _netbox_api_call(client.get, "dcim/devices", id=int(device))
    else:
        resp = _netbox_api_call(client.get, "dcim/devices", params={"name__ic": device, "limit": 5})
        results = resp.get("results", [])
        if not results:
            raise ValueError(f"No device found matching '{device}'.")
        if len(results) > 1:
            names = ", ".join(d.get("name", "?") for d in results)
            raise ValueError(
                f"Multiple devices match '{device}': {names}. Use an exact hostname or numeric ID."
            )
        device_obj = results[0]

    device_id = device_obj["id"]
    device_name = device_obj.get("name", device)

    patch_data: dict[str, Any] = {}
    changes: list[str] = []

    if status is not None:
        old_status = device_obj.get("status", {})
        old_val = (
            old_status.get("value", old_status) if isinstance(old_status, dict) else old_status
        )
        patch_data["status"] = status
        changes.append(f"status: {old_val} → {status}")

    if cluster is not None:
        cluster_resp = _netbox_api_call(
            client.get,
            "virtualization/clusters",
            params={"name": cluster, "limit": 1},
        )
        cluster_results = cluster_resp.get("results", [])
        if not cluster_results or cluster_results[0].get("name") != cluster:
            raise ValueError(f"Cluster '{cluster}' not found in NetBox.")
        cluster_id = cluster_results[0]["id"]
        old_cluster = device_obj.get("cluster", {})
        old_name = (
            old_cluster.get("name", old_cluster) if isinstance(old_cluster, dict) else old_cluster
        )
        patch_data["cluster"] = cluster_id
        changes.append(f"cluster: {old_name} → {cluster}")

    logger.info(
        "Updating device '%s' (id=%d): %s",
        device_name,
        device_id,
        "; ".join(changes),
    )

    updated = _netbox_api_call(client.patch, "dcim/devices", id=device_id, data=patch_data)

    return {"device": updated, "changes": changes}


SEARCH_DESCRIPTION = f"""Perform global search across multiple NetBox object types.

Searches names, descriptions, IP addresses, serial numbers, asset tags,
and other key fields. Use this for broad discovery when you don't know
the exact object type.

TIP: For device hostname lookups, prefer netbox_lookup_device — it's faster
and returns structured IP data for cross-MCP workflows.

Args:
    query: Search term (device names, IPs, serial numbers, hostnames, site names)
    object_types: Limit search to specific types (optional).
                 Default: {DEFAULT_SEARCH_TYPES}
    fields: Specific fields to return (RECOMMENDED to minimize token usage).
    limit: Max results per object type (default 5, max 100)

Returns:
    Dictionary with object_type keys and lists of matching objects.
"""


# Kept on plain ``@mcp.tool`` (NOT ``@dual_mode_tool``) on purpose. Although
# its parameters are framework-supported, the hand-written ``search`` CLI
# command in ``cli.py`` adds cluster auto-expansion (resolving a matched
# cluster to its member devices + sites) that this plain global-search tool
# does not implement — that richer, paginated behavior is the large-cluster
# path we must preserve. Synthesizing ``search`` from this function would lose
# it. (This is also an async tool wrapped by a guard decorator; cf.
# togethercomputer/mcp-common#112.)
@mcp.tool(
    annotations=ToolAnnotations(title="Search NetBox Objects", **_READ_ONLY),
    description=SEARCH_DESCRIPTION,
    tags={"query", "dcim", "ipam"},
    output_schema=SEARCH_SCHEMA,
)
@mcp_remediation_wrapper(project_repo="togethercomputer/mcp-common", logger=logger)
async def netbox_search_objects(
    query: str,
    object_types: list[ObjectTypeParam] | None = None,
    fields: list[str] | None = None,
    limit: Annotated[int, Field(default=5, ge=1, le=100)] = 5,
    ctx: Context | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Perform global search across NetBox infrastructure."""
    search_types = object_types if object_types is not None else DEFAULT_SEARCH_TYPES

    for obj_type in search_types:
        _validate_object_type(obj_type)

    client = _ensure_client()

    results: dict[str, list[dict[str, Any]]] = {obj_type: [] for obj_type in search_types}
    total = len(search_types)

    for i, obj_type in enumerate(search_types):
        try:
            params: dict[str, Any] = {"q": query, "limit": limit}
            if fields:
                params["fields"] = ",".join(fields)
            response = _netbox_api_call(
                client.get,
                _endpoint_for_type(obj_type),
                params=params,
            )
            results[obj_type] = response.get("results", [])
        except Exception as e:
            logger.warning("Search failed for %s: %s", obj_type, e)
            continue

        if ctx is not None:
            await ctx.report_progress(
                progress=i + 1,
                total=total,
                message=f"Searched {obj_type} ({i + 1}/{total})",
            )

    return results


# ---------------------------------------------------------------------------
# CLI subcommand mapping for the eval tool-selection scorer
# ---------------------------------------------------------------------------
# ``cli_tool_use_scorer`` (mcp-common >= v0.28.0) credits an expected MCP tool
# in a CLI eval when the agent runs any of that tool's acceptable ``netbox-cli``
# subcommands, read from ``tool_cli_subcommands(mcp)`` (built from
# ``@dual_mode_tool`` metadata + declared ``cli_aliases``). Two read-query tools
# are NOT dual-mode and are therefore absent from that mapping:
#
#   * ``netbox_get_objects`` — its ``filters: dict`` and
#     ``ordering: str | list[str] | None`` (a non-Optional union) cannot be
#     rendered by Typer; ``@dual_mode_tool`` rejects the union at decoration
#     time even with ``mcp_only=True`` (mcp-common #110/#111). Its CLI surface
#     is the hand-written ``list``/``devices``/``sites``/``clusters``/``ips``
#     commands, plus ``search`` (which auto-expands a matched cluster).
#   * ``netbox_search_objects`` — kept on plain ``@mcp.tool`` so the
#     hand-written ``search`` (cluster auto-expansion) stays the CLI surface.
#
# Declare their canonical tool -> subcommand mapping here, co-located with the
# tools (the non-dual-mode analog of ``cli_aliases``). Without it the scorer
# falls back to the kebab derivation (``netbox_get_objects`` -> ``get-objects``)
# and an agent that correctly answers via ``netbox-cli list`` scores
# tool-selection 0 (netbox-mcp#125).
CLI_SUBCOMMAND_ALIASES: dict[str, list[str]] = {
    "netbox_get_objects": ["list", "search", "devices"],
    "netbox_search_objects": ["search"],
}


def cli_subcommand_map() -> dict[str, list[str]]:
    """Return ``{mcp_tool_name: [cli_subcommand, ...]}`` for ``cli_tool_use_scorer``.

    Merges the dual-mode registry mapping (``tool_cli_subcommands(mcp)`` — every
    ``@dual_mode_tool`` plus its declared ``cli_aliases``) with
    :data:`CLI_SUBCOMMAND_ALIASES` (the read-query tools whose CLI form is
    hand-written and so can't carry ``cli_aliases``). Pass the result as
    ``cli_tool_use_scorer(tool_subcommands=...)`` so every expected read tool is
    credited for the real ``netbox-cli`` subcommand the agent runs.
    """
    return {**tool_cli_subcommands(mcp), **CLI_SUBCOMMAND_ALIASES}


# ---------------------------------------------------------------------------
# MCP Resources (static)
# ---------------------------------------------------------------------------


@mcp.resource(
    "netbox://object-types",
    tags={"discovery"},
    annotations={"audience": ["assistant"], "priority": 0.8},
)
def list_object_types() -> str:
    """List all supported NetBox object types and their API endpoints."""
    types_info = {
        obj_type: {"name": meta["name"], "endpoint": meta["endpoint"]}
        for obj_type, meta in sorted(NETBOX_OBJECT_TYPES.items())
    }
    return json.dumps(types_info, indent=2)


@mcp.resource(
    "netbox://server-info",
    tags={"meta"},
    annotations={"audience": ["user", "assistant"], "priority": 0.3},
)
def server_info() -> str:
    """Non-secret server configuration summary and status."""
    from netbox_mcp import __version__

    info = {
        "version": __version__,
        "tools": [
            "netbox_lookup_device",
            "netbox_oob_summary",
            "netbox_update_device",
            "netbox_get_objects",
            "netbox_get_object_by_id",
            "netbox_get_objects_by_ids",
            "netbox_get_changelogs",
            "netbox_search_objects",
        ],
        "supported_object_types_count": len(NETBOX_OBJECT_TYPES),
        "default_search_types": DEFAULT_SEARCH_TYPES,
    }
    return json.dumps(info, indent=2)


@mcp.resource(
    "netbox://health",
    tags={"meta"},
    annotations={"audience": ["user"], "priority": 0.2},
)
def health() -> str:
    """Health check with uptime and connectivity status."""
    from netbox_mcp import __version__

    checks: dict[str, Any] = {}
    if netbox is not None:
        try:
            netbox.get("status")
            checks["netbox_api"] = True
        except Exception:
            checks["netbox_api"] = False

    status = health_resource("netbox-mcp", __version__, checks)
    return json.dumps(status.to_dict(), indent=2)


# ---------------------------------------------------------------------------
# MCP Resource Templates
# ---------------------------------------------------------------------------


@mcp.resource(
    "netbox://device/{hostname}",
    tags={"dcim"},
    annotations={"audience": ["user", "assistant"], "priority": 0.5},
)
def get_device_resource(hostname: str) -> str:
    """Look up a device by hostname. Returns device details with enriched IP fields."""
    client = _ensure_client()
    params: dict[str, Any] = {"name__ic": hostname, "limit": 5}
    response = _netbox_api_call(client.get, "dcim/devices", params=params)
    devices = response.get("results", [])

    if not devices:
        fb_params: dict[str, Any] = {"cf_Provider_Machine_ID": hostname, "limit": 5}
        fb_resp = _netbox_api_call(client.get, "dcim/devices", params=fb_params)
        if fb_resp.get("count", 0) <= 50:
            devices = fb_resp.get("results", [])
    for device in devices:
        device["primary_ip4_address"] = _extract_ip_address(device.get("primary_ip4"))
        device["primary_ip6_address"] = _extract_ip_address(device.get("primary_ip6"))
        device["oob_ip_address"] = _extract_ip_address(device.get("oob_ip"))
        provider_id = device.get("custom_fields", {}).get("Provider_Machine_ID")
        if provider_id:
            device["provider_machine_id"] = provider_id
    return json.dumps({"count": len(devices), "results": devices, "query": hostname}, indent=2)


@mcp.resource(
    "netbox://site/{slug}",
    tags={"dcim"},
    annotations={"audience": ["user", "assistant"], "priority": 0.5},
)
def get_site_resource(slug: str) -> str:
    """Look up a site by slug. Returns site details."""
    client = _ensure_client()
    response = _netbox_api_call(client.get, "dcim/sites", params={"slug": slug, "limit": 1})
    results = response.get("results", [])
    if not results:
        return json.dumps({"error": f"No site found with slug '{slug}'"})
    return json.dumps(results[0], indent=2)


@mcp.resource(
    "netbox://ip/{address}",
    tags={"ipam"},
    annotations={"audience": ["user", "assistant"], "priority": 0.5},
)
def get_ip_resource(address: str) -> str:
    """Look up an IP address record. Accepts address with or without prefix length."""
    client = _ensure_client()
    response = _netbox_api_call(
        client.get, "ipam/ip-addresses", params={"address": address, "limit": 5}
    )
    results = response.get("results", [])
    return json.dumps({"count": len(results), "results": results, "query": address}, indent=2)


@mcp.resource(
    "netbox://rack/{site_slug}/{rack_name}",
    tags={"dcim"},
    annotations={"audience": ["user", "assistant"], "priority": 0.5},
)
def get_rack_resource(site_slug: str, rack_name: str) -> str:
    """Look up a rack by site slug and rack name."""
    client = _ensure_client()
    response = _netbox_api_call(
        client.get,
        "dcim/racks",
        params={"site": site_slug, "name__ic": rack_name, "limit": 5},
    )
    results = response.get("results", [])
    return json.dumps(
        {"count": len(results), "results": results, "query": f"{site_slug}/{rack_name}"},
        indent=2,
    )


# ---------------------------------------------------------------------------
# Completion Handler
# ---------------------------------------------------------------------------

_COMPLETION_QUERIES: dict[str, dict[str, str]] = {
    "hostname": {"endpoint": "dcim/devices", "filter": "name__isw", "field": "name"},
    "device": {"endpoint": "dcim/devices", "filter": "name__isw", "field": "name"},
    "device_a": {"endpoint": "dcim/devices", "filter": "name__isw", "field": "name"},
    "device_b": {"endpoint": "dcim/devices", "filter": "name__isw", "field": "name"},
    "site_name": {"endpoint": "dcim/sites", "filter": "name__isw", "field": "name"},
    "slug": {"endpoint": "dcim/sites", "filter": "slug__isw", "field": "slug"},
    "site_slug": {"endpoint": "dcim/sites", "filter": "slug__isw", "field": "slug"},
    "rack_name": {"endpoint": "dcim/racks", "filter": "name__isw", "field": "name"},
    "prefix": {"endpoint": "ipam/prefixes", "filter": "prefix__isw", "field": "prefix"},
    "address": {"endpoint": "ipam/ip-addresses", "filter": "address__isw", "field": "address"},
}


@mcp._mcp_server.completion()  # type: ignore[no-untyped-call,untyped-decorator]
async def handle_completion(
    ref: PromptReference | ResourceTemplateReference,
    argument: CompletionArgument,
    context: CompletionContext | None,
) -> Completion | None:
    """Provide autocompletion for prompt arguments and resource template parameters."""
    query_config = _COMPLETION_QUERIES.get(argument.name)
    if not query_config or not argument.value:
        return None

    if netbox is None:
        return None

    try:
        response = _netbox_api_call(
            netbox.get,
            query_config["endpoint"],
            params={query_config["filter"]: argument.value, "limit": 10, "brief": "1"},
        )
        results = response.get("results", [])
        preferred_field = query_config["field"]
        values = []
        for item in results:
            val = item.get(preferred_field)
            if val:
                values.append(str(val))
        return Completion(values=values, total=len(values), hasMore=False)
    except Exception:
        logger.debug("Completion query failed for %s=%s", argument.name, argument.value)
        return None


# ---------------------------------------------------------------------------
# MCP Prompts
# ---------------------------------------------------------------------------


@mcp.prompt(tags={"workflow", "dcim"})
def investigate_device(hostname: str) -> str:
    """Investigate a device's configuration, interfaces, IPs, and site context."""
    return (
        f"Investigate the NetBox device '{hostname}'. Follow these steps:\n"
        f"1. Search for the device: netbox_search_objects('{hostname}', "
        f"object_types=['dcim.device'], "
        f"fields=['id','name','status','site','device_type','primary_ip4','primary_ip6'])\n"
        f"2. Get full device details using netbox_get_object_by_id with the device ID\n"
        f"3. Find its interfaces: netbox_get_objects('dcim.interface', "
        f"{{'device_id': <id>}}, fields=['id','name','type','enabled','mac_address'])\n"
        f"4. Find its IP addresses: netbox_get_objects('ipam.ipaddress', "
        f"{{'device_id': <id>}}, fields=['id','address','status','dns_name'])\n"
        f"5. Summarize: device status, site, role, interfaces, IPs, and any notable config"
    )


@mcp.prompt(tags={"workflow", "dcim"})
def audit_site(site_name: str) -> str:
    """Audit all infrastructure at a NetBox site."""
    return (
        f"Audit the NetBox site '{site_name}'. Follow these steps:\n"
        f"1. Find the site: netbox_get_objects('dcim.site', "
        f"{{'name__ic': '{site_name}'}}, fields=['id','name','status','region','description'])\n"
        f"2. List devices: netbox_get_objects('dcim.device', "
        f"{{'site_id': <site_id>}}, fields=['id','name','status','device_type','rack'], "
        f"limit=50)\n"
        f"3. List racks: netbox_get_objects('dcim.rack', "
        f"{{'site_id': <site_id>}}, fields=['id','name','status','u_height'])\n"
        f"4. List prefixes: netbox_get_objects('ipam.prefix', "
        f"{{'site_id': <site_id>}}, fields=['id','prefix','status','vlan','description'])\n"
        f"5. List VLANs: netbox_get_objects('ipam.vlan', "
        f"{{'site_id': <site_id>}}, fields=['id','vid','name','status'])\n"
        f"6. Summarize: total devices by status, rack utilization, IP allocation, and any issues"
    )


@mcp.prompt(tags={"workflow", "dcim"})
def troubleshoot_connectivity(device_a: str, device_b: str) -> str:
    """Trace the connectivity path between two devices."""
    return (
        f"Troubleshoot connectivity between '{device_a}' and '{device_b}'.\n\n"
        f"1. Look up both devices:\n"
        f"   netbox_lookup_device('{device_a}')\n"
        f"   netbox_lookup_device('{device_b}')\n"
        f"2. Get interfaces for each device:\n"
        f"   netbox_get_objects('dcim.interface', {{'device_id': <id_a>}}, "
        f"fields=['id','name','type','enabled','connected_endpoints','cable'])\n"
        f"   netbox_get_objects('dcim.interface', {{'device_id': <id_b>}}, "
        f"fields=['id','name','type','enabled','connected_endpoints','cable'])\n"
        f"3. Check for direct cable connections between the two devices\n"
        f"4. If no direct connection, trace the path through intermediate devices\n"
        f"5. Check interface status (enabled/disabled) and cable status along the path\n"
        f"6. Summarize: connectivity path, link status, and any issues found"
    )


@mcp.prompt(tags={"workflow", "dcim"})
def inventory_report(site_name: str) -> str:
    """Generate an inventory report for a site."""
    return (
        f"Generate an inventory report for site '{site_name}'.\n\n"
        f"1. Find the site: netbox_get_objects('dcim.site', "
        f"{{'name__ic': '{site_name}'}}, fields=['id','name','status'])\n"
        f"2. Get all devices grouped by role:\n"
        f"   netbox_get_objects('dcim.device', {{'site_id': <site_id>}}, "
        f"fields=['id','name','status','device_type','device_role','serial','rack'], limit=100)\n"
        f"3. Get rack information:\n"
        f"   netbox_get_objects('dcim.rack', {{'site_id': <site_id>}}, "
        f"fields=['id','name','status','u_height','tenant'])\n"
        f"4. Produce a summary table with:\n"
        f"   - Device count by role and status\n"
        f"   - Device count by device type (model)\n"
        f"   - Rack utilization overview\n"
        f"   - List of any devices with 'failed' or 'offline' status"
    )


@mcp.prompt(tags={"workflow", "ipam"})
def find_available_ips(prefix: str) -> str:
    """Find available IP addresses within a prefix."""
    return (
        f"Find available IP addresses in prefix '{prefix}'.\n\n"
        f"1. Look up the prefix: netbox_get_objects('ipam.prefix', "
        f"{{'prefix': '{prefix}'}}, fields=['id','prefix','status','vlan','site','description'])\n"
        f"2. List existing IPs in the prefix:\n"
        f"   netbox_get_objects('ipam.ipaddress', {{'parent': '{prefix}'}}, "
        f"fields=['id','address','status','dns_name','assigned_object'], limit=100)\n"
        f"3. Check child prefixes: netbox_get_objects('ipam.prefix', "
        f"{{'within': '{prefix}'}}, fields=['id','prefix','status'])\n"
        f"4. Summarize:\n"
        f"   - Total IPs allocated vs available (based on prefix size)\n"
        f"   - List of allocated IPs with their assignments\n"
        f"   - Identify any gaps or available ranges"
    )


# ---------------------------------------------------------------------------
# Server initialization (shared by CLI and ASGI factory)
# ---------------------------------------------------------------------------

_initialized = False


def _initialize(settings: Settings) -> None:
    """Initialize the NetBox client and middleware from settings. Idempotent."""
    global netbox, vpn_monitor, _initialized
    if _initialized:
        return

    setup_logging(
        name="netbox-mcp",
        level=settings.log_level,
        json_output=settings.log_json,
        system_log=True,
    )
    suppress_noisy_loggers(settings.log_level)

    logger.info("Starting NetBox MCP Server")
    logger.info("Effective configuration: %s", settings.get_effective_config_summary())

    if not settings.verify_ssl:
        logger.warning(
            "SSL certificate verification is DISABLED. "
            "This is insecure and should only be used for testing."
        )

    if settings.transport == "http" and settings.host in ["0.0.0.0", "::", "[::]"]:
        logger.warning(
            "HTTP transport is bound to %s:%s, which exposes the service to all "
            "network interfaces. Ensure this is secured with TLS/reverse proxy.",
            settings.host,
            settings.port,
        )

    chain = CredentialChain(
        [
            CachedResolver(
                inner=EnvResolver("NETBOX_TOKEN"),
                key_name="mcp:netbox-token",
                ttl_seconds=1800,
            )
        ],
        name="netbox",
    )
    netbox = NetBoxRestClient(
        url=str(settings.netbox_url),
        token=chain,
        verify_ssl=settings.verify_ssl,
    )
    logger.debug("NetBox client initialized successfully")

    vpn_monitor = VPNMonitor(netbox)
    vpn_monitor.start()
    logger.debug(
        "VPN monitor started (interval=%ds, connected=%s)",
        VPN_CHECK_INTERVAL,
        vpn_monitor.is_connected,
    )

    _initialized = True


# ---------------------------------------------------------------------------
# ASGI app factory (for uvicorn / K8s deployment)
# ---------------------------------------------------------------------------


def create_app() -> Any:
    """Create an ASGI application for production HTTP deployment.

    Usage:
        uvicorn netbox_mcp.server:create_app --factory --host 0.0.0.0 --port 8000

    Configuration is read from environment variables / .env files
    (no CLI args in ASGI mode).
    """
    settings = Settings()  # type: ignore[call-arg]  # pydantic-settings loads from env
    _initialize(settings)

    token = (
        settings.mcp_http_access_token.get_secret_value()
        if settings.mcp_http_access_token
        else None
    )
    return create_http_app(
        mcp, path="/mcp", auth_token=token, stateless_http=settings.stateless_http
    )


# ---------------------------------------------------------------------------
# CLI entry point (stdio or direct HTTP)
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point: ``netbox-mcp`` command."""
    from mcp_common.env import load_env

    load_env()

    import sys

    suppress_ssl_warnings()
    cli_overlay = _parse_cli_args()

    try:
        settings = Settings(**cli_overlay)
    except Exception as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        _initialize(settings)
    except Exception as e:
        logger.error("Failed to initialize: %s", e)
        sys.exit(1)

    try:
        if settings.transport == "stdio":
            logger.info("Starting stdio transport")
            mcp.run(transport="stdio")
        elif settings.transport == "http":
            if settings.mcp_http_access_token:
                mcp.add_middleware(
                    HttpAccessTokenAuth(settings.mcp_http_access_token.get_secret_value())
                )
            logger.info("Starting HTTP transport on %s:%s", settings.host, settings.port)
            mcp.run(transport="http", host=settings.host, port=settings.port)
    except Exception as e:
        logger.error("Failed to start MCP server: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
