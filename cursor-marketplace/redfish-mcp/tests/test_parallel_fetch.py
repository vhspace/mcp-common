"""Tests for parallel_get_json / batch_get_json and inventory collector integration."""

from __future__ import annotations

import time
import unittest
from unittest.mock import MagicMock, patch

from redfish_mcp.firmware_inventory import collect_firmware_inventory
from redfish_mcp.redfish import (
    PARALLEL_MEMBER_THRESHOLD,
    RedfishEndpoint,
    batch_get_json,
    parallel_get_json,
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


if __name__ == "__main__":
    unittest.main()
