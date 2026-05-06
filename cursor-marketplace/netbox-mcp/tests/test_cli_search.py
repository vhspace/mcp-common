"""Tests for the CLI search command with cluster auto-expansion."""

import json
import os
import sys
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from netbox_mcp.cli import _DEFAULT_SEARCH_TYPES, _output, app

runner = CliRunner(mix_stderr=False)


CLUSTER_RESPONSE = {
    "count": 1,
    "results": [{"id": 615, "name": "cartesia5"}],
}

DEVICE_LIST_RESPONSE = {
    "count": 2,
    "results": [
        {
            "id": 1,
            "name": "node-01",
            "site": {"name": "ORI-TX"},
            "device_type": {"model": "H100"},
            "role": {"name": "gpu-node"},
            "status": {"value": "active"},
        },
        {
            "id": 2,
            "name": "node-02",
            "site": {"name": "VP-TX"},
            "device_type": {"model": "H100"},
            "role": {"name": "gpu-node"},
            "status": {"value": "active"},
        },
    ],
}

EMPTY = {"count": 0, "results": []}


def _mock_search_client(get_side_effect):
    """Patch _client with a custom .get() side_effect function."""
    client = MagicMock()
    client.get.side_effect = get_side_effect
    return patch("netbox_mcp.cli._client", return_value=client)


def _default_mock_get(endpoint, params=None):
    """Return cluster + device data for virtualization/clusters and dcim/devices,
    empty for everything else."""
    if "virtualization/clusters" in endpoint:
        return dict(CLUSTER_RESPONSE)
    if "dcim/devices" in endpoint:
        if params and params.get("cluster_id") == 615:
            return dict(DEVICE_LIST_RESPONSE)
        return dict(EMPTY)
    return dict(EMPTY)


# ── search types ─────────────────────────────────────────────────────


class TestSearchTypes:
    def test_search_includes_clusters_in_default_types(self):
        assert "virtualization.cluster" in _DEFAULT_SEARCH_TYPES

    def test_search_queries_cluster_endpoint(self):
        with _mock_search_client(_default_mock_get) as mock:
            result = runner.invoke(app, ["search", "cartesia5"])
            assert result.exit_code == 0
            endpoints_called = [call.args[0] for call in mock.return_value.get.call_args_list]
            assert "virtualization/clusters" in endpoints_called


# ── cluster expansion ────────────────────────────────────────────────


class TestSearchClusterExpansion:
    def test_search_expands_cluster_to_devices(self):
        with _mock_search_client(_default_mock_get):
            result = runner.invoke(app, ["search", "cartesia5"])
            assert result.exit_code == 0
            assert "Cluster: cartesia5" in result.output
            assert "node-01" in result.output
            assert "node-02" in result.output
            assert "ORI-TX" in result.output
            assert "VP-TX" in result.output

    def test_search_cluster_with_status_filter(self):
        def mock_get(endpoint, params=None):
            if "virtualization/clusters" in endpoint:
                return dict(CLUSTER_RESPONSE)
            if "dcim/devices" in endpoint:
                if params and params.get("cluster_id") == 615:
                    assert params.get("status") == "active"
                    return dict(DEVICE_LIST_RESPONSE)
                return dict(EMPTY)
            return dict(EMPTY)

        with _mock_search_client(mock_get):
            result = runner.invoke(app, ["search", "cartesia5", "--status", "active"])
            assert result.exit_code == 0
            assert "status=active" in result.output

    def test_search_cluster_shows_device_count_and_sites(self):
        with _mock_search_client(_default_mock_get):
            result = runner.invoke(app, ["search", "cartesia5"])
            assert result.exit_code == 0
            assert "2 devices" in result.output
            assert "Sites:" in result.output

    def test_search_cluster_hint_when_truncated(self):
        """When device count > shown results, a hint should appear."""

        def mock_get(endpoint, params=None):
            if "virtualization/clusters" in endpoint:
                return dict(CLUSTER_RESPONSE)
            if "dcim/devices" in endpoint:
                if params and params.get("cluster_id") == 615:
                    return {
                        "count": 100,
                        "results": DEVICE_LIST_RESPONSE["results"][:1],
                    }
                return dict(EMPTY)
            return dict(EMPTY)

        with _mock_search_client(mock_get):
            result = runner.invoke(app, ["search", "cartesia5"])
            assert result.exit_code == 0
            assert "100 devices" in result.output
            assert "netbox-cli devices --cluster cartesia5" in result.output

    def test_search_cluster_expansion_failure_falls_back(self):
        """If device expansion fails, cluster is shown as plain result."""

        def mock_get(endpoint, params=None):
            if "virtualization/clusters" in endpoint:
                return dict(CLUSTER_RESPONSE)
            if "dcim/devices" in endpoint and params and params.get("cluster_id"):
                raise ConnectionError("API down")
            return dict(EMPTY)

        with _mock_search_client(mock_get):
            result = runner.invoke(app, ["search", "cartesia5"])
            assert result.exit_code == 0
            assert "virtualization.cluster" in result.output
            assert "[615] cartesia5" in result.output
            assert "Cluster:" not in result.output

    def test_search_single_device_grammar(self):
        """When cluster has exactly 1 device, output should say 'device' not 'devices'."""

        def mock_get(endpoint, params=None):
            if "virtualization/clusters" in endpoint:
                return dict(CLUSTER_RESPONSE)
            if "dcim/devices" in endpoint:
                if params and params.get("cluster_id") == 615:
                    return {
                        "count": 1,
                        "results": [DEVICE_LIST_RESPONSE["results"][0]],
                    }
                return dict(EMPTY)
            return dict(EMPTY)

        with _mock_search_client(mock_get):
            result = runner.invoke(app, ["search", "cartesia5"])
            assert result.exit_code == 0
            assert "1 device (" in result.output
            assert "1 devices" not in result.output


