"""MCP Server for Ansible AWX / Automation Controller."""

import argparse
import atexit
import concurrent.futures
import functools
import json
import sys
from collections.abc import Callable
from typing import Annotated, Any, Literal, ParamSpec, TypeVar, cast

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.middleware import Middleware, MiddlewareContext
from mcp.types import ResourceUpdatedNotification, ResourceUpdatedNotificationParams
from mcp_common import (
    OperationStates,
    health_resource,
    poll_with_progress,
    setup_logging,
    suppress_ssl_warnings,
)
from mcp_common.agent_remediation import mcp_remediation_wrapper
from pydantic import AnyUrl, Field

from awx_mcp import __version__
from awx_mcp.awx_client import AwxRestClient
from awx_mcp.config import Settings

PageSizeParam = Annotated[int, Field(default=20, ge=1, le=200)]
PageNumParam = Annotated[int, Field(default=1, ge=1)]
LimitCharsParam = Annotated[int, Field(default=20000, ge=1000, le=200000)]
TimeoutSecondsParam = Annotated[int, Field(default=300, ge=1, le=3600)]
PollIntervalParam = Annotated[float, Field(default=3.0, ge=0.5, le=30.0)]
VerbosityParam = Annotated[int | None, Field(default=None, ge=0, le=5)]
FieldsParam = list[str] | None

ResourceType = Literal[
    "credentials",
    "credential_types",
    "job_templates",
    "workflow_job_templates",
    "jobs",
    "workflow_jobs",
    "inventories",
    "projects",
    "organizations",
    "teams",
    "users",
    "instance_groups",
    "execution_environments",
    "instances",
    "application_tokens",
    "schedules",
    "notifications",
    "job_events",
    "job_host_summaries",
    "activity_stream",
    "workflow_nodes",
    "inventory_sources",
    "project_updates",
    "tokens",
    "hosts",
]

RESOURCE_CAPABILITIES = {
    "credentials": {"list": True, "get": True, "create": True, "update": True, "delete": False},
    "credential_types": {
        "list": True,
        "get": True,
        "create": False,
        "update": False,
        "delete": False,
    },
    "job_templates": {"list": True, "get": True, "create": False, "update": False, "delete": False},
    "workflow_job_templates": {
        "list": True,
        "get": True,
        "create": False,
        "update": False,
        "delete": False,
    },
    "jobs": {"list": False, "get": True, "create": False, "update": False, "delete": True},
    "workflow_jobs": {
        "list": False,
        "get": True,
        "create": False,
        "update": False,
        "delete": False,
    },
    "inventories": {"list": True, "get": True, "create": False, "update": False, "delete": False},
    "projects": {"list": True, "get": True, "create": False, "update": True, "delete": False},
    "organizations": {"list": True, "get": True, "create": False, "update": False, "delete": False},
    "teams": {"list": True, "get": True, "create": False, "update": False, "delete": False},
    "users": {"list": True, "get": True, "create": False, "update": False, "delete": False},
    "instance_groups": {
        "list": True,
        "get": True,
        "create": False,
        "update": False,
        "delete": False,
    },
    "execution_environments": {
        "list": True,
        "get": True,
        "create": True,
        "update": True,
        "delete": False,
    },
    "instance_nodes": {
        "list": True,
        "get": True,
        "create": False,
        "update": False,
        "delete": False,
    },
    "application_tokens": {
        "list": True,
        "get": False,
        "create": False,
        "update": False,
        "delete": False,
    },
    "schedules": {"list": True, "get": True, "create": True, "update": True, "delete": True},
    "notifications": {"list": True, "get": True, "create": True, "update": False, "delete": False},
}


def _build_endpoint(
    resource_type: str,
    resource_id: int | None = None,
    property_path: str | None = None,
    parent_type: str | None = None,
    parent_id: int | None = None,
) -> str:
    """Build an AWX API endpoint path from components."""
    parts: list[str] = []
    if parent_type and parent_id:
        parts.append(f"{parent_type}/{parent_id}")
    parts.append(resource_type)
    if resource_id is not None:
        parts.append(str(resource_id))
    if property_path:
        parts.append(property_path)
    return "/".join(parts)


def parse_cli_args() -> dict[str, Any]:
    parser = argparse.ArgumentParser(
        description="AWX MCP Server - Model Context Protocol server for AWX/Controller",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Core AWX settings
    parser.add_argument(
        "--awx-host",
        type=str,
        help="Base URL of the AWX/Controller instance (e.g., https://awx.example.com/)",
    )
    parser.add_argument(
        "--awx-token",
        type=str,
        help="OAuth2 Personal Access Token for AWX/Controller",
    )
    parser.add_argument(
        "--api-base-path",
        type=str,
        help="API base path (default: /api/v2)",
    )

    # Transport settings
    parser.add_argument(
        "--transport",
        type=str,
        choices=["stdio", "http"],
        help="MCP transport protocol (default: stdio)",
    )
    parser.add_argument("--host", type=str, help="Host for HTTP server (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, help="Port for HTTP server (default: 8000)")

    # Security settings
    ssl_group = parser.add_mutually_exclusive_group()
    ssl_group.add_argument(
        "--verify-ssl",
        action="store_true",
        dest="verify_ssl",
        default=None,
        help="Verify SSL certificates (default)",
    )
    ssl_group.add_argument(
        "--no-verify-ssl",
        action="store_false",
        dest="verify_ssl",
        help="Disable SSL certificate verification (not recommended)",
    )

    # HTTP client settings
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        help="HTTP client timeout in seconds (default: 30)",
    )

    # Observability settings
    parser.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging verbosity level (default: INFO)",
    )

    args: argparse.Namespace = parser.parse_args()
    overlay: dict[str, Any] = {}
    if args.awx_host is not None:
        overlay["awx_host"] = args.awx_host
    if args.awx_token is not None:
        overlay["awx_token"] = args.awx_token
    if args.api_base_path is not None:
        overlay["api_base_path"] = args.api_base_path
    if args.transport is not None:
        overlay["transport"] = args.transport
    if args.host is not None:
        overlay["host"] = args.host
    if args.port is not None:
        overlay["port"] = args.port
    if args.verify_ssl is not None:
        overlay["verify_ssl"] = args.verify_ssl
    if args.timeout_seconds is not None:
        overlay["timeout_seconds"] = args.timeout_seconds
    if args.log_level is not None:
        overlay["log_level"] = args.log_level
    return overlay


def _ensure_json_serializable(obj: Any) -> Any:
    """
    Ensure an object is JSON-serializable by converting non-serializable types.

    This handles cases where AWX API returns objects that can't be directly serialized
    (e.g., datetime objects, custom types, etc.).
    """
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _ensure_json_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_ensure_json_serializable(item) for item in obj]
    # For any other type, convert to string representation
    # This handles datetime objects, custom types, etc.
    try:
        # Try to serialize it first to catch any issues
        json.dumps(obj, default=str)
        return obj
    except (TypeError, ValueError):
        return str(obj)


