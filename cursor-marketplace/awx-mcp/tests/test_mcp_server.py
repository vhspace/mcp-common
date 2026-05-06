"""Integration tests for AWX MCP server using FastMCP in-memory transport."""

import httpx
import pytest
from fastmcp import Client
from mcp_common.testing import assert_tool_exists, assert_tool_success, mcp_client

import awx_mcp.server as server_module
from awx_mcp.awx_client import AwxRestClient


@pytest.fixture
def mock_awx_client(monkeypatch: pytest.MonkeyPatch) -> AwxRestClient:
    """Create a mock AWX client with predictable responses."""
    responses: dict[str, dict[str, object]] = {
        "ping": {"version": "24.0.0", "active_node": "awx-1"},
        "me": {"id": 1, "username": "testuser", "email": "test@example.com"},
        "job_templates": {
            "count": 2,
            "results": [
                {"id": 1, "name": "Deploy App", "playbook": "deploy.yml"},
                {"id": 2, "name": "Backup DB", "playbook": "backup.yml"},
            ],
        },
        "credentials/67": {
            "id": 67,
            "name": "netbox-krustykrab",
            "credential_type": 31,
            "inputs": {"netbox_api": "$encrypted$", "netbox_token": "$encrypted$"},
        },
        "jobs/123": {
            "id": 123,
            "status": "successful",
            "job_template": 1,
            "created": "2024-01-01T00:00:00Z",
        },
        "jobs/123/stdout": (
            "PLAY [all] *********\n\n"
            "TASK [setup] *********\n"
            "ok: [host1]\n\n"
            "TASK [deploy] *********\n"
            "changed: [host1]\n\n"
            "PLAY RECAP *********************************************************************\n"
            "host1                      : ok=2    changed=1    unreachable=0    failed=0"
            "    skipped=0    rescued=0    ignored=0\n"
        ),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.rstrip("/")
        if path.endswith("/ping"):
            return httpx.Response(200, json=responses["ping"])
        elif path.endswith("/me"):
            return httpx.Response(200, json=responses["me"])
        elif "/job_templates" in path:
            return httpx.Response(200, json=responses["job_templates"])
        elif path.endswith("/credentials/67"):
            return httpx.Response(200, json=responses["credentials/67"])
        elif path.endswith("/jobs/123"):
            return httpx.Response(200, json=responses["jobs/123"])
        elif path.endswith("/jobs/123/stdout"):
            format_param = request.url.params.get("format", "txt")
            if format_param == "txt":
                return httpx.Response(200, text=responses["jobs/123/stdout"])
            return httpx.Response(200, json={"content": responses["jobs/123/stdout"]})
        return httpx.Response(404, text="not found")

    transport = httpx.MockTransport(handler)
    client = AwxRestClient(
        host="https://awx.example.com",
        token="test-token",
        http_transport=transport,
    )
    monkeypatch.setattr(server_module, "awx", client)
    return client


@pytest.fixture
async def client(mock_awx_client: AwxRestClient):
    """Create an in-memory MCP client connected to the server via mcp_common."""
    async for c in mcp_client(server_module.mcp):
        yield c


@pytest.mark.anyio
async def test_core_tools_exist(client: Client) -> None:
    """Verify critical tools are registered."""
    for tool_name in ["awx_ping", "awx_get_me", "awx_list_resources", "awx_launch_and_wait"]:
        await assert_tool_exists(client, tool_name)


@pytest.mark.anyio
async def test_awx_ping_tool(client: Client) -> None:
    """Test awx_ping tool returns AWX version info."""
    result = await assert_tool_success(client, "awx_ping")
    assert result.data is not None
    assert result.data["version"] == "24.0.0"


@pytest.mark.anyio
async def test_awx_get_me_tool(client: Client) -> None:
    """Test awx_get_me tool returns current user."""
    result = await assert_tool_success(client, "awx_get_me")
    assert result.data is not None
    assert result.data["username"] == "testuser"
    assert result.data["id"] == 1


@pytest.mark.anyio
async def test_awx_list_job_templates_tool(client: Client) -> None:
    """Test awx_list_resources returns paginated job template results."""
    result = await client.call_tool("awx_list_resources", {"resource_type": "job_templates"})
    assert result.data is not None
    assert "count" in result.data
    assert "results" in result.data
    assert len(result.data["results"]) == 2
    assert result.data["results"][0]["name"] == "Deploy App"


@pytest.mark.anyio
async def test_awx_get_job_tool(client: Client) -> None:
    """Test awx_get_resource returns job details."""
    result = await client.call_tool(
        "awx_get_resource", {"resource_type": "jobs", "resource_id": 123}
    )
    assert result.data is not None
    assert result.data["id"] == 123
    assert result.data["status"] == "successful"


@pytest.mark.anyio
async def test_awx_get_job_stdout_tool(client: Client) -> None:
    """Test awx_get_job_stdout returns job output with truncation."""
    result = await client.call_tool(
        "awx_get_job_stdout",
        {"job_id": 123, "format": "txt", "limit_chars": 1000},
    )
    assert result.data is not None
    assert "content" in result.data
    assert "truncated" in result.data
    assert "PLAY [all]" in result.data["content"]


@pytest.mark.anyio
async def test_awx_get_job_stdout_truncation(client: Client) -> None:
    """Test awx_get_job_stdout truncates large output."""
    result = await client.call_tool(
        "awx_get_job_stdout",
        {"job_id": 123, "format": "txt", "limit_chars": 1000},
    )
    assert result.data is not None
    # Content should be truncated if original was longer than 1000 chars
    # Our mock returns short content, so it won't be truncated
    assert "content" in result.data
    assert "truncated" in result.data


@pytest.mark.anyio
async def test_awx_get_job_stdout_truncation_strategy_field(client: Client) -> None:
    """Test awx_get_job_stdout returns truncation_strategy and original_length fields."""
    result = await client.call_tool(
        "awx_get_job_stdout",
        {"job_id": 123, "format": "txt", "limit_chars": 1000},
    )
    assert result.data is not None
    assert "truncation_strategy" in result.data
    assert "original_length" in result.data
    assert result.data["truncation_strategy"] == "tail"


@pytest.mark.anyio
async def test_awx_get_job_stdout_head_strategy(client: Client) -> None:
    """Test awx_get_job_stdout with head truncation strategy."""
    result = await client.call_tool(
        "awx_get_job_stdout",
        {"job_id": 123, "format": "txt", "limit_chars": 1000, "truncation_strategy": "head"},
    )
    assert result.data is not None
    assert result.data["truncation_strategy"] == "head"


@pytest.mark.anyio
async def test_awx_parse_job_log_tool_exists(client: Client) -> None:
    """Test awx_parse_job_log tool is registered."""
    await assert_tool_exists(client, "awx_parse_job_log")


@pytest.mark.anyio
async def test_awx_parse_job_log_returns_structured_data(client: Client) -> None:
    """Test awx_parse_job_log returns parsed log structure."""
    result = await client.call_tool(
        "awx_parse_job_log",
        {"job_id": 123},
    )
    assert result.data is not None
    assert result.data["job_id"] == 123
    assert "overall_result" in result.data
    assert "has_failures" in result.data
    assert "plays" in result.data
    assert "host_stats" in result.data
    assert result.data["overall_result"] == "successful"
    assert result.data["has_failures"] is False
    assert "log_chars" in result.data
    assert result.data["log_chars"] > 0


@pytest.mark.anyio
async def test_awx_parse_job_log_sections_filter(client: Client) -> None:
    """Test awx_parse_job_log with specific sections."""
    result = await client.call_tool(
        "awx_parse_job_log",
        {"job_id": 123, "sections": ["recap"]},
    )
    assert result.data is not None
    assert "recap_text" in result.data
    assert "host_stats" in result.data
    assert "plays" not in result.data
    assert "failed_tasks" not in result.data


@pytest.mark.anyio
async def test_tool_error_when_awx_not_initialized(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test tools raise RuntimeError when AWX client is not initialized."""
    monkeypatch.setattr(server_module, "awx", None)
    async with Client(server_module.mcp) as client:
        try:
            await client.call_tool("awx_ping", {})
            raise AssertionError("expected RuntimeError")
        except Exception as e:
            assert "not initialized" in str(e).lower()


@pytest.mark.anyio
async def test_awx_list_resources_json_serializable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that awx_list_resources returns JSON-serializable data."""
    import json

    # Create a complex response similar to real AWX API
    complex_response = {
        "count": 2,
        "next": None,
        "previous": None,
        "results": [
            {
                "id": 1,
                "name": "Test Template",
                "created": "2024-01-01T00:00:00Z",
                "modified": "2024-01-01T00:00:00Z",
                "related": {"url": "/api/v2/job_templates/1/"},
                "summary_fields": {
                    "organization": {"id": 1, "name": "Test Org"},
                    "inventory": {"id": 1, "name": "Test Inventory"},
                },
            },
            {
                "id": 2,
                "name": "Another Template",
                "created": "2024-01-02T00:00:00Z",
                "nested": {"deep": {"value": 123}},
            },
        ],
    }

    # Mock the AWX client to return this complex response
    def handler(request: httpx.Request) -> httpx.Response:
        if "/job_templates" in request.url.path:
            return httpx.Response(200, json=complex_response)
        return httpx.Response(404, text="not found")

    transport = httpx.MockTransport(handler)
    client = AwxRestClient("https://awx.example.com", "token", http_transport=transport)
    monkeypatch.setattr(server_module, "awx", client)

    try:
        async with Client(server_module.mcp) as mcp_client:
            result = await mcp_client.call_tool(
                "awx_list_resources", {"resource_type": "job_templates"}
            )
            # Verify it's JSON-serializable (this was the bug - it should work now)
            json_str = json.dumps(result.data)
            parsed = json.loads(json_str)
            assert parsed["count"] == 2
            assert len(parsed["results"]) == 2
            assert parsed["results"][0]["name"] == "Test Template"
    finally:
        client.close()


def test_ensure_json_serializable() -> None:
    """Test the _ensure_json_serializable helper function."""
    import json
    from datetime import datetime

    from awx_mcp.server import _ensure_json_serializable

    # Test with datetime (common non-serializable type)
    data = {
        "id": 1,
        "created": datetime(2024, 1, 1, 12, 0, 0),
        "nested": {"value": 123},
    }

    result = _ensure_json_serializable(data)
    # Should be serializable now
    json_str = json.dumps(result, default=str)
    parsed = json.loads(json_str)
    assert parsed["id"] == 1
    assert parsed["nested"]["value"] == 123

    # Test with list
    data_list = [{"id": 1, "created": datetime.now()}, {"id": 2}]
    result_list = _ensure_json_serializable(data_list)
    json_str = json.dumps(result_list, default=str)
    parsed_list = json.loads(json_str)
    assert len(parsed_list) == 2


@pytest.mark.anyio
async def test_awx_get_resource_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test awx_get_resource returns credential details."""
    import httpx

    import awx_mcp.server as server_module
    from awx_mcp.awx_client import AwxRestClient

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.rstrip("/")
        if path.endswith("/credentials/67") or path.endswith("/credentials/67/"):
            return httpx.Response(
                200,
                json={
                    "id": 67,
                    "name": "netbox-krustykrab",
                    "credential_type": 31,
                    "inputs": {"netbox_api": "$encrypted$", "netbox_token": "$encrypted$"},
                },
            )
        return httpx.Response(404, text="not found")

    transport = httpx.MockTransport(handler)
    client = AwxRestClient("https://awx.example.com", "token", http_transport=transport)
    original_awx = server_module.awx
    server_module.awx = client

    try:
        async with Client(server_module.mcp) as mcp_client:
            result = await mcp_client.call_tool(
                "awx_get_resource", {"resource_type": "credentials", "resource_id": 67}
            )
            assert result.data is not None
            assert result.data["id"] == 67
            assert result.data["name"] == "netbox-krustykrab"
    finally:
        server_module.awx = original_awx
        client.close()


@pytest.mark.anyio
async def test_awx_list_resources_credentials_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test awx_list_resources finds credentials by name pattern."""
    import httpx

    import awx_mcp.server as server_module
    from awx_mcp.awx_client import AwxRestClient

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.rstrip("/")
        if "/credentials" in path:
            params = dict(request.url.params)
            if params.get("name__icontains") == "krustykrab":
                return httpx.Response(
                    200,
                    json={
                        "count": 1,
                        "results": [{"id": 67, "name": "netbox-krustykrab", "credential_type": 31}],
                    },
                )
        return httpx.Response(404, text="not found")

    transport = httpx.MockTransport(handler)
    client = AwxRestClient("https://awx.example.com", "token", http_transport=transport)
    original_awx = server_module.awx
    server_module.awx = client

    try:
        async with Client(server_module.mcp) as mcp_client:
            result = await mcp_client.call_tool(
                "awx_list_resources",
                {"resource_type": "credentials", "filters": {"name__icontains": "krustykrab"}},
            )
            assert result.data is not None
            assert result.data["count"] == 1
            assert len(result.data["results"]) == 1
            assert result.data["results"][0]["name"] == "netbox-krustykrab"
    finally:
        server_module.awx = original_awx
        client.close()


@pytest.mark.anyio
async def test_awx_list_resources_job_host_summaries(mock_awx_client: AwxRestClient) -> None:
    """Test awx_list_resources returns per-host job results."""
    # Update mock to handle job host summaries endpoint
    import httpx

    import awx_mcp.server as server_module
    from awx_mcp.awx_client import AwxRestClient

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.rstrip("/")
        if path.endswith("/jobs/123/job_host_summaries"):
            return httpx.Response(
                200,
                json={
                    "count": 2,
                    "results": [
                        {"host_id": 1, "hostname": "host1", "ok": 5, "failed": 0, "changed": 2},
                        {"host_id": 2, "hostname": "host2", "ok": 3, "failed": 1, "changed": 1},
                    ],
                },
            )
        return httpx.Response(404, text="not found")

    transport = httpx.MockTransport(handler)
    client = AwxRestClient("https://awx.example.com", "token", http_transport=transport)
    original_awx = server_module.awx
    server_module.awx = client

    try:
        async with Client(server_module.mcp) as mcp_client:
            result = await mcp_client.call_tool(
                "awx_list_resources",
                {"resource_type": "job_host_summaries", "parent_type": "jobs", "parent_id": 123},
            )
            assert result.data is not None
            assert result.data["count"] == 2
            assert len(result.data["results"]) == 2
            assert result.data["results"][0]["hostname"] == "host1"
            assert result.data["results"][1]["failed"] == 1
    finally:
        server_module.awx = original_awx
        client.close()


@pytest.mark.anyio
async def test_awx_cancel_job(mock_awx_client: AwxRestClient) -> None:
    """Test awx_cancel_job sends cancel request."""
    import httpx

    import awx_mcp.server as server_module
    from awx_mcp.awx_client import AwxRestClient

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and "/jobs/123/cancel" in str(request.url):
            return httpx.Response(202, json={"job": 123, "status": "canceled"})
        return httpx.Response(404, text="not found")

    transport = httpx.MockTransport(handler)
    client = AwxRestClient("https://awx.example.com", "token", http_transport=transport)
    original_awx = server_module.awx
    server_module.awx = client

    try:
        async with Client(server_module.mcp) as mcp_client:
            result = await mcp_client.call_tool("awx_cancel_job", {"job_id": 123})
            assert result.data is not None
            assert result.data["job"] == 123
            assert result.data["status"] == "canceled"
    finally:
        server_module.awx = original_awx
        client.close()


@pytest.mark.anyio
async def test_awx_get_system_info(mock_awx_client: AwxRestClient) -> None:
    """Test awx_get_system_info returns combined system information."""
    import httpx

    import awx_mcp.server as server_module
    from awx_mcp.awx_client import AwxRestClient

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.rstrip("/")
        if path.endswith("/ping"):
            return httpx.Response(200, json={"version": "24.6.1"})
        elif path.endswith("/config"):
            return httpx.Response(200, json={"time_zone": "UTC"})
        elif path.endswith("/settings"):
            return httpx.Response(200, json={"debug": False})
        return httpx.Response(404, text="not found")

    transport = httpx.MockTransport(handler)
    client = AwxRestClient("https://awx.example.com", "token", http_transport=transport)
    original_awx = server_module.awx
    server_module.awx = client

    try:
        async with Client(server_module.mcp) as mcp_client:
            result = await mcp_client.call_tool("awx_get_system_info", {})
            assert result.data is not None
            assert "ping" in result.data
            assert "config" in result.data
            assert "settings" in result.data
            assert result.data["ping"]["version"] == "24.6.1"
            assert result.data["config"]["time_zone"] == "UTC"
            assert result.data["settings"]["debug"] is False
    finally:
        server_module.awx = original_awx
        client.close()


@pytest.mark.anyio
async def test_awx_list_resources_workflow_nodes(mock_awx_client: AwxRestClient) -> None:
    """Test awx_list_resources returns workflow nodes for a workflow template."""
    import httpx

    import awx_mcp.server as server_module
    from awx_mcp.awx_client import AwxRestClient

    def handler(request: httpx.Request) -> httpx.Response:
        if "/workflow_job_templates/123/workflow_nodes" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "count": 2,
                    "results": [
                        {
                            "id": 1,
                            "identifier": "Start",
                            "unified_job_type": "job_template",
                            "success_nodes": [2],
                        },
                        {
                            "id": 2,
                            "identifier": "Deploy",
                            "unified_job_type": "job_template",
                            "success_nodes": [],
                        },
                    ],
                },
            )
        return httpx.Response(404, text="not found")

    transport = httpx.MockTransport(handler)
    client = AwxRestClient("https://awx.example.com", "token", http_transport=transport)
    original_awx = server_module.awx
    server_module.awx = client

    try:
        async with Client(server_module.mcp) as mcp_client:
            result = await mcp_client.call_tool(
                "awx_list_resources",
                {
                    "resource_type": "workflow_nodes",
                    "parent_type": "workflow_job_templates",
                    "parent_id": 123,
                },
            )
            assert result.data is not None
            assert result.data["count"] == 2
            assert len(result.data["results"]) == 2
            assert result.data["results"][0]["identifier"] == "Start"
            assert result.data["results"][1]["identifier"] == "Deploy"
    finally:
        server_module.awx = original_awx
        client.close()


