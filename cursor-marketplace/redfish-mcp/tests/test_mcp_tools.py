"""Tests for MCP tools using mocked Redfish responses."""

import pytest
import responses

from redfish_mcp.mcp_server import create_mcp_app


@pytest.fixture
def mcp_tools(tmp_path, monkeypatch):
    """Create MCP app and return tools dict for testing."""
    monkeypatch.setenv("REDFISH_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("REDFISH_SITE", "test")
    _, tools = create_mcp_app()
    return tools


@pytest.fixture
def mock_host():
    return "192.168.1.100"


class TestGetInfo:
    @responses.activate
    @pytest.mark.anyio
    async def test_get_info_system_success(self, mcp_tools, mock_host):
        base = f"https://{mock_host}"
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems",
            json={"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]},
            status=200,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems/1",
            json={
                "Id": "1",
                "Name": "System",
                "Manufacturer": "Supermicro",
                "Model": "X12DPG-QT6",
                "SerialNumber": "S123456",
                "BiosVersion": "1.2.3",
                "PowerState": "On",
                "Status": {"State": "Enabled", "Health": "OK"},
            },
            status=200,
        )

        result = await mcp_tools["redfish_get_info"](
            host=mock_host,
            user="admin",
            password="password",
            info_types=["system"],
            verify_tls=False,
            timeout_s=15,
        )

        assert result["ok"] is True
        assert result["host"] == mock_host
        assert result["system"]["Manufacturer"] == "Supermicro"
        assert result["system"]["Model"] == "X12DPG-QT6"

    @pytest.mark.anyio
    async def test_get_info_render_curl(self, mcp_tools):
        result = await mcp_tools["redfish_get_info"](
            host="192.168.1.1",
            user="admin",
            password="password",
            info_types=["system"],
            execution_mode="render_curl",
        )

        assert result["ok"] is True
        assert result["execution_mode"] == "render_curl"
        assert "curl" in result
        assert len(result["curl"]) > 0


# Triage, nextboot, and pending BIOS changes functionality is now in redfish_get_info


class TestGetInfoComplete:
    @responses.activate
    @pytest.mark.anyio
    async def test_get_info_system_and_boot(self, mcp_tools, mock_host):
        base = f"https://{mock_host}"
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems",
            json={"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]},
            status=200,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems/1",
            json={
                "Id": "1",
                "Name": "System",
                "Manufacturer": "Supermicro",
                "Model": "X12",
                "SerialNumber": "S123",
                "BiosVersion": "3.7a",
                "PowerState": "On",
                "Boot": {
                    "BootSourceOverrideEnabled": "Once",
                    "BootSourceOverrideTarget": "Pxe",
                    "BootSourceOverrideTarget@Redfish.AllowableValues": ["Pxe", "Hdd"],
                },
            },
            status=200,
        )

        result = await mcp_tools["redfish_get_info"](
            host=mock_host,
            user="admin",
            password="password",
            info_types=["system", "boot"],
            verify_tls=False,
        )

        assert result["ok"] is True
        assert result["system"]["BiosVersion"] == "3.7a"
        assert result["system"]["Manufacturer"] == "Supermicro"
        assert result["boot"]["BootSourceOverrideTarget"] == "Pxe"
        assert result["boot"]["AllowableTargets"] == ["Pxe", "Hdd"]

    @responses.activate
    @pytest.mark.anyio
    async def test_get_info_default_types(self, mcp_tools, mock_host):
        base = f"https://{mock_host}"
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems",
            json={"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]},
            status=200,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems/1",
            json={
                "Id": "1",
                "Manufacturer": "Test",
                "BiosVersion": "1.0",
                "Boot": {},
            },
            status=200,
        )

        result = await mcp_tools["redfish_get_info"](
            host=mock_host,
            user="admin",
            password="password",
            # No info_types specified, should default to ["system", "boot"]
        )

        assert result["ok"] is True
        assert "system" in result
        assert "boot" in result
        assert "drives" not in result  # Not requested


class TestQuery:
    @responses.activate
    @pytest.mark.anyio
    async def test_query_bios_attribute_found(self, mcp_tools, mock_host):
        base = f"https://{mock_host}"
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems",
            json={"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]},
            status=200,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems/1",
            json={"Id": "1"},
            status=200,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems/1/Bios",
            json={
                "@Redfish.Settings": {
                    "SettingsObject": {"@odata.id": "/redfish/v1/Systems/1/Bios/Settings"}
                },
                "Attributes": {
                    "SMT_Enable": "Enabled",
                    "Re_SizeBARSupport_00B2": "Enabled",
                },
            },
            status=200,
        )

        result = await mcp_tools["redfish_query"](
            host=mock_host,
            user="admin",
            password="password",
            query_type="bios_attribute",
            key="SMT_Enable",
            include_setter_info=True,
        )

        assert result["ok"] is True
        assert result["found"] is True
        assert result["current_value"] == "Enabled"
        assert "setter_info" in result
        assert result["setter_info"]["writable"] is True

    @responses.activate
    @pytest.mark.anyio
    async def test_query_bios_attribute_not_found(self, mcp_tools, mock_host):
        base = f"https://{mock_host}"
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems",
            json={"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]},
            status=200,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems/1",
            json={"Id": "1"},
            status=200,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems/1/Bios",
            json={
                "Attributes": {
                    "OtherSetting": "Value",
                    "Similar_Setting": "Value2",
                },
            },
            status=200,
        )

        result = await mcp_tools["redfish_query"](
            host=mock_host,
            user="admin",
            password="password",
            query_type="bios_attribute",
            key="setting",  # Will find Similar_Setting
        )

        assert result["ok"] is True
        assert result["found"] is False
        assert "similar_keys" in result
        assert "Similar_Setting" in result["similar_keys"]

    @responses.activate
    @pytest.mark.anyio
    async def test_query_boot_setting(self, mcp_tools, mock_host):
        base = f"https://{mock_host}"
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems",
            json={"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]},
            status=200,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems/1",
            json={
                "Boot": {
                    "BootSourceOverrideEnabled": "Once",
                    "BootSourceOverrideTarget": "Pxe",
                    "BootSourceOverrideTarget@Redfish.AllowableValues": ["Pxe", "Hdd", "BiosSetup"],
                },
            },
            status=200,
        )

        result = await mcp_tools["redfish_query"](
            host=mock_host,
            user="admin",
            password="password",
            query_type="boot_setting",
            key="target",
            include_setter_info=True,
        )

        assert result["ok"] is True
        assert result["found"] is True
        assert result["current_value"] == "Pxe"
        assert "allowable_values" in result["setter_info"]

    @responses.activate
    @pytest.mark.anyio
    async def test_query_power_state(self, mcp_tools, mock_host):
        base = f"https://{mock_host}"
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems",
            json={"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]},
            status=200,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems/1",
            json={"PowerState": "On"},
            status=200,
        )

        result = await mcp_tools["redfish_query"](
            host=mock_host,
            user="admin",
            password="password",
            query_type="power_state",
        )

        assert result["ok"] is True
        assert result["found"] is True
        assert result["current_value"] == "On"


class TestListBmcUsers:
    @responses.activate
    @pytest.mark.anyio
    async def test_list_bmc_users_success(self, mcp_tools, mock_host):
        base = f"https://{mock_host}"
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/AccountService",
            json={"Accounts": {"@odata.id": "/redfish/v1/AccountService/Accounts"}},
            status=200,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/AccountService/Accounts",
            json={
                "Members": [
                    {"@odata.id": "/redfish/v1/AccountService/Accounts/2"},
                    {"@odata.id": "/redfish/v1/AccountService/Accounts/3"},
                ]
            },
            status=200,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/AccountService/Accounts/2",
            json={
                "Id": "2",
                "UserName": "ADMIN",
                "RoleId": "Administrator",
                "Enabled": True,
                "Locked": False,
            },
            status=200,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/AccountService/Accounts/3",
            json={
                "Id": "3",
                "UserName": "operator",
                "RoleId": "Operator",
                "Enabled": True,
                "Locked": False,
            },
            status=200,
        )

        result = await mcp_tools["redfish_list_bmc_users"](
            host=mock_host,
            user="admin",
            password="password",
            verify_tls=False,
            timeout_s=15,
        )

        assert result["ok"] is True
        assert result["host"] == mock_host
        assert result["count"] == 2
        assert [u["username"] for u in result["users"]] == ["ADMIN", "operator"]
        assert result["users"][0]["role_id"] == "Administrator"

    @responses.activate
    @pytest.mark.anyio
    async def test_list_bmc_users_accounts_not_dict_falls_back(self, mcp_tools, mock_host):
        base = f"https://{mock_host}"
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/AccountService",
            json={"Accounts": "not-a-dict"},
            status=200,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/AccountService/Accounts",
            json={"Members": [{"@odata.id": "/redfish/v1/AccountService/Accounts/2"}]},
            status=200,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/AccountService/Accounts/2",
            json={
                "Id": "2",
                "UserName": "ADMIN",
                "RoleId": "Administrator",
                "Enabled": True,
                "Locked": False,
            },
            status=200,
        )

        result = await mcp_tools["redfish_list_bmc_users"](
            host=mock_host,
            user="admin",
            password="password",
            verify_tls=False,
            timeout_s=15,
        )

        assert result["ok"] is True
        assert result["count"] == 1
        assert result["users"][0]["username"] == "ADMIN"

    @responses.activate
    @pytest.mark.anyio
    async def test_list_bmc_users_partial_member_fetch_failures(self, mcp_tools, mock_host):
        base = f"https://{mock_host}"
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/AccountService",
            json={"Accounts": {"@odata.id": "/redfish/v1/AccountService/Accounts"}},
            status=200,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/AccountService/Accounts",
            json={
                "Members": [
                    {"@odata.id": "/redfish/v1/AccountService/Accounts/2"},
                    {"@odata.id": "/redfish/v1/AccountService/Accounts/3"},
                ]
            },
            status=200,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/AccountService/Accounts/2",
            json={
                "Id": "2",
                "UserName": "ADMIN",
                "RoleId": "Administrator",
                "Enabled": True,
                "Locked": False,
            },
            status=200,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/AccountService/Accounts/3",
            status=404,
        )

        result = await mcp_tools["redfish_list_bmc_users"](
            host=mock_host,
            user="admin",
            password="password",
            verify_tls=False,
            timeout_s=15,
        )

        assert result["ok"] is True
        assert result["count"] == 1
        assert result["failed_member_count"] == 1
        assert len(result["failed_members"]) == 1

    @pytest.mark.anyio
    async def test_list_bmc_users_render_curl(self, mcp_tools):
        result = await mcp_tools["redfish_list_bmc_users"](
            host="192.168.1.1",
            user="admin",
            password="password",
            execution_mode="render_curl",
        )

        assert result["ok"] is True
        assert result["execution_mode"] == "render_curl"
        assert "curl" in result
        assert len(result["curl"]) > 0