def _select_fields(obj: Any, fields: list[str] | None) -> Any:
    """
    Project dict objects down to a subset of keys to reduce token usage.

    This is a client-side projection (AWX API doesn't guarantee native field filtering).
    """
    if not fields:
        return obj

    if isinstance(obj, list):
        return [_select_fields(item, fields) for item in obj]
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for f in fields:
            if f in obj:
                out[f] = obj[f]
        return out
    return obj


def _build_list_params(
    filters: dict[str, Any] | None = None,
    page_size: int = 20,
    page: int = 1,
    order_by: str | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"page_size": page_size, "page": page}
    if filters:
        params.update(filters)
    if order_by:
        params["order_by"] = order_by
    return params


def _process_list_response(resp: Any, fields: list[str] | None) -> dict[str, Any]:
    if fields and isinstance(resp, dict) and isinstance(resp.get("results"), list):
        resp = {**resp, "results": _select_fields(resp["results"], fields)}
    return cast(dict[str, Any], _ensure_json_serializable(resp))


mcp = FastMCP("AWX")
awx: AwxRestClient | None = None

_P = ParamSpec("_P")
_R = TypeVar("_R")


def _get_awx() -> AwxRestClient:
    """Return the AWX client, raising if not yet initialized."""
    if awx is None:
        raise RuntimeError("AWX client is not initialized")
    return awx


def require_awx_client(func: Callable[_P, _R]) -> Callable[_P, _R]:
    """Decorator to ensure AWX client is initialized before calling tool functions."""

    @functools.wraps(func)
    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        if awx is None:
            raise RuntimeError("AWX client is not initialized")
        return func(*args, **kwargs)

    return wrapper


class HttpAccessTokenAuth(Middleware):
    """Require an access token for HTTP transport calls."""

    def __init__(self, token: str):
        self._token = token

    async def on_call_tool(self, context: MiddlewareContext, call_next):  # type: ignore[no-untyped-def]
        headers = {str(k).lower(): str(v) for k, v in (get_http_headers() or {}).items()}

        # Accept either:
        # - Authorization: Bearer <token>
        # - X-API-Key: <token>
        auth = headers.get("authorization", "")
        api_key = headers.get("x-api-key", "")

        ok = False
        if self._token and api_key and api_key == self._token:
            ok = True
        elif self._token and auth.lower().startswith("bearer "):
            candidate = auth.split(" ", 1)[1].strip()
            if candidate and candidate == self._token:
                ok = True

        if not ok:
            raise ToolError(
                "Unauthorized: missing/invalid access token. "
                "Send 'Authorization: Bearer <token>' or 'X-API-Key: <token>'."
            )

        return await call_next(context)


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------

AWX_JOB_TERMINAL_STATES = OperationStates(
    success=["successful"],
    failure=["failed", "error", "canceled"],
    in_progress=["pending", "waiting", "running", "new"],
)


@mcp.resource(
    "awx://resource-capabilities",
    name="AWX Resource Capabilities",
    description="Supported AWX resource types and their CRUD capabilities",
    mime_type="application/json",
)
def resource_capabilities_resource() -> str:
    """Supported AWX resource types and their CRUD capabilities."""
    return json.dumps(RESOURCE_CAPABILITIES, indent=2)


@mcp.resource(
    "health://awx",
    name="AWX Health",
    description="Server health and uptime",
    mime_type="application/json",
)
def health() -> str:
    """Server health and uptime."""
    return json.dumps(health_resource(name="awx-mcp", version=__version__).to_dict())


@mcp.resource(
    "awx://jobs/{job_id}",
    name="AWX Job",
    description="AWX job status and details by ID",
    mime_type="application/json",
)
def job_resource(job_id: int) -> str:
    """AWX job status and details by ID."""
    return json.dumps(_ensure_json_serializable(_get_awx().get(f"jobs/{job_id}")))


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


@mcp.prompt
def triage_failed_job(job_id: int) -> str:
    """Guide the LLM through triaging a failed AWX job step-by-step."""
    return (
        f"AWX job {job_id} has failed. Help me triage it.\n\n"
        "1. First, get the failed events:\n"
        f'   awx_list_resources("job_events", filters={{"failed": "true"}}, '
        f'parent_type="jobs", parent_id={job_id}, page_size=10)\n\n'
        "2. Then get the job stdout:\n"
        f'   awx_get_job_stdout(job_id={job_id}, format="txt")\n\n'
        "3. Summarize:\n"
        "   - Which tasks failed and on which hosts\n"
        "   - The root cause from error messages\n"
        "   - Suggested remediation steps"
    )


@mcp.prompt
def launch_deployment(template_name: str) -> str:
    """Guide the LLM through finding and launching an AWX job template."""
    return (
        f'I want to launch the "{template_name}" job template.\n\n'
        "1. Find the template:\n"
        f'   awx_list_resources("job_templates", '
        f'filters={{"name__icontains": "{template_name}"}}, '
        f'fields=["id", "name", "playbook", "ask_variables_on_launch"])\n\n'
        "2. Check if it has a survey (for required variables):\n"
        '   awx_get_resource("job_templates", <id>, property_path="survey_spec")\n\n'
        "3. Launch and wait:\n"
        '   awx_launch_and_wait("job_template", <id>, extra_vars={...})\n\n'
        "4. If it fails, run the triage_failed_job prompt."
    )


@mcp.prompt
def check_cluster_health() -> str:
    """Guide the LLM through a comprehensive AWX cluster health check."""
    return (
        "Perform a comprehensive AWX cluster health check.\n\n"
        "1. Test connectivity:\n"
        "   awx_ping()\n\n"
        "2. Get cluster status (instances and instance groups):\n"
        "   awx_get_cluster_status()\n\n"
        "3. Get system metrics (job counts, active/failed):\n"
        "   awx_get_system_metrics()\n\n"
        "4. Summarize:\n"
        "   - Overall cluster health (all instances online?)\n"
        "   - Active vs failed job counts\n"
        "   - Any capacity concerns\n"
        "   - Recommended actions if issues found"
    )