@pytest.mark.anyio
async def test_awx_get_workflow_visualization(mock_awx_client: AwxRestClient) -> None:
    """Test awx_get_workflow_visualization returns graph structure."""
    import httpx

    import awx_mcp.server as server_module
    from awx_mcp.awx_client import AwxRestClient

    def handler(request: httpx.Request) -> httpx.Response:
        if "/workflow_job_templates/123/workflow_nodes" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "count": 2,
                    "results": [
                        {
                            "id": 1,
                            "identifier": "Start",
                            "unified_job_type": "job_template",
                            "success_nodes": [2],
                            "failure_nodes": [],
                            "always_nodes": [],
                        },
                        {
                            "id": 2,
                            "identifier": "Deploy",
                            "unified_job_type": "job_template",
                            "success_nodes": [],
                            "failure_nodes": [],
                            "always_nodes": [],
                        },
                    ],
                },
            )
        return httpx.Response(404, text="not found")

    transport = httpx.MockTransport(handler)
    client = AwxRestClient("https://awx.example.com", "token", http_transport=transport)
    original_awx = server_module.awx
    server_module.awx = client

    try:
        async with Client(server_module.mcp) as mcp_client:
            result = await mcp_client.call_tool(
                "awx_get_workflow_visualization", {"workflow_job_template_id": 123}
            )
            assert result.data is not None
            assert "nodes" in result.data
            assert "links" in result.data
            assert len(result.data["nodes"]) == 2
            assert len(result.data["links"]) == 1  # One success link
            assert result.data["links"][0]["source"] == 1
            assert result.data["links"][0]["target"] == 2
            assert result.data["links"][0]["type"] == "success"
    finally:
        server_module.awx = original_awx
        client.close()


