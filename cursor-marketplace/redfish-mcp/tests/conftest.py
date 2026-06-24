import os

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _cli_runner_simulates_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Treat CliRunner invocations as interactive (TTY) by default.

    ``mcp_common.cli.should_emit_json`` auto-emits JSON when stdout is not a
    TTY, so piped/captured output is machine-readable without ``--json``. Under
    Typer's ``CliRunner`` stdout is *never* a TTY, so without this fixture every
    human-mode CLI assertion in the suite would receive JSON instead. Patch
    ``should_emit_json`` to honor only the explicit ``--json`` flag, restoring
    interactive defaults. Tests that specifically exercise the piped/non-TTY
    behavior re-patch ``should_emit_json`` locally (the later patch wins).
    """

    def _identity(explicit_json: bool) -> bool:
        return explicit_json

    monkeypatch.setattr("mcp_common.dual_mode.builder.should_emit_json", _identity)
    monkeypatch.setattr("redfish_mcp.cli.should_emit_json", _identity)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip ``subprocess``-marked tests unless explicitly opted in.

    The ``subprocess`` KVM tests spawn real ``Xvfb`` / ``x11vnc`` / Java / VNC
    (vncdotool/Twisted) processes and threads. Beyond needing those binaries,
    the VNC client leaves a non-daemon reactor thread that prevents the Python
    process from exiting cleanly after the suite finishes — which would hang the
    monorepo CI ``test`` job (it runs ``-m "not integration and not e2e and not
    slow"``, which does NOT exclude ``subprocess``). Gate them behind an explicit
    ``REDFISH_RUN_SUBPROCESS_TESTS=1`` opt-in so the default dev loop and CI stay
    deterministic and fast; run them locally with that env var when iterating on
    the KVM backends.
    """
    if os.environ.get("REDFISH_RUN_SUBPROCESS_TESTS") == "1":
        return
    skip = pytest.mark.skip(
        reason="subprocess tests are opt-in; set REDFISH_RUN_SUBPROCESS_TESTS=1 to run them"
    )
    for item in items:
        if "subprocess" in item.keywords:
            item.add_marker(skip)
