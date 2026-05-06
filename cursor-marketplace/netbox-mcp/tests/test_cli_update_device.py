"""Tests for the CLI update-device command."""

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from netbox_mcp.cli import app

runner = CliRunner(mix_stderr=False)

MOCK_DEVICE = {
    "id": 42,
    "name": "gpu-node-01",
    "status": {"value": "active", "label": "Active"},
    "site": {"id": 1, "name": "DC1", "slug": "dc1"},
    "cluster": {"id": 5, "name": "cartesia5"},
}

UPDATED_DEVICE = {
    **MOCK_DEVICE,
    "status": {"value": "offline", "label": "Offline"},
}


def _all_output(result) -> str:
    """Combine stdout and stderr for assertion checks."""
    return (result.output or "") + (result.stderr or "")


class TestUpdateDeviceCLIValidation:
    def test_requires_confirm_flag(self):
        result = runner.invoke(app, ["update-device", "gpu-node-01", "--status", "offline"])
        assert result.exit_code != 0
        assert "--confirm" in _all_output(result)

    def test_requires_at_least_one_update_field(self):
        result = runner.invoke(app, ["update-device", "gpu-node-01", "--confirm"])
        assert result.exit_code != 0
        assert "at least one" in _all_output(result).lower()

    def test_rejects_invalid_status(self):
        result = runner.invoke(
            app, ["update-device", "gpu-node-01", "--status", "bogus", "--confirm"]
        )
        assert result.exit_code != 0
        assert "invalid status" in _all_output(result).lower()


class TestUpdateDeviceCLISuccess:
    @patch("netbox_mcp.cli._client")
    def test_updates_status(self, mock_client_fn):
        client = MagicMock()
        mock_client_fn.return_value = client
        client.get.return_value = {"count": 1, "results": [MOCK_DEVICE.copy()]}
        client.patch.return_value = UPDATED_DEVICE.copy()

        result = runner.invoke(
            app, ["update-device", "gpu-node-01", "--status", "offline", "--confirm"]
        )

        assert result.exit_code == 0
        assert "Updated device" in result.output
        assert "active → offline" in result.output
        client.patch.assert_called_once_with(
            "dcim/devices", id=42, data={"status": "offline"}
        )

    @patch("netbox_mcp.cli._client")
    def test_updates_by_numeric_id(self, mock_client_fn):
        client = MagicMock()
        mock_client_fn.return_value = client
        client.get.return_value = MOCK_DEVICE.copy()
        client.patch.return_value = UPDATED_DEVICE.copy()

        result = runner.invoke(
            app, ["update-device", "42", "--status", "offline", "--confirm"]
        )

        assert result.exit_code == 0
        client.get.assert_called_once_with("dcim/devices", id=42)

    @patch("netbox_mcp.cli._client")
    def test_updates_cluster(self, mock_client_fn):
        client = MagicMock()
        mock_client_fn.return_value = client
        client.get.side_effect = [
            {"count": 1, "results": [MOCK_DEVICE.copy()]},
            {"count": 1, "results": [{"id": 10, "name": "newcluster"}]},
        ]
        updated = {**MOCK_DEVICE, "cluster": {"id": 10, "name": "newcluster"}}
        client.patch.return_value = updated

        result = runner.invoke(
            app, ["update-device", "gpu-node-01", "--cluster", "newcluster", "--confirm"]
        )

        assert result.exit_code == 0
        assert "cartesia5 → newcluster" in result.output

    @patch("netbox_mcp.cli._client")
    def test_json_output(self, mock_client_fn):
        import json

        client = MagicMock()
        mock_client_fn.return_value = client
        client.get.return_value = {"count": 1, "results": [MOCK_DEVICE.copy()]}
        client.patch.return_value = UPDATED_DEVICE.copy()

        result = runner.invoke(
            app,
            ["update-device", "gpu-node-01", "--status", "offline", "--confirm", "--json"],
        )

        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["status"]["value"] == "offline"

    @patch("netbox_mcp.cli._client")
    def test_device_not_found(self, mock_client_fn):
        client = MagicMock()
        mock_client_fn.return_value = client
        client.get.return_value = {"count": 0, "results": []}

        result = runner.invoke(
            app, ["update-device", "nonexistent", "--status", "active", "--confirm"]
        )

        assert result.exit_code != 0

    @patch("netbox_mcp.cli._client")
    def test_ambiguous_device(self, mock_client_fn):
        client = MagicMock()
        mock_client_fn.return_value = client
        d1 = {**MOCK_DEVICE, "name": "gpu-node-01"}
        d2 = {**MOCK_DEVICE, "id": 43, "name": "gpu-node-02"}
        client.get.return_value = {"count": 2, "results": [d1, d2]}

        result = runner.invoke(
            app, ["update-device", "gpu-node", "--status", "active", "--confirm"]
        )

        assert result.exit_code != 0
        assert "multiple" in _all_output(result).lower()