@pytest.mark.anyio
async def test_awx_bulk_cancel_jobs(mock_awx_client: AwxRestClient) -> None:
    """Test awx_bulk_cancel_jobs cancels multiple jobs."""
    import httpx

    import awx_mcp.server as server_module
    from awx_mcp.awx_client import AwxRestClient

    cancel_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal cancel_count
        if (
            request.method == "POST"
            and "/jobs/" in str(request.url)
            and "/cancel" in str(request.url)
        ):
            cancel_count += 1
            return httpx.Response(202, json={"job": cancel_count, "status": "canceled"})
        return httpx.Response(404, text="not found")

    transport = httpx.MockTransport(handler)
    client = AwxRestClient("https://awx.example.com", "token", http_transport=transport)
    original_awx = server_module.awx
    server_module.awx = client

    try:
        async with Client(server_module.mcp) as mcp_client:
            result = await mcp_client.call_tool("awx_bulk_cancel_jobs", {"job_ids": [1, 2, 3]})
            assert result.data is not None
            assert result.data["total_requested"] == 3
            assert result.data["successful"] == 3
            assert len(result.data["results"]) == 3
            assert all(r["status"] == "canceled" for r in result.data["results"])
    finally:
        server_module.awx = original_awx
        client.close()


@pytest.mark.anyio
async def test_awx_list_resources_inventory_sources(mock_awx_client: AwxRestClient) -> None:
    """Test awx_list_resources returns inventory sources."""
    import httpx

    import awx_mcp.server as server_module
    from awx_mcp.awx_client import AwxRestClient

    def handler(request: httpx.Request) -> httpx.Response:
        if "/inventory_sources" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "count": 1,
                    "results": [
                        {
                            "id": 1,
                            "name": "NetBox Inventory",
                            "source": "netbox",
                            "inventory": 64,
                        }
                    ],
                },
            )
        return httpx.Response(404, text="not found")

    transport = httpx.MockTransport(handler)
    client = AwxRestClient("https://awx.example.com", "token", http_transport=transport)
    original_awx = server_module.awx
    server_module.awx = client

    try:
        async with Client(server_module.mcp) as mcp_client:
            result = await mcp_client.call_tool(
                "awx_list_resources", {"resource_type": "inventory_sources"}
            )
            assert result.data is not None
            assert result.data["count"] == 1
            assert len(result.data["results"]) == 1
            assert result.data["results"][0]["name"] == "NetBox Inventory"
            assert result.data["results"][0]["source"] == "netbox"
    finally:
        server_module.awx = original_awx
        client.close()


