"""Tests for parallel_get_json / batch_get_json and inventory collector integration."""

from __future__ import annotations

import os
import time
import unittest
from unittest.mock import MagicMock, patch

from redfish_mcp.firmware_inventory import collect_firmware_inventory
from redfish_mcp.redfish import (
    DEFAULT_PARALLEL_WORKERS,
    PARALLEL_MEMBER_THRESHOLD,
    RedfishClient,
    RedfishEndpoint,
    batch_get_json,
    parallel_get_json,
    resolve_parallel_workers,
)
from redfish_mcp.system_inventory import collect_processor_inventory


def _mock_client(responses: dict[str, tuple[dict | None, str | None]]) -> MagicMock:
    """Create a mock RedfishClient whose session.get returns canned responses."""
    c = MagicMock()
    c.base_url = "https://10.0.0.1"

    def get_json_maybe(url: str):
        for suffix, (data, err) in sorted(responses.items(), key=lambda x: -len(x[0])):
            if url.endswith(suffix):
                return data, err
        return None, f"404 not found: {url}"

    c.get_json_maybe = MagicMock(side_effect=get_json_maybe)

    def session_get(url: str, timeout: float | None = None):
        for suffix, (data, err) in sorted(responses.items(), key=lambda x: -len(x[0])):
            if url.endswith(suffix):
                if err:
                    mock_r = MagicMock()
                    mock_r.status_code = 404
                    mock_r.text = err
                    return mock_r
                mock_r = MagicMock()
                mock_r.status_code = 200
                mock_r.json.return_value = data
                return mock_r
        mock_r = MagicMock()
        mock_r.status_code = 404
        mock_r.text = f"not found: {url}"
        return mock_r

    c.session = MagicMock()
    c.session.get = MagicMock(side_effect=session_get)
    return c


