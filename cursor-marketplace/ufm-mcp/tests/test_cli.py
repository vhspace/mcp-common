"""Smoke tests for ufm-cli, dispatched through typer's CliRunner."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import click
import pytest
from typer.testing import CliRunner

from ufm_mcp.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _stub_init():
    """Skip the env-loading + site-manager init that ufm-cli does at startup."""
    with patch("ufm_mcp.cli._ensure_init"):
        yield


def test_ports_with_port_guid_invokes_tool_with_port_guid_kwarg() -> None:
    fake_response = {
        "ok": True,
        "system": {"system_name": "ori-024", "guid": "bbbb"},
        "health": {
            "system": {"system_name": "ori-024", "model": "?", "state": "?"},
            "ports": [{"number": 1, "dname": "mlx5_6"}],
        },
        "logs": {},
    }
    with patch("ufm_mcp.server.ufm_check_ports_recent", return_value=fake_response) as mock_tool:
        result = runner.invoke(app, ["ports", "--port-guid", "0xa088c20300556b96", "-s", "apld2"])

    assert result.exit_code == 0, result.output
    mock_tool.assert_called_once()
    kwargs = mock_tool.call_args.kwargs
    assert kwargs["port_guid"] == "0xa088c20300556b96"
    assert kwargs["site"] == "apld2"


def test_ports_with_node_guid_invokes_tool_with_node_guid_kwarg() -> None:
    fake_response = {
        "ok": True,
        "system": {"system_name": "ori-024", "guid": "bbbb"},
        "health": {
            "system": {"system_name": "ori-024", "model": "?", "state": "?"},
            "ports": [{"number": 1, "dname": "mlx5_0"}],
        },
        "logs": {},
    }
    with patch("ufm_mcp.server.ufm_check_ports_recent", return_value=fake_response) as mock_tool:
        result = runner.invoke(app, ["ports", "--node-guid", "bbbb2222bbbb2222", "-s", "apld2"])

    assert result.exit_code == 0, result.output
    mock_tool.assert_called_once()
    kwargs = mock_tool.call_args.kwargs
    assert kwargs["node_guid"] == "bbbb2222bbbb2222"
    assert kwargs["site"] == "apld2"


def test_ports_rejects_zero_selectors() -> None:
    result = runner.invoke(app, ["ports", "-s", "apld2"])
    assert result.exit_code == 2
    assert "exactly one" in (result.output or result.stderr or "")


def test_ports_rejects_multiple_selectors() -> None:
    result = runner.invoke(app, ["ports", "somehost", "--port-guid", "0xfoo", "-s", "apld2"])
    assert result.exit_code == 2
    assert "exactly one" in (result.output or result.stderr or "")


def test_inventory_doctor_cli_renders_text() -> None:
    fake = {
        "ok": True,
        "system": {"name": "b65c909e-16", "anchor_guid": "aaaa1111", "anchor_record_port_count": 7},
        "counts": {"record_ports": 7, "ports_by_name": 7, "ports_by_guid": 1},
        "ghost_ports": ["0xghost01_1"],
        "name_only_ports": [],
        "inferred_diagnosis": "stale_anchor",
        "remediation_hint": "On the UFM HA primary: `sudo pcs resource restart ufm-enterprise`.",
    }
    with patch("ufm_mcp.server.ufm_inventory_doctor", return_value=fake):
        result = runner.invoke(app, ["inventory-doctor", "b65c909e-16", "-s", "ori"])

    assert result.exit_code == 0, result.output
    assert "stale_anchor" in result.output
    assert "ports_by_name=7" in result.output
    assert "pcs resource restart ufm-enterprise" in result.output


def test_links_with_system_arg_filters_results() -> None:
    fake = {
        "ok": True,
        "links": {
            "total_links": 100,
            "severity_counts": {"Warning": 2, "Info": 98},
            "non_info_count": 2,
            "non_info_links": [
                {
                    "severity": "Warning",
                    "source_port_node_description": "hci-oh1-target",
                    "destination_port_node_description": "switch-x",
                    "source_guid": "aaaa",
                    "destination_guid": "bbbb",
                },
                {
                    "severity": "Warning",
                    "source_port_node_description": "other-host",
                    "destination_port_node_description": "switch-y",
                    "source_guid": "cccc",
                    "destination_guid": "dddd",
                },
            ],
        },
    }
    with patch("ufm_mcp.server.ufm_check_links_recent", return_value=fake):
        result = runner.invoke(app, ["links", "hci-oh1-target", "-s", "ori", "-j"])
    assert result.exit_code == 0, result.output
    import json

    payload = json.loads(result.output)
    filtered = payload["links"]["non_info_links"]
    assert len(filtered) == 1
    assert filtered[0]["source_port_node_description"] == "hci-oh1-target"


def test_links_without_system_arg_returns_all() -> None:
    """Backwards compat: omitting SYSTEM keeps current fabric-wide behavior."""
    fake = {
        "ok": True,
        "links": {
            "total_links": 100,
            "severity_counts": {},
            "non_info_count": 2,
            "non_info_links": [
                {
                    "severity": "Warning",
                    "source_port_node_description": "a",
                    "destination_port_node_description": "b",
                    "source_guid": "1",
                    "destination_guid": "2",
                },
                {
                    "severity": "Warning",
                    "source_port_node_description": "c",
                    "destination_port_node_description": "d",
                    "source_guid": "3",
                    "destination_guid": "4",
                },
            ],
        },
    }
    with patch("ufm_mcp.server.ufm_check_links_recent", return_value=fake):
        result = runner.invoke(app, ["links", "-s", "ori", "-j"])
    assert result.exit_code == 0
    import json

    payload = json.loads(result.output)
    assert len(payload["links"]["non_info_links"]) == 2  # unfiltered


def test_ports_filters_log_lines_to_relevant_system() -> None:
    """Log lines that don't mention the queried system are filtered out by default."""
    fake = {
        "ok": True,
        "system": {"system_name": "target-host", "guid": "0xanchor"},
        "health": {
            "system": {
                "system_name": "target-host",
                "guid": "0xanchor",
                "model": "?",
                "state": "?",
            },
            "ports": [{"number": 1, "dname": "mlx5_0", "name": "0xport01_1", "guid": "0xport01"}],
        },
        "logs": {
            "UFM": {
                "token_match_count": 0,
                "error_lines_count": 3,
                "error_lines_tail": [
                    "2026-05-01 12:00:00 Sysinfo cb. Failed for switch fc6a1c03...: invalid username/password",
                    "2026-05-01 12:00:01 something about target-host went wrong",
                    "2026-05-01 12:00:02 unrelated noise here",
                ],
            },
        },
    }
    with patch("ufm_mcp.server.ufm_check_ports_recent", return_value=fake):
        result = runner.invoke(app, ["ports", "target-host", "-s", "ori"])
    assert result.exit_code == 0, result.output
    # Should show the matching line, NOT the two unrelated ones.
    assert "target-host went wrong" in result.output
    assert "Sysinfo cb. Failed" not in result.output
    assert "unrelated noise" not in result.output
    # And should mention how many lines were filtered.
    assert "filtered out" in result.output


