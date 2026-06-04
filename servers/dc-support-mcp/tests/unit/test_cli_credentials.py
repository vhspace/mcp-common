"""Tests for credential-aware CLI surfaces: ``vendors`` and ``auth-status``.

Only credential *source* metadata (env vs op://) is ever surfaced — never the
secret values.  These tests assert that, plus the IREN dual-mode logic.
"""

import json
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from dc_support_mcp.cli import app

runner = CliRunner()


@patch.dict(
    "os.environ",
    {
        "ORI_PORTAL_USERNAME": "u@together.ai",
        "ORI_PORTAL_PASSWORD": "super-secret-pw",
        "RTB_API_KEY": "rtb-secret",
        "NETBOX_TOKEN": "op://Vault/NetBox/token",
    },
    clear=True,
)
def test_vendors_shows_sources_not_values():
    result = runner.invoke(app, ["vendors"])
    assert result.exit_code == 0, result.output
    out = result.output
    # Never leak secret values.
    assert "super-secret-pw" not in out
    assert "rtb-secret" not in out
    # ORI configured via env.
    assert "ori" in out
    assert "source=env" in out
    # Internal-ops integrations view present.
    assert "Internal-ops integrations" in out
    assert "RTB" in out
    assert "NetBox" in out
    assert "Grafana" in out
    assert "Linear" in out
    # NetBox token is an op:// reference.
    assert "op://" in out


@patch.dict(
    "os.environ",
    {"IREN_FRESHDESK_API_KEY": "fd-key"},
    clear=True,
)
def test_vendors_iren_configured_via_freshdesk_only():
    result = runner.invoke(app, ["vendors"])
    assert result.exit_code == 0, result.output
    assert "fd-key" not in result.output
    # IREN line should report configured=yes with freshdesk-api mode.
    iren_line = next(line for line in result.output.splitlines() if line.strip().startswith("iren"))
    assert "configured=yes" in iren_line
    assert "freshdesk-api" in iren_line


@patch.dict("os.environ", {"IREN_FRESHDESK_API_KEY": "fd-key"}, clear=True)
@patch("dc_support_mcp.cli._build_inspection_handler")
def test_auth_status_iren_usable_with_freshdesk_key(mock_build):
    """IREN is usable via the Freshdesk API key even without portal cookies."""
    handler = MagicMock(spec=[])
    handler.cookie_file = MagicMock()
    handler.cookie_file.exists.return_value = False
    handler.cookie_file.__str__.return_value = "/tmp/iren_cookies.pkl"
    handler.last_error = None
    mock_build.return_value = handler

    result = runner.invoke(app, ["auth-status", "--vendor", "iren", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["vendor"] == "iren"
    assert data["cookie_exists"] is False
    assert data["freshdesk_api_configured"] is True
    assert data["freshdesk_api_source"] == "env"
    assert data["usable"] is True
    # No secret value leaked.
    assert "fd-key" not in result.output


@patch.dict("os.environ", {}, clear=True)
@patch("dc_support_mcp.cli._build_inspection_handler")
def test_auth_status_reports_credential_source(mock_build):
    handler = MagicMock(spec=[])
    handler.cookie_file = MagicMock()
    handler.cookie_file.exists.return_value = False
    handler.cookie_file.__str__.return_value = "/tmp/ori_cookies.pkl"
    handler.last_error = None
    mock_build.return_value = handler

    result = runner.invoke(app, ["auth-status", "--vendor", "ori", "--json"])
    data = json.loads(result.output)
    assert data["credential_source"] is None
