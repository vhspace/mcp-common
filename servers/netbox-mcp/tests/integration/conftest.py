"""Session fixtures that boot the self-contained NetBox simulator.

All Docker probing happens INSIDE fixtures, never at import/collection time, so
the fast unit job can still import these modules while deselecting the
``integration`` marker.

Skip semantics (so a real, passing check is real — not a false green):

* No container runtime available -> ``pytest.skip`` (local machines without
  Docker stay green).
* ``NETBOX_REQUIRE_DOCKER=1`` (set by CI) -> never skip; fail loudly if Docker
  is missing or the stack/seed fails.

Teardown removes the DB volume in CI (``NETBOX_IT_CLEAN`` defaults to ``1`` when
``CI`` / ``GITHUB_ACTIONS`` is set) and keeps it locally for fast re-runs.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

# Import the shared seeder by file location so it works regardless of pytest's
# import mode; the Makefile runs the same seed.py as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import seed

HERE = Path(__file__).resolve().parent
COMPOSE_FILE = HERE / "docker-compose.yaml"
PROJECT = "netbox-mcp-it"

# Must match docker-compose.yaml / seed.py (kept in sync by hand).
TOKEN = seed.DEFAULT_TOKEN

WAIT_TIMEOUT = "600"


def _runtime() -> str:
    """Container runtime binary (``docker`` by default, ``podman`` opt-in)."""
    return os.environ.get("CONTAINER_RUNTIME", "docker")


def _compose_base() -> list[str]:
    return [_runtime(), "compose", "-f", str(COMPOSE_FILE), "-p", PROJECT]


def _docker_available() -> bool:
    runtime = _runtime()
    if shutil.which(runtime) is None:
        return False
    try:
        subprocess.run(
            [runtime, "info"],
            capture_output=True,
            timeout=30,
            check=True,
        )
    except (subprocess.SubprocessError, OSError):
        return False
    return True


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _require_docker() -> bool:
    return os.environ.get("NETBOX_REQUIRE_DOCKER", "").lower() in ("1", "true", "yes")


def _should_clean() -> bool:
    val = os.environ.get("NETBOX_IT_CLEAN")
    if val is not None:
        return val.lower() not in ("0", "false", "no", "")
    return bool(os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"))


def _run(cmd: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, env=env, capture_output=True, text=True, check=False)


def _dump_logs(env: dict[str, str]) -> str:
    logs = _run([*_compose_base(), "logs", "--no-color", "--tail", "120", "netbox"], env)
    return logs.stdout + logs.stderr


@pytest.fixture(scope="session")
def netbox_sim() -> Iterator[tuple[str, str]]:
    """Boot the NetBox sim, seed it, and yield ``(base_url, token)``."""
    if not _docker_available():
        msg = f"container runtime '{_runtime()}' not available; skipping NetBox integration tests"
        if _require_docker():
            pytest.fail(f"NETBOX_REQUIRE_DOCKER is set but {msg}")
        pytest.skip(msg)

    port = os.environ.get("NETBOX_SIM_PORT") or str(_free_port())
    base_url = f"http://127.0.0.1:{port}"
    env = {**os.environ, "NETBOX_SIM_PORT": port, "NETBOX_SIM_TOKEN": TOKEN}

    up = _run([*_compose_base(), "up", "-d", "--wait", "--wait-timeout", WAIT_TIMEOUT], env)
    if up.returncode != 0:
        details = f"{up.stdout}\n{up.stderr}\n--- netbox logs ---\n{_dump_logs(env)}"
        _run([*_compose_base(), "down", "-v"], env)
        pytest.fail(f"failed to start NetBox simulator:\n{details}")

    try:
        seed.seed_netbox(base_url, TOKEN, verify_ssl=False)
    except Exception as exc:  # surface any seed failure loudly
        details = f"{exc}\n--- netbox logs ---\n{_dump_logs(env)}"
        if _should_clean():
            _run([*_compose_base(), "down", "-v"], env)
        pytest.fail(f"failed to seed NetBox simulator:\n{details}")

    prev_env = {k: os.environ.get(k) for k in ("NETBOX_URL", "NETBOX_TOKEN", "VERIFY_SSL")}
    os.environ["NETBOX_URL"] = base_url
    os.environ["NETBOX_TOKEN"] = TOKEN
    os.environ["VERIFY_SSL"] = "false"

    try:
        yield base_url, TOKEN
    finally:
        for key, value in prev_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        down = ["down", "-v"] if _should_clean() else ["down"]
        _run([*_compose_base(), *down], env)


@pytest.fixture
def netbox_client(netbox_sim: tuple[str, str]) -> Iterator[object]:
    """Wire ``server.netbox`` to the sim so the real MCP tools can run.

    Disables the VPN monitor (no Cloudflare in front of a local sim, so writes
    are allowed) and clears ``MCP_ENFORCE_READONLY`` so the write round-trip
    exercises the real PATCH path.
    """
    from netbox_mcp import server
    from netbox_mcp.netbox_client import NetBoxRestClient

    base_url, token = netbox_sim
    client = NetBoxRestClient(url=base_url, token=token, verify_ssl=False)

    prev_client = server.netbox
    prev_monitor = server.vpn_monitor
    prev_readonly = os.environ.pop("MCP_ENFORCE_READONLY", None)
    server.netbox = client
    server.vpn_monitor = None

    try:
        yield client
    finally:
        server.netbox = prev_client
        server.vpn_monitor = prev_monitor
        if prev_readonly is not None:
            os.environ["MCP_ENFORCE_READONLY"] = prev_readonly