def test_ports_logs_all_shows_unfiltered_tail() -> None:
    """--logs-all restores the original noisy behavior."""
    fake = {
        "ok": True,
        "system": {"system_name": "target-host", "guid": "0xanchor"},
        "health": {
            "system": {
                "system_name": "target-host",
                "guid": "0xanchor",
                "model": "?",
                "state": "?",
            },
            "ports": [],
        },
        "logs": {
            "UFM": {
                "token_match_count": 0,
                "error_lines_count": 2,
                "error_lines_tail": ["unrelated noise A", "unrelated noise B"],
            },
        },
    }
    with patch("ufm_mcp.server.ufm_check_ports_recent", return_value=fake):
        result = runner.invoke(app, ["ports", "target-host", "-s", "ori", "--logs-all"])
    assert result.exit_code == 0, result.output
    assert "unrelated noise A" in result.output
    assert "unrelated noise B" in result.output
    assert "filtered out" not in result.output


def test_sites_verify_ok() -> None:
    """Successful probe returns status=ok and exit code 0."""
    fake_client = MagicMock()
    fake_client.get_json = MagicMock(return_value={"ufm_release_version": "6.x"})

    fake_cfg = MagicMock()
    fake_cfg.ufm_url = "https://10.1.2.3/"
    fake_cfg.ufm_api_base_path = "ufmRestV3"

    fake_sites = MagicMock()
    fake_sites.get_client.return_value = fake_client
    fake_sites.get_config.return_value = fake_cfg

    with patch("ufm_mcp.cli.sites", fake_sites):
        result = runner.invoke(app, ["sites-verify", "ori", "-j"])
    assert result.exit_code == 0, result.output
    import json

    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["site"] == "ori"


