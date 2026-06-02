"""Tests for CLI convenience alias commands."""

import json
from unittest.mock import MagicMock, patch

from mcp_common.testing.dual_mode import make_cli_runner

from netbox_mcp.cli import _format_device_line, app

runner = make_cli_runner()

EMPTY_RESPONSE = {"count": 0, "results": []}
DEVICE_RESPONSE = {
    "count": 1,
    "results": [
        {
            "id": 1,
            "name": "node01",
            "status": {"value": "active"},
            "site": {"name": "ORI-TX"},
            "device_type": {"model": "H100-80GB-SXM-8x"},
            "role": {"name": "gpu-node"},
        }
    ],
}

CLUSTER_LOOKUP = {"count": 1, "results": [{"id": 10, "name": "research-common-h100"}]}
SITE_LOOKUP = {"count": 1, "results": [{"id": 5, "name": "ORI-TX"}]}


def _mock_client(response=None, *, cluster_lookup=None, site_lookup=None):
    """Create a patched _client that returns *response* from .get().

    When *cluster_lookup* or *site_lookup* are provided the mock dispatches
    different responses based on the endpoint being queried, so resolution
    helpers (``_resolve_cluster_id``, ``_resolve_site_id``) work correctly.
    """
    default = response or EMPTY_RESPONSE
    client = MagicMock()

    if cluster_lookup or site_lookup:

        def _side_effect(endpoint, **kwargs):
            if "virtualization/clusters" in endpoint and cluster_lookup:
                return cluster_lookup
            if "dcim/sites" in endpoint and site_lookup:
                return site_lookup
            return default

        client.get.side_effect = _side_effect
    else:
        client.get.return_value = default

    return patch("netbox_mcp.cli._client", return_value=client)


# ── devices ──────────────────────────────────────────────────────────


