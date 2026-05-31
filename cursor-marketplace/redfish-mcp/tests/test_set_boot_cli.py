"""Tests for the set-boot CLI command."""

from __future__ import annotations

import json

import pytest
import responses
from typer.testing import CliRunner

from redfish_mcp.cli import app

runner = CliRunner(mix_stderr=False)

MOCK_HOST = "192.168.1.100"
BASE = f"https://{MOCK_HOST}"
SYSTEMS_URL = f"{BASE}/redfish/v1/Systems"
SYSTEM_URL = f"{BASE}/redfish/v1/Systems/1"
RESET_URL = f"{SYSTEM_URL}/Actions/ComputerSystem.Reset"

SYSTEM_JSON = {
    "PowerState": "On",
    "Manufacturer": "Supermicro",
    "Model": "SYS-421GU-TNX",
    "Boot": {
        "BootSourceOverrideEnabled": "Disabled",
        "BootSourceOverrideTarget": "None",
        "BootSourceOverrideMode": "UEFI",
        "BootSourceOverrideTarget@Redfish.AllowableValues": [
            "None",
            "Pxe",
            "Hdd",
            "BiosSetup",
            "Cd",
            "UefiHttp",
        ],
    },
}


@pytest.fixture(autouse=True)
def _redfish_creds(monkeypatch):
    monkeypatch.setenv("REDFISH_USER", "admin")
    monkeypatch.setenv("REDFISH_PASSWORD", "password")


def _mock_system(system_id: str = "1", system_json: dict | None = None) -> None:
    responses.add(
        responses.GET,
        SYSTEMS_URL,
        json={"Members": [{"@odata.id": f"/redfish/v1/Systems/{system_id}"}]},
        status=200,
    )
    responses.add(
        responses.GET,
        f"{BASE}/redfish/v1/Systems/{system_id}",
        json=system_json or SYSTEM_JSON,
        status=200,
    )


def _mock_patch_ok(system_id: str = "1") -> None:
    responses.add(
        responses.PATCH,
        f"{BASE}/redfish/v1/Systems/{system_id}",
        json={},
        status=200,
    )


def _mock_reset_ok(system_id: str = "1") -> None:
    responses.add(
        responses.POST,
        f"{BASE}/redfish/v1/Systems/{system_id}/Actions/ComputerSystem.Reset",
        json={},
        status=200,
    )


class TestSetBootBasic:
    @responses.activate
    def test_set_boot_pxe_once(self):
        _mock_system()
        _mock_patch_ok()

        result = runner.invoke(
            app, ["set-boot", MOCK_HOST, "--target", "Pxe", "--enabled", "Once", "--yes"]
        )
        assert result.exit_code == 0, result.stderr
        assert "Pxe" in result.stdout
        assert "Once" in result.stdout

        patch_calls = [c for c in responses.calls if c.request.method == "PATCH"]
        assert len(patch_calls) == 1
        body = json.loads(patch_calls[0].request.body)
        assert body["Boot"]["BootSourceOverrideTarget"] == "Pxe"
        assert body["Boot"]["BootSourceOverrideEnabled"] == "Once"
        assert body["Boot"]["BootSourceOverrideMode"] == "UEFI"

    @responses.activate
    def test_set_boot_hdd_continuous(self):
        _mock_system()
        _mock_patch_ok()

        result = runner.invoke(
            app, ["set-boot", MOCK_HOST, "--target", "Hdd", "--enabled", "Continuous", "--yes"]
        )
        assert result.exit_code == 0, result.stderr

        patch_calls = [c for c in responses.calls if c.request.method == "PATCH"]
        body = json.loads(patch_calls[0].request.body)
        assert body["Boot"]["BootSourceOverrideTarget"] == "Hdd"
        assert body["Boot"]["BootSourceOverrideEnabled"] == "Continuous"

    @responses.activate
    def test_set_boot_json_output(self):
        _mock_system()
        _mock_patch_ok()

        result = runner.invoke(app, ["set-boot", MOCK_HOST, "--target", "Pxe", "--yes", "--json"])
        assert result.exit_code == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["ok"] is True
        assert data["chosen_target"] == "Pxe"
        assert data["enabled"] == "Once"


