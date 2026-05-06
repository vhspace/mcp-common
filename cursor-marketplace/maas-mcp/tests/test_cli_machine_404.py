"""CLI commands — MAAS 404 handling and NetBox auto-resolve across all system_id-based commands."""

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from maas_mcp.cli import app
from maas_mcp.netbox_resolve import NetboxResolveFailureKind, NetboxResolveResult

_404_ERROR = (
    "MAAS GET http://maas.example.com:5240/MAAS/api/2.0/machines/abc/ "
    'failed: 404 {"error":"Not found"}'
)
_500_ERROR = (
    "MAAS GET http://maas.example.com:5240/MAAS/api/2.0/machines/abc/ failed: 500 server error"
)

_NETBOX_RESOLVE_PATCH = "maas_mcp.cli._resolve_via_netbox"


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner(mix_stderr=False)


def _mock_client_404() -> MagicMock:
    c = MagicMock()
    c.get.side_effect = RuntimeError(_404_ERROR)
    return c


def _assert_clean_404(result, *, check_remediation: bool = True) -> None:
    assert result.exit_code == 1
    err = result.stderr or ""
    assert "not found" in err.lower()
    assert "404" in err
    if check_remediation:
        assert "subagent" in err.lower()
        assert "thumbs-up" in err.lower()
    assert "Traceback" not in err


# --- machine command ---


def test_machine_404_prints_concise_message_and_remediation_no_traceback(
    runner: CliRunner,
) -> None:
    with (
        patch("maas_mcp.cli._get_client", return_value=("default", _mock_client_404())),
        patch(
            _NETBOX_RESOLVE_PATCH,
            return_value=NetboxResolveResult(None, NetboxResolveFailureKind.NOT_CONFIGURED),
        ),
    ):
        result = runner.invoke(app, ["machine", "abc"])
    _assert_clean_404(result)


def test_machine_non_404_runtime_error_not_silenced(runner: CliRunner) -> None:
    mock_client = MagicMock()
    mock_client.get.side_effect = RuntimeError(_500_ERROR)
    with patch("maas_mcp.cli._get_client", return_value=("default", mock_client)):
        result = runner.invoke(app, ["machine", "abc"])
    assert result.exit_code != 0
    assert result.exception is not None


def test_machine_404_auto_resolves_via_netbox(runner: CliRunner) -> None:
    """On 404, the CLI resolves via NetBox and retries with the real system_id."""
    mock_client = MagicMock()
    mock_client.get.side_effect = [
        RuntimeError(_404_ERROR),
        {
            "system_id": "xyz789",
            "hostname": "gpu001",
            "status_name": "Deployed",
            "power_state": "on",
            "zone": {"name": "z"},
            "pool": {"name": "p"},
            "cpu_count": 128,
            "memory": 1048576,
        },
    ]

    with (
        patch("maas_mcp.cli._get_client", return_value=("default", mock_client)),
        patch(_NETBOX_RESOLVE_PATCH, return_value=NetboxResolveResult.success("xyz789")),
    ):
        result = runner.invoke(app, ["machine", "abc"])

    assert result.exit_code == 0
    mock_client.get.assert_any_call("machines/xyz789")


# --- results command ---


def test_results_404(runner: CliRunner) -> None:
    with (
        patch("maas_mcp.cli._get_client", return_value=("default", _mock_client_404())),
        patch(
            _NETBOX_RESOLVE_PATCH,
            return_value=NetboxResolveResult(None, NetboxResolveFailureKind.NOT_CONFIGURED),
        ),
    ):
        result = runner.invoke(app, ["results", "abc"])
    _assert_clean_404(result)


def test_results_404_auto_resolves_via_netbox(runner: CliRunner) -> None:
    mock_client = MagicMock()
    mock_client.get.side_effect = [
        RuntimeError(_404_ERROR),
        [],
    ]
    with (
        patch("maas_mcp.cli._get_client", return_value=("default", mock_client)),
        patch(_NETBOX_RESOLVE_PATCH, return_value=NetboxResolveResult.success("xyz789")),
    ):
        result = runner.invoke(app, ["results", "abc"])
    assert result.exit_code == 0


# --- op command ---


def test_op_404(runner: CliRunner) -> None:
    with (
        patch("maas_mcp.cli._get_client", return_value=("default", _mock_client_404())),
        patch(
            _NETBOX_RESOLVE_PATCH,
            return_value=NetboxResolveResult(None, NetboxResolveFailureKind.DEVICE_NOT_FOUND),
        ),
    ):
        result = runner.invoke(app, ["op", "abc", "power_on", "--yes"])
    _assert_clean_404(result)


