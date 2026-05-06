"""Tests for firmware_checker and firmware_update modules."""

import responses

from redfish_mcp.firmware_checker import (
    compare_versions,
    extract_bios_version_from_text,
    get_motherboard_from_model,
)
from redfish_mcp.firmware_update import (
    extract_task_url_from_upload,
    normalize_task_url,
    upload_firmware_image,
    wait_for_task_completion,
)
from redfish_mcp.redfish import RedfishClient


class TestCompareVersions:
    def test_same_version(self):
        assert compare_versions("3.7a", "3.7a") == "same"

    def test_older_minor(self):
        assert compare_versions("3.7a", "3.8a") == "older"

    def test_newer_minor(self):
        assert compare_versions("3.9a", "3.8a") == "newer"

    def test_older_suffix(self):
        assert compare_versions("3.8a", "3.8b") == "older"

    def test_newer_suffix(self):
        assert compare_versions("3.8c", "3.8b") == "newer"

    def test_older_major(self):
        assert compare_versions("2.8a", "3.8a") == "older"

    def test_newer_major(self):
        assert compare_versions("4.1", "3.8") == "newer"

    def test_no_suffix(self):
        assert compare_versions("3.7", "3.7") == "same"
        assert compare_versions("3.7", "3.8") == "older"

    def test_unparseable_both_same(self):
        # Both unparseable strings parse to (0, 0, "") — so they compare as "same"
        assert compare_versions("abc", "def") == "same"

    def test_unparseable_vs_parseable(self):
        # Unparseable (0,0,"") vs parseable (3,8,"a") — "older"
        assert compare_versions("abc", "3.8a") == "older"


class TestExtractBiosVersion:
    def test_bios_revision_pattern(self):
        text = "BIOS Revision: 3.8a release notes"
        assert extract_bios_version_from_text(text) == "3.8a"

    def test_filename_pattern(self):
        text = "Download H13DSG-O-CPU_3.8a_AS01.04.07.bin"
        assert extract_bios_version_from_text(text) == "3.8a"

    def test_ver_pattern(self):
        text = "Ver 3.8a"
        assert extract_bios_version_from_text(text) == "3.8a"

    def test_no_version_found(self):
        text = "No version here at all"
        assert extract_bios_version_from_text(text) is None


class TestGetMotherboardFromModel:
    def test_known_mapping(self):
        assert get_motherboard_from_model("PIO-8125GS-TNHR-NODE") == "H13DSG-O-CPU-D"

    def test_partial_match(self):
        assert get_motherboard_from_model("PIO-8125GS-TNHR-NODE-v2") == "H13DSG-O-CPU-D"

    def test_unknown_model(self):
        assert get_motherboard_from_model("Unknown-Server-Model") is None


class TestNormalizeTaskUrl:
    def test_none_input(self):
        assert normalize_task_url("https://host", None) is None

    def test_empty_input(self):
        assert normalize_task_url("https://host", "") is None
        assert normalize_task_url("https://host", "  ") is None

    def test_absolute_url_passthrough(self):
        url = "https://other/redfish/v1/Tasks/1"
        assert normalize_task_url("https://host", url) == url

    def test_relative_path(self):
        assert (
            normalize_task_url(
                "https://host",
                "/redfish/v1/TaskService/Tasks/42",
            )
            == "https://host/redfish/v1/TaskService/Tasks/42"
        )

    def test_relative_without_leading_slash(self):
        assert (
            normalize_task_url(
                "https://host",
                "redfish/v1/Tasks/1",
            )
            == "https://host/redfish/v1/Tasks/1"
        )


def _mock_resp(headers=None, body=None):
    """Create a mock HTTP response for testing."""

    class MockResp:
        pass

    r = MockResp()
    r.headers = headers or {}
    r.json = lambda: body or {}
    return r


class TestExtractTaskUrlFromUpload:
    def test_from_location_header(self):
        resp = _mock_resp(headers={"Location": "/redfish/v1/Tasks/42"})
        url = extract_task_url_from_upload(resp, "https://host")
        assert url == "https://host/redfish/v1/Tasks/42"

    def test_from_body_odata_id(self):
        resp = _mock_resp(body={"@odata.id": "/redfish/v1/Tasks/99"})
        url = extract_task_url_from_upload(resp, "https://host")
        assert url == "https://host/redfish/v1/Tasks/99"

    def test_from_body_task_monitor(self):
        resp = _mock_resp(body={"TaskMonitor": "/redfish/v1/TaskMonitor/77"})
        url = extract_task_url_from_upload(resp, "https://host")
        assert url == "https://host/redfish/v1/TaskMonitor/77"

    def test_no_task_url_found(self):
        resp = _mock_resp()
        url = extract_task_url_from_upload(resp, "https://host")
        assert url is None


class TestWaitForTaskCompletion:
    @responses.activate
    def test_completed_immediately(self):
        task_url = "https://host/redfish/v1/Tasks/1"
        responses.add(
            responses.GET,
            task_url,
            json={"TaskState": "Completed", "Messages": [{"Message": "Done"}]},
            status=200,
        )
        c = RedfishClient(host="host", user="a", password="b", verify_tls=False, timeout_s=5)
        result = wait_for_task_completion(c, task_url, timeout_s=10, poll_interval_s=0)
        assert result["ok"] is True
        assert result["task_state"] == "Completed"

    @responses.activate
    def test_task_exception(self):
        task_url = "https://host/redfish/v1/Tasks/1"
        responses.add(
            responses.GET,
            task_url,
            json={"TaskState": "Exception", "Messages": [{"Message": "Error"}]},
            status=200,
        )
        c = RedfishClient(host="host", user="a", password="b", verify_tls=False, timeout_s=5)
        result = wait_for_task_completion(c, task_url, timeout_s=10, poll_interval_s=0)
        assert result["ok"] is False
        assert result["task_state"] == "Exception"

    @responses.activate
    def test_task_404_without_prior_success(self):
        task_url = "https://host/redfish/v1/Tasks/1"
        responses.add(responses.GET, task_url, status=404)
        c = RedfishClient(host="host", user="a", password="b", verify_tls=False, timeout_s=5)
        result = wait_for_task_completion(c, task_url, timeout_s=10, poll_interval_s=0)
        assert result["ok"] is False
        assert result["task_state"] == "NotFound"

    @responses.activate
    def test_task_monitor_404_after_success_is_ok(self):
        task_url = "https://host/redfish/v1/TaskMonitor/1"
        responses.add(
            responses.GET,
            task_url,
            json={"TaskState": "Running", "Messages": []},
            status=200,
        )
        responses.add(responses.GET, task_url, status=404)
        c = RedfishClient(host="host", user="a", password="b", verify_tls=False, timeout_s=5)
        result = wait_for_task_completion(c, task_url, timeout_s=10, poll_interval_s=0)
        assert result["ok"] is True
        assert result["task_state"] == "Completed"
        assert "TaskMonitor disappeared" in result.get("note", "")

    @responses.activate
    def test_upload_firmware_missing_file(self):
        c = RedfishClient(host="host", user="a", password="b", verify_tls=False, timeout_s=5)
        result = upload_firmware_image(c, "/nonexistent/firmware.bin")
        assert result["ok"] is False
        assert "Failed to upload" in result["error"]