class TestUpdateFirmware:
    @responses.activate
    @pytest.mark.anyio
    async def test_update_firmware_success_no_wait(self, mcp_tools, mock_host, tmp_path):
        image_path = tmp_path / "firmware.bin"
        image_path.write_bytes(b"test-firmware")
        base = f"https://{mock_host}"

        responses.add(
            responses.POST,
            f"{base}/redfish/v1/UpdateService/upload",
            status=202,
            headers={"Location": "/redfish/v1/TaskService/Tasks/42"},
            json={},
        )

        result = await mcp_tools["redfish_update_firmware"](
            host=mock_host,
            user="admin",
            password="password",
            image_path=str(image_path),
            wait_for_completion=False,
            allow_write=True,
            verify_tls=False,
            timeout_s=15,
        )

        assert result["ok"] is True
        assert result["host"] == mock_host
        assert result["http_status"] == 202
        assert result["task_url"].endswith("/redfish/v1/TaskService/Tasks/42")
        assert result["preserve_bmc_settings"] is True

    @pytest.mark.anyio
    async def test_update_firmware_non_preserving_requires_gate(self, mcp_tools):
        result = await mcp_tools["redfish_update_firmware"](
            host="192.168.1.1",
            user="admin",
            password="password",
            image_path="/tmp/firmware.bin",
            preserve_bmc_settings=False,
            allow_non_preserving_update=False,
            allow_write=True,
        )

        assert result["ok"] is False
        assert "allow_non_preserving_update" in result["error"]

    @responses.activate
    @pytest.mark.anyio
    async def test_update_firmware_wait_for_completion_success(
        self, mcp_tools, mock_host, tmp_path
    ):
        image_path = tmp_path / "firmware.bin"
        image_path.write_bytes(b"test-firmware")
        base = f"https://{mock_host}"
        task_url = f"{base}/redfish/v1/TaskService/Tasks/42"

        responses.add(
            responses.POST,
            f"{base}/redfish/v1/UpdateService/upload",
            status=202,
            headers={"Location": "/redfish/v1/TaskService/Tasks/42"},
            json={},
        )
        responses.add(
            responses.GET,
            task_url,
            status=200,
            json={"TaskState": "Running", "Messages": [{"Message": "Working"}]},
        )
        responses.add(
            responses.GET,
            task_url,
            status=200,
            json={"TaskState": "Completed", "Messages": [{"Message": "Done"}]},
        )

        result = await mcp_tools["redfish_update_firmware"](
            host=mock_host,
            user="admin",
            password="password",
            image_path=str(image_path),
            wait_for_completion=True,
            poll_interval_s=0,
            allow_write=True,
            verify_tls=False,
            timeout_s=15,
        )

        assert result["ok"] is True
        assert result["task_result"]["ok"] is True
        assert result["task_result"]["task_state"] == "Completed"

    @responses.activate
    @pytest.mark.anyio
    async def test_update_firmware_wait_for_completion_404_not_success(
        self, mcp_tools, mock_host, tmp_path
    ):
        image_path = tmp_path / "firmware.bin"
        image_path.write_bytes(b"test-firmware")
        base = f"https://{mock_host}"
        task_url = f"{base}/redfish/v1/TaskService/Tasks/404"

        responses.add(
            responses.POST,
            f"{base}/redfish/v1/UpdateService/upload",
            status=202,
            headers={"Location": "/redfish/v1/TaskService/Tasks/404"},
            json={},
        )
        responses.add(
            responses.GET,
            task_url,
            status=404,
            json={},
        )

        result = await mcp_tools["redfish_update_firmware"](
            host=mock_host,
            user="admin",
            password="password",
            image_path=str(image_path),
            wait_for_completion=True,
            poll_interval_s=0,
            allow_write=True,
            verify_tls=False,
            timeout_s=15,
        )

        assert result["ok"] is False
        assert result["task_result"]["ok"] is False
        assert result["task_result"]["task_state"] == "NotFound"

    @pytest.mark.anyio
    async def test_update_firmware_missing_image_returns_structured_error(self, mcp_tools):
        result = await mcp_tools["redfish_update_firmware"](
            host="192.168.1.1",
            user="admin",
            password="password",
            image_path="/tmp/does-not-exist-firmware.bin",
            wait_for_completion=False,
            allow_write=True,
        )

        assert result["ok"] is False
        assert "Failed to upload firmware image" in result["error"]

    @pytest.mark.anyio
    async def test_update_firmware_render_curl(self, mcp_tools):
        result = await mcp_tools["redfish_update_firmware"](
            host="192.168.1.1",
            user="admin",
            password="password",
            image_path="/tmp/firmware.bin",
            allow_write=True,
            execution_mode="render_curl",
        )

        assert result["ok"] is True
        assert result["execution_mode"] == "render_curl"
        assert "curl" in result
        assert len(result["curl"]) > 0


