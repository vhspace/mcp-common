"""Per-command SSH timeout tests.

Covers the reliability fix for a wedged switch whose ``nv show`` /
``journalctl`` never returns:

* a single-switch command raises a timeout ``NetworkDriverError`` instead
  of blocking forever, and
* a parallel ``find_port_*`` scan completes with the hung switch reported
  as a per-host error (its bounding-semaphore slot is released and
  ``asyncio.gather`` does not hang) rather than wedging the whole tool call.

A short ``COMMAND_TIMEOUT`` is patched in so the tests run fast; the hung
switch sleeps far longer than that, so any regression (missing timeout)
fails the wall-clock assertion in bounded time instead of hanging.
"""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import SecretStr

from mcp_network import server
from mcp_network.drivers import cumulus
from mcp_network.drivers.base import ConnectionInfo, NetworkDriverError
from mcp_network.drivers.cumulus import CumulusDriver

# Far longer than the patched COMMAND_TIMEOUT: a working timeout cancels the
# sleep almost immediately; a regression caps the failure at this many seconds.
_HANG_SECONDS = 10.0
_TEST_TIMEOUT = 0.25

_MAC_TABLE_CMD = "nv show bridge domain br_default mac-table -o json"
_TARGET_MAC = "9c:63:c0:26:ef:ec"


class _FakeSSH:
    """asyncssh stand-in whose ``run`` either returns a MAC table or hangs."""

    def __init__(self, *, hang: bool, stdout: str = "") -> None:
        self._hang = hang
        self._stdout = stdout

    async def run(self, cmd: str, check: bool = False) -> Any:
        if self._hang:
            await asyncio.sleep(_HANG_SECONDS)
        return SimpleNamespace(stdout=self._stdout, stderr="", exit_status=0)

    def close(self) -> None: ...

    async def wait_closed(self) -> None: ...


def _mac_table_json(interface: str) -> str:
    return json.dumps({"42": {"mac": _TARGET_MAC, "interface": interface, "vlan": 1229}})


# ---------------------------------------------------------------------------
# Single switch: a hung command surfaces as a timeout NetworkDriverError
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_single_command_timeout_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cumulus, "COMMAND_TIMEOUT", _TEST_TIMEOUT)

    async def fake_connect(*_args: Any, **_kwargs: Any) -> _FakeSSH:
        return _FakeSSH(hang=True)

    monkeypatch.setattr("mcp_network.drivers.cumulus.asyncssh.connect", fake_connect)

    drv = CumulusDriver(ConnectionInfo(host="10.0.0.9", user="ro", password=SecretStr("pw")))

    start = time.monotonic()
    with pytest.raises(NetworkDriverError) as exc:
        await drv.system_info()
    elapsed = time.monotonic() - start

    assert elapsed < _HANG_SECONDS / 2  # timed out, did not wait for the hung command
    msg = str(exc.value)
    assert "timed out" in msg
    assert "10.0.0.9" in msg
    assert exc.value.hint == "command timed out"


# ---------------------------------------------------------------------------
# Parallel scan: one hung switch does not hang the whole gather
# ---------------------------------------------------------------------------


def _wire_parallel_scan(
    monkeypatch: pytest.MonkeyPatch,
    *,
    hung_switch: str,
    good_switches: list[str],
) -> None:
    """Point ``find_port_*`` at a fake multi-switch site backed by real drivers.

    Each switch gets a real :class:`CumulusDriver` (so the genuine
    ``_exec`` timeout path runs); the underlying asyncssh connection is faked
    per host so ``hung_switch`` blocks while the others return a MAC table.
    """
    monkeypatch.setattr(cumulus, "COMMAND_TIMEOUT", _TEST_TIMEOUT)

    async def fake_connect(host: str, *_args: Any, **_kwargs: Any) -> _FakeSSH:
        if host == hung_switch:
            return _FakeSSH(hang=True)
        return _FakeSSH(hang=False, stdout=_mac_table_json("bond1"))

    monkeypatch.setattr("mcp_network.drivers.cumulus.asyncssh.connect", fake_connect)

    names = [*good_switches, hung_switch]
    fake_cfg = SimpleNamespace(
        site="testsite",
        switches=[SimpleNamespace(name=n, reachable=True) for n in names],
    )
    monkeypatch.setattr(server.site_manager, "get_site", lambda _site=None: fake_cfg)

    def driver_for(_cfg: Any, sw: Any) -> CumulusDriver:
        # host == switch name so fake_connect can decide which one hangs.
        return CumulusDriver(ConnectionInfo(host=sw.name, user="ro", password=SecretStr("pw")))

    monkeypatch.setattr(server, "get_driver", driver_for)


@pytest.mark.anyio
async def test_find_port_for_mac_one_hung_switch_does_not_hang(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hung = "sw-hung"
    # More good switches than MAX_PARALLEL_SSH so at least one queues behind the
    # hung switch: proves the semaphore slot is released on timeout.
    good = [f"sw-{i}" for i in range(server.MAX_PARALLEL_SSH + 2)]
    _wire_parallel_scan(monkeypatch, hung_switch=hung, good_switches=good)

    start = time.monotonic()
    result = await server.find_port_for_mac(mac=_TARGET_MAC)
    elapsed = time.monotonic() - start

    assert elapsed < _HANG_SECONDS / 2  # scan completed promptly despite the wedged switch

    # Every reachable, responsive switch was scanned and matched (indirect/bond).
    assert {hit["switch"] for hit in result["indirect"]} == set(good)
    assert result["direct"] == []

    # The wedged switch surfaced as a single per-host timeout error.
    assert len(result["errors"]) == 1
    err = result["errors"][0]
    assert err["switch"] == hung
    assert "timed out" in err["error"].lower()


@pytest.mark.anyio
async def test_find_port_for_node_one_hung_switch_does_not_hang(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hung = "sw-hung"
    good = [f"sw-{i}" for i in range(server.MAX_PARALLEL_SSH + 2)]
    _wire_parallel_scan(monkeypatch, hung_switch=hung, good_switches=good)

    nics = [{"name": "eth0", "mac": _TARGET_MAC, "type": "1000base-t"}]

    async def fake_nics(_node: str) -> list[dict[str, Any]]:
        return nics

    monkeypatch.setattr(server, "get_node_nic_macs", fake_nics)

    start = time.monotonic()
    result = await server.find_port_for_node(node="test-node-001")
    elapsed = time.monotonic() - start

    assert elapsed < _HANG_SECONDS / 2

    assert len(result["nics"]) == 1
    nic = result["nics"][0]
    assert {hit["switch"] for hit in nic["indirect"]} == set(good)
    # The hung switch is reported as an error for the NIC, not a hang.
    assert [e["switch"] for e in nic["errors"]] == [hung]
    assert "timed out" in nic["errors"][0]["error"].lower()