class TestParallelGetJson(unittest.TestCase):
    def test_empty_urls(self):
        c = _mock_client({})
        assert parallel_get_json(c, []) == []

    def test_single_url_success(self):
        c = _mock_client({"/foo": ({"ok": True}, None)})
        results = parallel_get_json(c, ["https://10.0.0.1/foo"])
        assert len(results) == 1
        url, data, err = results[0]
        assert url == "https://10.0.0.1/foo"
        assert data == {"ok": True}
        assert err is None

    def test_multiple_urls_preserves_order(self):
        c = _mock_client(
            {
                "/a": ({"id": "a"}, None),
                "/b": ({"id": "b"}, None),
                "/c": ({"id": "c"}, None),
            }
        )
        urls = [
            "https://10.0.0.1/c",
            "https://10.0.0.1/a",
            "https://10.0.0.1/b",
        ]
        results = parallel_get_json(c, urls)
        assert [r[0] for r in results] == urls
        assert results[0][1] == {"id": "c"}
        assert results[1][1] == {"id": "a"}
        assert results[2][1] == {"id": "b"}

    def test_partial_failure(self):
        c = _mock_client(
            {
                "/good": ({"ok": True}, None),
                "/bad": (None, "500 internal error"),
            }
        )
        results = parallel_get_json(
            c,
            [
                "https://10.0.0.1/good",
                "https://10.0.0.1/bad",
            ],
        )
        assert results[0][1] == {"ok": True}
        assert results[0][2] is None
        assert results[1][1] is None
        assert results[1][2] is not None

    def test_all_failures(self):
        c = _mock_client(
            {
                "/x": (None, "timeout"),
                "/y": (None, "refused"),
            }
        )
        results = parallel_get_json(
            c,
            [
                "https://10.0.0.1/x",
                "https://10.0.0.1/y",
            ],
        )
        assert all(r[1] is None for r in results)
        assert all(r[2] is not None for r in results)

    def test_connection_exception_handled(self):
        c = MagicMock()
        c.session = MagicMock()
        c.session.get = MagicMock(side_effect=ConnectionError("refused"))
        results = parallel_get_json(c, ["https://10.0.0.1/fail"])
        assert results[0][1] is None
        assert "refused" in results[0][2]

    def test_actually_parallel(self):
        """Verify that multiple requests execute concurrently, not serially."""
        call_count = 0
        delay_s = 0.2

        def slow_get(url: str, timeout: float | None = None):
            nonlocal call_count
            call_count += 1
            time.sleep(delay_s)
            mock_r = MagicMock()
            mock_r.status_code = 200
            mock_r.json.return_value = {"url": url}
            return mock_r

        c = MagicMock()
        c.session = MagicMock()
        c.session.get = MagicMock(side_effect=slow_get)

        urls = [f"https://10.0.0.1/item/{i}" for i in range(4)]
        start = time.monotonic()
        results = parallel_get_json(c, urls, max_workers=4)
        elapsed = time.monotonic() - start

        assert len(results) == 4
        assert all(r[1] is not None for r in results)
        # Serial would be ~0.8s; parallel should be ~0.2s
        assert elapsed < delay_s * len(urls) * 0.75

    def test_non_json_response(self):
        """Non-JSON 200 responses are returned as errors, not crashes."""
        c = MagicMock()
        c.session = MagicMock()
        mock_r = MagicMock()
        mock_r.status_code = 200
        mock_r.json.side_effect = ValueError("not JSON")
        c.session.get = MagicMock(return_value=mock_r)

        results = parallel_get_json(c, ["https://10.0.0.1/html-page"])
        assert results[0][1] is None
        assert "non-json" in results[0][2].lower()

    def test_collection_timeout(self):
        """Requests that haven't started by collection deadline get timeout errors."""

        def very_slow_get(url: str, timeout: float | None = None):
            time.sleep(5)
            mock_r = MagicMock()
            mock_r.status_code = 200
            mock_r.json.return_value = {}
            return mock_r

        c = MagicMock()
        c.session = MagicMock()
        c.session.get = MagicMock(side_effect=very_slow_get)

        results = parallel_get_json(
            c,
            ["https://10.0.0.1/slow"],
            collection_timeout_s=1,
            per_request_timeout_s=1,
        )
        assert len(results) == 1


class TestBatchGetJson(unittest.TestCase):
    """batch_get_json routes to serial or parallel based on threshold."""

    def test_below_threshold_uses_serial(self):
        c = _mock_client({"/a": ({"ok": True}, None)})
        with patch("redfish_mcp.redfish.parallel_get_json") as mock_par:
            results = batch_get_json(c, ["https://10.0.0.1/a"])
            mock_par.assert_not_called()
        assert results[0][1] == {"ok": True}

    def test_above_threshold_uses_parallel(self):
        urls = [f"https://10.0.0.1/{i}" for i in range(PARALLEL_MEMBER_THRESHOLD + 1)]
        c = _mock_client({f"/{i}": ({"i": i}, None) for i in range(PARALLEL_MEMBER_THRESHOLD + 1)})
        with patch(
            "redfish_mcp.redfish.parallel_get_json", return_value=[(u, {}, None) for u in urls]
        ) as mock_par:
            batch_get_json(c, urls)
            mock_par.assert_called_once()