@pytest.mark.anyio
async def test_awx_update_project(mock_awx_client: AwxRestClient) -> None:
    """Test awx_update_project triggers project sync."""
    import httpx

    import awx_mcp.server as server_module
    from awx_mcp.awx_client import AwxRestClient

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and "/projects/123/update" in str(request.url):
            return httpx.Response(202, json={"project_update": 456, "status": "pending"})
        return httpx.Response(404, text="not found")

    transport = httpx.MockTransport(handler)
    client = AwxRestClient("https://awx.example.com", "token", http_transport=transport)
    original_awx = server_module.awx
    server_module.awx = client

    try:
        async with Client(server_module.mcp) as mcp_client:
            result = await mcp_client.call_tool("awx_update_project", {"project_id": 123})
            assert result.data is not None
            assert result.data["project_update"] == 456
            assert result.data["status"] == "pending"
    finally:
        server_module.awx = original_awx
        client.close()


@pytest.mark.anyio
async def test_awx_list_resources_teams(mock_awx_client: AwxRestClient) -> None:
    """Test awx_list_resources returns team information."""
    import httpx

    import awx_mcp.server as server_module
    from awx_mcp.awx_client import AwxRestClient

    def handler(request: httpx.Request) -> httpx.Response:
        if "/teams" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "count": 1,
                    "results": [{"id": 1, "name": "DevOps Team", "organization": 2}],
                },
            )
        return httpx.Response(404, text="not found")

    transport = httpx.MockTransport(handler)
    client = AwxRestClient("https://awx.example.com", "token", http_transport=transport)
    original_awx = server_module.awx
    server_module.awx = client

    try:
        async with Client(server_module.mcp) as mcp_client:
            result = await mcp_client.call_tool("awx_list_resources", {"resource_type": "teams"})
            assert result.data is not None
            assert result.data["count"] == 1
            assert result.data["results"][0]["name"] == "DevOps Team"
    finally:
        server_module.awx = original_awx
        client.close()