def test_sites_verify_auth_fail() -> None:
    """HTTP 401 → status=auth_fail, exit code 2."""
    import httpx

    fake_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 401
    fake_client.get_json = MagicMock(
        side_effect=httpx.HTTPStatusError("401", request=MagicMock(), response=mock_response)
    )
    fake_cfg = MagicMock(ufm_url="https://10.1.2.3/", ufm_api_base_path="ufmRestV3")
    fake_sites = MagicMock()
    fake_sites.get_client.return_value = fake_client
    fake_sites.get_config.return_value = fake_cfg

    with patch("ufm_mcp.cli.sites", fake_sites):
        result = runner.invoke(app, ["sites-verify", "ori", "-j"])
    assert result.exit_code == 2, result.output
    import json

    payload = json.loads(result.output)
    assert payload["status"] == "auth_fail"


def test_sites_verify_wrong_api_path() -> None:
    """HTTP 404 → status=wrong_api_path."""
    import httpx

    fake_client = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 404
    fake_client.get_json = MagicMock(
        side_effect=httpx.HTTPStatusError("404", request=MagicMock(), response=mock_response)
    )
    fake_cfg = MagicMock(ufm_url="https://10.1.2.3/ufm", ufm_api_base_path="ufmRestV3")
    fake_sites = MagicMock(
        get_client=MagicMock(return_value=fake_client),
        get_config=MagicMock(return_value=fake_cfg),
    )
    with patch("ufm_mcp.cli.sites", fake_sites):
        result = runner.invoke(app, ["sites-verify", "apld2", "-j"])
    assert result.exit_code == 2
    import json

    payload = json.loads(result.output)
    assert payload["status"] == "wrong_api_path"


# ================================================================
#  Tests for upload-ibdiagnet CLI command (#57)
# ================================================================


def test_cli_upload_ibdiagnet_success(tmp_path) -> None:
    """Happy path: mocked ufm_upload_ibdiagnet returns success dict."""
    tarball = tmp_path / "ibdiagnet-host01-20260101T120000Z.tar.gz"
    tarball.write_bytes(b"FAKE_TAR_BYTES")

    fake_result = {
        "ok": True,
        "collection_id": "coll-abc123",
        "uploaded_bytes": len(b"FAKE_TAR_BYTES"),
        "site": "ori",
        "az_id": "us-south-2a",
        "source_path": str(tarball),
    }
    with patch("ufm_mcp.server.ufm_upload_ibdiagnet", return_value=fake_result) as mock_tool:
        result = runner.invoke(app, ["upload-ibdiagnet", str(tarball), "--site", "ori"])

    assert result.exit_code == 0, result.output
    mock_tool.assert_called_once()
    kwargs = mock_tool.call_args.kwargs
    assert kwargs["site"] == "ori"
    assert kwargs["ibdiagnet_path"] == str(tarball)
    assert "collection_id=coll-abc123" in result.output or "coll-abc123" in result.output
    assert "topaz-cables" in result.output
    assert "topaz-port-counters" in result.output