class TestFirmwareInventoryParallelPath(unittest.TestCase):
    """Firmware inventory uses batch_get_json for member fetching."""

    def _make_members(self, n: int) -> list[dict[str, str]]:
        return [
            {"@odata.id": f"/redfish/v1/UpdateService/FirmwareInventory/Comp{i}"} for i in range(n)
        ]

    def _make_responses(self, n: int) -> dict[str, tuple[dict | None, str | None]]:
        responses: dict[str, tuple[dict | None, str | None]] = {
            "/FirmwareInventory": ({"Members": self._make_members(n)}, None),
        }
        for i in range(n):
            responses[f"/Comp{i}"] = (
                {"Id": f"Comp{i}", "Name": f"Component {i}", "Version": f"1.{i}"},
                None,
            )
        return responses

    @patch("redfish_mcp.firmware_inventory.batch_get_json")
    def test_many_members_uses_batch(self, mock_batch):
        n = PARALLEL_MEMBER_THRESHOLD + 1
        responses = self._make_responses(n)
        c = _mock_client(responses)

        mock_batch.return_value = [
            (
                f"https://10.0.0.1/redfish/v1/UpdateService/FirmwareInventory/Comp{i}",
                {"Id": f"Comp{i}", "Name": f"Component {i}", "Version": f"1.{i}"},
                None,
            )
            for i in range(n)
        ]

        ep = RedfishEndpoint(base_url="https://10.0.0.1", system_path="/redfish/v1/Systems/1")
        result = collect_firmware_inventory(c, ep)

        mock_batch.assert_called_once()
        assert result["component_count"] == n

    def test_few_members_end_to_end(self):
        n = 3
        responses = self._make_responses(n)
        c = _mock_client(responses)
        ep = RedfishEndpoint(base_url="https://10.0.0.1", system_path="/redfish/v1/Systems/1")
        result = collect_firmware_inventory(c, ep)
        assert result["component_count"] == n


class TestProcessorInventoryParallelPath(unittest.TestCase):
    """Processor inventory uses batch_get_json for member fetching."""

    def _make_members(self, n: int) -> list[dict[str, str]]:
        return [{"@odata.id": f"/redfish/v1/Systems/1/Processors/CPU{i}"} for i in range(n)]

    def _make_responses(self, n: int) -> dict[str, tuple[dict | None, str | None]]:
        responses: dict[str, tuple[dict | None, str | None]] = {
            "/Processors": ({"Members": self._make_members(n)}, None),
        }
        for i in range(n):
            responses[f"/CPU{i}"] = (
                {
                    "Id": f"CPU{i}",
                    "Manufacturer": "AMD",
                    "Model": "EPYC 9654",
                    "TotalCores": 96,
                    "Status": {"Health": "OK"},
                },
                None,
            )
        return responses

    @patch("redfish_mcp.system_inventory.batch_get_json")
    def test_many_processors_uses_batch(self, mock_batch):
        n = PARALLEL_MEMBER_THRESHOLD + 1
        responses = self._make_responses(n)
        c = _mock_client(responses)

        mock_batch.return_value = [
            (
                f"https://10.0.0.1/redfish/v1/Systems/1/Processors/CPU{i}",
                {
                    "Id": f"CPU{i}",
                    "Manufacturer": "AMD",
                    "Model": "EPYC 9654",
                    "TotalCores": 96,
                    "Status": {"Health": "OK"},
                },
                None,
            )
            for i in range(n)
        ]

        ep = RedfishEndpoint(base_url="https://10.0.0.1", system_path="/redfish/v1/Systems/1")
        result = collect_processor_inventory(c, ep)

        mock_batch.assert_called_once()
        assert result["count"] == n

    def test_few_processors_end_to_end(self):
        n = 2
        responses = self._make_responses(n)
        c = _mock_client(responses)
        ep = RedfishEndpoint(base_url="https://10.0.0.1", system_path="/redfish/v1/Systems/1")
        result = collect_processor_inventory(c, ep)
        assert result["count"] == n

    @patch("redfish_mcp.system_inventory.batch_get_json")
    def test_b300_nine_processors_parallel(self, mock_batch):
        """B300 has 9 processor members (2 host CPUs + HGX GPU processors) -- must go parallel."""
        n = 9
        responses = self._make_responses(n)
        c = _mock_client(responses)

        mock_batch.return_value = [
            (
                f"https://10.0.0.1/redfish/v1/Systems/1/Processors/CPU{i}",
                {
                    "Id": f"CPU{i}",
                    "Manufacturer": "NVIDIA" if i >= 2 else "AMD",
                    "Model": "B200" if i >= 2 else "EPYC 9654",
                    "TotalCores": 1 if i >= 2 else 96,
                    "Status": {"Health": "OK"},
                },
                None,
            )
            for i in range(n)
        ]

        ep = RedfishEndpoint(base_url="https://10.0.0.1", system_path="/redfish/v1/Systems/1")
        result = collect_processor_inventory(c, ep)

        mock_batch.assert_called_once()
        assert result["count"] == 9