def test_op_404_auto_resolves_via_netbox(runner: CliRunner) -> None:
    mock_client = MagicMock()
    mock_client.get.side_effect = [
        RuntimeError(_404_ERROR),
        {"system_id": "xyz789", "hostname": "gpu001", "status_name": "Ready", "power_state": "off"},
    ]
    mock_client.post_fire.return_value = (
        {"system_id": "xyz789", "status_name": "Commissioning", "power_state": "on"},
        False,
    )
    with (
        patch("maas_mcp.cli._get_client", return_value=("default", mock_client)),
        patch(_NETBOX_RESOLVE_PATCH, return_value=NetboxResolveResult.success("xyz789")),
    ):
        result = runner.invoke(app, ["op", "abc", "power_on", "--yes", "--no-wait"])
    assert result.exit_code == 0


def test_op_poll_404_exits_without_traceback(runner: CliRunner) -> None:
    """Mid-operation poll must not dump a traceback if the machine disappears (404)."""
    mock_client = MagicMock()
    mock_client.get.side_effect = [
        {
            "system_id": "x1",
            "hostname": "h",
            "status_name": "Ready",
            "power_state": "off",
        },
        RuntimeError(_404_ERROR),
    ]
    mock_client.post_fire.return_value = (
        {"status_name": "Commissioning", "power_state": "on"},
        False,
    )
    with (
        patch("time.sleep"),
        patch("maas_mcp.cli._get_client", return_value=("default", mock_client)),
    ):
        result = runner.invoke(
            app,
            ["op", "x1", "power_on", "--yes", "--timeout", "30"],
        )
    assert result.exit_code == 1
    err = result.stderr or ""
    assert "Traceback" not in err
    assert "404" in err


# --- wait command ---


def test_wait_404(runner: CliRunner) -> None:
    with (
        patch("maas_mcp.cli._build_clients", return_value={"default": _mock_client_404()}),
        patch(
            _NETBOX_RESOLVE_PATCH,
            return_value=NetboxResolveResult(None, NetboxResolveFailureKind.NOT_CONFIGURED),
        ),
    ):
        result = runner.invoke(app, ["wait", "--id", "abc", "--until", "deployed"])
    _assert_clean_404(result)


def test_wait_poll_404_exits_without_traceback(runner: CliRunner) -> None:
    """During wait polling, 404 should surface as a clean CLI error."""
    mock_client = MagicMock()
    mock_client.get.side_effect = [
        {
            "system_id": "x1",
            "hostname": "h",
            "status_name": "Deploying",
            "power_state": "on",
        },
        RuntimeError(_404_ERROR),
    ]
    with (
        patch("time.sleep"),
        patch("maas_mcp.cli._build_clients", return_value={"default": mock_client}),
    ):
        result = runner.invoke(
            app,
            ["wait", "--id", "x1", "--until", "deployed", "--timeout", "30"],
        )
    assert result.exit_code == 1
    err = result.stderr or ""
    assert "Traceback" not in err
    assert "404" in err


# --- create-bond command ---


def test_create_bond_404(runner: CliRunner) -> None:
    with (
        patch("maas_mcp.cli._get_client", return_value=("default", _mock_client_404())),
        patch(
            _NETBOX_RESOLVE_PATCH,
            return_value=NetboxResolveResult(None, NetboxResolveFailureKind.NOT_CONFIGURED),
        ),
    ):
        result = runner.invoke(app, ["create-bond", "abc", "--yes"])
    _assert_clean_404(result)


# ---------------------------------------------------------------------------
# _resolve_via_netbox unit tests
# ---------------------------------------------------------------------------


