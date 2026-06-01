"""Tests for KVMConfig env-var loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from redfish_mcp.kvm.config import KVMConfig


class TestKVMConfigDefaults:
    def test_all_defaults(self, tmp_runtime_dir: Path, monkeypatch: pytest.MonkeyPatch):
        for var in [
            "REDFISH_KVM_DAEMON_PATH",
            "REDFISH_KVM_SOCKET_DIR",
            "REDFISH_KVM_SESSION_IDLE_S",
            "REDFISH_KVM_DAEMON_IDLE_S",
            "REDFISH_KVM_MAX_CONCURRENT",
            "REDFISH_KVM_BACKEND",
            "REDFISH_KVM_JAVA_BIN",
            "REDFISH_KVM_JAR_CACHE_DIR",
            "REDFISH_KVM_LOG_LEVEL",
        ]:
            monkeypatch.delenv(var, raising=False)

        cfg = KVMConfig.load()
        assert cfg.session_idle_s == 300
        assert cfg.daemon_idle_s == 600
        assert cfg.max_concurrent == 4
        assert cfg.backend == "java"
        assert cfg.java_bin == "java"
        assert cfg.log_level == "INFO"
        assert cfg.socket_dir == tmp_runtime_dir
        assert cfg.daemon_path is None  # sentinel: auto-derive


class TestKVMConfigOverrides:
    def test_env_overrides_take_effect(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        socket_dir = tmp_path / "sock"
        jar_dir = tmp_path / "jars"
        monkeypatch.setenv("REDFISH_KVM_SOCKET_DIR", str(socket_dir))
        monkeypatch.setenv("REDFISH_KVM_SESSION_IDLE_S", "120")
        monkeypatch.setenv("REDFISH_KVM_DAEMON_IDLE_S", "240")
        monkeypatch.setenv("REDFISH_KVM_MAX_CONCURRENT", "8")
        monkeypatch.setenv("REDFISH_KVM_BACKEND", "playwright")
        monkeypatch.setenv("REDFISH_KVM_JAVA_BIN", "/opt/jre/bin/java")
        monkeypatch.setenv("REDFISH_KVM_JAR_CACHE_DIR", str(jar_dir))
        monkeypatch.setenv("REDFISH_KVM_LOG_LEVEL", "DEBUG")

        cfg = KVMConfig.load()
        assert cfg.socket_dir == socket_dir
        assert cfg.session_idle_s == 120
        assert cfg.daemon_idle_s == 240
        assert cfg.max_concurrent == 8
        assert cfg.backend == "playwright"
        assert cfg.java_bin == "/opt/jre/bin/java"
        assert cfg.jar_cache_dir == jar_dir
        assert cfg.log_level == "DEBUG"


class TestKVMConfigFallback:
    def test_socket_dir_falls_back_to_tmp(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        monkeypatch.delenv("REDFISH_KVM_SOCKET_DIR", raising=False)
        cfg = KVMConfig.load()
        assert cfg.socket_dir == Path("/tmp")


class TestKVMConfigValidation:
    def test_invalid_int_raises(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("REDFISH_KVM_SESSION_IDLE_S", "not-a-number")
        with pytest.raises(ValueError, match="REDFISH_KVM_SESSION_IDLE_S"):
            KVMConfig.load()

    def test_backend_allowlist(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("REDFISH_KVM_BACKEND", "telepathy")
        with pytest.raises(ValueError, match="REDFISH_KVM_BACKEND"):
            KVMConfig.load()