class TestSupportsParallelReads(unittest.TestCase):
    """RedfishClient.supports_parallel_reads() vendor classification."""

    @staticmethod
    def _client(root, systems=None, *, root_err=None, systems_err=None):
        c = RedfishClient("10.0.0.1", "u", "p", verify_tls=False, timeout_s=5)

        def gjm(url: str):
            if url.endswith("/redfish/v1/Systems"):
                return systems, systems_err
            if url.endswith("/redfish/v1"):
                return root, root_err
            return None, "404"

        c.get_json_maybe = MagicMock(side_effect=gjm)
        return c

    def test_nvidia_via_oem(self):
        c = self._client({"Oem": {"Nvidia": {}}})
        assert c.supports_parallel_reads() is True

    def test_nvidia_via_vendor_field(self):
        c = self._client({"Vendor": "NVIDIA"})
        assert c.supports_parallel_reads() is True

    def test_openbmc_via_product(self):
        c = self._client({"Product": "OpenBMC by NVIDIA"})
        assert c.supports_parallel_reads() is True

    def test_supermicro_is_serial(self):
        c = self._client({"Oem": {"Supermicro": {}}})
        assert c.supports_parallel_reads() is False
        # Fragile vendor short-circuits — the Systems collection is never probed.
        assert c.get_json_maybe.call_count == 1

    def test_gigabyte_ami_is_serial(self):
        c = self._client({"Oem": {"Ami": {}}})
        assert c.supports_parallel_reads() is False

    def test_dell_with_hgx_baseboard_is_parallel(self):
        c = self._client(
            {"Oem": {"Dell": {}}},
            systems={
                "Members": [
                    {"@odata.id": "/redfish/v1/Systems/System.Embedded.1"},
                    {"@odata.id": "/redfish/v1/Systems/HGX_Baseboard_0"},
                ]
            },
        )
        assert c.supports_parallel_reads() is True

    def test_dell_without_hgx_is_serial(self):
        c = self._client(
            {"Oem": {"Dell": {}}},
            systems={"Members": [{"@odata.id": "/redfish/v1/Systems/System.Embedded.1"}]},
        )
        assert c.supports_parallel_reads() is False

    def test_unknown_with_hgx_member_is_parallel(self):
        c = self._client(
            {"Product": "Generic BMC"},
            systems={"Members": [{"@odata.id": "/redfish/v1/Systems/HGX_Baseboard_0"}]},
        )
        assert c.supports_parallel_reads() is True

    def test_root_error_then_no_hgx_is_serial(self):
        c = self._client(None, root_err="500 error", systems={"Members": []})
        assert c.supports_parallel_reads() is False

    def test_verdict_is_cached(self):
        c = self._client({"Oem": {"Nvidia": {}}})
        assert c.supports_parallel_reads() is True
        first = c.get_json_maybe.call_count
        assert c.supports_parallel_reads() is True
        assert c.get_json_maybe.call_count == first  # cached, no re-probe

    def test_explicit_override_skips_detection(self):
        c = self._client({"Oem": {"Supermicro": {}}})
        c._parallel_reads_ok = True
        assert c.supports_parallel_reads() is True
        c.get_json_maybe.assert_not_called()