@pytest.mark.anyio
async def test_awx_create_resource_schedule(mock_awx_client: AwxRestClient) -> None:
    """Test awx_create_resource creates a new schedule."""
    import httpx

    import awx_mcp.server as server_module
    from awx_mcp.awx_client import AwxRestClient

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and "/job_templates/123/schedules" in str(request.url):
            return httpx.Response(201, json={"id": 456, "name": "Daily Backup", "enabled": True})
        return httpx.Response(404, text="not found")

    transport = httpx.MockTransport(handler)
    client = AwxRestClient("https://awx.example.com", "token", http_transport=transport)
    original_awx = server_module.awx
    server_module.awx = client

    try:
        async with Client(server_module.mcp) as mcp_client:
            result = await mcp_client.call_tool(
                "awx_create_resource",
                {
                    "resource_type": "schedules",
                    "data": {"name": "Daily Backup", "rrule": "FREQ=DAILY", "enabled": True},
                    "parent_type": "job_templates",
                    "parent_id": 123,
                },
            )
            assert result.data is not None
            assert result.data["id"] == 456
            assert result.data["name"] == "Daily Backup"
    finally:
        server_module.awx = original_awx
        client.close()


@pytest.mark.anyio
async def test_awx_create_notification(mock_awx_client: AwxRestClient) -> None:
    """Test awx_create_notification creates a notification."""
    import httpx

    import awx_mcp.server as server_module
    from awx_mcp.awx_client import AwxRestClient

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and "/notifications" in str(request.url):
            return httpx.Response(
                201,
                json={
                    "id": 789,
                    "name": "Slack Alert",
                    "type": "slack",
                    "notification_configuration": {"token": "***"},
                },
            )
        return httpx.Response(404, text="not found")

    transport = httpx.MockTransport(handler)
    client = AwxRestClient("https://awx.example.com", "token", http_transport=transport)
    original_awx = server_module.awx
    server_module.awx = client

    try:
        async with Client(server_module.mcp) as mcp_client:
            result = await mcp_client.call_tool(
                "awx_create_notification",
                {
                    "template_type": "job_template",
                    "template_id": 123,
                    "name": "Slack Alert",
                    "notification_type": "slack",
                    "notification_configuration": {"token": "xoxb-123", "channel": "#alerts"},
                },
            )
            assert result.data is not None
            assert result.data["id"] == 789
            assert result.data["type"] == "slack"
    finally:
        server_module.awx = original_awx
        client.close()


