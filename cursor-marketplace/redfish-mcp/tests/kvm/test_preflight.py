"""Tests for the runtime-deps preflight check."""

from __future__ import annotations

import pytest

from redfish_mcp.kvm.daemon.preflight import check_runtime_deps
from redfish_mcp.kvm.exceptions import BackendUnsupportedError


class TestCheckRuntimeDeps:
    def test_all_present_does_not_raise(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "redfish_mcp.kvm.daemon.preflight.shutil.which",
            lambda name: f"/usr/bin/{name}",
        )
        check_runtime_deps()

    def test_missing_java_raises_with_install_hint(self, monkeypatch: pytest.MonkeyPatch):
        def fake_which(name: str) -> str | None:
            return None if name == "java" else f"/usr/bin/{name}"

        monkeypatch.setattr("redfish_mcp.kvm.daemon.preflight.shutil.which", fake_which)
        with pytest.raises(BackendUnsupportedError) as exc_info:
            check_runtime_deps()
        msg = str(exc_info.value)
        assert "java" in msg
        assert "apt install" in msg
        assert "openjdk-17-jre-headless" in msg

    def test_all_missing_lists_all_four(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("redfish_mcp.kvm.daemon.preflight.shutil.which", lambda _name: None)
        monkeypatch.setattr("redfish_mcp.kvm.daemon.preflight._find_unpack200", lambda: None)
        with pytest.raises(BackendUnsupportedError) as exc_info:
            check_runtime_deps()
        msg = str(exc_info.value)
        assert "java" in msg
        assert "Xvfb" in msg
        assert "x11vnc" in msg
        assert "unpack200" in msg
        assert "openjdk-11-jdk" in msg
        assert exc_info.value.reason == "backend_unsupported"

    def test_unpack200_found_via_known_path(self, monkeypatch: pytest.MonkeyPatch):
        def fake_which(name: str) -> str | None:
            return None if name == "unpack200" else f"/usr/bin/{name}"

        monkeypatch.setattr("redfish_mcp.kvm.daemon.preflight.shutil.which", fake_which)
        monkeypatch.setattr(
            "redfish_mcp.kvm.daemon.preflight._find_unpack200",
            lambda: "/usr/lib/jvm/java-11-openjdk-amd64/bin/unpack200",
        )
        check_runtime_deps()
