"""Tests for NetBoxRestClient.patch() method."""

from unittest.mock import MagicMock

import pytest

from netbox_mcp.netbox_client import NetBoxRestClient


@pytest.fixture
def client():
    return NetBoxRestClient(
        url="https://netbox.example.com/",
        token="test-token-123",
        verify_ssl=True,
    )


class TestPatch:
    def test_builds_correct_url(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'{"id": 42}'
        mock_resp.json.return_value = {"id": 42, "status": {"value": "active"}}
        client.session.patch = MagicMock(return_value=mock_resp)

        client.patch("dcim/devices", id=42, data={"status": "active"})

        client.session.patch.assert_called_once_with(
            "https://netbox.example.com/api/dcim/devices/42/",
            json={"status": "active"},
            verify=True,
            timeout=30,
        )

    def test_returns_response_json(self, client):
        expected = {"id": 42, "name": "gpu-node-01", "status": {"value": "active"}}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'{"id": 42}'
        mock_resp.json.return_value = expected
        client.session.patch = MagicMock(return_value=mock_resp)

        result = client.patch("dcim/devices", id=42, data={"status": "active"})

        assert result == expected

    def test_raises_on_http_error(self, client):
        import requests

        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.content = b'{"detail": "bad request"}'
        mock_resp.raise_for_status.side_effect = requests.HTTPError(response=mock_resp)
        client.session.patch = MagicMock(return_value=mock_resp)

        with pytest.raises(requests.HTTPError):
            client.patch("dcim/devices", id=42, data={"status": "invalid"})

    def test_sends_json_payload(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'{}'
        mock_resp.json.return_value = {"id": 42}
        client.session.patch = MagicMock(return_value=mock_resp)

        data = {"status": "offline", "cluster": 10}
        client.patch("dcim/devices", id=42, data=data)

        call_kwargs = client.session.patch.call_args
        assert call_kwargs.kwargs["json"] == data
