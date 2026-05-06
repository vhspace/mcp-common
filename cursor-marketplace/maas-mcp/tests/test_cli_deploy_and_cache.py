"""Tests for deploy --osystem/--distro-series, verify-image-cache, and resolve commands."""

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from maas_mcp.cli import _OP_DEFAULT_TIMEOUTS, _check_boot_resources, app
from maas_mcp.netbox_resolve import NetboxResolveFailureKind, NetboxResolveResult


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner(mix_stderr=False)


# ---------------------------------------------------------------------------
# _OP_DEFAULT_TIMEOUTS
# ---------------------------------------------------------------------------


class TestOpDefaultTimeouts:
    def test_deploy_timeout_600(self):
        assert _OP_DEFAULT_TIMEOUTS["deploy"] == 600

    def test_release_timeout_600(self):
        assert _OP_DEFAULT_TIMEOUTS["release"] == 600

    def test_commission_timeout_900(self):
        assert _OP_DEFAULT_TIMEOUTS["commission"] == 900


# ---------------------------------------------------------------------------
# op deploy --osystem / --distro-series
# ---------------------------------------------------------------------------


class TestOpDeployOsParams:
    def test_deploy_passes_osystem_and_distro_series(self, runner: CliRunner) -> None:
        mock_client = MagicMock()
        mock_client.get.return_value = {
            "system_id": "abc",
            "hostname": "gpu001",
            "status_name": "Ready",
            "power_state": "off",
        }
        mock_client.post_fire.return_value = (
            {"system_id": "abc", "status_name": "Deploying", "power_state": "on"},
            False,
        )

        with patch("maas_mcp.cli._get_client", return_value=("default", mock_client)):
            result = runner.invoke(
                app,
                [
                    "op",
                    "abc",
                    "deploy",
                    "--osystem",
                    "ubuntu",
                    "--distro-series",
                    "jammy",
                    "--yes",
                    "--no-wait",
                ],
            )

        assert result.exit_code == 0
        call_args = mock_client.post_fire.call_args
        data = call_args[1].get("data") if call_args[1] else call_args[0][1]
        assert data.get("osystem") == "ubuntu"
        assert data.get("distro_series") == "jammy"

    def test_deploy_without_os_params_sends_empty_data(self, runner: CliRunner) -> None:
        mock_client = MagicMock()
        mock_client.get.return_value = {
            "system_id": "abc",
            "hostname": "gpu001",
            "status_name": "Ready",
            "power_state": "off",
        }
        mock_client.post_fire.return_value = (
            {"system_id": "abc", "status_name": "Deploying", "power_state": "on"},
            False,
        )

        with patch("maas_mcp.cli._get_client", return_value=("default", mock_client)):
            result = runner.invoke(
                app,
                ["op", "abc", "deploy", "--yes", "--no-wait"],
            )

        assert result.exit_code == 0
        call_args = mock_client.post_fire.call_args
        data = call_args[1].get("data") if call_args[1] else call_args[0][1]
        assert "osystem" not in data
        assert "distro_series" not in data

    def test_osystem_warning_for_non_deploy_op(self, runner: CliRunner) -> None:
        mock_client = MagicMock()
        mock_client.get.return_value = {
            "system_id": "abc",
            "hostname": "gpu001",
            "status_name": "Ready",
            "power_state": "off",
        }
        mock_client.post_fire.return_value = (
            {"system_id": "abc", "status_name": "Commissioning", "power_state": "on"},
            False,
        )

        with patch("maas_mcp.cli._get_client", return_value=("default", mock_client)):
            result = runner.invoke(
                app,
                ["op", "abc", "commission", "--osystem", "ubuntu", "--yes", "--no-wait"],
            )

        assert result.exit_code == 0
        assert "only used with op=deploy" in (result.stderr or "")


# ---------------------------------------------------------------------------
# _check_boot_resources
# ---------------------------------------------------------------------------


class TestCheckBootResources:
    def test_healthy_cache(self) -> None:
        client = MagicMock()
        client.get.side_effect = [
            [
                {
                    "id": 1,
                    "name": "ubuntu/jammy",
                    "architecture": "amd64/generic",
                    "size": 500_000_000,
                    "complete": True,
                },
            ],
            False,
            [
                {
                    "hostname": "rack1",
                    "system_id": "r1",
                    "service_set": [
                        {"name": "http", "status": "running"},
                    ],
                }
            ],
        ]

        result = _check_boot_resources(client, osystem="ubuntu", series="jammy")
        assert result["ok"] is True
        assert len(result["matched_resources"]) == 1
        assert not result["issues"]

    def test_no_matching_resources(self) -> None:
        client = MagicMock()
        client.get.side_effect = [
            [{"id": 1, "name": "ubuntu/noble", "architecture": "amd64/generic"}],
            False,
            [],
        ]

        result = _check_boot_resources(client, osystem="ubuntu", series="jammy")
        assert result["ok"] is False
        assert any("No synced boot resources" in i for i in result["issues"])

    def test_importing_marks_unhealthy(self) -> None:
        client = MagicMock()
        client.get.side_effect = [
            [{"id": 1, "name": "ubuntu/jammy", "architecture": "amd64/generic"}],
            True,
            [],
        ]

        result = _check_boot_resources(client, osystem="ubuntu", series="jammy")
        assert result["ok"] is False
        assert result["is_importing"] is True


