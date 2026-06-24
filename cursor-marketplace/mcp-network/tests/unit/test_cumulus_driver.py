"""CumulusDriver tests — mocked asyncssh."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from mcp_network.drivers.base import ConnectionInfo, NetworkDriverError
from mcp_network.drivers.cumulus import (
    CumulusDriver,
    _parse_journal_lines,
    _to_list_of_dicts,
    _validate_arg,
    _validate_port,
)


def _conn() -> ConnectionInfo:
    return ConnectionInfo(
        host="10.0.0.1",
        user="ro",
        password=SecretStr("pw"),
    )


class _FakeSSH:
    """Minimal asyncssh stand-in."""

    def __init__(self, scripted: dict[str, str]) -> None:
        self.scripted = scripted
        self.commands: list[str] = []

    async def run(self, cmd: str, check: bool = False) -> Any:
        self.commands.append(cmd)
        stdout = self.scripted.get(cmd, "")
        return SimpleNamespace(stdout=stdout, stderr="", exit_status=0)

    def close(self) -> None: ...
    async def wait_closed(self) -> None: ...


_connect_calls: list[dict[str, Any]] = []


@pytest.fixture(autouse=True)
def _reset_calls() -> None:
    _connect_calls.clear()


@pytest.fixture
def fake_ssh() -> _FakeSSH:
    return _FakeSSH({})


@pytest.fixture
def drv(fake_ssh: _FakeSSH) -> CumulusDriver:
    """CumulusDriver with asyncssh.connect patched to return our fake."""
    d = CumulusDriver(_conn())

    async def fake_connect(*_args: Any, **_kwargs: Any) -> _FakeSSH:
        _connect_calls.append(_kwargs)
        return fake_ssh

    patcher = patch("mcp_network.drivers.cumulus.asyncssh.connect", side_effect=fake_connect)
    patcher.start()
    yield d
    patcher.stop()


# ---------------------------------------------------------------------------
# Session reuse
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_session_reuses_connection(drv: CumulusDriver, fake_ssh: _FakeSSH) -> None:
    """Within a session(), asyncssh.connect should be called exactly once
    even though we issue two commands."""
    fake_ssh.scripted["nv show system -o json"] = json.dumps({"hostname": {"value": "lea-03"}})
    fake_ssh.scripted["nv show bridge domain br_default mac-table -o json"] = "{}"

    async with drv.session():
        await drv.system_info()
        await drv.mac_table()

    assert len(_connect_calls) == 1
    assert fake_ssh.commands == [
        "nv show system -o json",
        "nv show bridge domain br_default mac-table -o json",
    ]


@pytest.mark.anyio
async def test_without_session_connects_per_command(drv: CumulusDriver, fake_ssh: _FakeSSH) -> None:
    """Without session(), each command opens its own connection."""
    fake_ssh.scripted["nv show system -o json"] = json.dumps({"hostname": {"value": "lea-03"}})
    fake_ssh.scripted["nv show bridge domain br_default mac-table -o json"] = "{}"

    await drv.system_info()
    await drv.mac_table()

    assert len(_connect_calls) == 2


@pytest.mark.anyio
async def test_session_nested_is_noop(drv: CumulusDriver, fake_ssh: _FakeSSH) -> None:
    """Nesting session() does not open a second connection."""
    fake_ssh.scripted["nv show system -o json"] = "{}"
    async with drv.session():
        async with drv.session():
            await drv.system_info()
    assert len(_connect_calls) == 1


@pytest.mark.anyio
async def test_session_keepalive_configured(drv: CumulusDriver, fake_ssh: _FakeSSH) -> None:
    async with drv.session():
        pass
    assert len(_connect_calls) == 1
    kwargs = _connect_calls[0]
    assert kwargs.get("keepalive_interval") == 60
    assert kwargs.get("keepalive_count_max") == 3


# ---------------------------------------------------------------------------
# Command + parse (existing, adapted for new mock wiring)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_system_info_command_and_parse(drv: CumulusDriver, fake_ssh: _FakeSSH) -> None:
    fake_ssh.scripted["nv show system -o json"] = json.dumps({"hostname": {"value": "lea-03"}})
    data = await drv.system_info()
    assert data == {"hostname": {"value": "lea-03"}}
    assert fake_ssh.commands == ["nv show system -o json"]


@pytest.mark.anyio
async def test_interface_command_and_parse(drv: CumulusDriver, fake_ssh: _FakeSSH) -> None:
    payload = {"link": {"admin-status": "up", "oper-status": "up", "speed": "400G"}}
    fake_ssh.scripted["nv show interface swp14s1 -o json"] = json.dumps(payload)
    data = await drv.interface("swp14s1")
    assert data == payload


@pytest.mark.anyio
async def test_mac_table_flattens_dict_keyed_output(drv: CumulusDriver, fake_ssh: _FakeSSH) -> None:
    payload = {
        "42": {
            "age": 22,
            "interface": "swp14s1",
            "mac": "9c:63:c0:26:ef:ec",
            "vlan": 1229,
        }
    }
    fake_ssh.scripted["nv show bridge domain br_default mac-table -o json"] = json.dumps(payload)
    entries = await drv.mac_table()
    assert entries == [
        {
            "id": "42",
            "age": 22,
            "interface": "swp14s1",
            "mac": "9c:63:c0:26:ef:ec",
            "vlan": 1229,
        }
    ]


@pytest.mark.anyio
async def test_interface_rejects_shell_injection(drv: CumulusDriver) -> None:
    with pytest.raises(NetworkDriverError):
        await drv.interface("swp14s1; rm -rf /")


@pytest.mark.anyio
async def test_parse_error_wraps(drv: CumulusDriver, fake_ssh: _FakeSSH) -> None:
    fake_ssh.scripted["nv show system -o json"] = "not-json"
    with pytest.raises(NetworkDriverError) as exc:
        await drv.system_info()
    assert "10.0.0.1" in str(exc.value)


@pytest.mark.anyio
async def test_nonzero_exit_raises(
    drv: CumulusDriver, fake_ssh: _FakeSSH, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def bad_run(self: _FakeSSH, cmd: str, check: bool = False) -> Any:
        self.commands.append(cmd)
        return SimpleNamespace(stdout="", stderr="boom", exit_status=1)

    monkeypatch.setattr(_FakeSSH, "run", bad_run)
    with pytest.raises(NetworkDriverError) as exc:
        await drv.system_info()
    assert "boom" in str(exc.value)


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def test_to_list_of_dicts_dict_input() -> None:
    out = _to_list_of_dicts({"a": {"x": 1}, "b": {"x": 2}}, key_field="name")
    assert out == [{"name": "a", "x": 1}, {"name": "b", "x": 2}]


def test_to_list_of_dicts_list_passthrough() -> None:
    out = _to_list_of_dicts([{"name": "a"}, {"name": "b"}], key_field="name")
    assert out == [{"name": "a"}, {"name": "b"}]


def test_to_list_of_dicts_other() -> None:
    assert _to_list_of_dicts(None, key_field="name") == []
    assert _to_list_of_dicts("x", key_field="name") == []


def test_validate_port_accepts_common_shapes() -> None:
    _validate_port("swp14s1")
    _validate_port("bond1")
    _validate_port("eth0")


def test_validate_port_rejects_metacharacters() -> None:
    for bad in ["swp1 swp2", "swp1;ls", "$(id)", "`id`", "a|b", "a&b"]:
        with pytest.raises(NetworkDriverError):
            _validate_port(bad)


# ---------------------------------------------------------------------------
# logs() tests
# ---------------------------------------------------------------------------

_SAMPLE_JOURNAL_LINE = json.dumps(
    {
        "__REALTIME_TIMESTAMP": "1713720303000000",
        "PRIORITY": "6",
        "_SYSTEMD_UNIT": "frr.service",
        "SYSLOG_IDENTIFIER": "bgpd",
        "_PID": "1234",
        "MESSAGE": "Neighbor 10.0.0.1 Up",
    }
)

_SAMPLE_JOURNAL_OUTPUT = "\n".join(
    [
        _SAMPLE_JOURNAL_LINE,
        json.dumps(
            {
                "__REALTIME_TIMESTAMP": "1713720302000000",
                "PRIORITY": "3",
                "_SYSTEMD_UNIT": "switchd.service",
                "SYSLOG_IDENTIFIER": "switchd",
                "_PID": "999",
                "MESSAGE": "Port swp1 state change: down",
            }
        ),
    ]
)


@pytest.mark.anyio
async def test_logs_default_command(drv: CumulusDriver, fake_ssh: _FakeSSH) -> None:
    fake_ssh.scripted["journalctl -o json -r --no-pager -n 75"] = _SAMPLE_JOURNAL_OUTPUT
    entries = await drv.logs()
    assert len(entries) == 2
    assert entries[0]["identifier"] == "bgpd"
    assert entries[0]["priority"] == "info"
    assert entries[0]["message"] == "Neighbor 10.0.0.1 Up"
    assert entries[0]["timestamp"] is not None
    assert entries[1]["priority"] == "err"


@pytest.mark.anyio
async def test_logs_with_filters(drv: CumulusDriver, fake_ssh: _FakeSSH) -> None:
    cmd = "journalctl -o json -r --no-pager -n 50 --since 1h -u frr.service -p err --grep link"
    fake_ssh.scripted[cmd] = _SAMPLE_JOURNAL_LINE
    entries = await drv.logs(
        lines=50,
        since="1h",
        unit="frr.service",
        priority="err",
        grep="link",
    )
    assert len(entries) == 1
    assert fake_ssh.commands[-1] == cmd


@pytest.mark.anyio
async def test_logs_with_boot_and_kernel(drv: CumulusDriver, fake_ssh: _FakeSSH) -> None:
    cmd = "journalctl -o json -r --no-pager -n 75 -b -k"
    fake_ssh.scripted[cmd] = ""
    entries = await drv.logs(boot=True, kernel=True)
    assert entries == []
    assert fake_ssh.commands[-1] == cmd


@pytest.mark.anyio
async def test_logs_with_until(drv: CumulusDriver, fake_ssh: _FakeSSH) -> None:
    cmd = "journalctl -o json -r --no-pager -n 75 --since yesterday --until today"
    fake_ssh.scripted[cmd] = ""
    await drv.logs(since="yesterday", until="today")
    assert fake_ssh.commands[-1] == cmd


@pytest.mark.anyio
async def test_logs_with_identifier(drv: CumulusDriver, fake_ssh: _FakeSSH) -> None:
    cmd = "journalctl -o json -r --no-pager -n 75 -t zebra"
    fake_ssh.scripted[cmd] = _SAMPLE_JOURNAL_LINE
    await drv.logs(identifier="zebra")
    assert fake_ssh.commands[-1] == cmd


@pytest.mark.anyio
async def test_logs_clamps_lines(drv: CumulusDriver, fake_ssh: _FakeSSH) -> None:
    fake_ssh.scripted["journalctl -o json -r --no-pager -n 500"] = ""
    await drv.logs(lines=9999)
    assert "-n 500" in fake_ssh.commands[-1]

    fake_ssh.scripted["journalctl -o json -r --no-pager -n 1"] = ""
    await drv.logs(lines=-5)
    assert "-n 1" in fake_ssh.commands[-1]


@pytest.mark.anyio
async def test_logs_empty_output(drv: CumulusDriver, fake_ssh: _FakeSSH) -> None:
    fake_ssh.scripted["journalctl -o json -r --no-pager -n 75"] = ""
    entries = await drv.logs()
    assert entries == []


def test_validate_arg_rejects_metacharacters() -> None:
    for bad in ["foo;bar", "$(cmd)", "a|b", "a&b", "`id`"]:
        with pytest.raises(NetworkDriverError):
            _validate_arg(bad, "test")


def test_validate_arg_rejects_whitespace_by_default() -> None:
    with pytest.raises(NetworkDriverError):
        _validate_arg("frr.service --output cat", "unit")
    with pytest.raises(NetworkDriverError):
        _validate_arg("err\tstuff", "priority")
    with pytest.raises(NetworkDriverError):
        _validate_arg("   ", "unit")


def test_validate_arg_allows_spaces_when_opted_in() -> None:
    _validate_arg("1 hour ago", "since", allow_spaces=True)
    _validate_arg("2026-04-21 12:00", "since", allow_spaces=True)
    _validate_arg("link down", "grep", allow_spaces=True)


def test_validate_arg_accepts_normal_values() -> None:
    _validate_arg("frr.service", "unit")
    _validate_arg("err", "priority")
    _validate_arg("emerg..err", "priority")
    _validate_arg("link.*down", "grep", allow_spaces=True)


def test_parse_journal_lines_handles_bad_json() -> None:
    raw = _SAMPLE_JOURNAL_LINE + "\nnot-json\n" + _SAMPLE_JOURNAL_LINE
    entries = _parse_journal_lines(raw)
    assert len(entries) == 2


# ---------------------------------------------------------------------------
# wjh() tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_wjh_command(drv: CumulusDriver, fake_ssh: _FakeSSH) -> None:
    payload = {
        "1": {"reason": "ACL deny", "ingress-port": "swp1"},
        "2": {"reason": "TTL expired", "ingress-port": "swp14"},
    }
    fake_ssh.scripted["nv show system wjh packet-buffer -o json"] = json.dumps(payload)
    entries = await drv.wjh()
    assert len(entries) == 2
    assert entries[0]["id"] == "1"
    assert entries[0]["reason"] == "ACL deny"


@pytest.mark.anyio
async def test_wjh_empty(drv: CumulusDriver, fake_ssh: _FakeSSH) -> None:
    fake_ssh.scripted["nv show system wjh packet-buffer -o json"] = "{}"
    entries = await drv.wjh()
    assert entries == []