class TestDevicesAlias:
    def test_basic_json(self):
        with _mock_client(DEVICE_RESPONSE) as mock:
            result = runner.invoke(app, ["devices", "--json"])
            assert result.exit_code == 0
            mock.return_value.get.assert_called_once()
            call_kwargs = mock.return_value.get.call_args
            assert "dcim/devices" in call_kwargs.args[0]

    def test_cluster_filter(self):
        with _mock_client(cluster_lookup=CLUSTER_LOOKUP) as mock:
            result = runner.invoke(app, ["devices", "--cluster", "research-common-h100", "--json"])
            assert result.exit_code == 0
            params = mock.return_value.get.call_args[1]["params"]
            assert params["cluster_id"] == "10"

    def test_site_filter(self):
        with _mock_client(site_lookup=SITE_LOOKUP) as mock:
            result = runner.invoke(app, ["devices", "--site", "ORI-TX", "--json"])
            assert result.exit_code == 0
            params = mock.return_value.get.call_args[1]["params"]
            assert params["site_id"] == "5"

    def test_status_filter(self):
        with _mock_client() as mock:
            result = runner.invoke(app, ["devices", "--status", "active", "--json"])
            assert result.exit_code == 0
            params = mock.return_value.get.call_args[1]["params"]
            assert params["status"] == "active"

    def test_role_filter(self):
        with _mock_client() as mock:
            result = runner.invoke(app, ["devices", "--role", "gpu-node", "--json"])
            assert result.exit_code == 0
            params = mock.return_value.get.call_args[1]["params"]
            assert params["role"] == "gpu-node"

    def test_combined_filters(self):
        c1_lookup = {"count": 1, "results": [{"id": 11, "name": "c1"}]}
        s1_lookup = {"count": 1, "results": [{"id": 6, "name": "s1"}]}
        with _mock_client(cluster_lookup=c1_lookup, site_lookup=s1_lookup) as mock:
            result = runner.invoke(
                app,
                [
                    "devices",
                    "--cluster",
                    "c1",
                    "--site",
                    "s1",
                    "--status",
                    "active",
                    "--role",
                    "gpu-node",
                ],
            )
            assert result.exit_code == 0
            params = mock.return_value.get.call_args[1]["params"]
            assert params["cluster_id"] == "11"
            assert params["site_id"] == "6"
            assert params["status"] == "active"
            assert params["role"] == "gpu-node"

    def test_default_limit_is_200(self):
        with _mock_client() as mock:
            runner.invoke(app, ["devices"])
            params = mock.return_value.get.call_args[1]["params"]
            assert params["limit"] == 200

    def test_custom_limit(self):
        with _mock_client() as mock:
            runner.invoke(app, ["devices", "--limit", "5"])
            params = mock.return_value.get.call_args[1]["params"]
            assert params["limit"] == 5

    def test_extra_filter_flag_comma_separated(self):
        with _mock_client() as mock:
            runner.invoke(app, ["devices", "--filter", "tenant=acme,rack_id=10"])
            params = mock.return_value.get.call_args[1]["params"]
            assert params["tenant"] == "acme"
            assert params["rack_id"] == "10"

    def test_multiple_filter_flags(self):
        """Multiple --filter flags should produce AND semantics."""
        with _mock_client() as mock:
            runner.invoke(
                app, ["devices", "--filter", "site=ori-tx", "--filter", "cluster=cartesia5"]
            )
            params = mock.return_value.get.call_args[1]["params"]
            assert params["site"] == "ori-tx"
            assert params["cluster"] == "cartesia5"

    def test_filter_preserves_comma_in_value(self):
        """Commas within a value should be preserved (NetBox OR semantics)."""
        with _mock_client() as mock:
            runner.invoke(app, ["devices", "--filter", "status=active,planned"])
            params = mock.return_value.get.call_args[1]["params"]
            assert params["status"] == "active,planned"

    def test_fields_flag(self):
        with _mock_client() as mock:
            runner.invoke(app, ["devices", "--fields", "id,name,site"])
            params = mock.return_value.get.call_args[1]["params"]
            assert params["fields"] == "id,name,site"

    def test_brief_flag(self):
        with _mock_client() as mock:
            runner.invoke(app, ["devices", "--brief"])
            params = mock.return_value.get.call_args[1]["params"]
            assert params["brief"] == 1

    def test_compact_text_output(self):
        with _mock_client(DEVICE_RESPONSE):
            result = runner.invoke(app, ["devices"])
            assert result.exit_code == 0
            assert "node01" in result.output
            assert "1 result(s)" in result.output

    def test_site_not_found(self):
        with _mock_client():
            result = runner.invoke(app, ["devices", "--site", "NO-SUCH-SITE"])
            assert result.exit_code == 1
            assert "not found" in result.stderr


# ── list command ─────────────────────────────────────────────────────


class TestListCommand:
    def test_default_limit_is_100(self):
        with _mock_client() as mock:
            runner.invoke(app, ["list", "dcim.device"])
            params = mock.return_value.get.call_args[1]["params"]
            assert params["limit"] == 100

    def test_custom_limit(self):
        with _mock_client() as mock:
            runner.invoke(app, ["list", "dcim.device", "--limit", "10"])
            params = mock.return_value.get.call_args[1]["params"]
            assert params["limit"] == 10


# ── truncation hint ──────────────────────────────────────────────────


class TestTruncationHint:
    def test_truncated_output_shows_limit_hint(self):
        truncated_response = {
            "count": 66,
            "results": [
                {
                    "id": i,
                    "name": f"node-{i:02d}",
                    "status": {"value": "active"},
                    "site": {"name": "ORI-TX"},
                    "device_type": {"model": "H100"},
                    "role": {"name": "gpu-node"},
                }
                for i in range(5)
            ],
        }
        with _mock_client(truncated_response):
            result = runner.invoke(app, ["devices"])
            assert "66 result(s) (showing 5)" in result.output
            assert "--limit 66" in result.output

    def test_non_truncated_output_has_no_limit_hint(self):
        with _mock_client(DEVICE_RESPONSE):
            result = runner.invoke(app, ["devices"])
            assert "1 result(s)" in result.output
            assert "--limit" not in result.output

    # The hand-written ``lookup`` command was removed when
    # ``netbox_lookup_device`` migrated to ``@dual_mode_tool``; the
    # synthesized ``lookup-device`` command emits raw JSON via
    # ``echo_result`` rather than the legacy paginated-list formatting,
    # so the legacy "showing N" + ``--limit`` hint test no longer applies
    # and is covered by ``tests/unit/test_dual_mode_migration.py``.