# ---------------------------------------------------------------------------
# verify-image-cache command
# ---------------------------------------------------------------------------


class TestVerifyImageCacheCommand:
    def test_healthy_output(self, runner: CliRunner) -> None:
        mock_client = MagicMock()
        mock_client.get.side_effect = [
            [
                {
                    "id": 1,
                    "name": "ubuntu/jammy",
                    "architecture": "amd64/generic",
                    "size": 500_000_000,
                    "complete": True,
                }
            ],
            False,
            [
                {
                    "hostname": "rack1",
                    "system_id": "r1",
                    "service_set": [
                        {"name": "http", "status": "running"},
                    ],
                }
            ],
        ]

        with patch("maas_mcp.cli._get_client", return_value=("default", mock_client)):
            result = runner.invoke(
                app,
                ["verify-image-cache", "--os", "ubuntu", "--series", "jammy"],
            )

        assert result.exit_code == 0
        assert "HEALTHY" in result.stdout

    def test_unhealthy_output(self, runner: CliRunner) -> None:
        mock_client = MagicMock()
        mock_client.get.side_effect = [[], False, []]

        with patch("maas_mcp.cli._get_client", return_value=("default", mock_client)):
            result = runner.invoke(
                app,
                ["verify-image-cache", "--os", "ubuntu", "--series", "noble"],
            )

        assert result.exit_code == 0
        assert "UNHEALTHY" in result.stdout


# ---------------------------------------------------------------------------
# resolve command
# ---------------------------------------------------------------------------


class TestResolveCommand:
    def test_successful_resolve(self, runner: CliRunner) -> None:
        mock_client = MagicMock()
        mock_client.get.return_value = {
            "system_id": "xyz789",
            "hostname": "gpu001",
            "status_name": "Deployed",
            "power_state": "on",
        }

        nb_result = NetboxResolveResult.success("xyz789", maas_hostname="gpu001")
        with (
            patch("maas_mcp.cli._get_client", return_value=("default", mock_client)),
            patch("maas_mcp.cli._resolve_via_netbox", return_value=nb_result),
        ):
            result = runner.invoke(app, ["resolve", "research-common-h100-001"])

        assert result.exit_code == 0
        assert "xyz789" in result.stdout
        assert "gpu001" in result.stdout

    def test_failed_resolve(self, runner: CliRunner) -> None:
        mock_client = MagicMock()
        nb_result = NetboxResolveResult(None, NetboxResolveFailureKind.DEVICE_NOT_FOUND)
        with (
            patch("maas_mcp.cli._get_client", return_value=("default", mock_client)),
            patch("maas_mcp.cli._resolve_via_netbox", return_value=nb_result),
        ):
            result = runner.invoke(app, ["resolve", "no-such-device"])

        assert result.exit_code == 1
        assert "Could not resolve" in (result.stderr or "")

    def test_resolve_not_configured(self, runner: CliRunner) -> None:
        mock_client = MagicMock()
        nb_result = NetboxResolveResult(None, NetboxResolveFailureKind.NOT_CONFIGURED)
        with (
            patch("maas_mcp.cli._get_client", return_value=("default", mock_client)),
            patch("maas_mcp.cli._resolve_via_netbox", return_value=nb_result),
        ):
            result = runner.invoke(app, ["resolve", "something"])

        assert result.exit_code == 1

    def test_resolve_json_output(self, runner: CliRunner) -> None:
        mock_client = MagicMock()
        mock_client.get.return_value = {
            "system_id": "xyz789",
            "hostname": "gpu001",
            "status_name": "Ready",
            "power_state": "off",
        }
        nb_result = NetboxResolveResult.success("xyz789", maas_hostname="gpu001")
        with (
            patch("maas_mcp.cli._get_client", return_value=("default", mock_client)),
            patch("maas_mcp.cli._resolve_via_netbox", return_value=nb_result),
        ):
            result = runner.invoke(app, ["resolve", "test-device", "--json"])

        assert result.exit_code == 0
        import json

        data = json.loads(result.stdout)
        assert data["resolved"] is True
        assert data["system_id"] == "xyz789"
