import os

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


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
