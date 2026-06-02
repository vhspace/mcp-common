"""Tests for VPN connectivity checking (Issue #13)."""

from unittest.mock import MagicMock

import pytest
import requests

from netbox_mcp.netbox_client import NetBoxRestClient, _is_cloudflare_block

# ---------------------------------------------------------------------------
# _is_cloudflare_block
# ---------------------------------------------------------------------------


class TestIsCloudflareBlock:
    def _make_response(self, status_code: int, content_type: str, body: str) -> MagicMock:
        resp = MagicMock(spec=requests.Response)
        resp.status_code = status_code
        resp.headers = {"content-type": content_type}
        resp.text = body
        return resp

    def test_detects_cloudflare_403_html(self):
        resp = self._make_response(
            403,
            "text/html",
            "<html><head><title>Attention Required! | Cloudflare</title></head>"
            "<body>__cf_chl_ challenge-platform</body></html>",
        )
        assert _is_cloudflare_block(resp) is True

    def test_ignores_non_403(self):
        resp = self._make_response(200, "text/html", "cloudflare")
        assert _is_cloudflare_block(resp) is False

    def test_ignores_json_403(self):
        """A real NetBox 403 returns JSON, not HTML."""
        resp = self._make_response(403, "application/json", '{"detail": "Authentication failed"}')
        assert _is_cloudflare_block(resp) is False

    def test_ignores_html_403_without_signatures(self):
        resp = self._make_response(403, "text/html", "<html><body>Access denied</body></html>")
        assert _is_cloudflare_block(resp) is False

    def test_detects_cf_mitigated(self):
        resp = self._make_response(
            403, "text/html; charset=utf-8", "<html>cf-mitigated: challenge</html>"
        )
        assert _is_cloudflare_block(resp) is True


# ---------------------------------------------------------------------------
# NetBoxRestClient.check_vpn
# ---------------------------------------------------------------------------


class TestCheckVpn:
    @pytest.fixture
    def client(self):
        return NetBoxRestClient(
            url="https://netbox.example.com/", token="test-token", verify_ssl=True
        )

    def test_returns_true_when_netbox_responds(self, client):
        """On VPN: PATCH to /status/ returns 405 JSON — not a CF block."""
        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.status_code = 405
        mock_resp.headers = {"content-type": "application/json"}
        mock_resp.text = '{"detail": "Method not allowed"}'
        client.session.patch = MagicMock(return_value=mock_resp)

        assert client.check_vpn() is True

    def test_returns_false_when_cloudflare_blocks(self, client):
        """Off VPN: PATCH to /status/ returns 403 CF HTML block."""
        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.status_code = 403
        mock_resp.headers = {"content-type": "text/html"}
        mock_resp.text = "<html>cloudflare __cf_chl_ blocked</html>"
        client.session.patch = MagicMock(return_value=mock_resp)

        assert client.check_vpn() is False

    def test_returns_false_on_connection_error(self, client):
        client.session.patch = MagicMock(side_effect=requests.ConnectionError("unreachable"))
        assert client.check_vpn() is False

    def test_returns_false_on_timeout(self, client):
        client.session.patch = MagicMock(side_effect=requests.Timeout("timed out"))
        assert client.check_vpn() is False


# ---------------------------------------------------------------------------
# VPNMonitor
# ---------------------------------------------------------------------------


class TestVPNMonitor:
    def test_is_connected_reflects_check_result(self):
        from netbox_mcp.server import VPNMonitor

        mock_client = MagicMock(spec=NetBoxRestClient)
        mock_client.check_vpn.return_value = True

        monitor = VPNMonitor(mock_client, interval=9999)
        monitor._check()
        assert monitor.is_connected is True

        mock_client.check_vpn.return_value = False
        monitor._check()
        assert monitor.is_connected is False

    def test_require_vpn_raises_when_disconnected(self):
        from netbox_mcp.server import VPNMonitor

        mock_client = MagicMock(spec=NetBoxRestClient)
        mock_client.check_vpn.return_value = False

        monitor = VPNMonitor(mock_client, interval=9999)
        monitor._check()

        with pytest.raises(ValueError, match="VPN not connected"):
            monitor.require_vpn()

    def test_require_vpn_passes_when_connected(self):
        from netbox_mcp.server import VPNMonitor

        mock_client = MagicMock(spec=NetBoxRestClient)
        mock_client.check_vpn.return_value = True

        monitor = VPNMonitor(mock_client, interval=9999)
        monitor._check()
        monitor.require_vpn()  # should not raise

    def test_start_and_stop(self):
        from netbox_mcp.server import VPNMonitor

        mock_client = MagicMock(spec=NetBoxRestClient)
        mock_client.check_vpn.return_value = True

        monitor = VPNMonitor(mock_client, interval=9999)
        monitor.start()
        assert monitor._thread is not None
        assert monitor._thread.is_alive()
        assert monitor.is_connected is True

        monitor.stop()
        assert not monitor._thread.is_alive()
