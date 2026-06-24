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
PROJECT_BASE = "netbox-mcp-it"

# Must match docker-compose.yaml / seed.py (kept in sync by hand).
TOKEN = seed.DEFAULT_TOKEN

WAIT_TIMEOUT = "600"

# How many host ports to try before giving up. >1 absorbs the rare TOCTOU race
# where the free port we picked gets grabbed before compose can publish it.
_MAX_PORT_ATTEMPTS = 3


def _runtime() -> str:
    """Container runtime binary (``docker`` by default, ``podman`` opt-in)."""
    return os.environ.get("CONTAINER_RUNTIME", "docker")


def _project_name(port: str) -> str:
    """Per-session compose project name.

    Suffixing with this process's PID and the chosen host port keeps the
    project (and therefore its network + named volume, which compose prefixes
    with the project name) unique, so two concurrent fixture sessions on one
    host (pytest-xdist / a shared runner) don't collide or tear down each
    other's stack.
    """
    return f"{PROJECT_BASE}-{os.getpid()}-{port}"


def _compose_base(project: str) -> list[str]:
    return [_runtime(), "compose", "-f", str(COMPOSE_FILE), "-p", project]


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
    # Defaults to skip (false) when unset; CI sets this to fail loudly instead.
    return seed._env_bool("NETBOX_REQUIRE_DOCKER", False)


def _should_clean() -> bool:
    # When the knob is unset, default to cleaning in CI (drop the DB volume) and
    # keeping it locally for fast re-runs; an explicit value always wins.
    default = bool(os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"))
    return seed._env_bool("NETBOX_IT_CLEAN", default)


def _port_in_use(result: subprocess.CompletedProcess[str]) -> bool:
    """True when ``compose up`` failed because the host port was already taken."""
    blob = f"{result.stdout}\n{result.stderr}".lower()
    return any(
        marker in blob
        for marker in (
            "address already in use",
            "port is already allocated",
            "failed to bind host port",
            "bind for",
        )
    )


def _run(cmd: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, env=env, capture_output=True, text=True, check=False)


def _dump_logs(project: str, env: dict[str, str]) -> str:
    logs = _run([*_compose_base(project), "logs", "--no-color", "--tail", "120", "netbox"], env)
    return logs.stdout + logs.stderr


def _bring_up_sim() -> tuple[str, str, dict[str, str]]:
    """Boot the compose stack on a host port; return ``(base_url, project, env)``.

    A ``:0``-probed free port can be grabbed by another process between the
    probe and compose publishing it (TOCTOU), so when the port isn't pinned via
    ``NETBOX_SIM_PORT`` we retry the whole bring-up on a fresh port a few times.
    The partial stack (and its volume) is always torn down before retrying or
    failing, and any non-port error fails loudly immediately.
    """
    pinned = os.environ.get("NETBOX_SIM_PORT")
    attempts = 1 if pinned else _MAX_PORT_ATTEMPTS
    details = ""
    for attempt in range(1, attempts + 1):
        port = pinned or str(_free_port())
        project = _project_name(port)
        base_url = f"http://127.0.0.1:{port}"
        env = {**os.environ, "NETBOX_SIM_PORT": port, "NETBOX_SIM_TOKEN": TOKEN}

        up = _run(
            [*_compose_base(project), "up", "-d", "--wait", "--wait-timeout", WAIT_TIMEOUT], env
        )
        if up.returncode == 0:
            return base_url, project, env

        details = f"{up.stdout}\n{up.stderr}\n--- netbox logs ---\n{_dump_logs(project, env)}"
        _run([*_compose_base(project), "down", "-v"], env)
        if pinned or not _port_in_use(up) or attempt == attempts:
            break  # nothing left to retry — fall through and fail loudly

    pytest.fail(f"failed to start NetBox simulator:\n{details}")


@pytest.fixture(scope="session")
def netbox_sim() -> Iterator[tuple[str, str]]:
    """Boot the NetBox sim, seed it, and yield ``(base_url, token)``."""
    if not _docker_available():
        msg = f"container runtime '{_runtime()}' not available; skipping NetBox integration tests"
        if _require_docker():
            pytest.fail(f"NETBOX_REQUIRE_DOCKER is set but {msg}")
        pytest.skip(msg)

    base_url, project, env = _bring_up_sim()

    try:
        seed.seed_netbox(base_url, TOKEN, verify_ssl=False)
    except Exception as exc:  # surface any seed failure loudly
        details = f"{exc}\n--- netbox logs ---\n{_dump_logs(project, env)}"
        if _should_clean():
            _run([*_compose_base(project), "down", "-v"], env)
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
        _run([*_compose_base(project), *down], env)


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
