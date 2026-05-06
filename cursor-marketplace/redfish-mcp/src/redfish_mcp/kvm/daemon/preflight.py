"""Preflight check for KVM runtime system dependencies.

Backend-aware: the Java iKVM backend requires openjdk, Xvfb, x11vnc,
and unpack200 at the OS level. The Playwright backend only needs
Chromium (installed via ``playwright install chromium``).
"""

from __future__ import annotations

import os
import shutil

from redfish_mcp.kvm.exceptions import BackendUnsupportedError

_JAVA_REQUIRED_BINARIES: tuple[str, ...] = ("java", "Xvfb", "x11vnc")
_JAVA_APT_INSTALL_HINT = "sudo apt install -y openjdk-17-jre-headless openjdk-11-jdk xvfb x11vnc"
_PLAYWRIGHT_INSTALL_HINT = (
    "uv add playwright --optional kvm-playwright && uv run playwright install chromium"
)


def _find_unpack200() -> str | None:
    """Search known JDK installation directories for unpack200."""
    candidates = [
        "/usr/lib/jvm/java-11-openjdk-arm64/bin/unpack200",
        "/usr/lib/jvm/java-11-openjdk-amd64/bin/unpack200",
        "/usr/lib/jvm/java-8-openjdk-arm64/bin/unpack200",
        "/usr/lib/jvm/java-8-openjdk-amd64/bin/unpack200",
        "/usr/local/opt/openjdk@11/bin/unpack200",
        "/opt/homebrew/opt/openjdk@11/bin/unpack200",
    ]
    for path in candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def _check_java_deps() -> None:
    """Raise if Java backend dependencies are missing."""
    missing = [b for b in _JAVA_REQUIRED_BINARIES if shutil.which(b) is None]
    if shutil.which("unpack200") is None and _find_unpack200() is None:
        missing.append("unpack200")
    if missing:
        raise BackendUnsupportedError(
            f"Missing KVM runtime dependencies: {', '.join(missing)}. "
            f"Install with: {_JAVA_APT_INSTALL_HINT}"
        )


def _check_playwright_deps() -> None:
    """Raise if Playwright backend dependencies are missing."""
    try:
        import playwright  # noqa: F401
    except ImportError as exc:
        raise BackendUnsupportedError(
            f"playwright is not installed. Install with: {_PLAYWRIGHT_INSTALL_HINT}"
        ) from exc

    browser_path = os.path.expanduser("~/.cache/ms-playwright")
    if not os.path.isdir(browser_path):
        raise BackendUnsupportedError(
            "Playwright browsers not installed. Run: uv run playwright install chromium"
        )


def check_runtime_deps(backend: str = "java") -> None:
    """Raise ``BackendUnsupportedError`` if required deps for *backend* are missing."""
    if backend == "playwright":
        _check_playwright_deps()
    elif backend == "java" or backend == "auto":
        _check_java_deps()
