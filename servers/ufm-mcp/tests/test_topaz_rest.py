"""Tests for TopazRestClient."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from ufm_mcp.topaz_rest_client import TopazRestClient


@pytest.fixture()
def mock_transport():
    """Provide an httpx.MockTransport that can be configured per-test."""
    responses: dict[str, httpx.Response] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path in responses:
            return responses[path]
        return httpx.Response(404, json={"error": "not found"})

    transport = httpx.MockTransport(handler)
    return transport, responses


def _make_client(transport) -> TopazRestClient:
    client = TopazRestClient("https://topaz.test")
    client._client = httpx.Client(
        base_url="https://topaz.test",
        transport=transport,
        timeout=5,
    )
    return client


# ------------------------------------------------------------------
# list_availability_zones / discover_az_map
# ------------------------------------------------------------------


class TestListAvailabilityZones:
    def test_returns_list_of_azs(self, mock_transport):
        transport, responses = mock_transport
        responses["/api/az"] = httpx.Response(
            200,
            json=[
                {"id": "us-south-2a", "name": "ori", "connected": True},
                {"id": "us-central-8a", "name": "5c_oh1", "connected": True},
            ],
        )
        client = _make_client(transport)
        azs = client.list_availability_zones()
        assert len(azs) == 2
        assert azs[0]["name"] == "ori"
        assert azs[1]["id"] == "us-central-8a"
        client.close()

    def test_returns_error_on_failure(self, mock_transport):
        transport, responses = mock_transport
        responses["/api/az"] = httpx.Response(500, json={"error": "internal"})
        client = _make_client(transport)
        azs = client.list_availability_zones()
        assert len(azs) == 1
        assert azs[0]["ok"] is False
        client.close()


class TestDiscoverAzMap:
    def test_builds_mapping(self, mock_transport):
        transport, responses = mock_transport
        responses["/api/az"] = httpx.Response(
            200,
            json=[
                {"id": "us-south-2a", "name": "ori"},
                {"id": "us-central-4a", "name": "apld2"},
            ],
        )
        client = _make_client(transport)
        mapping = client.discover_az_map()
        assert mapping == {"ori": "us-south-2a", "apld2": "us-central-4a"}
        client.close()

    def test_empty_on_failure(self, mock_transport):
        transport, responses = mock_transport
        responses["/api/az"] = httpx.Response(500)
        client = _make_client(transport)
        mapping = client.discover_az_map()
        assert mapping == {}
        client.close()

    def test_handles_az_id_key(self, mock_transport):
        """Some API versions use ``azId`` instead of ``id``."""
        transport, responses = mock_transport
        responses["/api/az"] = httpx.Response(
            200,
            json=[{"azId": "ca-west-1a", "name": "iren_b300"}],
        )
        client = _make_client(transport)
        mapping = client.discover_az_map()
        assert mapping == {"iren_b300": "ca-west-1a"}
        client.close()


# ------------------------------------------------------------------
# get_fabric_health
# ------------------------------------------------------------------


class TestGetFabricHealth:
    def test_success(self, mock_transport):
        transport, responses = mock_transport
        responses["/api/health"] = httpx.Response(
            200,
            json={"status": "HEALTHY", "score": 95, "total_errors": 3},
        )
        client = _make_client(transport)
        result = client.get_fabric_health("us-south-2a")
        assert result["status"] == "HEALTHY"
        assert result["score"] == 95
        client.close()

    def test_http_error(self, mock_transport):
        transport, responses = mock_transport
        responses["/api/health"] = httpx.Response(502, text="Bad Gateway")
        client = _make_client(transport)
        result = client.get_fabric_health("bad-az")
        assert result["ok"] is False
        assert result["http_status"] == 502
        client.close()


# ------------------------------------------------------------------
# list_switches
# ------------------------------------------------------------------


class TestListSwitches:
    def test_filters_switches_from_topology(self, mock_transport):
        transport, responses = mock_transport
        responses["/api/topology/server"] = httpx.Response(
            200,
            json={
                "nodes": [
                    {"type": "switch", "description": "leaf01", "guid": "aaa", "total_errors": 0},
                    {"type": "switch", "description": "spine01", "guid": "bbb", "total_errors": 5},
                    {"type": "host", "description": "gpu-node01", "guid": "ccc", "total_errors": 0},
                ],
                "links": [],
            },
        )
        client = _make_client(transport)
        result = client.list_switches("us-south-2a")
        assert result["total_count"] == 2
        assert all(s["type"] == "switch" for s in result["switches"])
        client.close()

    def test_errors_only(self, mock_transport):
        transport, responses = mock_transport
        responses["/api/topology/server"] = httpx.Response(
            200,
            json={
                "nodes": [
                    {"type": "switch", "description": "ok-sw", "total_errors": 0},
                    {"type": "switch", "description": "bad-sw", "total_errors": 3},
                ],
                "links": [],
            },
        )
        client = _make_client(transport)
        result = client.list_switches("us-south-2a", errors_only=True)
        assert result["total_count"] == 1
        assert result["switches"][0]["description"] == "bad-sw"
        client.close()

    def test_collection_id_rejected(self, mock_transport):
        """REST cannot scope to a collection; it must fail loudly, not return live data."""
        transport, _ = mock_transport
        client = _make_client(transport)
        result = client.list_switches("us-south-2a", collection_id="coll-abc-123")
        assert result["ok"] is False
        assert "collection_id is not supported" in result["error"]
        client.close()


# ------------------------------------------------------------------
# list_port_counters
# ------------------------------------------------------------------


class TestListPortCounters:
    def test_returns_links_as_counters(self, mock_transport):
        transport, responses = mock_transport
        responses["/api/topology/server"] = httpx.Response(
            200,
            json={
                "nodes": [],
                "links": [
                    {"port": 1, "guid": "aaa", "total_errors": 2},
                    {"port": 2, "guid": "bbb", "total_errors": 0},
                ],
            },
        )
        client = _make_client(transport)
        result = client.list_port_counters("us-south-2a")
        assert result["total_count"] == 2
        client.close()

    def test_errors_only_filter(self, mock_transport):
        transport, responses = mock_transport
        responses["/api/topology/server"] = httpx.Response(
            200,
            json={
                "nodes": [],
                "links": [
                    {"port": 1, "guid": "aaa", "total_errors": 2},
                    {"port": 2, "guid": "bbb", "total_errors": 0},
                ],
            },
        )
        client = _make_client(transport)
        result = client.list_port_counters("us-south-2a", errors_only=True)
        assert result["total_count"] == 1
        client.close()

    def test_guid_filter(self, mock_transport):
        transport, responses = mock_transport
        responses["/api/topology/server"] = httpx.Response(
            200,
            json={
                "nodes": [],
                "links": [
                    {"port": 1, "guid": "aaa111", "total_errors": 1},
                    {"port": 2, "guid": "bbb222", "total_errors": 1},
                ],
            },
        )
        client = _make_client(transport)
        result = client.list_port_counters("us-south-2a", guid_filter="aaa111")
        assert result["total_count"] == 1
        assert result["port_counters"][0]["guid"] == "aaa111"
        client.close()

    def test_collection_id_rejected(self, mock_transport):
        """REST cannot scope to a collection; it must fail loudly, not return live data."""
        transport, _ = mock_transport
        client = _make_client(transport)
        result = client.list_port_counters("us-south-2a", collection_id="coll-abc-123")
        assert result["ok"] is False
        assert "collection_id is not supported" in result["error"]
        client.close()


# ------------------------------------------------------------------
# list_cables (unsupported via REST)
# ------------------------------------------------------------------


class TestListCables:
    def test_returns_not_available(self, mock_transport):
        transport, _ = mock_transport
        client = _make_client(transport)
        result = client.list_cables("us-south-2a")
        assert result["ok"] is False
        assert "not available" in result["error"]
        assert result["total_count"] == 0
        client.close()


# ------------------------------------------------------------------
# upload_ibdiagnet (unsupported via REST)
# ------------------------------------------------------------------


class TestUploadIbdiagnet:
    def test_returns_not_available(self, mock_transport):
        transport, _ = mock_transport
        client = _make_client(transport)
        result = client.upload_ibdiagnet("az-1", b"data", "test.tar.gz")
        assert result["ok"] is False
        assert "not available" in result["error"]
        client.close()


# ------------------------------------------------------------------
# Config: new settings
# ------------------------------------------------------------------


class TestConfigSettings:
    def test_topaz_rest_url_default(self):
        from ufm_mcp.config import Settings

        s = Settings(ufm_url="https://ufm.example.com/", verify_ssl=False)
        assert s.topaz_rest_url == "https://topaz.internal.together.ai"

    def test_topaz_transport_default(self):
        from ufm_mcp.config import Settings

        s = Settings(ufm_url="https://ufm.example.com/", verify_ssl=False)
        assert s.topaz_transport == "auto"

    def test_topaz_transport_validation(self):
        from ufm_mcp.config import Settings

        with pytest.raises(ValueError, match="TOPAZ_TRANSPORT must be one of"):
            Settings(
                ufm_url="https://ufm.example.com/",
                verify_ssl=False,
                topaz_transport="invalid",
            )

    def test_topaz_transport_rest(self):
        from ufm_mcp.config import Settings

        s = Settings(
            ufm_url="https://ufm.example.com/",
            verify_ssl=False,
            topaz_transport="rest",
        )
        assert s.topaz_transport == "rest"

    def test_topaz_transport_grpc(self):
        from ufm_mcp.config import Settings

        s = Settings(
            ufm_url="https://ufm.example.com/",
            verify_ssl=False,
            topaz_transport="grpc",
        )
        assert s.topaz_transport == "grpc"


# ------------------------------------------------------------------
# CLI: _get_topaz_client transport selection
# ------------------------------------------------------------------


class TestGetTopazClientTransport:
    def test_rest_transport(self):
        import ufm_mcp.cli as cli_mod

        fake_settings = MagicMock(
            topaz_transport="rest",
            topaz_rest_url="https://topaz.test",
        )
        with (
            patch.object(cli_mod, "_cli_settings", fake_settings),
            patch.object(cli_mod, "_initialized", True),
        ):
            client = cli_mod._get_topaz_client()
        assert type(client).__name__ == "TopazRestClient"
        client.close()

    def test_grpc_transport(self):
        import ufm_mcp.cli as cli_mod

        fake_settings = MagicMock(
            topaz_transport="grpc",
            topaz_endpoint="grpc.test:50051",
        )
        mock_topaz_cls = MagicMock()
        with (
            patch.object(cli_mod, "_cli_settings", fake_settings),
            patch.object(cli_mod, "_initialized", True),
            patch(
                "ufm_mcp.topaz_client.TopazClient",
                mock_topaz_cls,
            ),
        ):
            result = cli_mod._get_topaz_client()
        mock_topaz_cls.assert_called_once_with("grpc.test:50051")
        assert result is mock_topaz_cls.return_value

    def test_auto_transport_rest_succeeds(self):
        """auto mode uses REST when probe succeeds."""
        import ufm_mcp.cli as cli_mod

        fake_settings = MagicMock(
            topaz_transport="auto",
            topaz_rest_url="https://topaz.test",
            topaz_endpoint="localhost:50051",
        )
        mock_rest_client = MagicMock()
        mock_rest_cls = MagicMock(
            return_value=mock_rest_client,
        )

        with (
            patch.object(
                cli_mod,
                "_cli_settings",
                fake_settings,
            ),
            patch.object(cli_mod, "_initialized", True),
            patch(
                "ufm_mcp.cli.TopazRestClient",
                mock_rest_cls,
                create=True,
            ),
            patch(
                "ufm_mcp.topaz_rest_client.TopazRestClient",
                mock_rest_cls,
            ),
        ):
            client = cli_mod._get_topaz_client()
        assert client is mock_rest_client

    def test_auto_rest_fails_falls_back_to_grpc(self):
        """auto falls back to gRPC when REST fails."""
        import ufm_mcp.cli as cli_mod

        fake_settings = MagicMock(
            topaz_transport="auto",
            topaz_rest_url="https://topaz.test",
            topaz_endpoint="grpc.test:50051",
        )
        mock_rest_client = MagicMock()
        mock_rest_client._client.get.side_effect = Exception("connection refused")
        mock_rest_cls = MagicMock(
            return_value=mock_rest_client,
        )
        mock_grpc_cls = MagicMock()

        with (
            patch.object(
                cli_mod,
                "_cli_settings",
                fake_settings,
            ),
            patch.object(cli_mod, "_initialized", True),
            patch(
                "ufm_mcp.topaz_rest_client.TopazRestClient",
                mock_rest_cls,
            ),
            patch(
                "ufm_mcp.topaz_client.TopazClient",
                mock_grpc_cls,
            ),
        ):
            client = cli_mod._get_topaz_client()
        mock_grpc_cls.assert_called_once_with(
            "grpc.test:50051",
        )
        assert client is mock_grpc_cls.return_value


# ------------------------------------------------------------------
# CLI: _resolve_topaz_az auto-discovery
# ------------------------------------------------------------------


class TestResolveTopazAzAutoDiscovery:
    def test_known_site_uses_static_map(self):
        import ufm_mcp.cli as cli_mod

        fake_settings = MagicMock(topaz_az_map={"ori": "us-south-2a"})
        with (
            patch.object(cli_mod, "_cli_settings", fake_settings),
            patch.object(cli_mod, "_initialized", True),
        ):
            assert cli_mod._resolve_topaz_az("ori") == "us-south-2a"

    def test_unknown_site_discovers_from_rest(self):
        import ufm_mcp.cli as cli_mod

        fake_settings = MagicMock(
            topaz_az_map={"ori": "us-south-2a"},
            topaz_rest_url="https://topaz.test",
        )
        mock_rest = MagicMock()
        mock_rest.discover_az_map.return_value = {
            "new_site": "us-west-9a",
        }
        mock_rest_cls = MagicMock(return_value=mock_rest)

        with (
            patch.object(
                cli_mod,
                "_cli_settings",
                fake_settings,
            ),
            patch.object(cli_mod, "_initialized", True),
            patch(
                "ufm_mcp.topaz_rest_client.TopazRestClient",
                mock_rest_cls,
            ),
        ):
            result = cli_mod._resolve_topaz_az("new_site")
            assert result == "us-west-9a"

    def test_unknown_site_rest_fails_exits(self):
        from typer import Exit

        import ufm_mcp.cli as cli_mod

        fake_settings = MagicMock(
            topaz_az_map={"ori": "us-south-2a"},
            topaz_rest_url="https://topaz.test",
        )
        mock_rest_cls = MagicMock(
            side_effect=Exception("unreachable"),
        )

        with (
            patch.object(
                cli_mod,
                "_cli_settings",
                fake_settings,
            ),
            patch.object(cli_mod, "_initialized", True),
            patch(
                "ufm_mcp.topaz_rest_client.TopazRestClient",
                mock_rest_cls,
            ),
        ):
            with pytest.raises(Exit):
                cli_mod._resolve_topaz_az("nonexistent")


# ------------------------------------------------------------------
# CLI: topaz-list-azs command
# ------------------------------------------------------------------


class TestTopazListAzsCli:
    def test_json_output(self):
        import json

        from typer.testing import CliRunner

        import ufm_mcp.cli as cli_mod
        from ufm_mcp.cli import app

        runner = CliRunner()
        mock_rest = MagicMock()
        mock_rest.list_availability_zones.return_value = [
            {"id": "us-south-2a", "name": "ori", "connected": True},
        ]
        fake_settings = MagicMock(
            topaz_rest_url="https://topaz.test",
        )

        with (
            patch("ufm_mcp.cli._ensure_init"),
            patch.object(cli_mod, "_cli_settings", fake_settings),
            patch(
                "ufm_mcp.topaz_rest_client.TopazRestClient",
                return_value=mock_rest,
            ),
        ):
            result = runner.invoke(app, ["topaz-list-azs", "--json"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert len(payload) == 1
        assert payload[0]["name"] == "ori"

    def test_table_output(self):
        from typer.testing import CliRunner

        import ufm_mcp.cli as cli_mod
        from ufm_mcp.cli import app

        runner = CliRunner()
        mock_rest = MagicMock()
        mock_rest.list_availability_zones.return_value = [
            {
                "id": "us-south-2a",
                "name": "ori",
                "connected": True,
            },
            {
                "id": "us-central-8a",
                "name": "5c_oh1",
                "connected": False,
            },
        ]
        fake_settings = MagicMock(
            topaz_rest_url="https://topaz.test",
        )

        with (
            patch("ufm_mcp.cli._ensure_init"),
            patch.object(cli_mod, "_cli_settings", fake_settings),
            patch(
                "ufm_mcp.topaz_rest_client.TopazRestClient",
                return_value=mock_rest,
            ),
        ):
            result = runner.invoke(app, ["topaz-list-azs"])

        assert result.exit_code == 0, result.output
        assert "ori" in result.output
        assert "us-south-2a" in result.output
        assert "5c_oh1" in result.output


# ------------------------------------------------------------------
# gRPC error hint for UNAVAILABLE
# ------------------------------------------------------------------


class TestGrpcErrorHint:
    def test_unavailable_includes_hint(self):
        from ufm_mcp.topaz_client import _grpc_error_dict

        class FakeExc:
            def code(self):
                return "UNAVAILABLE"

            def details(self):
                return "Connection refused"

        result = _grpc_error_dict("GetFabricHealth", FakeExc())
        assert result["ok"] is False
        assert "hint" in result
        assert "TOPAZ_TRANSPORT=rest" in result["hint"]

    def test_non_unavailable_no_hint(self):
        from ufm_mcp.topaz_client import _grpc_error_dict

        class FakeExc:
            def code(self):
                return "INTERNAL"

            def details(self):
                return "Something broke"

        result = _grpc_error_dict("GetFabricHealth", FakeExc())
        assert result["ok"] is False
        assert "hint" not in result