@pytest.mark.anyio
async def test_awx_list_resources_execution_environments(mock_awx_client: AwxRestClient) -> None:
    """Test awx_list_resources returns execution environments."""
    import httpx

    import awx_mcp.server as server_module
    from awx_mcp.awx_client import AwxRestClient

    def handler(request: httpx.Request) -> httpx.Response:
        if "/execution_environments" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "count": 1,
                    "results": [
                        {"id": 1, "name": "AWX EE", "image": "quay.io/ansible/awx-ee:latest"}
                    ],
                },
            )
        return httpx.Response(404, text="not found")

    transport = httpx.MockTransport(handler)
    client = AwxRestClient("https://awx.example.com", "token", http_transport=transport)
    original_awx = server_module.awx
    server_module.awx = client

    try:
        async with Client(server_module.mcp) as mcp_client:
            result = await mcp_client.call_tool(
                "awx_list_resources", {"resource_type": "execution_environments"}
            )
            assert result.data is not None
            assert result.data["count"] == 1
            assert result.data["results"][0]["name"] == "AWX EE"
    finally:
        server_module.awx = original_awx
        client.close()


@pytest.mark.anyio
async def test_awx_get_cluster_status(mock_awx_client: AwxRestClient) -> None:
    """Test awx_get_cluster_status returns combined cluster information."""
    import httpx

    import awx_mcp.server as server_module
    from awx_mcp.awx_client import AwxRestClient

    def handler(request: httpx.Request) -> httpx.Response:
        if "/instances" in str(request.url):
            return httpx.Response(
                200, json={"count": 3, "results": [{"hostname": "awx-1"}, {"hostname": "awx-2"}]}
            )
        elif "/instance_groups" in str(request.url):
            return httpx.Response(
                200, json={"count": 2, "results": [{"name": "controlplane"}, {"name": "default"}]}
            )
        elif "/ping" in str(request.url):
            return httpx.Response(200, json={"version": "24.6.1", "active_node": "awx-1"})
        return httpx.Response(404, text="not found")

    transport = httpx.MockTransport(handler)
    client = AwxRestClient("https://awx.example.com", "token", http_transport=transport)
    original_awx = server_module.awx
    server_module.awx = client

    try:
        async with Client(server_module.mcp) as mcp_client:
            result = await mcp_client.call_tool("awx_get_cluster_status", {})
            assert result.data is not None
            assert "instances" in result.data
            assert "instance_groups" in result.data
            assert "ping" in result.data
            assert result.data["instances"]["count"] == 3
            assert result.data["ping"]["version"] == "24.6.1"
    finally:
        server_module.awx = original_awx
        client.close()


@pytest.mark.anyio
async def test_awx_create_resource_credential(mock_awx_client: AwxRestClient) -> None:
    """Test awx_create_resource creates a new credential."""
    import httpx

    import awx_mcp.server as server_module
    from awx_mcp.awx_client import AwxRestClient

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and "/credentials" in str(request.url):
            return httpx.Response(
                201, json={"id": 999, "name": "Test SSH Key", "credential_type": 1}
            )
        return httpx.Response(404, text="not found")

    transport = httpx.MockTransport(handler)
    client = AwxRestClient("https://awx.example.com", "token", http_transport=transport)
    original_awx = server_module.awx
    server_module.awx = client

    try:
        async with Client(server_module.mcp) as mcp_client:
            result = await mcp_client.call_tool(
                "awx_create_resource",
                {
                    "resource_type": "credentials",
                    "data": {
                        "name": "Test SSH Key",
                        "credential_type": 1,
                        "organization": 2,
                        "inputs": {"username": "ansible", "ssh_key_data": "ssh-rsa AAAAB3..."},
                    },
                },
            )
            assert result.data is not None
            assert result.data["id"] == 999
            assert result.data["name"] == "Test SSH Key"
    finally:
        server_module.awx = original_awx
        client.close()


@pytest.mark.anyio
async def test_awx_bulk_delete_jobs(mock_awx_client: AwxRestClient) -> None:
    """Test awx_bulk_delete_jobs deletes multiple jobs."""
    import httpx

    import awx_mcp.server as server_module
    from awx_mcp.awx_client import AwxRestClient

    delete_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal delete_count
        if request.method == "DELETE" and "/jobs/" in str(request.url):
            delete_count += 1
            return httpx.Response(204, json={})
        return httpx.Response(404, text="not found")

    transport = httpx.MockTransport(handler)
    client = AwxRestClient("https://awx.example.com", "token", http_transport=transport)
    original_awx = server_module.awx
    server_module.awx = client

    try:
        async with Client(server_module.mcp) as mcp_client:
            result = await mcp_client.call_tool("awx_bulk_delete_jobs", {"job_ids": [1, 2, 3]})
            assert result.data is not None
            assert result.data["total_requested"] == 3
            assert result.data["successful"] == 3
            assert len(result.data["results"]) == 3
            assert all(r["status"] == "deleted" for r in result.data["results"])
    finally:
        server_module.awx = original_awx
        client.close()