class TestSetBootWithReboot:
    @responses.activate
    def test_reboot_after_set(self):
        _mock_system()
        _mock_patch_ok()
        _mock_reset_ok()

        result = runner.invoke(
            app,
            ["set-boot", MOCK_HOST, "--target", "Pxe", "--reboot", "--yes"],
        )
        assert result.exit_code == 0, result.stderr
        assert "reboot_ok" in result.stdout or "True" in result.stdout

        post_calls = [c for c in responses.calls if c.request.method == "POST"]
        assert len(post_calls) == 1
        body = json.loads(post_calls[0].request.body)
        assert body["ResetType"] == "GracefulRestart"

    @responses.activate
    def test_reboot_custom_reset_type(self):
        _mock_system()
        _mock_patch_ok()
        _mock_reset_ok()

        result = runner.invoke(
            app,
            [
                "set-boot",
                MOCK_HOST,
                "--target",
                "BiosSetup",
                "--reboot",
                "--reset-type",
                "ForceRestart",
                "--yes",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["reboot_ok"] is True
        assert data["reset_type"] == "ForceRestart"

    @responses.activate
    def test_reboot_failure_reported(self):
        _mock_system()
        _mock_patch_ok()
        responses.add(
            responses.POST,
            f"{BASE}/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
            body="Reset failed",
            status=500,
        )

        result = runner.invoke(
            app,
            ["set-boot", MOCK_HOST, "--target", "Pxe", "--reboot", "--yes", "--json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["ok"] is True
        assert data["reboot_ok"] is False


class TestSetBootValidation:
    def test_invalid_enabled_value(self):
        result = runner.invoke(
            app, ["set-boot", MOCK_HOST, "--target", "Pxe", "--enabled", "Invalid", "--yes"]
        )
        assert result.exit_code == 1
        assert "Once" in result.stderr

    @responses.activate
    def test_patch_failure(self):
        _mock_system()
        responses.add(responses.PATCH, SYSTEM_URL, body="Bad Request", status=400)

        result = runner.invoke(app, ["set-boot", MOCK_HOST, "--target", "Pxe", "--yes"])
        assert result.exit_code == 1

    @responses.activate
    def test_target_alias_resolution(self):
        """The 'pxe' alias should resolve to 'Pxe' from AllowableValues."""
        _mock_system()
        _mock_patch_ok()

        result = runner.invoke(app, ["set-boot", MOCK_HOST, "--target", "pxe", "--yes", "--json"])
        assert result.exit_code == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["chosen_target"] == "Pxe"

    @responses.activate
    def test_bios_alias_resolution(self):
        """The 'bios' alias should resolve to 'BiosSetup' from AllowableValues."""
        _mock_system()
        _mock_patch_ok()

        result = runner.invoke(app, ["set-boot", MOCK_HOST, "--target", "bios", "--yes", "--json"])
        assert result.exit_code == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["chosen_target"] == "BiosSetup"


class TestSetBootConfirmation:
    @responses.activate
    def test_requires_yes_flag(self):
        """Without --yes, the command should prompt and abort on 'n'."""
        _mock_system()

        result = runner.invoke(app, ["set-boot", MOCK_HOST, "--target", "Pxe"], input="n\n")
        assert result.exit_code != 0

        patch_calls = [c for c in responses.calls if c.request.method == "PATCH"]
        assert len(patch_calls) == 0


class TestSetBootDynamicSystem:
    @responses.activate
    def test_works_with_systems_self(self):
        """Verify set-boot works when system member is /Systems/Self."""
        _mock_system(system_id="Self")
        responses.add(
            responses.PATCH,
            f"{BASE}/redfish/v1/Systems/Self",
            json={},
            status=200,
        )

        result = runner.invoke(app, ["set-boot", MOCK_HOST, "--target", "Pxe", "--yes", "--json"])
        assert result.exit_code == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["ok"] is True
        assert "/Systems/Self" in data["system_url"]