# ── sites ────────────────────────────────────────────────────────────


class TestSitesAlias:
    def test_basic(self):
        with _mock_client() as mock:
            result = runner.invoke(app, ["sites", "--json"])
            assert result.exit_code == 0
            call_kwargs = mock.return_value.get.call_args
            assert "dcim/sites" in call_kwargs.args[0]

    def test_status_filter(self):
        with _mock_client() as mock:
            runner.invoke(app, ["sites", "--status", "active"])
            params = mock.return_value.get.call_args[1]["params"]
            assert params["status"] == "active"

    def test_region_filter(self):
        with _mock_client() as mock:
            runner.invoke(app, ["sites", "--region", "us-central"])
            params = mock.return_value.get.call_args[1]["params"]
            assert params["region"] == "us-central"

    def test_default_limit_is_200(self):
        with _mock_client() as mock:
            runner.invoke(app, ["sites"])
            params = mock.return_value.get.call_args[1]["params"]
            assert params["limit"] == 200


# ── clusters ─────────────────────────────────────────────────────────


class TestClustersAlias:
    def test_basic(self):
        with _mock_client() as mock:
            result = runner.invoke(app, ["clusters", "--json"])
            assert result.exit_code == 0
            call_kwargs = mock.return_value.get.call_args
            assert "virtualization/clusters" in call_kwargs.args[0]

    def test_site_filter(self):
        with _mock_client(site_lookup=SITE_LOOKUP) as mock:
            runner.invoke(app, ["clusters", "--site", "ORI-TX"])
            params = mock.return_value.get.call_args[1]["params"]
            assert params["site_id"] == "5"

    def test_type_filter(self):
        with _mock_client() as mock:
            runner.invoke(app, ["clusters", "--type", "gpu"])
            params = mock.return_value.get.call_args[1]["params"]
            assert params["type"] == "gpu"

    def test_default_limit_is_200(self):
        with _mock_client() as mock:
            runner.invoke(app, ["clusters"])
            params = mock.return_value.get.call_args[1]["params"]
            assert params["limit"] == 200


# ── ips ──────────────────────────────────────────────────────────────


class TestIpsAlias:
    def test_basic(self):
        with _mock_client() as mock:
            result = runner.invoke(app, ["ips", "--json"])
            assert result.exit_code == 0
            call_kwargs = mock.return_value.get.call_args
            assert "ip-addresses" in call_kwargs.args[0]

    def test_device_filter(self):
        with _mock_client() as mock:
            runner.invoke(app, ["ips", "--device", "node01"])
            params = mock.return_value.get.call_args[1]["params"]
            assert params["device"] == "node01"

    def test_interface_filter(self):
        with _mock_client() as mock:
            runner.invoke(app, ["ips", "--interface", "eth0"])
            params = mock.return_value.get.call_args[1]["params"]
            assert params["interface"] == "eth0"

    def test_default_limit_is_200(self):
        with _mock_client() as mock:
            runner.invoke(app, ["ips"])
            params = mock.return_value.get.call_args[1]["params"]
            assert params["limit"] == 200


# ── lookup → migrated to ``@dual_mode_tool`` ──────────────────────────
#
# The hand-written ``lookup`` command was removed when
# ``netbox_lookup_device`` migrated to ``@dual_mode_tool``; the synthesized
# ``lookup-device`` command + its IP/Provider_Machine_ID resolution paths
# are covered by ``tests/unit/test_dual_mode_migration.py`` and the
# function-level unit tests in ``tests/test_lookup_device.py``.


# ── Unknown command handling ─────────────────────────────────────────