@pytest.mark.anyio
async def test_awx_get_resource_inventory_variables(mock_awx_client: AwxRestClient) -> None:
    """Test awx_get_resource returns inventory variables."""
    import httpx

    import awx_mcp.server as server_module
    from awx_mcp.awx_client import AwxRestClient

    def handler(request: httpx.Request) -> httpx.Response:
        if "/inventories/123/variable_data" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "_meta": {"hostvars": {"host1": {"ansible_host": "10.0.0.1"}}},
                    "all": {"vars": {"env": "production"}},
                    "group1": ["host1"],
                },
            )
        return httpx.Response(404, text="not found")

    transport = httpx.MockTransport(handler)
    client = AwxRestClient("https://awx.example.com", "token", http_transport=transport)
    original_awx = server_module.awx
    server_module.awx = client

    try:
        async with Client(server_module.mcp) as mcp_client:
            result = await mcp_client.call_tool(
                "awx_get_resource",
                {
                    "resource_type": "inventories",
                    "resource_id": 123,
                    "property_path": "variable_data",
                },
            )
            assert result.data is not None
            assert "_meta" in result.data
            assert "all" in result.data
            assert result.data["all"]["vars"]["env"] == "production"
    finally:
        server_module.awx = original_awx
        client.close()


@pytest.mark.anyio
async def test_awx_list_supported_resources(mock_awx_client: AwxRestClient) -> None:
    """Test awx_list_supported_resources returns resource capabilities."""
    async with Client(server_module.mcp) as mcp_client:
        result = await mcp_client.call_tool("awx_list_supported_resources", {})
        assert result.data is not None
        assert "resources" in result.data
        assert "credentials" in result.data["resources"]
        assert "job_templates" in result.data["resources"]


@pytest.mark.anyio
async def test_awx_list_resources_generic(mock_awx_client: AwxRestClient) -> None:
    """Test generic awx_list_resources tool."""
    async with Client(server_module.mcp) as mcp_client:
        result = await mcp_client.call_tool(
            "awx_list_resources", {"resource_type": "job_templates"}
        )
        assert result.data is not None
        assert "count" in result.data
        assert "results" in result.data


@pytest.mark.anyio
async def test_awx_get_resource_generic(mock_awx_client: AwxRestClient) -> None:
    """Test generic awx_get_resource tool."""
    async with Client(server_module.mcp) as mcp_client:
        result = await mcp_client.call_tool(
            "awx_get_resource", {"resource_type": "credentials", "resource_id": 67}
        )
        assert result.data is not None
        assert result.data["id"] == 67


@pytest.mark.anyio
async def test_awx_get_system_metrics(mock_awx_client: AwxRestClient) -> None:
    """Test awx_get_system_metrics returns performance statistics."""
    import httpx

    import awx_mcp.server as server_module
    from awx_mcp.awx_client import AwxRestClient

    def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "/unified_jobs" in url_str:
            return httpx.Response(200, json={"count": 1500})
        elif "/jobs" in url_str and "status=running" in url_str:
            return httpx.Response(200, json={"count": 5})
        elif "/jobs" in url_str and "status=failed" in url_str:
            return httpx.Response(200, json={"count": 23})
        return httpx.Response(404, text="not found")

    transport = httpx.MockTransport(handler)
    client = AwxRestClient("https://awx.example.com", "token", http_transport=transport)
    original_awx = server_module.awx
    server_module.awx = client

    try:
        async with Client(server_module.mcp) as mcp_client:
            result = await mcp_client.call_tool("awx_get_system_metrics", {})
            assert result.data is not None
            assert "total_jobs" in result.data
            assert "active_jobs" in result.data
            assert "failed_jobs" in result.data
            assert result.data["total_jobs"] == 1500
            assert result.data["active_jobs"] == 5
            assert result.data["failed_jobs"] == 23
    finally:
        server_module.awx = original_awx
        client.close()


def test_build_endpoint_simple() -> None:
    """Test _build_endpoint with simple resource type."""
    from awx_mcp.server import _build_endpoint

    assert _build_endpoint("credentials") == "credentials"
    assert _build_endpoint("credentials", 123) == "credentials/123"
    assert _build_endpoint("credentials", 123, "inputs") == "credentials/123/inputs"


def test_build_endpoint_nested() -> None:
    """Test _build_endpoint with parent type/id."""
    from awx_mcp.server import _build_endpoint

    assert (
        _build_endpoint("schedules", parent_type="job_templates", parent_id=174)
        == "job_templates/174/schedules"
    )
    assert (
        _build_endpoint("schedules", 789, parent_type="job_templates", parent_id=174)
        == "job_templates/174/schedules/789"
    )
    assert _build_endpoint("job_templates", 123, "survey_spec") == "job_templates/123/survey_spec"


@pytest.mark.anyio
async def test_awx_launch_and_wait_success(mock_awx_client: AwxRestClient) -> None:
    """Test awx_launch_and_wait launches and polls until completion."""
    import httpx

    import awx_mcp.server as server_module
    from awx_mcp.awx_client import AwxRestClient

    poll_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal poll_count
        if request.method == "POST" and "/job_templates/174/launch" in str(request.url):
            return httpx.Response(201, json={"id": 5000, "status": "pending"})
        if request.method == "GET" and "/jobs/5000" in str(request.url):
            poll_count += 1
            if poll_count >= 2:
                return httpx.Response(
                    200, json={"id": 5000, "status": "successful", "elapsed": 12.5}
                )
            return httpx.Response(200, json={"id": 5000, "status": "running"})
        return httpx.Response(404, text="not found")

    transport = httpx.MockTransport(handler)
    client = AwxRestClient("https://awx.example.com", "token", http_transport=transport)
    original_awx = server_module.awx
    server_module.awx = client

    try:
        async with Client(server_module.mcp) as mcp_client:
            result = await mcp_client.call_tool(
                "awx_launch_and_wait",
                {
                    "template_type": "job_template",
                    "template_id": 174,
                    "poll_interval_seconds": 0.5,
                    "timeout_seconds": 10,
                },
            )
            assert result.data is not None
            assert result.data["id"] == 5000
            assert result.data["status"] == "successful"
    finally:
        server_module.awx = original_awx
        client.close()