def test_cli_upload_ibdiagnet_json_output(tmp_path) -> None:
    """--json flag outputs JSON."""
    import json

    tarball = tmp_path / "test.tar.gz"
    tarball.write_bytes(b"DATA")

    fake_result = {
        "ok": True,
        "collection_id": "coll-xyz",
        "uploaded_bytes": 4,
        "site": "ori",
        "az_id": "us-south-2a",
        "source_path": str(tarball),
    }
    with patch("ufm_mcp.server.ufm_upload_ibdiagnet", return_value=fake_result):
        result = runner.invoke(app, ["upload-ibdiagnet", str(tarball), "--site", "ori", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["collection_id"] == "coll-xyz"


def test_cli_upload_ibdiagnet_help_renders() -> None:
    """--help exits 0 and shows --site option."""
    result = runner.invoke(app, ["upload-ibdiagnet", "--help"])
    assert result.exit_code == 0, result.output
    # Strip color styling first: CI's forced-color terminal splits the long
    # option into styled spans, which would otherwise hide the "--site" literal.
    assert "--site" in click.unstyle(result.output)


# ================================================================
#  Tests for --collection wiring on topaz query commands (#review)
# ================================================================


@pytest.mark.parametrize(
    ("command", "client_method"),
    [
        ("topaz-cables", "list_cables"),
        ("topaz-port-counters", "list_port_counters"),
        ("topaz-switches", "list_switches"),
    ],
)
def test_cli_topaz_collection_forwarded(command: str, client_method: str) -> None:
    """`--collection <id>` must reach the Topaz client as collection_id (#review)."""
    mock_client = MagicMock()
    getattr(mock_client, client_method).return_value = {"total_count": 0}

    with (
        patch("ufm_mcp.cli._resolve_topaz_az", return_value="us-south-2a"),
        patch("ufm_mcp.cli._get_topaz_client", return_value=mock_client),
    ):
        result = runner.invoke(
            app, [command, "--site", "ori", "--collection", "coll-abc-123", "--json"]
        )

    assert result.exit_code == 0, result.output
    call = getattr(mock_client, client_method)
    call.assert_called_once()
    assert call.call_args.kwargs["collection_id"] == "coll-abc-123"


@pytest.mark.parametrize("command", ["topaz-cables", "topaz-port-counters", "topaz-switches"])
def test_cli_topaz_collection_help_renders(command: str) -> None:
    """--help exits 0 and advertises the new --collection option."""
    result = runner.invoke(app, [command, "--help"])
    assert result.exit_code == 0, result.output
    # Under CI (GITHUB_ACTIONS) typer forces a color terminal, and its
    # OptionHighlighter styles "--collection" as two spans ("-" + "-collection"),
    # so the literal substring only survives once the SGR escapes are stripped.
    assert "--collection" in click.unstyle(result.output)


# ================================================================
#  Tests for pkey commands routed to the consolidated ufm_get_pkey
# ================================================================


def test_cli_pkey_resolve_hosts_calls_consolidated_tool() -> None:
    """`pkey --resolve-hosts` must call ufm_get_pkey(resolve_hosts=True)."""
    fake = {
        "ok": True,
        "pkey": "0x1",
        "total_guids": 2,
        "hosts_count": 1,
        "hosts": [{"hostname": "n1", "guid_count": 2, "membership_types": ["full"]}],
        "unresolved_count": 0,
        "unresolved": [],
    }
    with patch("ufm_mcp.server.ufm_get_pkey", return_value=fake) as mock_tool:
        result = runner.invoke(app, ["pkey", "0x1", "--resolve-hosts", "-s", "ori"])
    assert result.exit_code == 0, result.output
    assert mock_tool.call_args.kwargs["resolve_hosts"] is True


def test_cli_pkey_hosts_alias_calls_consolidated_tool() -> None:
    """`pkey-hosts` alias must call ufm_get_pkey(resolve_hosts=True)."""
    fake = {
        "ok": True,
        "pkey": "0x1",
        "total_guids": 0,
        "hosts_count": 0,
        "hosts": [],
        "unresolved_count": 0,
        "unresolved": [],
    }
    with patch("ufm_mcp.server.ufm_get_pkey", return_value=fake) as mock_tool:
        result = runner.invoke(app, ["pkey-hosts", "0x1", "-s", "ori"])
    assert result.exit_code == 0, result.output
    assert mock_tool.call_args.kwargs["resolve_hosts"] is True


def test_cli_pkey_raw_passes_high_max_guids() -> None:
    """Raw `pkey` view keeps the full GUID dump (CLI is already low-token)."""
    fake = {"ok": True, "pkey": "0x1", "data": {"guids": []}, "total_guids": 0}
    with patch("ufm_mcp.server.ufm_get_pkey", return_value=fake) as mock_tool:
        result = runner.invoke(app, ["pkey", "0x1", "-s", "ori"])
    assert result.exit_code == 0, result.output
    # Raw path leaves resolve_hosts at its default (False) and lifts the GUID cap.
    assert mock_tool.call_args.kwargs.get("resolve_hosts", False) is not True
    assert mock_tool.call_args.kwargs["max_guids"] >= 100000


# ================================================================
#  Auto-JSON-when-piped behavior (#82)
#
#  ufm-cli adopts mcp_common.cli.should_emit_json so non-TTY (piped /
#  captured) invocations emit machine-readable JSON by default, while an
#  interactive TTY keeps the human rendering and an explicit --json always
#  wins. The autouse conftest fixture simulates a TTY for the rest of the
#  suite; these tests re-patch should_emit_json to drive each branch.
# ================================================================


def test_piped_non_tty_emits_json_without_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-TTY stdout (piped) → JSON even when --json is omitted.

    Restores the *real* ``should_emit_json`` (the autouse conftest fixture
    replaces it with a TTY-simulating identity) so this exercises the genuine
    ``sys.stdout.isatty()`` auto-JSON path: CliRunner's captured stdout is not a
    TTY, so JSON is emitted without ``--json``.
    """
    import json

    from mcp_common.cli import should_emit_json as real_should_emit_json

    monkeypatch.setattr("ufm_mcp.cli.should_emit_json", real_should_emit_json)
    with patch("ufm_mcp.server.ufm_get_version", return_value={"version": "6.5.0"}):
        result = runner.invoke(app, ["version", "-s", "ori"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)  # must parse as JSON
    assert payload["version"] == "6.5.0"


def test_tty_emits_human_without_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Interactive TTY → human-readable text (not JSON) when --json is omitted."""
    import json

    # Simulate a TTY: only an explicit --json triggers JSON.
    monkeypatch.setattr("ufm_mcp.cli.should_emit_json", lambda explicit_json: explicit_json)
    with patch("ufm_mcp.server.ufm_get_version", return_value={"version": "6.5.0"}):
        result = runner.invoke(app, ["version", "-s", "ori"])
    assert result.exit_code == 0, result.output
    assert "UFM version: 6.5.0" in result.output
    with pytest.raises(json.JSONDecodeError):
        json.loads(result.output)


def test_explicit_json_flag_emits_json_at_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--json` forces JSON even at an interactive TTY."""
    import json

    monkeypatch.setattr("ufm_mcp.cli.should_emit_json", lambda explicit_json: explicit_json)
    with patch("ufm_mcp.server.ufm_get_version", return_value={"version": "6.5.0"}):
        result = runner.invoke(app, ["version", "-s", "ori", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["version"] == "6.5.0"