@mcp.prompt
def investigate_host(hostname: str) -> str:
    """Guide the LLM through investigating AWX job failures for a specific host."""
    return (
        f'Investigate AWX job failures for host "{hostname}".\n\n'
        "1. Look up the host in NetBox MCP to get the FQDN:\n"
        f'   netbox_search_objects("{hostname}")\n\n'
        "2. Find recent failed jobs targeting this host in AWX:\n"
        f'   awx_list_resources("jobs", filters={{"status": "failed"}}, '
        f'order_by="-created", page_size=10, fields=["id", "name", "created", "status"])\n\n'
        "3. For each failed job, check events for this host:\n"
        f'   awx_list_resources("job_events", filters={{"failed": "true", '
        f'"host_name__icontains": "{hostname}"}}, parent_type="jobs", parent_id=<id>)\n\n'
        "4. Summarize findings: which jobs failed, what tasks, root causes."
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool
@mcp_remediation_wrapper(project_repo="vhspace/awx-mcp")
@require_awx_client
def awx_list_supported_resources() -> dict[str, Any]:
    """
    List all supported AWX resource types and their capabilities.

    Returns a comprehensive list of resource types that can be used with the generic
    awx_list_resources, awx_get_resource, awx_create_resource, awx_update_resource,
    and awx_delete_resource tools.

    Each resource type includes:
    - list: Whether awx_list_resources supports this type
    - get: Whether awx_get_resource supports this type
    - create: Whether awx_create_resource supports this type
    - update: Whether awx_update_resource supports this type
    - delete: Whether awx_delete_resource supports this type
    """
    return {
        "resources": RESOURCE_CAPABILITIES,
        "note": "Use these resource types with the generic awx_*_resource tools for simplified API access",
    }


@mcp.tool
@mcp_remediation_wrapper(project_repo="vhspace/awx-mcp")
@require_awx_client
def awx_list_resources(
    resource_type: ResourceType,
    filters: dict[str, Any] | None = None,
    fields: FieldsParam = None,
    page_size: PageSizeParam = 20,
    page: PageNumParam = 1,
    order_by: str | None = None,
    parent_type: str | None = None,
    parent_id: int | None = None,
) -> dict[str, Any]:
    """
    List AWX resources of any type with filtering, pagination, and field selection.

    Covers 25+ resource types in one tool. Use parent_type/parent_id for nested
    resources (e.g. job events under a job, credentials under a template).

    Common filters: name__icontains, status, organization, failed.
    Hostnames from NetBox MCP can be used in host-related filters.

    Examples:
        awx_list_resources("job_templates", filters={"name__icontains": "deploy"})
        awx_list_resources("job_events", filters={"failed": "true"}, parent_type="jobs", parent_id=4353)
        awx_list_resources("hosts", parent_type="inventories", parent_id=64)
    """
    client = _get_awx()
    endpoint = _build_endpoint(resource_type, parent_type=parent_type, parent_id=parent_id)
    params = _build_list_params(filters, page_size, page, order_by)
    resp = client.get(endpoint, params=params)
    return _process_list_response(resp, fields)


@mcp.tool
@mcp_remediation_wrapper(project_repo="vhspace/awx-mcp")
@require_awx_client
def awx_get_resource(
    resource_type: ResourceType,
    resource_id: int,
    fields: FieldsParam = None,
    property_path: str | None = None,
    parent_type: str | None = None,
    parent_id: int | None = None,
) -> Any:
    """
    Get a single AWX resource by ID, with optional property sub-path.

    Use property_path for special endpoints: "survey_spec", "variable_data",
    "webhook_key", "playbooks", "stdout".

    Examples:
        awx_get_resource("credentials", 123)
        awx_get_resource("job_templates", 456, fields=["id", "name", "playbook"])
        awx_get_resource("job_templates", 123, property_path="survey_spec")
        awx_get_resource("inventories", 64, property_path="variable_data")
    """
    endpoint = _build_endpoint(
        resource_type,
        resource_id,
        property_path,
        parent_type=parent_type,
        parent_id=parent_id,
    )
    resp = _get_awx().get(endpoint)
    return _select_fields(resp, fields)


@mcp.tool
@mcp_remediation_wrapper(project_repo="vhspace/awx-mcp")
@require_awx_client
def awx_create_resource(
    resource_type: ResourceType,
    data: dict[str, Any],
    parent_type: str | None = None,
    parent_id: int | None = None,
) -> dict[str, Any]:
    """
    Create a new AWX resource. Use parent_type/parent_id for nested resources.

    Examples:
        awx_create_resource("credentials", {"name": "my-ssh-key", "credential_type": 1, ...})
        awx_create_resource("schedules", {"name": "Daily", "rrule": "FREQ=DAILY"}, parent_type="job_templates", parent_id=123)
    """
    endpoint = _build_endpoint(resource_type, parent_type=parent_type, parent_id=parent_id)
    return cast(dict[str, Any], _get_awx().post(endpoint, json=data))


@mcp.tool
@mcp_remediation_wrapper(project_repo="vhspace/awx-mcp")
@require_awx_client
def awx_update_resource(
    resource_type: ResourceType,
    resource_id: int,
    data: dict[str, Any],
    parent_type: str | None = None,
    parent_id: int | None = None,
) -> dict[str, Any]:
    """
    Update an existing AWX resource. Uses PATCH for credentials/schedules, PUT otherwise.

    Examples:
        awx_update_resource("credentials", 123, {"name": "new-name"})
        awx_update_resource("schedules", 789, {"enabled": False}, parent_type="job_templates", parent_id=123)
    """
    client = _get_awx()
    endpoint = _build_endpoint(
        resource_type,
        resource_id,
        parent_type=parent_type,
        parent_id=parent_id,
    )
    if resource_type in {"credentials", "schedules"}:
        return cast(dict[str, Any], client.patch(endpoint, json=data))
    return cast(dict[str, Any], client.put(endpoint, json=data))


@mcp.tool
@mcp_remediation_wrapper(project_repo="vhspace/awx-mcp")
@require_awx_client
def awx_delete_resource(
    resource_type: ResourceType,
    resource_id: int,
    parent_type: str | None = None,
    parent_id: int | None = None,
) -> dict[str, Any]:
    """
    Delete an AWX resource by type and ID.

    Examples:
        awx_delete_resource("jobs", 123)
        awx_delete_resource("schedules", 789, parent_type="job_templates", parent_id=123)
    """
    endpoint = _build_endpoint(
        resource_type,
        resource_id,
        parent_type=parent_type,
        parent_id=parent_id,
    )
    return cast(dict[str, Any], _get_awx().delete(endpoint))


@mcp.tool
@mcp_remediation_wrapper(project_repo="vhspace/awx-mcp")
@require_awx_client
def awx_ping() -> dict[str, Any]:
    """
    Check basic connectivity to AWX/Controller.

    Returns:
        Dict from GET /api/v2/ping/ (version, active_node, etc).
    """
    return cast(dict[str, Any], _get_awx().get("ping"))


@mcp.tool
@mcp_remediation_wrapper(project_repo="vhspace/awx-mcp")
@require_awx_client
def awx_get_me(fields: list[str] | None = None) -> Any:
    """
    Get the current user for the configured AWX token.

    Notes:
    - Some AWX versions return a list wrapper with `results[0]` for /me/.
    """
    resp = _get_awx().get("me")
    # AWX can return either a user dict or a list-wrapper
    if isinstance(resp, dict) and "results" in resp and isinstance(resp.get("results"), list):
        user = resp["results"][0] if resp["results"] else {}
        return _select_fields(user, fields)
    return _select_fields(resp, fields)


@mcp.tool
@mcp_remediation_wrapper(project_repo="vhspace/awx-mcp")
@require_awx_client
def awx_debug_job_template_credentials(job_template_id: int) -> dict[str, Any]:
    """
    Convenience helper for debugging a Job Template's attached credentials.

    Returns:
      - Job template id/name/playbook
      - Attached credentials with credential_type name/kind resolved
    """
    client = _get_awx()
    jt = client.get(f"job_templates/{job_template_id}")
    creds = client.get(f"job_templates/{job_template_id}/credentials", params={"page_size": 200})

    out_creds: list[dict[str, Any]] = []
    for c in creds.get("results") or []:
        ct = c.get("credential_type")
        ct_name: str | None = None
        ct_kind: str | None = None
        if isinstance(ct, dict):
            ct_name = ct.get("name")
            ct_kind = ct.get("kind")
        elif isinstance(ct, int):
            ct_obj = client.get(f"credential_types/{ct}")
            if isinstance(ct_obj, dict):
                ct_name = ct_obj.get("name")
                ct_kind = ct_obj.get("kind")

        out_creds.append(
            {
                "id": c.get("id"),
                "name": c.get("name"),
                "kind": c.get("kind"),
                "credential_type": ct_name or ct,
                "credential_type_kind": ct_kind,
            }
        )

    result = {
        "job_template": {
            "id": jt.get("id"),
            "name": jt.get("name"),
            "playbook": jt.get("playbook"),
            "inventory": jt.get("inventory"),
            "project": jt.get("project"),
            "execution_environment": jt.get("execution_environment"),
        },
        "credentials": out_creds,
        "count": len(out_creds),
    }
    return cast(dict[str, Any], _ensure_json_serializable(result))


@mcp.tool
@mcp_remediation_wrapper(project_repo="vhspace/awx-mcp")
@require_awx_client
def awx_list_aws_like_credentials() -> dict[str, Any]:
    """
    Best-effort helper to find AWS-related credentials in AWX by credential type name/kind.

    Returns:
      - AWS-like credential types
      - Credentials that use those types (id/name)
    """
    client = _get_awx()
    cts = client.get("credential_types", params={"page_size": 200})
    aws_types: list[dict[str, Any]] = []
    aws_type_ids: list[int] = []
    for ct in cts.get("results") or []:
        name = str(ct.get("name", "")).lower()
        kind = str(ct.get("kind", "")).lower()
        if "aws" in name or "amazon" in name or "aws" in kind:
            aws_types.append({"id": ct.get("id"), "name": ct.get("name"), "kind": ct.get("kind")})
            if isinstance(ct.get("id"), int):
                aws_type_ids.append(ct["id"])

    creds_out: list[dict[str, Any]] = []

    if aws_type_ids:
        # Fetch credentials for all AWS types in parallel for better performance
        # This reduces multiple sequential API calls to parallel execution
        def fetch_credentials_for_type(ct_id: int) -> list[dict[str, Any]]:
            """Fetch credentials for a specific type."""
            try:
                creds = client.get(
                    "credentials", params={"page_size": 200, "credential_type": ct_id}
                )
                return [
                    {"id": c.get("id"), "name": c.get("name"), "credential_type_id": ct_id}
                    for c in (creds.get("results") or [])
                ]
            except Exception:
                return []

        # Execute calls in parallel using threads for better performance
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(len(aws_type_ids), 5)
        ) as executor:
            future_to_ct_id = {
                executor.submit(fetch_credentials_for_type, ct_id): ct_id for ct_id in aws_type_ids
            }

            # Collect results as they complete
            for future in concurrent.futures.as_completed(future_to_ct_id):
                creds_out.extend(future.result())

    return cast(
        dict[str, Any],
        _ensure_json_serializable(
            {"aws_credential_types": aws_types, "aws_like_credentials": creds_out}
        ),
    )