class TestUnknownCommand:
    def test_suggests_closest_match(self):
        """Typo of 'devices' should suggest the correct command."""
        result = runner.invoke(app, ["devces"])
        assert result.exit_code == 2
        assert "Did you mean" in result.stderr
        assert "devices" in result.stderr

    def test_json_error_for_typo(self):
        """Unknown command with --json should output structured JSON.

        Now served by ``mcp_common.cli.SuggestingTyperGroup`` (the shared group
        that replaced the local ``_NetBoxGroup``); its JSON ``error`` is the
        Click-style ``"No such command 'X'."`` string.
        """
        result = runner.invoke(app, ["devces", "--json"])
        assert result.exit_code == 2
        parsed = json.loads(result.stderr)
        assert parsed["error"] == "No such command 'devces'."
        assert "devices" in parsed["suggestions"]
        assert len(parsed["available_commands"]) > 0

    def test_no_match_reports_no_such_command(self):
        """A command with no close matches exits 2 with a 'No such command' error.

        ``SuggestingTyperGroup`` exposes the full command list only in ``--json``
        mode (see ``test_json_error_when_no_match``); in text mode an unmatched
        command yields a plain usage error with no "Did you mean" suggestions.
        """
        result = runner.invoke(app, ["zzzznotacommand"])
        assert result.exit_code == 2
        assert "No such command" in result.stderr
        assert "zzzznotacommand" in result.stderr
        assert "Did you mean" not in result.stderr

    def test_json_error_when_no_match(self):
        """Completely unknown command with --json returns empty suggestions."""
        result = runner.invoke(app, ["zzzznotacommand", "--json"])
        assert result.exit_code == 2
        parsed = json.loads(result.stderr)
        assert parsed["suggestions"] == []
        assert "devices" in parsed["available_commands"]

    def test_valid_command_no_suggestion_noise(self):
        """Valid commands should not trigger the unknown-command handler."""
        with _mock_client():
            result = runner.invoke(app, ["devices"])
            assert result.exit_code == 0
            assert "Did you mean" not in result.output
            assert "Available commands" not in result.output


# ── _format_device_line ───────────────────────────────────────────────


class TestFormatDeviceLine:
    def test_includes_cluster(self):
        """Cluster field should appear in the device line output."""
        device = {
            "name": "node01",
            "status": {"value": "active"},
            "site": {"name": "ORI-TX"},
            "cluster": {"name": "research-common-h100"},
            "device_type": {"model": "H100"},
            "role": {"name": "gpu-node"},
        }
        line = _format_device_line(device)
        assert "cluster=research-common-h100" in line

    def test_cluster_string_value(self):
        """Cluster as a plain string (from trimmed response) should work."""
        device = {
            "name": "node01",
            "status": {"value": "active"},
            "site": {"name": "ORI-TX"},
            "cluster": "my-cluster",
            "device_type": {"model": "H100"},
            "role": {"name": "gpu-node"},
        }
        line = _format_device_line(device)
        assert "cluster=my-cluster" in line

    def test_no_cluster_field(self):
        """When cluster is absent, no cluster= should appear in output."""
        device = {
            "name": "node01",
            "status": {"value": "active"},
            "site": {"name": "ORI-TX"},
            "device_type": {"model": "H100"},
            "role": {"name": "gpu-node"},
        }
        line = _format_device_line(device)
        assert "cluster=" not in line

    def test_cluster_none_value(self):
        """When cluster is None, no cluster= should appear in output."""
        device = {
            "name": "node01",
            "status": {"value": "active"},
            "site": {"name": "ORI-TX"},
            "cluster": None,
            "device_type": {"model": "H100"},
            "role": {"name": "gpu-node"},
        }
        line = _format_device_line(device)
        assert "cluster=" not in line

    def test_includes_provider_id_from_top_level(self):
        """provider_machine_id top-level field should appear as provider_id=."""
        device = {
            "name": "node01",
            "status": {"value": "active"},
            "site": {"name": "ORI-TX"},
            "device_type": {"model": "H100"},
            "role": {"name": "gpu-node"},
            "provider_machine_id": "ori-gpu001",
        }
        line = _format_device_line(device)
        assert "provider_id=ori-gpu001" in line

    def test_includes_provider_id_from_custom_fields(self):
        """Provider_Machine_ID in custom_fields should appear when top-level is absent."""
        device = {
            "name": "node01",
            "status": {"value": "active"},
            "site": {"name": "ORI-TX"},
            "device_type": {"model": "H100"},
            "role": {"name": "gpu-node"},
            "custom_fields": {"Provider_Machine_ID": "ori-gpu001"},
        }
        line = _format_device_line(device)
        assert "provider_id=ori-gpu001" in line

    def test_no_provider_id_when_absent(self):
        """No provider_id= should appear when neither source is present."""
        device = {
            "name": "node01",
            "status": {"value": "active"},
            "site": {"name": "ORI-TX"},
            "device_type": {"model": "H100"},
            "role": {"name": "gpu-node"},
        }
        line = _format_device_line(device)
        assert "provider_id=" not in line

    def test_cluster_appears_between_site_and_role(self):
        """Cluster should appear after site and before role in the output."""
        device = {
            "name": "node01",
            "status": {"value": "active"},
            "site": {"name": "ORI-TX"},
            "cluster": {"name": "research-common-h100"},
            "device_type": {"model": "H100"},
            "role": {"name": "gpu-node"},
        }
        line = _format_device_line(device)
        site_pos = line.index("site=")
        cluster_pos = line.index("cluster=")
        role_pos = line.index("role=")
        assert site_pos < cluster_pos < role_pos


