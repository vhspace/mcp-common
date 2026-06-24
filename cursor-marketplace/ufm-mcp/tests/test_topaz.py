"""Tests for Topaz fabric health integration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ufm_mcp.config import Settings


def test_topaz_az_map_defaults() -> None:
    settings = Settings(ufm_url="https://ufm.example.com/", verify_ssl=False)
    az_map = settings.topaz_az_map
    assert az_map["ori"] == "us-south-2a"
    assert az_map["5c_oh1"] == "us-central-8a"
    assert az_map["iren_b300"] == "ca-west-1c"


def test_topaz_az_map_override() -> None:
    settings = Settings(
        ufm_url="https://ufm.example.com/",
        verify_ssl=False,
        topaz_az_map_json='{"ori": "custom-az", "new_site": "new-az"}',
    )
    az_map = settings.topaz_az_map
    assert az_map["ori"] == "custom-az"
    assert az_map["new_site"] == "new-az"
    assert az_map["5c_oh1"] == "us-central-8a"


def test_topaz_az_map_invalid_json() -> None:
    settings = Settings(
        ufm_url="https://ufm.example.com/",
        verify_ssl=False,
        topaz_az_map_json="not-valid-json",
    )
    az_map = settings.topaz_az_map
    assert az_map["ori"] == "us-south-2a"


def test_topaz_endpoint_default() -> None:
    settings = Settings(ufm_url="https://ufm.example.com/", verify_ssl=False)
    assert settings.topaz_endpoint == "localhost:50051"


def _mock_load_grpc(mock_grpc=None):
    """Build a mock _load_grpc return tuple using the real proto stubs."""
    if mock_grpc is None:
        mock_grpc = MagicMock()
        mock_grpc.insecure_channel.return_value = MagicMock()
    from google.protobuf.json_format import MessageToDict

    from ufm_mcp.proto import health_pb2, health_pb2_grpc

    return mock_grpc, MessageToDict, health_pb2, health_pb2_grpc


def test_topaz_client_init() -> None:
    mock_grpc = MagicMock()
    mock_grpc.insecure_channel.return_value = MagicMock()
    with patch("ufm_mcp.topaz_client._load_grpc", return_value=_mock_load_grpc(mock_grpc)):
        from ufm_mcp.topaz_client import TopazClient

        client = TopazClient("test:50051")
        call_args = mock_grpc.insecure_channel.call_args
        assert call_args[0][0] == "test:50051"
        # Verify 64MB message-size options are set
        options = dict(
            call_args[1].get("options", call_args[0][1] if len(call_args[0]) > 1 else [])
        )
        assert options.get("grpc.max_send_message_length") == 64 * 1024 * 1024
        assert options.get("grpc.max_receive_message_length") == 64 * 1024 * 1024
        client.close()


def test_topaz_client_get_fabric_health() -> None:
    mock_grpc = MagicMock()
    mock_channel = MagicMock()
    mock_grpc.insecure_channel.return_value = mock_channel
    load_rv = _mock_load_grpc(mock_grpc)
    health_pb2 = load_rv[2]

    with patch("ufm_mcp.topaz_client._load_grpc", return_value=load_rv):
        from ufm_mcp.topaz_client import TopazClient

        client = TopazClient("test:50051")
        mock_stub = client._stub

        mock_response = health_pb2.FabricHealthResponse(
            status=health_pb2.HEALTH_STATUS_HEALTHY,
            score=100,
            total_errors=0,
            total_warnings=2,
            az_id="us-south-2a",
        )
        mock_stub.GetFabricHealth.return_value = mock_response

        result = client.get_fabric_health("us-south-2a")
        assert result["status"] == "HEALTH_STATUS_HEALTHY"
        assert result["score"] == 100
        assert result["total_warnings"] == 2
        client.close()


def test_topaz_client_grpc_error() -> None:
    mock_grpc = MagicMock()
    mock_channel = MagicMock()
    mock_grpc.insecure_channel.return_value = mock_channel
    mock_grpc.RpcError = type("RpcError", (Exception,), {})
    rpc_error = mock_grpc.RpcError("Connection refused")
    rpc_error.code = MagicMock(return_value=mock_grpc.StatusCode.UNAVAILABLE)
    rpc_error.details = MagicMock(return_value="Connection refused")

    with patch("ufm_mcp.topaz_client._load_grpc", return_value=_mock_load_grpc(mock_grpc)):
        from ufm_mcp.topaz_client import TopazClient

        client = TopazClient("test:50051")
        client._stub.GetFabricHealth.side_effect = rpc_error

        result = client.get_fabric_health("test-az")
        assert result["ok"] is False
        assert "gRPC error" in result["error"]
        assert result["grpc_details"] == "Connection refused"
        client.close()


def test_topaz_client_list_port_counters() -> None:
    mock_grpc = MagicMock()
    mock_channel = MagicMock()
    mock_grpc.insecure_channel.return_value = mock_channel
    load_rv = _mock_load_grpc(mock_grpc)
    health_pb2 = load_rv[2]

    with patch("ufm_mcp.topaz_client._load_grpc", return_value=load_rv):
        from ufm_mcp.topaz_client import TopazClient

        client = TopazClient("test:50051")
        mock_stub = client._stub

        mock_response = health_pb2.ListPortCountersResponse(total_count=1)
        port_data = mock_response.port_counters.add()
        port_data.port = 1
        port_data.node_desc = "switch01"
        port_data.total_errors = 5
        mock_stub.ListPortCounters.return_value = mock_response

        result = client.list_port_counters("us-south-2a", errors_only=True)
        assert result["total_count"] == 1
        assert len(result["port_counters"]) == 1
        client.close()


def test_topaz_client_list_cables() -> None:
    mock_grpc = MagicMock()
    mock_channel = MagicMock()
    mock_grpc.insecure_channel.return_value = mock_channel
    load_rv = _mock_load_grpc(mock_grpc)
    health_pb2 = load_rv[2]

    with patch("ufm_mcp.topaz_client._load_grpc", return_value=load_rv):
        from ufm_mcp.topaz_client import TopazClient

        client = TopazClient("test:50051")
        mock_stub = client._stub

        mock_response = health_pb2.ListCablesResponse(total_count=1)
        cable = mock_response.cables.add()
        cable.vendor = "Mellanox"
        cable.part_number = "MFS1S00-H003E"
        cable.serial_number = "SN12345"
        cable.temperature_c = 42.5
        mock_stub.ListCables.return_value = mock_response

        result = client.list_cables("us-south-2a", alarms_only=False)
        assert result["total_count"] == 1
        assert result["cables"][0]["vendor"] == "Mellanox"
        assert result["cables"][0]["temperature_c"] == 42.5
        client.close()


def test_topaz_client_list_port_counters_forwards_collection_id() -> None:
    """collection_id must be forwarded into the gRPC ListPortCountersRequest (#review)."""
    mock_grpc = MagicMock()
    mock_grpc.insecure_channel.return_value = MagicMock()
    load_rv = _mock_load_grpc(mock_grpc)
    health_pb2 = load_rv[2]

    with patch("ufm_mcp.topaz_client._load_grpc", return_value=load_rv):
        from ufm_mcp.topaz_client import TopazClient

        client = TopazClient("test:50051")
        client._stub.ListPortCounters.return_value = health_pb2.ListPortCountersResponse(
            total_count=0
        )

        client.list_port_counters("us-south-2a", collection_id="coll-abc-123")

        req = client._stub.ListPortCounters.call_args[0][0]
        assert req.az_id == "us-south-2a"
        assert req.collection_id == "coll-abc-123"
        client.close()


def test_topaz_client_list_cables_forwards_collection_id() -> None:
    """collection_id must be forwarded into the gRPC ListCablesRequest (#review)."""
    mock_grpc = MagicMock()
    mock_grpc.insecure_channel.return_value = MagicMock()
    load_rv = _mock_load_grpc(mock_grpc)
    health_pb2 = load_rv[2]

    with patch("ufm_mcp.topaz_client._load_grpc", return_value=load_rv):
        from ufm_mcp.topaz_client import TopazClient

        client = TopazClient("test:50051")
        client._stub.ListCables.return_value = health_pb2.ListCablesResponse(total_count=0)

        client.list_cables("us-south-2a", collection_id="coll-abc-123")

        req = client._stub.ListCables.call_args[0][0]
        assert req.az_id == "us-south-2a"
        assert req.collection_id == "coll-abc-123"
        client.close()


def test_topaz_client_list_switches_forwards_collection_id() -> None:
    """collection_id must be forwarded into the gRPC ListSwitchesRequest (#review)."""
    mock_grpc = MagicMock()
    mock_grpc.insecure_channel.return_value = MagicMock()
    load_rv = _mock_load_grpc(mock_grpc)
    health_pb2 = load_rv[2]

    with patch("ufm_mcp.topaz_client._load_grpc", return_value=load_rv):
        from ufm_mcp.topaz_client import TopazClient

        client = TopazClient("test:50051")
        client._stub.ListSwitches.return_value = health_pb2.ListSwitchesResponse(total_count=0)

        client.list_switches("us-south-2a", collection_id="coll-abc-123")

        req = client._stub.ListSwitches.call_args[0][0]
        assert req.az_id == "us-south-2a"
        assert req.collection_id == "coll-abc-123"
        client.close()


def test_topaz_client_collection_id_defaults_to_empty() -> None:
    """When no collection_id is given the gRPC request must send the proto default ''."""
    mock_grpc = MagicMock()
    mock_grpc.insecure_channel.return_value = MagicMock()
    load_rv = _mock_load_grpc(mock_grpc)
    health_pb2 = load_rv[2]

    with patch("ufm_mcp.topaz_client._load_grpc", return_value=load_rv):
        from ufm_mcp.topaz_client import TopazClient

        client = TopazClient("test:50051")
        client._stub.ListPortCounters.return_value = health_pb2.ListPortCountersResponse(
            total_count=0
        )

        client.list_port_counters("us-south-2a")

        req = client._stub.ListPortCounters.call_args[0][0]
        assert req.collection_id == ""
        client.close()


def test_topaz_client_upload_ibdiagnet() -> None:
    mock_grpc = MagicMock()
    mock_channel = MagicMock()
    mock_grpc.insecure_channel.return_value = mock_channel
    load_rv = _mock_load_grpc(mock_grpc)
    health_pb2 = load_rv[2]

    with patch("ufm_mcp.topaz_client._load_grpc", return_value=load_rv):
        from ufm_mcp.topaz_client import TopazClient

        client = TopazClient("test:50051")
        mock_stub = client._stub

        fake_tarball = b"FAKE_TAR_BYTES"
        mock_response = health_pb2.ImportCollectionResponse(
            success=True,
            message="imported",
            collection_id="coll-abc-123",
        )
        mock_stub.UploadIbdiagnet.return_value = mock_response

        result = client.upload_ibdiagnet(
            az_id="ori-tx",
            tarball_data=fake_tarball,
            filename="ibdiagnet.tar.gz",
        )

        # Verify the stub was called with the right request fields
        call_args = mock_stub.UploadIbdiagnet.call_args
        req = call_args[0][0]
        assert req.tarball_data == fake_tarball
        assert req.az_id == "ori-tx"
        assert req.filename == "ibdiagnet.tar.gz"

        # Verify the response was parsed
        assert result["collection_id"] == "coll-abc-123"
        assert result["success"] is True
        client.close()


def test_topaz_client_upload_ibdiagnet_grpc_error() -> None:
    mock_grpc = MagicMock()
    mock_channel = MagicMock()
    mock_grpc.insecure_channel.return_value = mock_channel
    mock_grpc.RpcError = type("RpcError", (Exception,), {})
    rpc_error = mock_grpc.RpcError("deadline exceeded")
    rpc_error.code = MagicMock(return_value=mock_grpc.StatusCode.DEADLINE_EXCEEDED)
    rpc_error.details = MagicMock(return_value="deadline exceeded")

    with patch("ufm_mcp.topaz_client._load_grpc", return_value=_mock_load_grpc(mock_grpc)):
        from ufm_mcp.topaz_client import TopazClient

        client = TopazClient("test:50051")
        client._stub.UploadIbdiagnet.side_effect = rpc_error

        result = client.upload_ibdiagnet(az_id="ori-tx", tarball_data=b"data")
        assert result["ok"] is False
        assert "UploadIbdiagnet" in result["error"]
        assert result["grpc_details"] == "deadline exceeded"
        client.close()


@pytest.fixture()
def configured_topaz_server():
    """Set up server with mocked TopazClient."""
    import ufm_mcp.server as srv
    from ufm_mcp.site_manager import SiteManager

    settings = Settings(
        ufm_url="https://ufm.example.com/",
        verify_ssl=False,
        timeout_seconds=10,
        topaz_endpoint="test:50051",
    )
    srv.sites = SiteManager()
    srv.sites.configure(settings)
    srv._base_settings = settings

    mock_ufm_client = MagicMock()
    srv.sites._clients["default"] = mock_ufm_client

    yield srv, mock_ufm_client

    srv.sites.close_all()


def test_ufm_topaz_fabric_health_tool(configured_topaz_server) -> None:
    srv, _ = configured_topaz_server
    mock_topaz = MagicMock()
    mock_topaz.get_fabric_health.return_value = {
        "status": "HEALTH_STATUS_HEALTHY",
        "score": 98,
        "total_errors": 2,
        "total_warnings": 5,
        "fabric_summary": {"total_nodes": 100, "switches": 10},
    }
    with patch.object(srv, "_get_topaz_client", return_value=mock_topaz):
        result = srv.ufm_topaz_fabric_health(site="ori")
    assert result["ok"] is True
    assert result["site"] == "ori"
    assert result["az_id"] == "us-south-2a"
    assert result["score"] == 98
    mock_topaz.close.assert_called_once()


def test_ufm_topaz_port_counters_tool(configured_topaz_server) -> None:
    srv, _ = configured_topaz_server
    mock_topaz = MagicMock()
    mock_topaz.list_port_counters.return_value = {
        "port_counters": [{"port": 1, "total_errors": 5}],
        "total_count": 1,
    }
    with patch.object(srv, "_get_topaz_client", return_value=mock_topaz):
        result = srv.ufm_topaz_port_counters(site="ori", errors_only=True)
    assert result["ok"] is True
    assert result["total_count"] == 1
    mock_topaz.close.assert_called_once()


def test_ufm_topaz_cables_tool(configured_topaz_server) -> None:
    srv, _ = configured_topaz_server
    mock_topaz = MagicMock()
    mock_topaz.list_cables.return_value = {
        "cables": [{"vendor": "Mellanox", "temperature_c": 42.5}],
        "total_count": 1,
    }
    with patch.object(srv, "_get_topaz_client", return_value=mock_topaz):
        result = srv.ufm_topaz_cables(site="ori")
    assert result["ok"] is True
    assert result["total_count"] == 1
    mock_topaz.close.assert_called_once()


def test_ufm_topaz_port_counters_tool_forwards_collection_id(configured_topaz_server) -> None:
    """The MCP tool must forward collection_id to the Topaz client (#review)."""
    srv, _ = configured_topaz_server
    mock_topaz = MagicMock()
    mock_topaz.list_port_counters.return_value = {"port_counters": [], "total_count": 0}
    with patch.object(srv, "_get_topaz_client", return_value=mock_topaz):
        srv.ufm_topaz_port_counters(site="ori", collection_id="coll-abc-123")
    mock_topaz.list_port_counters.assert_called_once()
    assert mock_topaz.list_port_counters.call_args.kwargs["collection_id"] == "coll-abc-123"


def test_ufm_topaz_cables_tool_forwards_collection_id(configured_topaz_server) -> None:
    """The MCP tool must forward collection_id to the Topaz client (#review)."""
    srv, _ = configured_topaz_server
    mock_topaz = MagicMock()
    mock_topaz.list_cables.return_value = {"cables": [], "total_count": 0}
    with patch.object(srv, "_get_topaz_client", return_value=mock_topaz):
        srv.ufm_topaz_cables(site="ori", collection_id="coll-abc-123")
    mock_topaz.list_cables.assert_called_once()
    assert mock_topaz.list_cables.call_args.kwargs["collection_id"] == "coll-abc-123"


def test_ufm_topaz_switches_tool_forwards_collection_id(configured_topaz_server) -> None:
    """The MCP tool must forward collection_id to the Topaz client (#review)."""
    srv, _ = configured_topaz_server
    mock_topaz = MagicMock()
    mock_topaz.list_switches.return_value = {"switches": [], "total_count": 0}
    with patch.object(srv, "_get_topaz_client", return_value=mock_topaz):
        srv.ufm_topaz_switches(site="ori", collection_id="coll-abc-123")
    mock_topaz.list_switches.assert_called_once()
    assert mock_topaz.list_switches.call_args.kwargs["collection_id"] == "coll-abc-123"


def test_ufm_topaz_port_counters_truncates(configured_topaz_server) -> None:
    """A multi-thousand-port fabric must be bounded by max_items (token-saving)."""
    srv, _ = configured_topaz_server
    mock_topaz = MagicMock()
    mock_topaz.list_port_counters.return_value = {
        "port_counters": [{"port": i} for i in range(500)],
        "total_count": 500,
    }
    with patch.object(srv, "_get_topaz_client", return_value=mock_topaz):
        result = srv.ufm_topaz_port_counters(site="ori", max_items=10)
    assert result["ok"] is True
    assert result["total_count"] == 500
    assert result["port_counters_truncated"] is True
    assert len(result["port_counters"]) == 10


def test_ufm_topaz_cables_truncates(configured_topaz_server) -> None:
    srv, _ = configured_topaz_server
    mock_topaz = MagicMock()
    mock_topaz.list_cables.return_value = {
        "cables": [{"vendor": "Mellanox", "id": i} for i in range(300)],
        "total_count": 300,
    }
    with patch.object(srv, "_get_topaz_client", return_value=mock_topaz):
        result = srv.ufm_topaz_cables(site="ori", max_items=5)
    assert result["cables_truncated"] is True
    assert len(result["cables"]) == 5
    assert result["total_count"] == 300


def test_ufm_topaz_switches_truncates(configured_topaz_server) -> None:
    srv, _ = configured_topaz_server
    mock_topaz = MagicMock()
    mock_topaz.list_switches.return_value = {
        "switches": [{"guid": f"0x{i:x}"} for i in range(200)],
        "total_count": 200,
    }
    with patch.object(srv, "_get_topaz_client", return_value=mock_topaz):
        result = srv.ufm_topaz_switches(site="ori", max_items=20)
    assert result["switches_truncated"] is True
    assert len(result["switches"]) == 20


def test_ufm_topaz_port_counters_no_truncation_under_cap(configured_topaz_server) -> None:
    srv, _ = configured_topaz_server
    mock_topaz = MagicMock()
    mock_topaz.list_port_counters.return_value = {
        "port_counters": [{"port": 1}, {"port": 2}],
        "total_count": 2,
    }
    with patch.object(srv, "_get_topaz_client", return_value=mock_topaz):
        result = srv.ufm_topaz_port_counters(site="ori", max_items=100)
    assert "port_counters_truncated" not in result
    assert len(result["port_counters"]) == 2


def test_ufm_topaz_unknown_site(configured_topaz_server) -> None:
    srv, _ = configured_topaz_server
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError, match="Unknown Topaz site"):
        srv.ufm_topaz_fabric_health(site="nonexistent")