@mcp.tool
@mcp_remediation_wrapper(project_repo="vhspace/awx-mcp")
@require_awx_client
def awx_launch(
    template_type: Literal["job_template", "workflow_job_template"],
    template_id: int,
    extra_vars: dict[str, Any] | None = None,
    limit: str | None = None,
    inventory_id: int | None = None,
    tags: str | None = None,
    skip_tags: str | None = None,
    verbosity: VerbosityParam = None,
    scm_branch: str | None = None,
) -> dict[str, Any]:
    """
    Launch a job or workflow template (fire-and-forget). Use awx_launch_and_wait
    if you need to wait for completion.

    The 'limit' parameter accepts comma-separated hostnames that can come directly
    from NetBox MCP lookups (e.g. "b65c909e-41.cloud.together.ai,host2").

    Use 'scm_branch' to override the project's default SCM branch (the template
    must have "Allow Branch Override" enabled).

    Examples:
        awx_launch("job_template", 174, extra_vars={"my_var": "value"}, limit="host1")
        awx_launch("workflow_job_template", 456, extra_vars={"env": "prod"})
        awx_launch("job_template", 174, scm_branch="feature/new-playbook")
    """
    payload: dict[str, Any] = {}
    if extra_vars is not None:
        payload["extra_vars"] = extra_vars
    if limit is not None:
        payload["limit"] = limit
    if inventory_id is not None:
        payload["inventory"] = inventory_id
    if scm_branch is not None:
        payload["scm_branch"] = scm_branch

    if template_type == "job_template":
        if tags is not None:
            payload["job_tags"] = tags
        if skip_tags is not None:
            payload["skip_tags"] = skip_tags
        if verbosity is not None:
            payload["verbosity"] = verbosity

    return cast(
        dict[str, Any],
        _get_awx().post(f"{template_type}s/{template_id}/launch", json=payload),
    )