class TestBatchGetJsonVendorGating(unittest.TestCase):
    """batch_get_json honors the vendor verdict / explicit parallel_ok flag."""

    @staticmethod
    def _urls():
        return [f"https://10.0.0.1/c/{i}" for i in range(PARALLEL_MEMBER_THRESHOLD + 1)]

    def test_robust_vendor_fans_out(self):
        urls = self._urls()
        c = _mock_client({})
        c.supports_parallel_reads = MagicMock(return_value=True)
        with patch(
            "redfish_mcp.redfish.parallel_get_json",
            return_value=[(u, {}, None) for u in urls],
        ) as mock_par:
            batch_get_json(c, urls)
            mock_par.assert_called_once()

    def test_fragile_vendor_stays_serial(self):
        urls = self._urls()
        c = _mock_client({f"/c/{i}": ({"i": i}, None) for i in range(len(urls))})
        c.supports_parallel_reads = MagicMock(return_value=False)
        with patch("redfish_mcp.redfish.parallel_get_json") as mock_par:
            results = batch_get_json(c, urls)
            mock_par.assert_not_called()
        assert len(results) == len(urls)

    def test_explicit_parallel_ok_false_overrides_robust_client(self):
        urls = self._urls()
        c = _mock_client({f"/c/{i}": ({"i": i}, None) for i in range(len(urls))})
        c.supports_parallel_reads = MagicMock(return_value=True)
        with patch("redfish_mcp.redfish.parallel_get_json") as mock_par:
            batch_get_json(c, urls, parallel_ok=False)
            mock_par.assert_not_called()
            c.supports_parallel_reads.assert_not_called()

    def test_explicit_parallel_ok_true(self):
        urls = self._urls()
        c = _mock_client({})
        with patch(
            "redfish_mcp.redfish.parallel_get_json",
            return_value=[(u, {}, None) for u in urls],
        ) as mock_par:
            batch_get_json(c, urls, parallel_ok=True)
            mock_par.assert_called_once()


class TestResolveParallelWorkers(unittest.TestCase):
    """REDFISH_PARALLEL_WORKERS env override resolution."""

    def test_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("REDFISH_PARALLEL_WORKERS", None)
            assert resolve_parallel_workers() == DEFAULT_PARALLEL_WORKERS

    def test_override(self):
        with patch.dict(os.environ, {"REDFISH_PARALLEL_WORKERS": "4"}):
            assert resolve_parallel_workers() == 4

    def test_one_disables(self):
        with patch.dict(os.environ, {"REDFISH_PARALLEL_WORKERS": "1"}):
            assert resolve_parallel_workers() == 1

    def test_zero_clamped_to_one(self):
        with patch.dict(os.environ, {"REDFISH_PARALLEL_WORKERS": "0"}):
            assert resolve_parallel_workers() == 1

    def test_negative_clamped_to_one(self):
        with patch.dict(os.environ, {"REDFISH_PARALLEL_WORKERS": "-3"}):
            assert resolve_parallel_workers() == 1

    def test_invalid_falls_back_to_default(self):
        with patch.dict(os.environ, {"REDFISH_PARALLEL_WORKERS": "lots"}):
            assert resolve_parallel_workers() == DEFAULT_PARALLEL_WORKERS


class TestBatchGetJsonWorkerEnv(unittest.TestCase):
    """batch_get_json respects the worker-count env knob."""

    @staticmethod
    def _urls():
        return [f"https://10.0.0.1/c/{i}" for i in range(PARALLEL_MEMBER_THRESHOLD + 1)]

    def test_env_one_forces_serial_even_for_robust(self):
        urls = self._urls()
        c = _mock_client({f"/c/{i}": ({"i": i}, None) for i in range(len(urls))})
        c.supports_parallel_reads = MagicMock(return_value=True)
        with (
            patch.dict(os.environ, {"REDFISH_PARALLEL_WORKERS": "1"}),
            patch("redfish_mcp.redfish.parallel_get_json") as mock_par,
        ):
            results = batch_get_json(c, urls)
            mock_par.assert_not_called()
        assert len(results) == len(urls)

    def test_env_changes_worker_count(self):
        urls = self._urls()
        c = _mock_client({})
        c.supports_parallel_reads = MagicMock(return_value=True)
        with (
            patch.dict(os.environ, {"REDFISH_PARALLEL_WORKERS": "3"}),
            patch(
                "redfish_mcp.redfish.parallel_get_json",
                return_value=[(u, {}, None) for u in urls],
            ) as mock_par,
        ):
            batch_get_json(c, urls)
            mock_par.assert_called_once()
            _, kwargs = mock_par.call_args
            assert kwargs["max_workers"] == 3