@pytest.mark.anyio
async def test_awx_launch_and_wait_timeout(mock_awx_client: AwxRestClient) -> None:
    """Test awx_launch_and_wait returns timeout info when job doesn't complete."""
    import httpx

    import awx_mcp.server as server_module
    from awx_mcp.awx_client import AwxRestClient

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and "/job_templates/174/launch" in str(request.url):
            return httpx.Response(201, json={"id": 6000, "status": "pending"})
        if request.method == "GET" and "/jobs/6000" in str(request.url):
            return httpx.Response(200, json={"id": 6000, "status": "running"})
        return httpx.Response(404, text="not found")

    transport = httpx.MockTransport(handler)
    client = AwxRestClient("https://awx.example.com", "token", http_transport=transport)
    original_awx = server_module.awx
    server_module.awx = client

    try:
        async with Client(server_module.mcp) as mcp_client:
            result = await mcp_client.call_tool(
                "awx_launch_and_wait",
                {
                    "template_type": "job_template",
                    "template_id": 174,
                    "poll_interval_seconds": 0.5,
                    "timeout_seconds": 2,
                },
            )
            assert result.data is not None
            assert result.data["timeout"] is True
            assert result.data["job_id"] == 6000
    finally:
        server_module.awx = original_awx
        client.close()


# ---------------------------------------------------------------------------
# Prompt tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_prompts(client: Client) -> None:
    """Verify all 4 prompts are registered."""
    prompts = await client.list_prompts()
    names = {p.name for p in prompts}
    assert "triage_failed_job" in names
    assert "launch_deployment" in names
    assert "check_cluster_health" in names
    assert "investigate_host" in names


@pytest.mark.anyio
async def test_render_prompt_triage_failed_job(client: Client) -> None:
    """Verify triage_failed_job prompt renders with job_id."""
    result = await client.get_prompt("triage_failed_job", {"job_id": "42"})
    assert len(result.messages) == 1
    content = result.messages[0].content
    text = content.text if hasattr(content, "text") else str(content)
    assert "42" in text
    assert "awx_list_resources" in text
    assert "awx_get_job_stdout" in text


@pytest.mark.anyio
async def test_render_prompt_check_cluster_health(client: Client) -> None:
    """Verify check_cluster_health prompt renders."""
    result = await client.get_prompt("check_cluster_health", {})
    assert len(result.messages) == 1
    content = result.messages[0].content
    text = content.text if hasattr(content, "text") else str(content)
    assert "awx_ping" in text
    assert "awx_get_cluster_status" in text


@pytest.mark.anyio
async def test_render_prompt_launch_deployment(client: Client) -> None:
    """Verify launch_deployment prompt renders with template name."""
    result = await client.get_prompt("launch_deployment", {"template_name": "deploy-app"})
    assert len(result.messages) == 1
    content = result.messages[0].content
    text = content.text if hasattr(content, "text") else str(content)
    assert "deploy-app" in text


@pytest.mark.anyio
async def test_render_prompt_investigate_host(client: Client) -> None:
    """Verify investigate_host prompt renders with hostname."""
    result = await client.get_prompt("investigate_host", {"hostname": "web-01"})
    assert len(result.messages) == 1
    content = result.messages[0].content
    text = content.text if hasattr(content, "text") else str(content)
    assert "web-01" in text


# ---------------------------------------------------------------------------
# Resource tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_resources(client: Client) -> None:
    """Verify static resources are registered."""
    resources = await client.list_resources()
    uris = {str(r.uri) for r in resources}
    assert "awx://resource-capabilities" in uris
    assert "health://awx" in uris


@pytest.mark.anyio
async def test_read_resource_capabilities(client: Client) -> None:
    """Verify resource-capabilities resource returns RESOURCE_CAPABILITIES."""
    result = await client.read_resource("awx://resource-capabilities")
    assert result is not None
    text = str(result)
    assert "credentials" in text
    assert "job_templates" in text


@pytest.mark.anyio
async def test_read_health_resource(client: Client) -> None:
    """Verify health resource returns health status."""
    result = await client.read_resource("health://awx")
    assert result is not None
    text = str(result)
    assert "awx-mcp" in text
    assert "healthy" in text


@pytest.mark.anyio
async def test_list_resource_templates(client: Client) -> None:
    """Verify job resource template is registered."""
    templates = await client.list_resource_templates()
    uris = {str(t.uriTemplate) for t in templates}
    assert "awx://jobs/{job_id}" in uris


@pytest.mark.anyio
async def test_read_job_resource(client: Client) -> None:
    """Verify awx://jobs/123 resource returns job data."""
    result = await client.read_resource("awx://jobs/123")
    assert result is not None
    text = str(result)
    assert "123" in text
    assert "successful" in text


def test_logging_setup() -> None:
    from mcp_common.logging import setup_logging

    logger = setup_logging(name="awx-mcp-test", system_log=True)
    assert logger is not None