@mcp.tool
@mcp_remediation_wrapper(project_repo="vhspace/awx-mcp")
@require_awx_client
async def awx_launch_and_wait(
    template_type: Literal["job_template", "workflow_job_template"],
    template_id: int,
    ctx: Context,
    extra_vars: dict[str, Any] | None = None,
    limit: str | None = None,
    inventory_id: int | None = None,
    tags: str | None = None,
    skip_tags: str | None = None,
    verbosity: VerbosityParam = None,
    scm_branch: str | None = None,
    timeout_seconds: TimeoutSecondsParam = 300,
    poll_interval_seconds: PollIntervalParam = 5.0,
) -> dict[str, Any]:
    """
    Launch a job/workflow template AND wait for it to finish in a single call.

    This is the recommended tool for running AWX jobs. It combines awx_launch +
    awx_wait_for_job to avoid extra round-trips. Returns the final job status.

    The 'limit' parameter accepts comma-separated hostnames — these can come
    directly from NetBox MCP lookups.

    Use 'scm_branch' to override the project's default SCM branch (the template
    must have "Allow Branch Override" enabled).

    Examples:
        awx_launch_and_wait("job_template", 174, extra_vars={"env": "prod"})
        awx_launch_and_wait("job_template", 174, limit="host1,host2", timeout_seconds=600)
        awx_launch_and_wait("job_template", 174, scm_branch="feature/new-playbook")
    """
    payload: dict[str, Any] = {}
    if extra_vars is not None:
        payload["extra_vars"] = extra_vars
    if limit is not None:
        payload["limit"] = limit
    if inventory_id is not None:
        payload["inventory"] = inventory_id
    if scm_branch is not None:
        payload["scm_branch"] = scm_branch
    if template_type == "job_template":
        if tags is not None:
            payload["job_tags"] = tags
        if skip_tags is not None:
            payload["skip_tags"] = skip_tags
        if verbosity is not None:
            payload["verbosity"] = verbosity

    client = _get_awx()
    launch_resp = client.post(f"{template_type}s/{template_id}/launch", json=payload)
    job_id = launch_resp.get("id") if isinstance(launch_resp, dict) else None
    if not job_id:
        return {"error": "Launch did not return a job ID", "launch_response": launch_resp}

    job_type = "workflow_jobs" if template_type == "workflow_job_template" else "jobs"

    async def _notify_resource_updated() -> None:
        try:
            await ctx.send_notification(
                ResourceUpdatedNotification(
                    params=ResourceUpdatedNotificationParams(uri=AnyUrl(f"awx://jobs/{job_id}"))
                )
            )
        except Exception:
            pass

    def check_job() -> dict[str, Any]:
        job = client.get(f"{job_type}/{job_id}")
        return cast(dict[str, Any], job) if isinstance(job, dict) else {"status": "unknown"}

    async def check_and_notify() -> dict[str, Any]:
        result = check_job()
        await _notify_resource_updated()
        return result

    result = await poll_with_progress(
        ctx,
        check_and_notify,
        "status",
        AWX_JOB_TERMINAL_STATES,
        timeout_s=float(timeout_seconds),
        interval_s=float(poll_interval_seconds),
        format_message=lambda state, elapsed: (
            f"Job {job_id}: {state.get('status', 'unknown')} ({elapsed:.0f}s elapsed)"
        ),
    )

    if result.timed_out:
        return {
            "timeout": True,
            "job_id": job_id,
            "last_seen": result.extra,
            "message": f"Timed out after {timeout_seconds}s waiting for job {job_id}",
        }
    return result.extra


@mcp.tool
@mcp_remediation_wrapper(project_repo="vhspace/awx-mcp")
@require_awx_client
def awx_get_job_stdout(
    job_id: int,
    format: Literal["txt", "ansi", "json", "html"] = "txt",
    start_line: Annotated[int | None, Field(default=None, ge=1)] = None,
    end_line: Annotated[int | None, Field(default=None, ge=1)] = None,
    limit_chars: LimitCharsParam = 20000,
    truncation_strategy: Literal["head", "tail", "head_tail", "recap_context"] = "tail",
    filter: Literal["all", "errors", "changed"] | None = None,
    play: str | None = None,
    host: str | None = None,
    task_filter: str | None = None,
) -> dict[str, Any]:
    """
    Fetch job stdout/output logs (often large), with optional filtering.

    Endpoint: GET /api/v2/jobs/{id}/stdout/

    Args:
        job_id: The job ID to get stdout for
        format: Output format - "txt" (plain), "ansi" (colored), "json" (structured), "html"
        start_line: Starting line number (optional, for pagination)
        end_line: Ending line number (optional, for pagination)
        limit_chars: Maximum characters to return (default: 20000, max: 200000)
        truncation_strategy: How to truncate when content exceeds limit_chars:
            - "tail" (default): Last N chars — best for seeing failures and PLAY RECAP
            - "head": First N chars — original behavior
            - "head_tail": First 25% + last 75% — see beginning and end
            - "recap_context": PLAY RECAP section + surrounding context
        filter: Filter output by status — "errors" (failed/fatal only), "changed",
            or "all" (default, no filtering). Applied before truncation.
        play: Filter by play name (substring match) or 1-based play index.
        host: Filter by hostname pattern (supports wildcards via fnmatch).
        task_filter: Filter by task name pattern (supports wildcards via fnmatch).

    Returns:
        {
            "job_id": 4348,
            "format": "txt",
            "truncated": false,
            "truncation_strategy": "tail",
            "limit_chars": 20000,
            "original_length": 85000,
            "filtered": true,
            "content": "...PLAY RECAP *****\nhost1 : ok=5 changed=2..."
        }

    Common Usage:
        # Get tail of output to see results (default):
        awx_get_job_stdout(job_id=4348)

        # Show only errors:
        awx_get_job_stdout(job_id=4348, filter="errors")

        # Show only changed tasks for a specific host:
        awx_get_job_stdout(job_id=4348, filter="changed", host="gpu*")

        # Show a specific play's tasks matching a pattern:
        awx_get_job_stdout(job_id=4348, play="1", task_filter="Configure *")

        # See both beginning and end of a long log:
        awx_get_job_stdout(job_id=4348, truncation_strategy="head_tail", limit_chars=50000)

    Notes:
    - Filters are applied before truncation for accurate results
    - Use start_line/end_line to fetch specific sections (reduces payload)
    - Default "tail" strategy shows failures and PLAY RECAP which appear at the end
    - For structured failure data, use awx_parse_job_log() instead
    """
    from awx_mcp.log_parser import filter_stdout, smart_truncate

    client = _get_awx()
    params: dict[str, Any] = {"format": format}
    if start_line is not None:
        params["start_line"] = start_line
    if end_line is not None:
        params["end_line"] = end_line

    filter_mode = filter or "all"
    has_filters = (
        filter_mode != "all" or play is not None or host is not None or task_filter is not None
    )

    if format in {"txt", "ansi", "html"}:
        content = client.get_text(f"jobs/{job_id}/stdout", params=params, accept="text/plain")
        original_length = len(content)
        if has_filters:
            content = filter_stdout(
                content, filter_mode=filter_mode, play=play, host=host, task=task_filter
            )
        trunc = smart_truncate(content, limit_chars, strategy=truncation_strategy)
        return {
            "job_id": job_id,
            "format": format,
            "truncated": trunc["truncated"],
            "truncation_strategy": trunc["strategy"],
            "limit_chars": limit_chars,
            "original_length": original_length,
            "filtered": has_filters,
            "content": trunc["content"],
        }

    out = client.get(f"jobs/{job_id}/stdout", params=params)
    if isinstance(out, dict) and isinstance(out.get("content"), str):
        content = out["content"]
        if has_filters:
            content = filter_stdout(
                content, filter_mode=filter_mode, play=play, host=host, task=task_filter
            )
        trunc = smart_truncate(content, limit_chars, strategy=truncation_strategy)
        out = {
            **out,
            "content": trunc["content"],
            "truncated": trunc["truncated"],
            "truncation_strategy": trunc["strategy"],
            "limit_chars": limit_chars,
            "original_length": trunc["original_length"],
            "filtered": has_filters,
        }
    return cast(dict[str, Any], _ensure_json_serializable(out))