# ── JSON output ──────────────────────────────────────────────────────


class TestSearchClusterJson:
    def test_search_cluster_json_output(self):
        with _mock_search_client(_default_mock_get):
            result = runner.invoke(app, ["search", "cartesia5", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert "cluster_devices" in data
            cd = data["cluster_devices"]["cartesia5"]
            assert cd["count"] == 2
            assert "ORI-TX" in cd["sites"]
            assert len(cd["results"]) == 2

    def test_search_no_cluster_json_has_no_cluster_devices_key(self):
        def mock_get(endpoint, params=None):
            return dict(EMPTY)

        with _mock_search_client(mock_get):
            result = runner.invoke(app, ["search", "nonexistent", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert "cluster_devices" not in data


# ── no cluster match ─────────────────────────────────────────────────


class TestSearchNoCluster:
    def test_search_no_cluster_match_unchanged(self):
        """Without cluster matches, search behaves as before."""
        device_resp = {
            "count": 1,
            "results": [{"id": 10, "name": "myhost", "display": "myhost"}],
        }

        def mock_get(endpoint, params=None):
            if "dcim/devices" in endpoint:
                return dict(device_resp)
            return dict(EMPTY)

        with _mock_search_client(mock_get):
            result = runner.invoke(app, ["search", "myhost"])
            assert result.exit_code == 0
            assert "dcim.device (1)" in result.output
            assert "[10] myhost" in result.output
            assert "Cluster:" not in result.output

    def test_search_no_results_at_all(self):
        def mock_get(endpoint, params=None):
            return dict(EMPTY)

        with _mock_search_client(mock_get):
            result = runner.invoke(app, ["search", "zzz_nothing"])
            assert result.exit_code == 0
            assert "No results found." in result.output

    def test_search_status_flag_ignored_without_cluster(self):
        """--status should not affect non-cluster search results."""
        device_resp = {
            "count": 1,
            "results": [{"id": 10, "name": "myhost", "display": "myhost"}],
        }

        def mock_get(endpoint, params=None):
            if "dcim/devices" in endpoint:
                assert "status" not in (params or {}), (
                    "status should not be in initial search params"
                )
                return dict(device_resp)
            return dict(EMPTY)

        with _mock_search_client(mock_get):
            result = runner.invoke(app, ["search", "myhost", "--status", "active"])
            assert result.exit_code == 0
            assert "[10] myhost" in result.output


# ── pagination header ────────────────────────────────────────────────


class TestPaginationHeader:
    def test_pagination_header_shows_showing_count(self):
        """When results are truncated, header should say 'showing N'."""
        paginated = {
            "count": 100,
            "next": "http://netbox/api/dcim/devices/?limit=5&offset=5",
            "results": [{"id": i, "name": f"dev{i}"} for i in range(5)],
        }

        captured = StringIO()
        old_stdout = sys.stdout
        try:
            sys.stdout = captured
            _output(paginated)
        finally:
            sys.stdout = old_stdout

        output = captured.getvalue()
        assert "100 result(s)" in output
        assert "showing 5" in output

    def test_no_pagination_header_when_all_shown(self):
        full = {
            "count": 2,
            "results": [
                {"id": 1, "name": "dev1"},
                {"id": 2, "name": "dev2"},
            ],
        }

        captured = StringIO()
        old_stdout = sys.stdout
        try:
            sys.stdout = captured
            _output(full)
        finally:
            sys.stdout = old_stdout

        output = captured.getvalue()
        assert "2 result(s)" in output
        assert "showing" not in output


# ── help text ────────────────────────────────────────────────────────


class TestHelpText:
    def test_lookup_help_text(self):
        result = runner.invoke(app, ["lookup", "--help"])
        assert result.exit_code == 0
        assert "hostname" in result.output.lower()
        assert "provider" in result.output.lower()

    def test_search_help_text(self):
        result = runner.invoke(app, ["search", "--help"])
        assert result.exit_code == 0
        assert "cluster" in result.output.lower()
        assert "lookup" in result.output.lower()


# ── E2E tests (require live NetBox) ──────────────────────────────────


_skip_no_netbox = pytest.mark.skipif(not os.environ.get("NETBOX_URL"), reason="NETBOX_URL not set")


@pytest.mark.e2e
@_skip_no_netbox
def test_search_cluster_e2e():
    """E2E: search for a known cluster should return devices."""
    result = runner.invoke(app, ["search", "cartesia5", "--status", "active"])
    assert result.exit_code == 0
    assert "Cluster: cartesia5" in result.output or "cartesia5" in result.output


@pytest.mark.e2e
@_skip_no_netbox
def test_search_research_common_e2e():
    """E2E: search for research-common-h100 should show cluster with devices."""
    result = runner.invoke(
        app, ["search", "research-common-h100", "--status", "active", "--limit", "5"]
    )
    assert result.exit_code == 0
    assert "Cluster:" in result.output
    assert "devices" in result.output.lower()