class TestResolveViaNetbox:
    """Test the _resolve_via_netbox helper in isolation."""

    def test_returns_system_id_on_success(self) -> None:
        from maas_mcp.cli import _resolve_via_netbox

        mock_client = MagicMock()
        mock_client.get.return_value = [
            {"system_id": "xyz789", "hostname": "gpu001"},
        ]

        mock_nb = MagicMock()
        mock_nb.lookup_device.return_value = {
            "name": "research-common-h100-001",
            "custom_fields": {"Provider_Machine_ID": "gpu001"},
        }

        with (
            patch("maas_mcp.netbox_resolve.Settings") as mock_settings_cls,
            patch("maas_mcp.netbox_resolve.NetboxClient", return_value=mock_nb),
        ):
            settings = MagicMock()
            settings.netbox_url = "https://netbox.example.com"
            settings.netbox_token = MagicMock()
            settings.netbox_token.get_secret_value.return_value = "tok"
            mock_settings_cls.return_value = settings

            result = _resolve_via_netbox("research-common-h100-001", mock_client)

        assert result.system_id == "xyz789"
        assert result.ok
        mock_nb.lookup_device.assert_called_once_with("research-common-h100-001")
        mock_client.get.assert_called_once_with("machines", params={"hostname": "gpu001"})

    def test_returns_none_when_no_netbox_config(self) -> None:
        from maas_mcp.cli import _resolve_via_netbox

        mock_client = MagicMock()

        with patch("maas_mcp.netbox_resolve.Settings") as mock_settings_cls:
            settings = MagicMock()
            settings.netbox_url = None
            settings.netbox_token = None
            mock_settings_cls.return_value = settings

            result = _resolve_via_netbox("anything", mock_client)

        assert result.system_id is None
        assert result.failure == NetboxResolveFailureKind.NOT_CONFIGURED

    def test_returns_none_when_netbox_device_not_found(self) -> None:
        from maas_mcp.cli import _resolve_via_netbox

        mock_client = MagicMock()
        mock_nb = MagicMock()
        mock_nb.lookup_device.return_value = None

        with (
            patch("maas_mcp.netbox_resolve.Settings") as mock_settings_cls,
            patch("maas_mcp.netbox_resolve.NetboxClient", return_value=mock_nb),
        ):
            settings = MagicMock()
            settings.netbox_url = "https://netbox.example.com"
            settings.netbox_token = MagicMock()
            settings.netbox_token.get_secret_value.return_value = "tok"
            mock_settings_cls.return_value = settings

            result = _resolve_via_netbox("no-such-device", mock_client)

        assert result.system_id is None
        assert result.failure == NetboxResolveFailureKind.DEVICE_NOT_FOUND

    def test_returns_none_when_no_provider_machine_id(self) -> None:
        from maas_mcp.cli import _resolve_via_netbox

        mock_client = MagicMock()
        mock_nb = MagicMock()
        mock_nb.lookup_device.return_value = {
            "name": "device-no-pmid",
            "custom_fields": {},
        }

        with (
            patch("maas_mcp.netbox_resolve.Settings") as mock_settings_cls,
            patch("maas_mcp.netbox_resolve.NetboxClient", return_value=mock_nb),
        ):
            settings = MagicMock()
            settings.netbox_url = "https://netbox.example.com"
            settings.netbox_token = MagicMock()
            settings.netbox_token.get_secret_value.return_value = "tok"
            mock_settings_cls.return_value = settings

            result = _resolve_via_netbox("device-no-pmid", mock_client)

        assert result.system_id is None
        assert result.failure == NetboxResolveFailureKind.NO_PROVIDER_MACHINE_ID

    def test_returns_none_when_maas_hostname_not_found(self) -> None:
        from maas_mcp.cli import _resolve_via_netbox

        mock_client = MagicMock()
        mock_client.get.return_value = []

        mock_nb = MagicMock()
        mock_nb.lookup_device.return_value = {
            "name": "research-common-h100-001",
            "custom_fields": {"Provider_Machine_ID": "gpu001"},
        }

        with (
            patch("maas_mcp.netbox_resolve.Settings") as mock_settings_cls,
            patch("maas_mcp.netbox_resolve.NetboxClient", return_value=mock_nb),
        ):
            settings = MagicMock()
            settings.netbox_url = "https://netbox.example.com"
            settings.netbox_token = MagicMock()
            settings.netbox_token.get_secret_value.return_value = "tok"
            mock_settings_cls.return_value = settings

            result = _resolve_via_netbox("research-common-h100-001", mock_client)

        assert result.system_id is None
        assert result.failure == NetboxResolveFailureKind.MAAS_NO_MACHINE_FOR_HOSTNAME

    def test_returns_none_on_settings_error(self) -> None:
        from maas_mcp.cli import _resolve_via_netbox

        mock_client = MagicMock()

        with patch("maas_mcp.netbox_resolve.Settings", side_effect=Exception("no config")):
            result = _resolve_via_netbox("anything", mock_client)

        assert result.system_id is None
        assert result.failure == NetboxResolveFailureKind.SETTINGS_ERROR