@mcp.tool
@mcp_remediation_wrapper(project_repo="vhspace/awx-mcp")
@require_awx_client
def awx_parse_job_log(
    job_id: int,
    sections: list[Literal["summary", "failures", "warnings", "recap", "all"]] | None = None,
) -> dict[str, Any]:
    """
    Parse Ansible job log into structured data — much faster than reading raw stdout.

    Fetches the full job stdout and parses it to extract play names, failures,
    warnings, PLAY RECAP, and per-host statistics. Ideal for triaging failed
    jobs without reading thousands of lines of raw output.

    Unlike awx_get_job_stdout, this tool fetches the complete log (needed to find
    PLAY RECAP at the end) but returns only small structured data — not the raw text.

    Args:
        job_id: The job ID to parse
        sections: Which sections to include (default: all). Options:
            - "summary": plays, task count, overall result, has_failures
            - "failures": detailed failed task list with host, task, module, message
            - "warnings": all [WARNING] messages
            - "recap": PLAY RECAP text and per-host ok/changed/failed/unreachable stats
            - "all": everything above

    Returns:
        {
            "job_id": 4348,
            "log_chars": 615862,
            "overall_result": "failed",
            "has_failures": true,
            "total_lines": 4790,
            "plays": ["Prep ORI GPU nodes"],
            "total_tasks": 242,
            "failed_tasks": [
                {"host": "gpu103", "task": "Configure mlxconfig", "module": "FAILED",
                 "message": "mlxconfig: command not found"}
            ],
            "warnings": ["Host 'research-common-h100-095' is using the discovered Python..."],
            "host_stats": [
                {"host": "gpu103", "ok": 188, "changed": 66, "unreachable": 0, "failed": 1, ...}
            ],
            "recap_text": "PLAY RECAP ****\\ngpu103 : ok=188 changed=66 ..."
        }

    Common Usage:
        # Quick triage — did the job fail and why?
        parsed = awx_parse_job_log(job_id=4348)
        if parsed["has_failures"]:
            for f in parsed["failed_tasks"]:
                print(f"{f['host']}: {f['task']} — {f['message']}")

        # Just get the recap:
        awx_parse_job_log(job_id=4348, sections=["recap"])

        # Failures only:
        awx_parse_job_log(job_id=4348, sections=["failures"])
    """
    from awx_mcp.log_parser import parse_ansible_log

    client = _get_awx()
    content = client.get_text(
        f"jobs/{job_id}/stdout", params={"format": "txt"}, accept="text/plain"
    )

    parsed = parse_ansible_log(content)
    full = parsed.to_dict()

    requested = set(sections or ["all"])
    if "all" in requested:
        result = full
    else:
        result: dict[str, Any] = {
            "total_lines": full["total_lines"],
            "has_failures": full["has_failures"],
            "overall_result": full["overall_result"],
        }
        if "summary" in requested:
            result["plays"] = full["plays"]
            result["total_tasks"] = full["total_tasks"]
        if "failures" in requested:
            result["failed_tasks"] = full["failed_tasks"]
        if "warnings" in requested:
            result["warnings"] = full["warnings"]
        if "recap" in requested:
            result["recap_text"] = full["recap_text"]
            result["host_stats"] = full["host_stats"]

    result["job_id"] = job_id
    result["log_chars"] = len(content)
    return result


@mcp.tool
@mcp_remediation_wrapper(project_repo="vhspace/awx-mcp")
@require_awx_client
def awx_cancel_job(job_id: int) -> dict[str, Any]:
    """
    Cancel a running job.

    Args:
        job_id: The job ID to cancel
    """
    return cast(dict[str, Any], _get_awx().post(f"jobs/{job_id}/cancel"))