def test_cli_resolve_topaz_az_uses_cached_settings() -> None:
    """Verify _resolve_topaz_az reads topaz_az_map from cached _cli_settings (#31)."""
    import ufm_mcp.cli as cli_mod
    from ufm_mcp.cli import _resolve_topaz_az

    fake_settings = MagicMock(topaz_az_map={"mysite": "az-123", "other": "az-456"})
    with (
        patch.object(cli_mod, "_cli_settings", fake_settings),
        patch.object(cli_mod, "_initialized", True),
    ):
        assert _resolve_topaz_az("mysite") == "az-123"


def test_cli_resolve_topaz_az_unknown_site_exits() -> None:
    """Verify _resolve_topaz_az exits for unknown sites (#31)."""
    from typer import Exit

    import ufm_mcp.cli as cli_mod
    from ufm_mcp.cli import _resolve_topaz_az

    fake_settings = MagicMock(topaz_az_map={"ori": "az-1"})
    with (
        patch.object(cli_mod, "_cli_settings", fake_settings),
        patch.object(cli_mod, "_initialized", True),
    ):
        with pytest.raises(Exit):
            _resolve_topaz_az("nonexistent")


def test_cli_get_topaz_client_uses_cached_settings() -> None:
    """Verify _get_topaz_client reads topaz_endpoint from cached _cli_settings (#31)."""
    import ufm_mcp.cli as cli_mod
    from ufm_mcp.cli import _get_topaz_client

    fake_settings = MagicMock(topaz_endpoint="grpc.example.com:50051")
    mock_topaz_cls = MagicMock()
    with (
        patch.object(cli_mod, "_cli_settings", fake_settings),
        patch.object(cli_mod, "_initialized", True),
        patch("ufm_mcp.topaz_client.TopazClient", mock_topaz_cls),
    ):
        _get_topaz_client()
    mock_topaz_cls.assert_called_once_with("grpc.example.com:50051")


def test_cli_ensure_init_caches_settings() -> None:
    """Verify _ensure_init stores Settings instance in _cli_settings (#31)."""
    import ufm_mcp.cli as cli_mod

    fake_settings = MagicMock()
    with (
        patch.object(cli_mod, "_initialized", False),
        patch.object(cli_mod, "_cli_settings", None),
        patch("ufm_mcp.cli.Settings", return_value=fake_settings),
        patch.object(cli_mod.sites, "configure"),
    ):
        cli_mod._ensure_init()
        assert cli_mod._cli_settings is fake_settings