class TestBiosSet:
    @responses.activate
    @pytest.mark.anyio
    async def test_set_bios_attributes_sync(self, mcp_tools, mock_host):
        base = f"https://{mock_host}"
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems",
            json={"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]},
            status=200,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems/1/Bios",
            json={
                "@Redfish.Settings": {
                    "SettingsObject": {"@odata.id": "/redfish/v1/Systems/1/Bios/Settings"}
                },
                "Attributes": {"Re_SizeBARSupport_00B2": "Disabled"},
                "@odata.etag": 'W/"123456"',
            },
            status=200,
        )
        responses.add(
            responses.PATCH,
            f"{base}/redfish/v1/Systems/1/Bios/Settings",
            json={},
            status=200,
        )

        result = await mcp_tools["redfish_set_bios_attributes"](
            host=mock_host,
            user="admin",
            password="password",
            attributes={"Re_SizeBARSupport_00B2": "Enabled"},
            allow_write=True,
            async_mode=False,
        )

        assert result["ok"] is True
        assert result["host"] == mock_host
        assert result["staged_attributes"]["Re_SizeBARSupport_00B2"] == "Enabled"

    @pytest.mark.anyio
    async def test_set_bios_attributes_without_write_permission(self, mcp_tools):
        result = await mcp_tools["redfish_set_bios_attributes"](
            host="192.168.1.1",
            user="admin",
            password="password",
            attributes={"Setting": "Value"},
            allow_write=False,
        )

        assert result["ok"] is False
        assert "allow_write" in result["error"]


