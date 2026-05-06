"""Environment-variable-driven configuration for the KVM feature."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_ALLOWED_BACKENDS = {"java", "playwright", "auto"}


@dataclass(frozen=True)
class KVMConfig:
    socket_dir: Path
    session_idle_s: int
    daemon_idle_s: int
    max_concurrent: int
    backend: str
    java_bin: str
    jar_cache_dir: Path
    log_level: str
    daemon_path: Path | None

    @classmethod
    def load(cls) -> KVMConfig:
        socket_dir_env = os.getenv("REDFISH_KVM_SOCKET_DIR")
        if socket_dir_env:
            socket_dir = Path(socket_dir_env).expanduser()
        else:
            xdg = os.getenv("XDG_RUNTIME_DIR")
            socket_dir = Path(xdg).expanduser() if xdg else Path("/tmp")

        jar_dir_env = os.getenv("REDFISH_KVM_JAR_CACHE_DIR")
        if jar_dir_env:
            jar_cache_dir = Path(jar_dir_env).expanduser()
        else:
            xdg_cache = os.getenv("XDG_CACHE_HOME")
            base = Path(xdg_cache).expanduser() if xdg_cache else Path.home() / ".cache"
            jar_cache_dir = base / "redfish-mcp" / "kvm" / "jars"

        daemon_path_env = os.getenv("REDFISH_KVM_DAEMON_PATH")
        daemon_path = Path(daemon_path_env).expanduser() if daemon_path_env else None

        backend = os.getenv("REDFISH_KVM_BACKEND", "java")
        if backend not in _ALLOWED_BACKENDS:
            raise ValueError(
                f"REDFISH_KVM_BACKEND must be one of {sorted(_ALLOWED_BACKENDS)}, got {backend!r}"
            )

        return cls(
            socket_dir=socket_dir,
            session_idle_s=_env_int("REDFISH_KVM_SESSION_IDLE_S", 300),
            daemon_idle_s=_env_int("REDFISH_KVM_DAEMON_IDLE_S", 600),
            max_concurrent=_env_int("REDFISH_KVM_MAX_CONCURRENT", 4),
            backend=backend,
            java_bin=os.getenv("REDFISH_KVM_JAVA_BIN", "java"),
            jar_cache_dir=jar_cache_dir,
            log_level=os.getenv("REDFISH_KVM_LOG_LEVEL", "INFO"),
            daemon_path=daemon_path,
        )


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