@mcp.tool
@mcp_remediation_wrapper(project_repo="vhspace/awx-mcp")
@require_awx_client
def awx_relaunch_job(
    job_id: int,
    hosts: str | None = None,
    on_failed: bool = False,
    password_prompts: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Relaunch a job with optional host limits and credential updates.

    Args:
        job_id: The job ID to relaunch
        hosts: Optional host limit pattern (e.g., "host1,host2")
        on_failed: If True, relaunch only on hosts that failed
        password_prompts: Optional credential password updates
    """
    payload: dict[str, Any] = {}
    if on_failed:
        payload["hosts"] = "failed"
    elif hosts:
        payload["hosts"] = hosts
    if password_prompts:
        payload["credential_passwords"] = password_prompts

    return cast(dict[str, Any], _get_awx().post(f"jobs/{job_id}/relaunch", json=payload))


@mcp.tool
@mcp_remediation_wrapper(project_repo="vhspace/awx-mcp")
@require_awx_client
async def awx_get_system_info(ctx: Context) -> dict[str, Any]:
    """
    Get system information and health status.
    """
    client = _get_awx()
    info: dict[str, Any] = {}
    endpoints = ["ping", "config", "settings"]

    await ctx.info("Fetching system info from AWX...")

    def fetch_endpoint(endpoint: str) -> tuple[str, Any]:
        try:
            return endpoint, client.get(endpoint)
        except Exception:
            return endpoint, None

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(endpoints)) as executor:
        future_to_endpoint = {
            executor.submit(fetch_endpoint, endpoint): endpoint for endpoint in endpoints
        }
        for future in concurrent.futures.as_completed(future_to_endpoint):
            endpoint_name, data = future.result()
            if data is not None:
                info[endpoint_name] = data

    await ctx.info(f"Collected {len(info)}/{len(endpoints)} endpoints")
    return info


@mcp.tool
@mcp_remediation_wrapper(project_repo="vhspace/awx-mcp")
@require_awx_client
def awx_get_workflow_visualization(workflow_job_template_id: int) -> dict[str, Any]:
    """
    Get workflow visualization data (graph structure for UI rendering).

    Args:
        workflow_job_template_id: The workflow template ID to visualize
    """
    resp = _get_awx().get(f"workflow_job_templates/{workflow_job_template_id}/workflow_nodes")
    nodes = resp.get("results", []) if isinstance(resp, dict) else []

    graph: dict[str, Any] = {"nodes": [], "links": [], "node_map": {}}

    for node in nodes:
        node_id = node.get("id")
        node_data = {
            "id": node_id,
            "type": node.get("unified_job_type", "unknown"),
            "name": node.get("identifier", f"Node {node_id}"),
            "job_template_id": node.get("unified_job_template"),
            "success_nodes": node.get("success_nodes", []),
            "failure_nodes": node.get("failure_nodes", []),
            "always_nodes": node.get("always_nodes", []),
        }
        graph["nodes"].append(node_data)
        graph["node_map"][node_id] = node_data

        # Add edges
        for successor_list, edge_type in [
            (node.get("success_nodes", []), "success"),
            (node.get("failure_nodes", []), "failure"),
            (node.get("always_nodes", []), "always"),
        ]:
            for successor_id in successor_list:
                graph["links"].append(
                    {"source": node_id, "target": successor_id, "type": edge_type}
                )

    return graph


@mcp.tool
@mcp_remediation_wrapper(project_repo="vhspace/awx-mcp")
@require_awx_client
def awx_bulk_cancel_jobs(job_ids: list[int]) -> dict[str, Any]:
    """
    Cancel multiple running jobs at once.

    Args:
        job_ids: List of job IDs to cancel
    """
    client = _get_awx()
    results = []
    for job_id in job_ids:
        try:
            result = client.post(f"jobs/{job_id}/cancel")
            results.append({"job_id": job_id, "status": "canceled", "result": result})
        except Exception as e:
            results.append({"job_id": job_id, "status": "error", "error": str(e)})

    return {
        "results": results,
        "total_requested": len(job_ids),
        "successful": len([r for r in results if r["status"] == "canceled"]),
    }


@mcp.tool
@mcp_remediation_wrapper(project_repo="vhspace/awx-mcp")
@require_awx_client
def awx_sync_inventory_source(source_id: int) -> dict[str, Any]:
    """
    Manually sync a dynamic inventory source.

    Args:
        source_id: The inventory source ID to sync
    """
    return cast(dict[str, Any], _get_awx().post(f"inventory_sources/{source_id}/update"))


@mcp.tool
@mcp_remediation_wrapper(project_repo="vhspace/awx-mcp")
@require_awx_client
def awx_update_project(project_id: int) -> dict[str, Any]:
    """
    Manually sync/update a project from its SCM source.

    Args:
        project_id: The project ID to update
    """
    return cast(dict[str, Any], _get_awx().post(f"projects/{project_id}/update"))


@mcp.tool
@mcp_remediation_wrapper(project_repo="vhspace/awx-mcp")
@require_awx_client
def awx_cancel_project_update(update_id: int) -> dict[str, Any]:
    """
    Cancel a running project update/sync operation.

    Args:
        update_id: The project update ID to cancel
    """
    return cast(dict[str, Any], _get_awx().post(f"project_updates/{update_id}/cancel"))


@mcp.tool
@mcp_remediation_wrapper(project_repo="vhspace/awx-mcp")
@require_awx_client
def awx_create_notification(
    template_type: Literal["job_template", "workflow_job_template"],
    template_id: int,
    name: str,
    notification_type: Literal[
        "email", "slack", "webhook", "irc", "mattermost", "pagerduty", "twilio"
    ],
    notification_configuration: dict[str, Any],
) -> dict[str, Any]:
    """
    Create a notification for job/workflow template events.

    Args:
        template_type: Type of template
        template_id: Template ID
        name: Notification name
        notification_type: Type of notification (email, slack, webhook, etc.)
        notification_configuration: Configuration specific to notification type
    """
    payload = {
        "name": name,
        "type": notification_type,
        "notification_configuration": notification_configuration,
    }

    return cast(dict[str, Any], _get_awx().post("notifications", json=payload))


@mcp.tool
@mcp_remediation_wrapper(project_repo="vhspace/awx-mcp")
@require_awx_client
def awx_attach_notification_to_template(
    template_type: Literal["job_template", "workflow_job_template"],
    template_id: int,
    notification_id: int,
    event_type: Literal["started", "success", "error"],
) -> dict[str, Any]:
    """
    Attach an existing notification to a template for specific events.

    Args:
        template_type: Type of template
        template_id: Template ID
        notification_id: Notification ID to attach
        event_type: Event type (started, success, error)
    """
    return cast(
        dict[str, Any],
        _get_awx().post(
            f"{template_type}s/{template_id}/notification_templates_{event_type}",
            json={"id": notification_id},
        ),
    )


@mcp.tool
@mcp_remediation_wrapper(project_repo="vhspace/awx-mcp")
@require_awx_client
def awx_detach_notification_from_template(
    template_type: Literal["job_template", "workflow_job_template"],
    template_id: int,
    notification_id: int,
    event_type: Literal["started", "success", "error"],
) -> dict[str, Any]:
    """
    Remove a notification from a template for specific events.

    Args:
        template_type: Type of template
        template_id: Template ID
        notification_id: Notification ID to detach
        event_type: Event type (started, success, error)
    """
    return cast(
        dict[str, Any],
        _get_awx().delete(
            f"{template_type}s/{template_id}/notification_templates_{event_type}/{notification_id}"
        ),
    )


@mcp.tool
@mcp_remediation_wrapper(project_repo="vhspace/awx-mcp")
@require_awx_client
def awx_test_notification(notification_id: int) -> dict[str, Any]:
    """
    Send a test notification to verify configuration.

    Args:
        notification_id: Notification ID to test
    """
    return cast(dict[str, Any], _get_awx().post(f"notifications/{notification_id}/test"))


@mcp.tool
@mcp_remediation_wrapper(project_repo="vhspace/awx-mcp")
@require_awx_client
def awx_pull_execution_environment(execution_environment_id: int) -> dict[str, Any]:
    """
    Pull/update an execution environment image.

    Args:
        execution_environment_id: Execution environment ID to pull
    """
    return cast(
        dict[str, Any],
        _get_awx().post(f"execution_environments/{execution_environment_id}/pull"),
    )


@mcp.tool
@mcp_remediation_wrapper(project_repo="vhspace/awx-mcp")
@require_awx_client
async def awx_get_cluster_status(ctx: Context) -> dict[str, Any]:
    """
    Get overall AWX cluster health: instances, instance groups, and ping in parallel.
    """
    client = _get_awx()
    endpoints = ["instances", "instance_groups", "ping"]

    await ctx.info("Checking AWX cluster health...")

    def _fetch(ep: str) -> tuple[str, Any]:
        try:
            return ep, client.get(ep)
        except Exception:
            return ep, None

    status: dict[str, Any] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(endpoints)) as pool:
        for ep, data in pool.map(lambda e: _fetch(e), endpoints):
            if data is not None:
                status[ep] = data

    await ctx.info(f"Cluster check complete: {len(status)}/{len(endpoints)} endpoints responded")
    return status


@mcp.tool
@mcp_remediation_wrapper(project_repo="vhspace/awx-mcp")
@require_awx_client
def awx_test_credential(credential_id: int) -> dict[str, Any]:
    """
    Test credential connectivity and validity.

    Args:
        credential_id: Credential ID to test
    """
    return cast(dict[str, Any], _get_awx().post(f"credentials/{credential_id}/test"))


@mcp.tool
@mcp_remediation_wrapper(project_repo="vhspace/awx-mcp")
@require_awx_client
def awx_copy_credential(credential_id: int, name: str) -> dict[str, Any]:
    """
    Copy an existing credential with a new name.

    Args:
        credential_id: Source credential ID
        name: Name for the new credential copy
    """
    return cast(
        dict[str, Any],
        _get_awx().post(f"credentials/{credential_id}/copy", json={"name": name}),
    )


@mcp.tool
@mcp_remediation_wrapper(project_repo="vhspace/awx-mcp")
@require_awx_client
def awx_update_system_setting(category: str, name: str, value: Any) -> dict[str, Any]:
    """
    Update a system setting value.

    Args:
        category: Setting category (e.g., "system", "jobs", "logging")
        name: Setting name
        value: New value for the setting
    """
    return cast(
        dict[str, Any],
        _get_awx().patch(f"settings/{category}", json={name: value}),
    )


@mcp.tool
@mcp_remediation_wrapper(project_repo="vhspace/awx-mcp")
@require_awx_client
def awx_bulk_delete_jobs(job_ids: list[int]) -> dict[str, Any]:
    """
    Delete multiple jobs at once.

    Args:
        job_ids: List of job IDs to delete
    """
    client = _get_awx()
    results = []
    for job_id in job_ids:
        try:
            result = client.delete(f"jobs/{job_id}")
            results.append({"job_id": job_id, "status": "deleted", "result": result})
        except Exception as e:
            results.append({"job_id": job_id, "status": "error", "error": str(e)})

    return {
        "results": results,
        "total_requested": len(job_ids),
        "successful": len([r for r in results if r["status"] == "deleted"]),
    }


@mcp.tool
@mcp_remediation_wrapper(project_repo="vhspace/awx-mcp")
@require_awx_client
def awx_get_system_metrics() -> dict[str, Any]:
    """
    Get system performance metrics and statistics.
    """
    client = _get_awx()
    metrics: dict[str, Any] = {}

    try:
        jobs_resp = client.get("unified_jobs", params={"page_size": 1})
        if isinstance(jobs_resp, dict):
            metrics["total_jobs"] = jobs_resp.get("count", 0)
    except Exception:
        pass

    try:
        active_jobs = client.get("jobs", params={"status": "running", "page_size": 1})
        if isinstance(active_jobs, dict):
            metrics["active_jobs"] = active_jobs.get("count", 0)
    except Exception:
        pass

    try:
        failed_jobs = client.get("jobs", params={"status": "failed", "page_size": 1})
        if isinstance(failed_jobs, dict):
            metrics["failed_jobs"] = failed_jobs.get("count", 0)
    except Exception:
        pass

    return metrics


@mcp.tool
@mcp_remediation_wrapper(project_repo="vhspace/awx-mcp")
@require_awx_client
async def awx_wait_for_job(
    job_id: int,
    ctx: Context,
    timeout_seconds: TimeoutSecondsParam = 300,
    poll_interval_seconds: PollIntervalParam = 3.0,
) -> dict[str, Any]:
    """
    Poll a Job until it reaches a terminal state.

    Terminal states include: successful, failed, error, canceled.
    """
    client = _get_awx()

    async def check_and_notify() -> dict[str, Any]:
        job = client.get(f"jobs/{job_id}")
        result = cast(dict[str, Any], job) if isinstance(job, dict) else {"status": "unknown"}
        try:
            await ctx.send_notification(
                ResourceUpdatedNotification(
                    params=ResourceUpdatedNotificationParams(uri=AnyUrl(f"awx://jobs/{job_id}"))
                )
            )
        except Exception:
            pass
        return result

    result = await poll_with_progress(
        ctx,
        check_and_notify,
        "status",
        AWX_JOB_TERMINAL_STATES,
        timeout_s=float(timeout_seconds),
        interval_s=float(poll_interval_seconds),
        format_message=lambda state, elapsed: (
            f"Job {job_id}: {state.get('status', 'unknown')} ({elapsed:.0f}s elapsed)"
        ),
    )

    if result.timed_out:
        return {
            "timeout": True,
            "job_id": job_id,
            "last_seen": result.extra,
            "message": f"Timed out after {timeout_seconds}s waiting for job {job_id}",
        }
    return result.extra


def main() -> None:
    global awx
    suppress_ssl_warnings()

    cli_overlay = parse_cli_args()
    try:
        settings = Settings(**cli_overlay)
    except Exception as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)

    import logging as _logging

    logger = setup_logging(level=settings.log_level, name="awx-mcp", system_log=True)
    _logging.getLogger("httpx").setLevel(
        _logging.DEBUG if settings.log_level == "DEBUG" else _logging.WARNING
    )
    logger.info("Starting AWX MCP Server v%s", __version__)
    logger.info(f"Effective configuration: {settings.get_effective_config_summary()}")

    if not settings.verify_ssl:
        logger.warning(
            "SSL certificate verification is DISABLED. "
            "This is insecure and should only be used for testing."
        )

    if settings.transport == "http" and settings.host in ["0.0.0.0", "::", "[::]"]:
        logger.warning(
            f"HTTP transport is bound to {settings.host}:{settings.port}, which exposes the service to all network interfaces. "
            "This is insecure and should only be used for testing. Ensure this is secured with TLS/reverse proxy if exposed."
        )

    if settings.transport == "http":
        if (
            settings.mcp_http_access_token is None
            or not settings.mcp_http_access_token.get_secret_value().strip()
        ):
            logger.error(
                "HTTP transport requires MCP_HTTP_ACCESS_TOKEN (or AWX_MCP_HTTP_ACCESS_TOKEN). "
                "Refusing to start an unauthenticated HTTP MCP server."
            )
            sys.exit(1)
        mcp.add_middleware(
            HttpAccessTokenAuth(settings.mcp_http_access_token.get_secret_value().strip())
        )

    try:
        awx = AwxRestClient(
            host=str(settings.awx_host),
            token=settings.awx_token.get_secret_value(),
            api_base_path=settings.api_base_path,
            verify_ssl=settings.verify_ssl,
            timeout_seconds=settings.timeout_seconds,
        )
        atexit.register(awx.close)
        logger.debug("AWX client initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize AWX client: {e}")
        sys.exit(1)

    try:
        if settings.transport == "stdio":
            logger.info("Starting stdio transport")
            mcp.run(transport="stdio")
        elif settings.transport == "http":
            logger.info(f"Starting HTTP transport on {settings.host}:{settings.port}")
            mcp.run(transport="http", host=settings.host, port=settings.port)
        else:
            raise ValueError(f"Unsupported transport: {settings.transport}")
    except Exception as e:
        logger.error(f"Failed to start MCP server: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