class TestGetBmcLogs:
    @responses.activate
    @pytest.mark.anyio
    async def test_get_sel_entries(self, mcp_tools, mock_host):
        base = f"https://{mock_host}"
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Sel",
            json={"Id": "Sel", "Name": "SEL"},
            status=200,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Sel/Entries",
            json={
                "Members": [
                    {
                        "Id": "1",
                        "Created": "2026-03-04T14:03:00-06:00",
                        "Severity": "Warning",
                        "Message": "Inlet temp below threshold",
                        "MessageId": "01508383",
                        "SensorType": "Temperature",
                        "EntryCode": "Lower Non-critical",
                    },
                    {
                        "Id": "2",
                        "Created": "2026-03-04T14:05:00-06:00",
                        "Severity": "OK",
                        "Message": "Inlet temp normal",
                        "MessageId": "81508683",
                        "SensorType": "Temperature",
                        "EntryCode": "Lower Non-critical",
                    },
                ]
            },
            status=200,
        )

        result = await mcp_tools["redfish_get_bmc_logs"](
            host=mock_host,
            user="admin",
            password="password",
            log_service="Sel",
            verify_tls=False,
            timeout_s=15,
        )

        assert result["ok"] is True
        assert result["log_service"] == "Sel"
        assert result["filtered_count"] == 2
        # Entries are sorted newest-first; 14:05 > 14:03
        assert result["entries"][0]["severity"] == "OK"

    @responses.activate
    @pytest.mark.anyio
    async def test_date_filter(self, mcp_tools, mock_host):
        base = f"https://{mock_host}"
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Lclog",
            json={"Id": "Lclog", "Name": "Lifecycle Log"},
            status=200,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Lclog/Entries",
            json={
                "Members": [
                    {
                        "Id": "100",
                        "Created": "2026-03-04T10:00:00-06:00",
                        "Severity": "OK",
                        "Message": "Login",
                        "MessageId": "USR0030",
                    },
                    {
                        "Id": "99",
                        "Created": "2026-03-03T10:00:00-06:00",
                        "Severity": "OK",
                        "Message": "Old entry",
                        "MessageId": "USR0030",
                    },
                ]
            },
            status=200,
        )

        result = await mcp_tools["redfish_get_bmc_logs"](
            host=mock_host,
            user="admin",
            password="password",
            log_service="Lclog",
            date_filter="2026-03-04",
            verify_tls=False,
            timeout_s=15,
        )

        assert result["ok"] is True
        assert result["filtered_count"] == 1
        assert result["entries"][0]["id"] == "100"

    @responses.activate
    @pytest.mark.anyio
    async def test_severity_filter(self, mcp_tools, mock_host):
        base = f"https://{mock_host}"
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Sel",
            json={"Id": "Sel", "Name": "SEL"},
            status=200,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Sel/Entries",
            json={
                "Members": [
                    {
                        "Id": "1",
                        "Created": "2026-03-04T10:00:00Z",
                        "Severity": "Warning",
                        "Message": "warn",
                    },
                    {
                        "Id": "2",
                        "Created": "2026-03-04T11:00:00Z",
                        "Severity": "OK",
                        "Message": "ok",
                    },
                    {
                        "Id": "3",
                        "Created": "2026-03-04T12:00:00Z",
                        "Severity": "Critical",
                        "Message": "crit",
                    },
                ]
            },
            status=200,
        )

        result = await mcp_tools["redfish_get_bmc_logs"](
            host=mock_host,
            user="admin",
            password="password",
            severity_filter="Critical",
            verify_tls=False,
            timeout_s=15,
        )

        assert result["ok"] is True
        assert result["filtered_count"] == 1
        assert result["entries"][0]["message"] == "crit"

    @responses.activate
    @pytest.mark.anyio
    async def test_idrac_fallback_to_generic_manager(self, mcp_tools, mock_host):
        base = f"https://{mock_host}"
        # iDRAC Sel not found — auto-discovery enumerates first manager
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Sel",
            json={"error": "not found"},
            status=404,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers",
            json={"Members": [{"@odata.id": "/redfish/v1/Managers/BMC"}]},
            status=200,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/BMC/LogServices",
            json={
                "Members": [
                    {"@odata.id": "/redfish/v1/Managers/BMC/LogServices/Sel"},
                ]
            },
            status=200,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/BMC/LogServices/Sel/Entries",
            json={
                "Members": [
                    {
                        "Id": "1",
                        "Created": "2026-03-04T10:00:00Z",
                        "Severity": "OK",
                        "Message": "test",
                    }
                ]
            },
            status=200,
        )

        result = await mcp_tools["redfish_get_bmc_logs"](
            host=mock_host,
            user="admin",
            password="password",
            verify_tls=False,
            timeout_s=15,
        )

        assert result["ok"] is True
        assert result["filtered_count"] == 1

    @pytest.mark.anyio
    async def test_render_curl(self, mcp_tools, mock_host):
        result = await mcp_tools["redfish_get_bmc_logs"](
            host=mock_host,
            user="admin",
            password="password",
            execution_mode="render_curl",
        )

        assert result["ok"] is True
        assert "curl" in result