# ── Name filter resolution ───────────────────────────────────────────


class TestNameFilterResolution:
    def test_cluster_filter_resolved_to_id(self):
        """--filter cluster=X should resolve to cluster_id."""
        with _mock_client(cluster_lookup=CLUSTER_LOOKUP) as mock:
            result = runner.invoke(
                app, ["list", "dcim.device", "--filter", "cluster=research-common-h100"]
            )
            assert result.exit_code == 0
            params = mock.return_value.get.call_args[1]["params"]
            assert "cluster_id" in params
            assert "cluster" not in params
            assert params["cluster_id"] == "10"

    def test_site_filter_resolved_to_id(self):
        """--filter site=X should resolve to site_id."""
        with _mock_client(site_lookup=SITE_LOOKUP) as mock:
            result = runner.invoke(app, ["list", "dcim.device", "--filter", "site=ORI-TX"])
            assert result.exit_code == 0
            params = mock.return_value.get.call_args[1]["params"]
            assert "site_id" in params
            assert "site" not in params
            assert params["site_id"] == "5"

    def test_cluster_id_not_overridden(self):
        """If cluster_id is already set, cluster should not override it."""
        with _mock_client() as mock:
            result = runner.invoke(app, ["list", "dcim.device", "--filter", "cluster_id=99"])
            assert result.exit_code == 0
            params = mock.return_value.get.call_args[1]["params"]
            assert params["cluster_id"] == "99"

    def test_unknown_cluster_left_as_text(self):
        """If cluster name can't be resolved, leave it as text filter."""
        with _mock_client() as mock:
            result = runner.invoke(app, ["list", "dcim.device", "--filter", "cluster=nonexistent"])
            assert result.exit_code == 0
            params = mock.return_value.get.call_args[1]["params"]
            assert params["cluster"] == "nonexistent"

    def test_resolution_in_list_helper(self):
        """_list_helper (used by alias commands) should also resolve filters."""
        with _mock_client(cluster_lookup=CLUSTER_LOOKUP) as mock:
            result = runner.invoke(app, ["devices", "--filter", "cluster=research-common-h100"])
            assert result.exit_code == 0
            params = mock.return_value.get.call_args[1]["params"]
            assert "cluster_id" in params
            assert params["cluster_id"] == "10"


class TestVersionFlag:
    def test_version_flag_prints_version(self):
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert result.stdout.strip()

    def test_version_flag_matches_package_version(self):
        from mcp_common import get_version

        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert result.stdout.strip() == get_version("netbox-mcp")
