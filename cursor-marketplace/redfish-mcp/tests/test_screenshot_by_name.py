"""Tests for screenshot-by-name CLI command and OOB IP resolution."""

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest
from click.exceptions import Exit as ClickExit
from typer.testing import CliRunner

from redfish_mcp.cli import _resolve_oob_ip, app

runner = CliRunner()

NETBOX_RESPONSE_OOB_IP_ADDRESS = {
    "count": 1,
    "results": [
        {
            "name": "research-common-h100-097",
            "oob_ip_address": "192.168.196.97",
            "site": {"name": "ORI-TX"},
        }
    ],
}

NETBOX_RESPONSE_OOB_IP_OBJECT = {
    "count": 1,
    "results": [
        {
            "name": "research-common-h100-097",
            "oob_ip": {"address": "192.168.196.97/24"},
            "site": {"name": "ORI-TX"},
        }
    ],
}

NETBOX_RESPONSE_NO_OOB = {
    "count": 1,
    "results": [
        {
            "name": "research-common-h100-097",
            "site": {"name": "ORI-TX"},
        }
    ],
}

NETBOX_RESPONSE_EMPTY = {"count": 0, "results": []}


class TestResolveOobIp:
    """Tests for the _resolve_oob_ip helper function."""

    @patch("subprocess.run")
    def test_resolves_oob_ip_address_field(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(NETBOX_RESPONSE_OOB_IP_ADDRESS),
            stderr="",
        )
        oob_ip, name, site = _resolve_oob_ip("research-common-h100-097")
        assert oob_ip == "192.168.196.97"
        assert name == "research-common-h100-097"
        assert site == "ORI-TX"
        mock_run.assert_called_once_with(
            ["netbox-cli", "lookup", "research-common-h100-097", "--json"],
            capture_output=True,
            text=True,
            timeout=15,
        )

    @patch("subprocess.run")
    def test_resolves_oob_ip_nested_object(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(NETBOX_RESPONSE_OOB_IP_OBJECT),
            stderr="",
        )
        oob_ip, name, site = _resolve_oob_ip("research-common-h100-097")
        assert oob_ip == "192.168.196.97"
        assert name == "research-common-h100-097"
        assert site == "ORI-TX"

    @patch("subprocess.run")
    def test_no_device_found(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(NETBOX_RESPONSE_EMPTY),
            stderr="",
        )
        with pytest.raises(ClickExit):
            _resolve_oob_ip("nonexistent-host")

    @patch("subprocess.run")
    def test_no_oob_ip(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(NETBOX_RESPONSE_NO_OOB),
            stderr="",
        )
        with pytest.raises(ClickExit):
            _resolve_oob_ip("research-common-h100-097")

    @patch("subprocess.run")
    def test_netbox_cli_failure(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="connection refused",
        )
        with pytest.raises(ClickExit):
            _resolve_oob_ip("research-common-h100-097")

    @patch("subprocess.run")
    def test_netbox_cli_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="netbox-cli", timeout=15)
        with pytest.raises(ClickExit):
            _resolve_oob_ip("research-common-h100-097")

    @patch("subprocess.run")
    def test_netbox_cli_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        with pytest.raises(ClickExit):
            _resolve_oob_ip("research-common-h100-097")

    @patch("subprocess.run")
    def test_invalid_json_response(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="not json",
            stderr="",
        )
        with pytest.raises(ClickExit):
            _resolve_oob_ip("research-common-h100-097")


class TestScreenshotByNameCommand:
    """Tests for the screenshot-by-name CLI command integration."""

    @patch("redfish_mcp.cli._do_screenshot")
    @patch("redfish_mcp.cli._resolve_oob_ip")
    def test_resolves_and_delegates(self, mock_resolve, mock_screenshot):
        mock_resolve.return_value = ("192.168.196.97", "research-common-h100-097", "ORI-TX")
        result = runner.invoke(
            app,
            [
                "--user",
                "testuser",
                "--password",
                "testpass",
                "screenshot-by-name",
                "research-common-h100-097",
                "--output",
                "/tmp/test.jpg",
            ],
        )
        assert result.exit_code == 0
        assert "192.168.196.97" in result.output
        assert "ORI-TX" in result.output
        mock_resolve.assert_called_once_with("research-common-h100-097")
        mock_screenshot.assert_called_once_with(
            host="192.168.196.97",
            output="/tmp/test.jpg",
            method="auto",
            text_only=False,
            ocr=False,
            analyze="",
            verify_tls=False,
            timeout=30,
            analysis_timeout=None,
        )

    @patch("redfish_mcp.cli._do_screenshot")
    @patch("redfish_mcp.cli._resolve_oob_ip")
    def test_passes_analyze_flag(self, mock_resolve, mock_screenshot):
        mock_resolve.return_value = ("192.168.196.97", "test-host", "5C-OH1")
        result = runner.invoke(
            app,
            [
                "--user",
                "u",
                "--password",
                "p",
                "screenshot-by-name",
                "test-host",
                "--analyze",
                "summary",
            ],
        )
        assert result.exit_code == 0
        mock_screenshot.assert_called_once()
        call_kwargs = mock_screenshot.call_args[1]
        assert call_kwargs["analyze"] == "summary"
        assert call_kwargs["host"] == "192.168.196.97"

    @patch("redfish_mcp.cli._resolve_oob_ip")
    def test_exits_on_resolution_failure(self, mock_resolve):
        mock_resolve.side_effect = SystemExit(1)
        result = runner.invoke(
            app,
            ["--user", "u", "--password", "p", "screenshot-by-name", "bad-host"],
        )
        assert result.exit_code != 0