class TestGetBmcLogServices:
    @responses.activate
    @pytest.mark.anyio
    async def test_list_log_services(self, mcp_tools, mock_host):
        base = f"https://{mock_host}"
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems",
            json={"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]},
            status=200,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems/1",
            json={"Id": "1", "PowerState": "On", "Status": {"Health": "OK"}},
            status=200,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers",
            json={
                "Members": [
                    {"@odata.id": "/redfish/v1/Managers/iDRAC.Embedded.1"},
                ]
            },
            status=200,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/iDRAC.Embedded.1/LogServices",
            json={
                "Members": [
                    {"@odata.id": "/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Sel"},
                    {"@odata.id": "/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Lclog"},
                    {"@odata.id": "/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/FaultList"},
                ]
            },
            status=200,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems/1/LogServices",
            json={"Members": []},
            status=200,
        )

        result = await mcp_tools["redfish_query"](
            host=mock_host,
            user="admin",
            password="password",
            query_type="bmc_log_services",
            verify_tls=False,
            timeout_s=15,
        )

        assert result["ok"] is True
        assert result["count"] == 3
        names = [s["name"] for s in result["log_services"]]
        assert "Sel" in names
        assert "Lclog" in names
        assert "FaultList" in names


class TestClearBmcLog:
    @responses.activate
    @pytest.mark.anyio
    async def test_clear_sel(self, mcp_tools, mock_host):
        base = f"https://{mock_host}"
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Sel",
            json={"Id": "Sel", "Name": "SEL"},
            status=200,
        )
        responses.add(
            responses.POST,
            f"{base}/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Sel/Actions/LogService.ClearLog",
            json={},
            status=200,
        )

        result = await mcp_tools["redfish_clear_bmc_log"](
            host=mock_host,
            user="admin",
            password="password",
            log_service="Sel",
            verify_tls=False,
            timeout_s=15,
        )

        assert result["ok"] is True
        assert "cleared" in result["message"]

    @responses.activate
    @pytest.mark.anyio
    async def test_clear_log_failure(self, mcp_tools, mock_host):
        base = f"https://{mock_host}"
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Sel",
            json={"Id": "Sel", "Name": "SEL"},
            status=200,
        )
        responses.add(
            responses.POST,
            f"{base}/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Sel/Actions/LogService.ClearLog",
            json={"error": "forbidden"},
            status=403,
        )

        result = await mcp_tools["redfish_clear_bmc_log"](
            host=mock_host,
            user="admin",
            password="password",
            log_service="Sel",
            verify_tls=False,
            timeout_s=15,
        )

        assert result["ok"] is False
        assert "ClearLog failed" in result["error"]