class TestCollectorVendorGating(unittest.TestCase):
    """Collectors fan out only for robust BMCs (vendor verdict mocked)."""

    def _firmware_responses(self, n):
        members = [
            {"@odata.id": f"/redfish/v1/UpdateService/FirmwareInventory/Comp{i}"} for i in range(n)
        ]
        responses = {"/FirmwareInventory": ({"Members": members}, None)}
        for i in range(n):
            responses[f"/Comp{i}"] = ({"Id": f"Comp{i}", "Version": f"1.{i}"}, None)
        return responses

    @patch("redfish_mcp.redfish.parallel_get_json")
    def test_firmware_fans_out_for_robust_bmc(self, mock_par):
        n = PARALLEL_MEMBER_THRESHOLD + 1
        c = _mock_client(self._firmware_responses(n))
        c.supports_parallel_reads = MagicMock(return_value=True)
        comp_urls = [
            f"https://10.0.0.1/redfish/v1/UpdateService/FirmwareInventory/Comp{i}" for i in range(n)
        ]
        mock_par.return_value = [
            (u, {"Id": f"Comp{i}", "Version": f"1.{i}"}, None) for i, u in enumerate(comp_urls)
        ]
        ep = RedfishEndpoint(base_url="https://10.0.0.1", system_path="/redfish/v1/Systems/1")
        result = collect_firmware_inventory(c, ep)
        mock_par.assert_called_once()
        assert result["component_count"] == n

    @patch("redfish_mcp.redfish.parallel_get_json")
    def test_firmware_serial_for_fragile_bmc(self, mock_par):
        n = PARALLEL_MEMBER_THRESHOLD + 1
        c = _mock_client(self._firmware_responses(n))
        c.supports_parallel_reads = MagicMock(return_value=False)
        ep = RedfishEndpoint(base_url="https://10.0.0.1", system_path="/redfish/v1/Systems/1")
        result = collect_firmware_inventory(c, ep)
        mock_par.assert_not_called()
        assert result["component_count"] == n

    @patch("redfish_mcp.redfish.parallel_get_json")
    def test_processors_fan_out_for_robust_bmc(self, mock_par):
        n = PARALLEL_MEMBER_THRESHOLD + 1
        members = [{"@odata.id": f"/redfish/v1/Systems/1/Processors/CPU{i}"} for i in range(n)]
        responses = {"/Processors": ({"Members": members}, None)}
        for i in range(n):
            responses[f"/CPU{i}"] = ({"Id": f"CPU{i}", "Manufacturer": "NVIDIA"}, None)
        c = _mock_client(responses)
        c.supports_parallel_reads = MagicMock(return_value=True)
        proc_urls = [f"https://10.0.0.1/redfish/v1/Systems/1/Processors/CPU{i}" for i in range(n)]
        mock_par.return_value = [
            (u, {"Id": f"CPU{i}", "Manufacturer": "NVIDIA"}, None) for i, u in enumerate(proc_urls)
        ]
        ep = RedfishEndpoint(base_url="https://10.0.0.1", system_path="/redfish/v1/Systems/1")
        result = collect_processor_inventory(c, ep)
        mock_par.assert_called_once()
        assert result["count"] == n


if __name__ == "__main__":
    unittest.main()
