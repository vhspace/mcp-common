"""Tests for ``network-cli`` — the dual-mode Typer CLI.

Every command is synthesized by :func:`mcp_common.dual_mode.build_cli_from_mcp`
from the ``@dual_mode_tool`` functions in :mod:`mcp_network.server`, so these
tests exercise the same functions the MCP tools call. They run against the
bundled ORI-TX inventory (the default site, loaded when ``mcp_network.server``
is imported) and mock the switch driver to avoid live SSH.

Note: under Typer's ``CliRunner`` stdout is not a TTY, so ``should_emit_json``
makes every command emit JSON even without ``--json`` — assertions parse stdout
as JSON accordingly.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import typer
from mcp_common.testing.dual_mode import (
    assert_parity,
    call_tool_via_cli,
    call_tool_via_mcp,
    make_cli_runner,
)

from mcp_network.cli import app
from mcp_network.server import mcp

runner = make_cli_runner()

# A switch present in the bundled ORI-TX inventory (the default site).
SWITCH = "dfw01-inb-sw-lea-03"

ALL_COMMANDS = {
    "sites",
    "switches",
    "system-info",
    "port-status",
    "port-counters",
    "lldp",
    "bgp",
    "mac-table",
    "find-mac",
    "find-node",
    "logs",
    "wjh",
}


# ---------------------------------------------------------------------------
# CLI introspection helpers (width-/ANSI-independent — scraping the Rich
# ``--help`` text is unreliable in a no-TTY shell). typer >=0.26 vendored Click,
# so the resolved group/params are ``typer._click`` types, not the installed
# ``click`` ones — ``isinstance`` against ``click.*`` is unreliable. We key off
# ``param_type_name`` ("option"/"argument"), a stable Click attribute present on
# both the vendored and installed parameter classes.
# ---------------------------------------------------------------------------


def _cli_group() -> Any:
    group = typer.main.get_command(app)
    assert hasattr(group, "commands"), f"expected a command group, got {type(group)!r}"
    return group


def _command_params(command_name: str) -> list[Any]:
    return list(_cli_group().commands[command_name].params)


def _command_option_flags(command_name: str) -> set[str]:
    flags: set[str] = set()
    for param in _command_params(command_name):
        if getattr(param, "param_type_name", "") == "option":
            flags.update(param.opts)
            flags.update(param.secondary_opts)
    return flags


def _command_arg_names(command_name: str) -> list[str]:
    return [
        p.name
        for p in _command_params(command_name)
        if getattr(p, "param_type_name", "") == "argument"
    ]


# ---------------------------------------------------------------------------
# Command + flag surface (preserved across the dual-mode migration)
# ---------------------------------------------------------------------------


class TestCommandSurface:
    def test_all_commands_registered(self) -> None:
        names = set(_cli_group().commands)
        assert names >= ALL_COMMANDS

    def test_site_short_flag_preserved(self) -> None:
        for command in ("switches", "system-info", "mac-table", "logs", "find-mac"):
            flags = _command_option_flags(command)
            assert "--site" in flags, command
            assert "-s" in flags, command

    def test_json_flag_present(self) -> None:
        for command in ALL_COMMANDS:
            flags = _command_option_flags(command)
            assert "--json" in flags, command
            assert "-j" in flags, command

    def test_logs_flags_preserved(self) -> None:
        flags = _command_option_flags("logs")
        # long flags
        for flag in (
            "--lines",
            "--since",
            "--until",
            "--unit",
            "--identifier",
            "--priority",
            "--grep",
            "--boot",
            "--kernel",
            "--preset",
        ):
            assert flag in flags, flag
        # short flags carried over from the original click CLI
        for short in ("-n", "-u", "-t", "-p", "-s"):
            assert short in flags, short

    def test_mac_table_filter_flags(self) -> None:
        flags = _command_option_flags("mac-table")
        for flag in ("--mac", "--port", "--vlan"):
            assert flag in flags, flag

    def test_token_use_flags_present(self) -> None:
        # brief projections mirror system-info's --brief
        for command in ("port-status", "lldp", "bgp"):
            assert "--brief" in _command_option_flags(command), command
        # mac-table gains --limit / --count-only; wjh gains --limit
        mac_flags = _command_option_flags("mac-table")
        assert "--limit" in mac_flags
        assert "--count-only" in mac_flags
        assert "--limit" in _command_option_flags("wjh")

    def test_switch_is_positional(self) -> None:
        for command in (
            "system-info",
            "port-status",
            "port-counters",
            "lldp",
            "bgp",
            "mac-table",
            "logs",
            "wjh",
        ):
            assert "switch" in _command_arg_names(command), command

    def test_find_commands_positional(self) -> None:
        assert "mac" in _command_arg_names("find-mac")
        assert "node" in _command_arg_names("find-node")

    def test_port_status_optional_positional_port(self) -> None:
        assert _command_arg_names("port-status") == ["switch", "port"]


# ---------------------------------------------------------------------------
# sites
# ---------------------------------------------------------------------------


class TestSites:
    def test_sites_lists_default_site(self) -> None:
        result = runner.invoke(app, ["sites"])
        assert result.exit_code == 0, result.stderr
        assert "ori" in result.stdout

    def test_sites_json(self) -> None:
        result = runner.invoke(app, ["sites", "--json"])
        assert result.exit_code == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["default"] == "ori"
        assert any(s["site"] == "ori" for s in data["sites"])

    def test_sites_parity(self) -> None:
        mcp_result = run_mcp("list_sites")
        cli_result = call_tool_via_cli(app, "sites", runner=runner)
        assert_parity(mcp_result, cli_result)


# ---------------------------------------------------------------------------
# switches
# ---------------------------------------------------------------------------


class TestSwitches:
    def test_switches_json(self) -> None:
        result = runner.invoke(app, ["switches", "--json"])
        assert result.exit_code == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["site"] == "ori"
        names = {s["name"] for s in data["switches"]}
        assert SWITCH in names
        assert len(data["switches"]) == 6

    def test_switches_with_site_option(self) -> None:
        result = runner.invoke(app, ["switches", "-s", "ori", "--json"])
        assert result.exit_code == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["site"] == "ori"

    def test_switches_unknown_site(self) -> None:
        result = runner.invoke(app, ["switches", "-s", "nope"])
        assert result.exit_code != 0

    def test_switches_parity(self) -> None:
        mcp_result = run_mcp("list_switches", site="ori")
        cli_result = call_tool_via_cli(app, "switches", ["-s", "ori"], runner=runner)
        assert_parity(mcp_result, cli_result)


# ---------------------------------------------------------------------------
# system-info
# ---------------------------------------------------------------------------


class TestSystemInfo:
    def test_system_info_json_brief_by_default(self) -> None:
        # brief=True (the default) filters the raw blob down to key fields.
        mock_data = {"hostname": SWITCH, "health": "ok", "uptime": "3d", "internal-blob": {"x": 1}}
        with _mock_driver(system_info=mock_data):
            result = runner.invoke(app, ["system-info", SWITCH, "--json"])
        assert result.exit_code == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["switch"] == SWITCH
        assert data["data"]["hostname"] == SWITCH
        assert "internal-blob" not in data["data"]

    def test_system_info_brief_flag_present(self) -> None:
        # The MCP tool's ``brief`` parameter surfaces as a ``--brief`` CLI flag.
        assert "--brief" in _command_option_flags("system-info")


class TestErrorHandling:
    def test_unknown_switch(self) -> None:
        result = runner.invoke(app, ["system-info", "no-such-switch"])
        assert result.exit_code != 0
        combined = f"{result.stdout}{result.stderr}{result.exception}"
        assert "not found" in combined.lower()


# ---------------------------------------------------------------------------
# port-status / port-counters
# ---------------------------------------------------------------------------


class TestPortStatus:
    def test_port_status_all_json(self) -> None:
        mock_ports = [{"name": "swp1", "oper-state": "up", "speed": "100G"}]
        with _mock_driver(interfaces_brief=mock_ports):
            result = runner.invoke(app, ["port-status", SWITCH, "--json"])
        assert result.exit_code == 0, result.stderr
        data = json.loads(result.stdout)
        assert "ports" in data

    def test_port_status_single_json(self) -> None:
        with _mock_driver(interface={"oper-state": "up", "speed": "100G"}):
            result = runner.invoke(app, ["port-status", SWITCH, "swp1", "--json"])
        assert result.exit_code == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["port"] == "swp1"

    def test_port_counters_json(self) -> None:
        with _mock_driver(interface_counters={"in-bytes": 1, "out-bytes": 2}):
            result = runner.invoke(app, ["port-counters", SWITCH, "swp1", "--json"])
        assert result.exit_code == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["port"] == "swp1"
        assert data["data"]["in-bytes"] == 1


# ---------------------------------------------------------------------------
# mac-table
# ---------------------------------------------------------------------------


class TestMacTable:
    def test_mac_table_json(self) -> None:
        entries = [{"mac": "AA:BB:CC:DD:EE:FF", "interface": "swp1", "vlan": 100, "age": 300}]
        with _mock_driver(mac_table=entries):
            result = runner.invoke(app, ["mac-table", SWITCH, "--json"])
        assert result.exit_code == 0, result.stderr
        data = json.loads(result.stdout)
        assert len(data["entries"]) == 1

    def test_mac_table_filter(self) -> None:
        entries = [
            {"mac": "aa:bb:cc:dd:ee:ff", "interface": "swp1", "vlan": 100},
            {"mac": "11:22:33:44:55:66", "interface": "swp2", "vlan": 200},
        ]
        with _mock_driver(mac_table=entries):
            result = runner.invoke(
                app, ["mac-table", SWITCH, "--mac", "aa:bb:cc:dd:ee:ff", "--json"]
            )
        assert result.exit_code == 0, result.stderr
        data = json.loads(result.stdout)
        assert len(data["entries"]) == 1
        assert data["entries"][0]["mac"] == "aa:bb:cc:dd:ee:ff"


# ---------------------------------------------------------------------------
# logs
# ---------------------------------------------------------------------------


class TestLogs:
    def test_logs_json(self) -> None:
        entries = [
            {
                "timestamp": "2026-04-21T18:05:03Z",
                "priority": "info",
                "unit": "frr.service",
                "identifier": "bgpd",
                "message": "Neighbor 10.0.0.1 Up",
            }
        ]
        with _mock_driver(logs=entries):
            result = runner.invoke(app, ["logs", SWITCH, "--json"])
        assert result.exit_code == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["entries"][0]["identifier"] == "bgpd"

    def test_logs_with_preset(self) -> None:
        with _mock_driver(logs=[]) as drv:
            result = runner.invoke(app, ["logs", SWITCH, "--preset", "routing", "--json"])
        assert result.exit_code == 0, result.stderr
        drv.logs.assert_called_once()
        assert drv.logs.call_args.kwargs.get("unit") == "frr.service"

    def test_logs_short_lines_flag(self) -> None:
        with _mock_driver(logs=[]) as drv:
            result = runner.invoke(app, ["logs", SWITCH, "-n", "10", "--json"])
        assert result.exit_code == 0, result.stderr
        assert drv.logs.call_args.kwargs.get("lines") == 10


# ---------------------------------------------------------------------------
# wjh
# ---------------------------------------------------------------------------


class TestWjh:
    def test_wjh_json(self) -> None:
        entries = [{"id": "1", "reason": "ACL deny", "ingress-port": "swp1"}]
        with _mock_driver(wjh=entries):
            result = runner.invoke(app, ["wjh", SWITCH, "--json"])
        assert result.exit_code == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["entries"][0]["reason"] == "ACL deny"


# ---------------------------------------------------------------------------
# --help
# ---------------------------------------------------------------------------


class TestHelp:
    @pytest.mark.parametrize(
        "argv",
        [
            ["--help"],
            ["sites", "--help"],
            ["find-mac", "--help"],
            ["logs", "--help"],
            ["wjh", "--help"],
        ],
    )
    def test_help_exits_zero(self, argv: list[str]) -> None:
        result = runner.invoke(app, argv)
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# .env auto-loading
# ---------------------------------------------------------------------------


class TestDotenvLoading:
    def test_main_calls_load_env(self) -> None:
        with (
            patch("mcp_common.env.load_env") as mock_load,
            patch("mcp_network.cli.run_cli") as mock_run,
        ):
            from mcp_network.cli import main

            main()
            mock_load.assert_called_once()
            mock_run.assert_called_once()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run_mcp(tool_name: str, **arguments: Any) -> Any:
    import asyncio

    return asyncio.run(call_tool_via_mcp(mcp, tool_name, **arguments))


def _mock_driver(**methods: Any) -> Any:
    """Patch ``mcp_network.server.get_driver`` to return an AsyncMock driver.

    Each keyword sets an async driver method's return value, e.g.
    ``_mock_driver(system_info={...})``. Returns the patch context manager whose
    ``__enter__`` yields the mock driver (so tests can assert call args).
    """
    drv = AsyncMock()
    for name, value in methods.items():
        getattr(drv, name).return_value = value

    class _Ctx:
        def __enter__(self) -> Any:
            self._patcher = patch("mcp_network.server.get_driver", return_value=drv)
            self._patcher.start()
            return drv

        def __exit__(self, *exc: Any) -> None:
            self._patcher.stop()

    return _Ctx()