class TestPowerControl:
    @responses.activate
    @pytest.mark.anyio
    async def test_power_on(self, mcp_tools, mock_host):
        base = f"https://{mock_host}"
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems",
            json={"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]},
            status=200,
        )
        responses.add(
            responses.GET,
            f"{base}/redfish/v1/Systems/1",
            json={"PowerState": "Off"},
            status=200,
        )
        responses.add(
            responses.POST,
            f"{base}/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
            json={},
            status=200,
        )

        result = await mcp_tools["redfish_power_control"](
            host=mock_host,
            user="admin",
            password="password",
            action="on",
            allow_write=True,
            verify_tls=False,
            timeout_s=15,
        )

        assert result["ok"] is True
        assert result["host"] == mock_host
        assert result["action"] == "on"
        assert result["reset_type"] == "On"
        assert result["prior_power_state"] == "Off"

    @pytest.mark.anyio
    async def test_invalid_action(self, mcp_tools, mock_host):
        result = await mcp_tools["redfish_power_control"](
            host=mock_host,
            user="admin",
            password="password",
            action="bogus",
            allow_write=True,
            verify_tls=False,
            timeout_s=15,
        )

        assert result["ok"] is False
        assert "Invalid action" in result["error"]
        assert "bogus" in result["error"]

    @pytest.mark.anyio
    async def test_requires_allow_write(self, mcp_tools, mock_host):
        result = await mcp_tools["redfish_power_control"](
            host=mock_host,
            user="admin",
            password="password",
            action="restart",
            allow_write=False,
        )

        assert result["ok"] is False
        assert "allow_write" in result["error"]

    @pytest.mark.anyio
    async def test_render_curl(self, mcp_tools):
        result = await mcp_tools["redfish_power_control"](
            host="192.168.1.1",
            user="admin",
            password="password",
            action="force_restart",
            allow_write=True,
            execution_mode="render_curl",
        )

        assert result["ok"] is True
        assert result["execution_mode"] == "render_curl"
        assert "curl" in result
        assert len(result["curl"]) > 0


class TestAgentObservationStore:
    @pytest.mark.anyio
    async def test_report_and_list_observations(self, mcp_tools, mock_host):
        report = await mcp_tools["redfish_agent_report_observation"](
            host=mock_host,
            kind="bmc",
            summary="BMC is flaky under concurrency",
            details={"note": "timeouts observed at >1 concurrent request"},
            tags=["triage", "timeouts"],
            confidence=0.8,
            ttl_hours=1,
        )
        assert report["ok"] is True
        assert report["host"] == mock_host
        assert isinstance(report["observation_id"], int)

        listed = await mcp_tools["redfish_agent_list_observations"](
            host=mock_host,
            limit=10,
        )
        assert listed["ok"] is True
        assert listed["host"] == mock_host
        assert listed["count"] >= 1
        assert any(o["summary"] == "BMC is flaky under concurrency" for o in listed["observations"])

    @pytest.mark.anyio
    async def test_get_host_stats_empty_ok(self, mcp_tools, mock_host):
        stats = await mcp_tools["redfish_agent_get_host_stats"](
            host=mock_host,
            window_minutes=5,
        )
        assert stats["ok"] is True
        assert stats["host"] == mock_host
        assert "calls_total" in stats
