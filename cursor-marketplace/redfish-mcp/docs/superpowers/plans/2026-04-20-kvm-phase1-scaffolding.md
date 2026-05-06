# KVM Phase 1 — Scaffolding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the non-functional scaffolding for the KVM console feature: module layout, backend protocol, local daemon with UNIX-socket RPC, session cache, idle reaper, observation logging, client library, MCP tool stubs, and CLI command stubs. No real backend — all tools return `not_implemented`.

**Architecture:** Plug-in `KVMBackend` protocol sits behind a local UNIX-socket daemon. Daemon is auto-started by a client library shared between the stdio MCP server and the `redfish-cli` one-shot. Daemon owns session lifecycle (cache, idle reaper) and logs observations into the existing `agent_state.sqlite3`. Phase 2 (#64) will swap in a real `JavaIkvmBackend`.

**Tech Stack:** Python 3.12+, `asyncio` UNIX streams (stdlib), `typer` for CLI, `fastmcp` for MCP tools, `sqlite3` via existing `AgentStateStore`, `pytest` + `pytest.mark.anyio` for async tests.

**Design spec:** `docs/superpowers/specs/2026-04-20-kvm-console-design.md`.

---

## File layout

New files under `src/redfish_mcp/kvm/`:

```
src/redfish_mcp/kvm/
├── __init__.py                 # package marker + re-exports
├── config.py                   # KVMConfig dataclass (env-var parsing)
├── exceptions.py               # KVMError hierarchy
├── backend.py                  # KVMBackend Protocol + SessionHandle + ProgressCallback
├── fake_backend.py             # FakeBackend recorder (test-only, in src for reuse)
├── protocol.py                 # JSON line-framed request/response/progress envelopes
├── client.py                   # DaemonClient (talks to the UNIX socket)
├── autostart.py                # ensure_daemon_running()
├── tools.py                    # MCP tool stubs (registered from mcp_server.py)
├── cli_commands.py             # typer subcommand stubs (registered from cli.py)
└── daemon/
    ├── __init__.py
    ├── __main__.py             # python -m redfish_mcp.kvm.daemon
    ├── lifecycle.py            # socket path, PID file, stale detection
    ├── cache.py                # SessionCache (host, user, backend) → SessionHandle
    ├── reaper.py               # IdleReaper with injectable clock
    ├── progress.py             # ProgressPublisher pub/sub
    ├── router.py               # JSON-RPC-style dispatcher
    ├── observations.py         # AgentStateStore wiring helpers
    └── server.py               # asyncio UNIX socket server + main()
```

New test files under `tests/kvm/`:

```
tests/kvm/
├── __init__.py
├── conftest.py                 # shared fixtures (tmp socket dir, mock clock)
├── test_config.py
├── test_exceptions.py
├── test_backend_protocol.py
├── test_fake_backend.py
├── test_protocol.py
├── test_lifecycle.py
├── test_cache.py
├── test_reaper.py
├── test_router.py
├── test_progress.py
├── test_observations.py
├── test_server.py              # end-to-end daemon accept → respond
├── test_client.py
├── test_autostart.py
├── test_tools.py               # MCP tool registration stubs
└── test_cli.py                 # CLI subcommand stubs
```

Modified files:

- `pyproject.toml` — add `[project.optional-dependencies.kvm]` (empty for phase 1).
- `src/redfish_mcp/mcp_server.py` — register KVM tool stubs.
- `src/redfish_mcp/cli.py` — register `kvm` typer subcommand.

Every new file starts with `from __future__ import annotations`. Target line length 100 (ruff default for this repo). Type hints strict (mypy strict).

---

## Task 1 — Package skeleton and pyproject extra

**Goal:** Get an empty `redfish_mcp.kvm` package importable; register the `kvm` optional-dependency group.

**Files:**
- Create: `src/redfish_mcp/kvm/__init__.py`
- Create: `src/redfish_mcp/kvm/daemon/__init__.py`
- Create: `tests/kvm/__init__.py`
- Create: `tests/kvm/conftest.py`
- Create: `tests/kvm/test_package.py`
- Modify: `pyproject.toml`

**Steps:**

- [ ] **Step 1: Write the failing test** — `tests/kvm/test_package.py`

```python
"""Package-import smoke test for the kvm subpackage."""

from __future__ import annotations


def test_kvm_package_importable():
    import redfish_mcp.kvm
    import redfish_mcp.kvm.daemon

    assert hasattr(redfish_mcp.kvm, "__name__")
    assert redfish_mcp.kvm.__name__ == "redfish_mcp.kvm"
    assert redfish_mcp.kvm.daemon.__name__ == "redfish_mcp.kvm.daemon"
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/kvm/test_package.py -v
```

Expected: `ModuleNotFoundError: No module named 'redfish_mcp.kvm'`.

- [ ] **Step 3: Create `tests/kvm/__init__.py`** — empty file.

- [ ] **Step 4: Create `tests/kvm/conftest.py`**

```python
"""Shared fixtures for kvm tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def tmp_runtime_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A fresh per-test runtime dir exposed via XDG_RUNTIME_DIR."""
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    yield runtime
```

- [ ] **Step 5: Create `src/redfish_mcp/kvm/__init__.py`**

```python
"""KVM console feature for redfish-mcp.

Phase 1 — scaffolding only. See docs/superpowers/specs/2026-04-20-kvm-console-design.md.
"""

from __future__ import annotations

__all__: list[str] = []
```

- [ ] **Step 6: Create `src/redfish_mcp/kvm/daemon/__init__.py`**

```python
"""KVM daemon — local UNIX-socket supervisor for KVM sessions."""

from __future__ import annotations

__all__: list[str] = []
```

- [ ] **Step 7: Add pyproject optional-dependency group** — append to `pyproject.toml` after the `[dependency-groups]` block:

```toml
[project.optional-dependencies]
kvm = []
```

- [ ] **Step 8: Run test to verify it passes**

```
uv run pytest tests/kvm/test_package.py -v
```

Expected: `1 passed`.

- [ ] **Step 9: Run full test suite to verify no regression**

```
uv run pytest -q --no-header
```

Expected: `59 passed, 1 failed` (pre-existing `test_netbox_resolves_ori_site` — documented baseline).

- [ ] **Step 10: Commit**

```bash
git add src/redfish_mcp/kvm tests/kvm pyproject.toml
git commit -m "feat(kvm): add kvm subpackage skeleton and optional-dependency group"
```

---

## Task 2 — Exceptions module

**Goal:** Central error hierarchy so callers can `except KVMError` broadly or target specific failures.

**Files:**
- Create: `src/redfish_mcp/kvm/exceptions.py`
- Create: `tests/kvm/test_exceptions.py`

**Steps:**

- [ ] **Step 1: Write the failing test** — `tests/kvm/test_exceptions.py`

```python
"""Tests for kvm.exceptions module."""

from __future__ import annotations

import pytest

from redfish_mcp.kvm.exceptions import (
    AuthFailed,
    BackendUnsupported,
    DaemonUnavailable,
    JarMismatch,
    JnlpUnavailable,
    KVMError,
    SessionLost,
    SlotBusy,
    StaleSession,
)


class TestExceptionHierarchy:
    def test_all_inherit_from_kvm_error(self):
        subclasses = [
            AuthFailed, SlotBusy, StaleSession, SessionLost,
            BackendUnsupported, JarMismatch, JnlpUnavailable,
            DaemonUnavailable,
        ]
        for cls in subclasses:
            assert issubclass(cls, KVMError), f"{cls.__name__} must inherit KVMError"

    def test_kvm_error_has_stage_and_reason(self):
        err = KVMError("boom", stage="launching_java", reason="kvm_slot_busy")
        assert str(err) == "boom"
        assert err.stage == "launching_java"
        assert err.reason == "kvm_slot_busy"

    def test_kvm_error_stage_reason_optional(self):
        err = KVMError("no context")
        assert err.stage is None
        assert err.reason is None

    def test_subclasses_preset_reason(self):
        cases: list[tuple[type[KVMError], str]] = [
            (AuthFailed, "auth_failed"),
            (SlotBusy, "kvm_slot_busy"),
            (StaleSession, "stale"),
            (SessionLost, "session_lost"),
            (BackendUnsupported, "backend_unsupported"),
            (JarMismatch, "jar_mismatch"),
            (JnlpUnavailable, "jnlp_unavailable"),
            (DaemonUnavailable, "daemon_unavailable"),
        ]
        for cls, expected_reason in cases:
            err = cls("msg")
            assert err.reason == expected_reason, cls.__name__

    def test_kvm_error_can_be_raised(self):
        with pytest.raises(AuthFailed) as exc_info:
            raise AuthFailed("bad creds", stage="authenticating")
        assert exc_info.value.reason == "auth_failed"
        assert exc_info.value.stage == "authenticating"
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/kvm/test_exceptions.py -v
```

Expected: `ModuleNotFoundError` for `redfish_mcp.kvm.exceptions`.

- [ ] **Step 3: Implement** — `src/redfish_mcp/kvm/exceptions.py`

```python
"""Error hierarchy for the KVM console feature.

Subclasses carry a preset ``reason`` token so callers can pattern-match without
string comparisons. ``stage`` tracks which cold-start stage failed (see spec).
"""

from __future__ import annotations


class KVMError(Exception):
    """Base class for all KVM-feature errors."""

    reason: str | None = None

    def __init__(
        self,
        message: str,
        *,
        stage: str | None = None,
        reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        if reason is not None:
            self.reason = reason


class AuthFailed(KVMError):
    reason = "auth_failed"


class SlotBusy(KVMError):
    reason = "kvm_slot_busy"


class StaleSession(KVMError):
    reason = "stale"


class SessionLost(KVMError):
    reason = "session_lost"


class BackendUnsupported(KVMError):
    reason = "backend_unsupported"


class JarMismatch(KVMError):
    reason = "jar_mismatch"


class JnlpUnavailable(KVMError):
    reason = "jnlp_unavailable"


class DaemonUnavailable(KVMError):
    reason = "daemon_unavailable"
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/kvm/test_exceptions.py -v
```

Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/redfish_mcp/kvm/exceptions.py tests/kvm/test_exceptions.py
git commit -m "feat(kvm): add exceptions hierarchy with preset failure reasons"
```

---

## Task 3 — Config module

**Goal:** Single source of truth for env-var-driven config. Zero-arg `KVMConfig.load()` reads env and returns a frozen dataclass.

**Files:**
- Create: `src/redfish_mcp/kvm/config.py`
- Create: `tests/kvm/test_config.py`

**Steps:**

- [ ] **Step 1: Write the failing test** — `tests/kvm/test_config.py`

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/kvm/test_config.py -v
```

Expected: `ModuleNotFoundError` for `redfish_mcp.kvm.config`.

- [ ] **Step 3: Implement** — `src/redfish_mcp/kvm/config.py`

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/kvm/test_config.py -v
```

Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/redfish_mcp/kvm/config.py tests/kvm/test_config.py
git commit -m "feat(kvm): add KVMConfig env-var loader with validation"
```

---

## Task 4 — Backend protocol types

**Goal:** Define `SessionHandle`, `ProgressCallback`, `ProgressEvent`, and the `KVMBackend` Protocol so backends and the daemon can be developed independently.

**Files:**
- Create: `src/redfish_mcp/kvm/backend.py`
- Create: `tests/kvm/test_backend_protocol.py`

**Steps:**

- [ ] **Step 1: Write the failing test** — `tests/kvm/test_backend_protocol.py`

```python
"""Tests for the KVMBackend protocol and its supporting types."""

from __future__ import annotations

from dataclasses import asdict

import pytest

from redfish_mcp.kvm.backend import (
    KVMBackend,
    ProgressCallback,
    ProgressEvent,
    SessionHandle,
)


class TestSessionHandle:
    def test_handle_is_frozen_with_expected_fields(self):
        h = SessionHandle(
            session_id="sess-1",
            host="10.0.0.1",
            user="admin",
            backend="java",
            opened_at_ms=123456,
        )
        assert h.session_id == "sess-1"
        assert h.host == "10.0.0.1"
        assert h.user == "admin"
        assert h.backend == "java"
        assert h.opened_at_ms == 123456
        with pytest.raises(Exception):
            h.host = "other"  # type: ignore[misc]


class TestProgressEvent:
    def test_roundtrip_via_asdict(self):
        e = ProgressEvent(stage="ready", detail="")
        assert asdict(e) == {"stage": "ready", "detail": ""}


class TestKVMBackendProtocol:
    def test_a_class_with_correct_methods_satisfies_protocol(self):
        class MiniBackend:
            async def open(
                self,
                host: str,
                user: str,
                password: str,
                progress: ProgressCallback,
            ) -> SessionHandle:
                return SessionHandle(
                    session_id="x", host=host, user=user, backend="mini", opened_at_ms=0
                )

            async def screenshot(self, session: SessionHandle) -> bytes:
                return b""

            async def sendkeys(self, session: SessionHandle, text: str) -> None:
                return None

            async def sendkey(
                self, session: SessionHandle, key: str, modifiers: list[str] | None = None
            ) -> None:
                return None

            async def close(self, session: SessionHandle) -> None:
                return None

            async def health(self, session: SessionHandle) -> str:
                return "ok"

        b: KVMBackend = MiniBackend()
        assert isinstance(b, KVMBackend)  # runtime-checkable

    def test_a_class_missing_methods_is_rejected(self):
        class NoOp:
            pass

        assert not isinstance(NoOp(), KVMBackend)
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/kvm/test_backend_protocol.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement** — `src/redfish_mcp/kvm/backend.py`

```python
"""KVMBackend protocol and supporting types.

Backends implement this protocol. Consumers (the daemon) depend only on the
protocol, never on a concrete implementation, so the Java backend and future
Playwright backend are drop-in interchangeable.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class SessionHandle:
    """Opaque handle returned by ``KVMBackend.open``."""

    session_id: str
    host: str
    user: str
    backend: str
    opened_at_ms: int


@dataclass(frozen=True)
class ProgressEvent:
    """One progress tick emitted by a backend during ``open()``."""

    stage: str
    detail: str = ""


ProgressCallback = Callable[[ProgressEvent], Awaitable[None]]
"""Async callable that a backend invokes to report progress."""


@runtime_checkable
class KVMBackend(Protocol):
    """Protocol every KVM backend must satisfy."""

    async def open(
        self,
        host: str,
        user: str,
        password: str,
        progress: ProgressCallback,
    ) -> SessionHandle: ...

    async def screenshot(self, session: SessionHandle) -> bytes: ...

    async def sendkeys(self, session: SessionHandle, text: str) -> None: ...

    async def sendkey(
        self,
        session: SessionHandle,
        key: str,
        modifiers: list[str] | None = None,
    ) -> None: ...

    async def close(self, session: SessionHandle) -> None: ...

    async def health(self, session: SessionHandle) -> str: ...
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/kvm/test_backend_protocol.py -v
```

Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/redfish_mcp/kvm/backend.py tests/kvm/test_backend_protocol.py
git commit -m "feat(kvm): add KVMBackend protocol and SessionHandle/ProgressEvent types"
```

---

## Task 5 — FakeBackend recorder

**Goal:** An in-memory backend usable from tests at any layer (daemon, router, client). Records every call in order.

**Files:**
- Create: `src/redfish_mcp/kvm/fake_backend.py`
- Create: `tests/kvm/test_fake_backend.py`

**Steps:**

- [ ] **Step 1: Write the failing test** — `tests/kvm/test_fake_backend.py`

```python
"""Tests for FakeBackend."""

from __future__ import annotations

import pytest

from redfish_mcp.kvm.backend import ProgressEvent, SessionHandle
from redfish_mcp.kvm.fake_backend import FakeBackend


class TestFakeBackendRecording:
    @pytest.mark.anyio
    async def test_open_emits_progress_and_returns_handle(self):
        events: list[ProgressEvent] = []

        async def capture(e: ProgressEvent) -> None:
            events.append(e)

        be = FakeBackend()
        handle = await be.open("10.0.0.1", "admin", "pw", capture)
        assert isinstance(handle, SessionHandle)
        assert handle.host == "10.0.0.1"
        assert handle.user == "admin"
        assert handle.backend == "fake"
        assert [e.stage for e in events] == [
            "authenticating", "fetching_jar", "starting_xvfb",
            "launching_java", "starting_vnc", "handshaking", "ready",
        ]
        assert be.calls == [("open", "10.0.0.1", "admin")]

    @pytest.mark.anyio
    async def test_screenshot_returns_stub_png(self):
        be = FakeBackend()
        handle = await _open(be)
        png = await be.screenshot(handle)
        assert png.startswith(b"\x89PNG")
        assert ("screenshot", handle.session_id) in be.calls

    @pytest.mark.anyio
    async def test_sendkeys_and_sendkey_recorded(self):
        be = FakeBackend()
        handle = await _open(be)
        await be.sendkeys(handle, "hello")
        await be.sendkey(handle, "Enter", ["Ctrl"])
        assert ("sendkeys", handle.session_id, "hello") in be.calls
        assert ("sendkey", handle.session_id, "Enter", ("Ctrl",)) in be.calls

    @pytest.mark.anyio
    async def test_close_is_idempotent(self):
        be = FakeBackend()
        handle = await _open(be)
        await be.close(handle)
        await be.close(handle)
        assert be.calls.count(("close", handle.session_id)) == 2

    @pytest.mark.anyio
    async def test_health_ok_by_default(self):
        be = FakeBackend()
        handle = await _open(be)
        assert await be.health(handle) == "ok"

    @pytest.mark.anyio
    async def test_fail_on_stage_raises(self):
        from redfish_mcp.kvm.exceptions import AuthFailed

        be = FakeBackend(fail_on_stage="authenticating", fail_as=AuthFailed)
        with pytest.raises(AuthFailed):
            await be.open("h", "u", "p", _noop)


async def _open(be: FakeBackend) -> SessionHandle:
    return await be.open("h", "u", "p", _noop)


async def _noop(_e: ProgressEvent) -> None:
    return None
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/kvm/test_fake_backend.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement** — `src/redfish_mcp/kvm/fake_backend.py`

```python
"""In-memory KVMBackend for tests."""

from __future__ import annotations

import itertools
import struct
import time
import zlib

from redfish_mcp.kvm.backend import ProgressCallback, ProgressEvent, SessionHandle
from redfish_mcp.kvm.exceptions import KVMError

_STAGES = (
    "authenticating",
    "fetching_jar",
    "starting_xvfb",
    "launching_java",
    "starting_vnc",
    "handshaking",
    "ready",
)

# Minimal 1x1 PNG so screenshot() returns real PNG bytes.
_PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n"
    + b"\x00\x00\x00\rIHDR"
    + b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    + struct.pack(">I", zlib.crc32(b"IHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"))
    + b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
    + b"\x0d\n-\xb4"
    + b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


class FakeBackend:
    """Backend that records calls; optionally fails at a given stage."""

    def __init__(
        self,
        *,
        fail_on_stage: str | None = None,
        fail_as: type[KVMError] = KVMError,
    ) -> None:
        self.calls: list[tuple[object, ...]] = []
        self._fail_stage = fail_on_stage
        self._fail_cls = fail_as
        self._ids = (f"fake-{n}" for n in itertools.count(1))

    async def open(
        self,
        host: str,
        user: str,
        password: str,
        progress: ProgressCallback,
    ) -> SessionHandle:
        self.calls.append(("open", host, user))
        for stage in _STAGES:
            if self._fail_stage == stage:
                raise self._fail_cls(f"fake failure at {stage}", stage=stage)
            await progress(ProgressEvent(stage=stage))
        return SessionHandle(
            session_id=next(self._ids),
            host=host,
            user=user,
            backend="fake",
            opened_at_ms=int(time.time() * 1000),
        )

    async def screenshot(self, session: SessionHandle) -> bytes:
        self.calls.append(("screenshot", session.session_id))
        return _PNG_1X1

    async def sendkeys(self, session: SessionHandle, text: str) -> None:
        self.calls.append(("sendkeys", session.session_id, text))

    async def sendkey(
        self,
        session: SessionHandle,
        key: str,
        modifiers: list[str] | None = None,
    ) -> None:
        self.calls.append(("sendkey", session.session_id, key, tuple(modifiers or [])))

    async def close(self, session: SessionHandle) -> None:
        self.calls.append(("close", session.session_id))

    async def health(self, session: SessionHandle) -> str:
        self.calls.append(("health", session.session_id))
        return "ok"
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/kvm/test_fake_backend.py -v
```

Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/redfish_mcp/kvm/fake_backend.py tests/kvm/test_fake_backend.py
git commit -m "feat(kvm): add FakeBackend recorder for tests"
```

---

## Task 6 — Socket protocol (JSON line framing)

**Goal:** Wire protocol primitives shared by daemon and client. Line-delimited JSON envelopes with `id`, `method`, `params`, `result`, `error`, `progress`.

**Files:**
- Create: `src/redfish_mcp/kvm/protocol.py`
- Create: `tests/kvm/test_protocol.py`

**Steps:**

- [ ] **Step 1: Write the failing test** — `tests/kvm/test_protocol.py`

```python
"""Tests for the JSON line-framed socket protocol."""

from __future__ import annotations

import json

import pytest

from redfish_mcp.kvm.protocol import (
    ErrorPayload,
    ProtocolError,
    Request,
    Response,
    decode_message,
    encode_message,
)


class TestRequestEncoding:
    def test_request_roundtrip(self):
        req = Request(id=1, method="open", params={"host": "x"})
        line = encode_message(req)
        assert line.endswith(b"\n")
        assert b"\n" not in line[:-1]
        back = decode_message(line)
        assert isinstance(back, Request)
        assert back.id == 1
        assert back.method == "open"
        assert back.params == {"host": "x"}


class TestResponseEncoding:
    def test_success_result(self):
        resp = Response(id=2, result={"png_b64": "AAA"})
        line = encode_message(resp)
        back = decode_message(line)
        assert isinstance(back, Response)
        assert back.id == 2
        assert back.result == {"png_b64": "AAA"}
        assert back.error is None
        assert back.progress is None

    def test_error_payload(self):
        resp = Response(
            id=3,
            error=ErrorPayload(code="auth_failed", message="bad creds", stage="authenticating"),
        )
        line = encode_message(resp)
        back = decode_message(line)
        assert isinstance(back, Response)
        assert back.error is not None
        assert back.error.code == "auth_failed"
        assert back.error.stage == "authenticating"

    def test_progress_envelope(self):
        resp = Response(id=4, progress={"stage": "starting_vnc", "detail": ""})
        line = encode_message(resp)
        back = decode_message(line)
        assert isinstance(back, Response)
        assert back.progress == {"stage": "starting_vnc", "detail": ""}


class TestDecodeErrors:
    def test_malformed_json_raises_protocol_error(self):
        with pytest.raises(ProtocolError):
            decode_message(b"not-json\n")

    def test_missing_id_raises(self):
        with pytest.raises(ProtocolError):
            decode_message(json.dumps({"method": "x"}).encode() + b"\n")

    def test_request_and_response_shape_conflict(self):
        with pytest.raises(ProtocolError):
            decode_message(json.dumps({"id": 1, "method": "x", "result": {}}).encode() + b"\n")
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/kvm/test_protocol.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement** — `src/redfish_mcp/kvm/protocol.py`

```python
"""Line-delimited JSON RPC envelopes for the KVM daemon."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


class ProtocolError(Exception):
    """Raised when a wire message cannot be parsed into a valid envelope."""


@dataclass(frozen=True)
class Request:
    id: int
    method: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ErrorPayload:
    code: str
    message: str
    stage: str | None = None


@dataclass(frozen=True)
class Response:
    id: int
    result: dict[str, Any] | None = None
    error: ErrorPayload | None = None
    progress: dict[str, Any] | None = None


def encode_message(msg: Request | Response) -> bytes:
    if isinstance(msg, Request):
        payload: dict[str, Any] = {"id": msg.id, "method": msg.method, "params": msg.params}
    else:
        payload = {"id": msg.id}
        if msg.result is not None:
            payload["result"] = msg.result
        if msg.error is not None:
            payload["error"] = {
                "code": msg.error.code,
                "message": msg.error.message,
                "stage": msg.error.stage,
            }
        if msg.progress is not None:
            payload["progress"] = msg.progress
    line = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return line.encode("utf-8") + b"\n"


def decode_message(line: bytes) -> Request | Response:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"invalid JSON: {exc}") from exc
    if not isinstance(payload, dict) or "id" not in payload:
        raise ProtocolError("missing 'id' field")

    is_request = "method" in payload
    has_response_fields = any(k in payload for k in ("result", "error", "progress"))

    if is_request and has_response_fields:
        raise ProtocolError("message has both request and response fields")
    if is_request:
        return Request(
            id=int(payload["id"]),
            method=str(payload["method"]),
            params=dict(payload.get("params") or {}),
        )

    err_raw = payload.get("error")
    err: ErrorPayload | None = None
    if err_raw is not None:
        err = ErrorPayload(
            code=str(err_raw["code"]),
            message=str(err_raw["message"]),
            stage=err_raw.get("stage"),
        )
    return Response(
        id=int(payload["id"]),
        result=payload.get("result"),
        error=err,
        progress=payload.get("progress"),
    )
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/kvm/test_protocol.py -v
```

Expected: `7 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/redfish_mcp/kvm/protocol.py tests/kvm/test_protocol.py
git commit -m "feat(kvm): add JSON line-framed socket protocol envelopes"
```

---

## Task 7 — Lifecycle primitives (socket path, PID file, stale detect)

**Goal:** One module owns socket/PID paths and stale-daemon detection so the server and client share logic.

**Files:**
- Create: `src/redfish_mcp/kvm/daemon/lifecycle.py`
- Create: `tests/kvm/test_lifecycle.py`

**Steps:**

- [ ] **Step 1: Write the failing test** — `tests/kvm/test_lifecycle.py`

```python
"""Tests for daemon lifecycle helpers."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from redfish_mcp.kvm.config import KVMConfig
from redfish_mcp.kvm.daemon.lifecycle import (
    DaemonLifecycle,
    is_process_alive,
)


def _cfg(socket_dir: Path, **overrides: object) -> KVMConfig:
    return KVMConfig(
        socket_dir=socket_dir,
        session_idle_s=int(overrides.get("session_idle_s", 300)),
        daemon_idle_s=int(overrides.get("daemon_idle_s", 600)),
        max_concurrent=int(overrides.get("max_concurrent", 4)),
        backend=str(overrides.get("backend", "java")),
        java_bin=str(overrides.get("java_bin", "java")),
        jar_cache_dir=Path(overrides.get("jar_cache_dir", socket_dir / "jars")),  # type: ignore[arg-type]
        log_level=str(overrides.get("log_level", "INFO")),
        daemon_path=None,
    )


class TestPaths:
    def test_socket_and_pid_paths_contain_uid(self, tmp_path: Path):
        lc = DaemonLifecycle(_cfg(tmp_path))
        uid = os.getuid()
        assert lc.socket_path == tmp_path / f"redfish-mcp-kvm-{uid}.sock"
        assert lc.pid_path == tmp_path / f"redfish-mcp-kvm-{uid}.pid"


class TestIsProcessAlive:
    def test_pid_0_is_never_alive(self):
        assert not is_process_alive(0)

    def test_self_is_alive(self):
        assert is_process_alive(os.getpid())

    def test_absurdly_large_pid_is_dead(self):
        assert not is_process_alive(99_999_999)


class TestClaimedBy:
    def test_missing_pid_file_means_no_claim(self, tmp_path: Path):
        lc = DaemonLifecycle(_cfg(tmp_path))
        assert lc.claimed_by_live_daemon() is False

    def test_stale_pid_file_is_removed(self, tmp_path: Path):
        lc = DaemonLifecycle(_cfg(tmp_path))
        lc.pid_path.write_text("99999999\n")
        lc.socket_path.touch()
        assert lc.claimed_by_live_daemon() is False
        assert not lc.pid_path.exists()
        assert not lc.socket_path.exists()

    def test_live_pid_is_honored(self, tmp_path: Path):
        lc = DaemonLifecycle(_cfg(tmp_path))
        lc.pid_path.write_text(f"{os.getpid()}\n")
        lc.socket_path.touch()
        assert lc.claimed_by_live_daemon() is True
        assert lc.pid_path.exists()


class TestWriteAndClear:
    def test_write_pid_sets_0600(self, tmp_path: Path):
        lc = DaemonLifecycle(_cfg(tmp_path))
        lc.write_pid(4242)
        assert lc.pid_path.read_text().strip() == "4242"
        mode = lc.pid_path.stat().st_mode & 0o777
        assert mode == 0o600

    def test_clear_removes_both(self, tmp_path: Path):
        lc = DaemonLifecycle(_cfg(tmp_path))
        lc.pid_path.write_text("1\n")
        lc.socket_path.touch()
        lc.clear()
        assert not lc.pid_path.exists()
        assert not lc.socket_path.exists()
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/kvm/test_lifecycle.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement** — `src/redfish_mcp/kvm/daemon/lifecycle.py`

```python
"""Socket + PID file lifecycle for the KVM daemon."""

from __future__ import annotations

import errno
import os
from dataclasses import dataclass
from pathlib import Path

from redfish_mcp.kvm.config import KVMConfig


def is_process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno == errno.EPERM
    return True


@dataclass
class DaemonLifecycle:
    config: KVMConfig

    @property
    def _uid(self) -> int:
        return os.getuid()

    @property
    def socket_path(self) -> Path:
        return self.config.socket_dir / f"redfish-mcp-kvm-{self._uid}.sock"

    @property
    def pid_path(self) -> Path:
        return self.config.socket_dir / f"redfish-mcp-kvm-{self._uid}.pid"

    def claimed_by_live_daemon(self) -> bool:
        if not self.pid_path.exists():
            return False
        try:
            pid = int(self.pid_path.read_text().strip())
        except (OSError, ValueError):
            self.clear()
            return False
        if is_process_alive(pid):
            return True
        self.clear()
        return False

    def write_pid(self, pid: int) -> None:
        self.config.socket_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.pid_path.with_suffix(".pid.tmp")
        tmp.write_text(f"{pid}\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.pid_path)

    def clear(self) -> None:
        for p in (self.pid_path, self.socket_path):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/kvm/test_lifecycle.py -v
```

Expected: `10 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/redfish_mcp/kvm/daemon/lifecycle.py tests/kvm/test_lifecycle.py
git commit -m "feat(kvm): add daemon lifecycle helpers with stale-PID detection"
```

---

## Task 8 — Session cache

**Goal:** In-memory cache keyed by `(host, user, backend)` with last-activity timestamps and eviction APIs the reaper uses.

**Files:**
- Create: `src/redfish_mcp/kvm/daemon/cache.py`
- Create: `tests/kvm/test_cache.py`

**Steps:**

- [ ] **Step 1: Write the failing test** — `tests/kvm/test_cache.py`

```python
"""Tests for SessionCache."""

from __future__ import annotations

from redfish_mcp.kvm.backend import SessionHandle
from redfish_mcp.kvm.daemon.cache import CacheEntry, SessionCache


def _handle(sid: str = "s1") -> SessionHandle:
    return SessionHandle(
        session_id=sid, host="h", user="u", backend="fake", opened_at_ms=0
    )


class TestSessionCache:
    def test_put_then_get(self):
        c = SessionCache(clock=lambda: 100)
        entry = c.put("h", "u", "fake", _handle())
        got = c.get("h", "u", "fake")
        assert got is entry
        assert entry.last_activity_ms == 100

    def test_get_updates_last_activity(self):
        now = [100]
        c = SessionCache(clock=lambda: now[0])
        c.put("h", "u", "fake", _handle())
        now[0] = 250
        e = c.get("h", "u", "fake")
        assert e is not None
        assert e.last_activity_ms == 250

    def test_miss_returns_none(self):
        c = SessionCache(clock=lambda: 0)
        assert c.get("nope", "u", "fake") is None

    def test_pop_removes_entry(self):
        c = SessionCache(clock=lambda: 0)
        c.put("h", "u", "fake", _handle())
        e = c.pop("h", "u", "fake")
        assert e is not None
        assert c.get("h", "u", "fake") is None

    def test_snapshot_is_a_copy(self):
        c = SessionCache(clock=lambda: 0)
        c.put("a", "u", "fake", _handle("sa"))
        c.put("b", "u", "fake", _handle("sb"))
        snap = c.snapshot()
        assert len(snap) == 2
        snap.clear()
        assert len(c.snapshot()) == 2

    def test_idle_entries_returns_stale(self):
        now = [100]
        c = SessionCache(clock=lambda: now[0])
        c.put("a", "u", "fake", _handle("sa"))
        now[0] = 500
        c.put("b", "u", "fake", _handle("sb"))
        # a is 400ms idle, b is 0ms idle
        stale = c.idle_entries(threshold_ms=300)
        assert len(stale) == 1
        assert stale[0].handle.session_id == "sa"

    def test_cacheentry_touch(self):
        e = CacheEntry(handle=_handle(), last_activity_ms=0)
        e.touch(1234)
        assert e.last_activity_ms == 1234
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/kvm/test_cache.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement** — `src/redfish_mcp/kvm/daemon/cache.py`

```python
"""Per-daemon in-memory session cache keyed by (host, user, backend)."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from redfish_mcp.kvm.backend import SessionHandle

Clock = Callable[[], int]


@dataclass
class CacheEntry:
    handle: SessionHandle
    last_activity_ms: int
    open_lock: object = field(default=None, repr=False)

    def touch(self, now_ms: int) -> None:
        self.last_activity_ms = now_ms


class SessionCache:
    """Thread-safe map of (host, user, backend) → CacheEntry."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._by_key: dict[tuple[str, str, str], CacheEntry] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(host: str, user: str, backend: str) -> tuple[str, str, str]:
        return (host, user, backend)

    def put(self, host: str, user: str, backend: str, handle: SessionHandle) -> CacheEntry:
        entry = CacheEntry(handle=handle, last_activity_ms=self._clock())
        with self._lock:
            self._by_key[self._key(host, user, backend)] = entry
        return entry

    def get(self, host: str, user: str, backend: str) -> CacheEntry | None:
        with self._lock:
            entry = self._by_key.get(self._key(host, user, backend))
            if entry is not None:
                entry.touch(self._clock())
            return entry

    def pop(self, host: str, user: str, backend: str) -> CacheEntry | None:
        with self._lock:
            return self._by_key.pop(self._key(host, user, backend), None)

    def snapshot(self) -> list[CacheEntry]:
        with self._lock:
            return list(self._by_key.values())

    def idle_entries(self, *, threshold_ms: int) -> list[CacheEntry]:
        cutoff = self._clock() - threshold_ms
        with self._lock:
            return [e for e in self._by_key.values() if e.last_activity_ms <= cutoff]
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/kvm/test_cache.py -v
```

Expected: `7 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/redfish_mcp/kvm/daemon/cache.py tests/kvm/test_cache.py
git commit -m "feat(kvm): add SessionCache with injectable clock and idle detection"
```

---

## Task 9 — Idle reaper

**Goal:** Drive periodic reaping. Uses the cache's `idle_entries` to pick stale sessions, closes them via the backend, and signals the daemon to self-exit after the configured all-empty grace period.

**Files:**
- Create: `src/redfish_mcp/kvm/daemon/reaper.py`
- Create: `tests/kvm/test_reaper.py`

**Steps:**

- [ ] **Step 1: Write the failing test** — `tests/kvm/test_reaper.py`

```python
"""Tests for IdleReaper."""

from __future__ import annotations

import asyncio

import pytest

from redfish_mcp.kvm.backend import SessionHandle
from redfish_mcp.kvm.daemon.cache import SessionCache
from redfish_mcp.kvm.daemon.reaper import IdleReaper


def _handle(sid: str) -> SessionHandle:
    return SessionHandle(session_id=sid, host="h", user="u", backend="fake", opened_at_ms=0)


class TestReaperSessionIdle:
    @pytest.mark.anyio
    async def test_reaps_stale_sessions(self):
        closed: list[str] = []
        now = [1000]
        cache = SessionCache(clock=lambda: now[0])
        e = cache.put("h", "u", "fake", _handle("s1"))
        e.last_activity_ms = 0  # idle for 1000ms

        async def closer(entry):
            closed.append(entry.handle.session_id)
            cache.pop("h", "u", "fake")

        reaper = IdleReaper(
            cache=cache,
            session_idle_ms=500,
            daemon_idle_ms=10_000,
            close_session=closer,
            clock=lambda: now[0],
        )
        await reaper.tick()
        assert closed == ["s1"]

    @pytest.mark.anyio
    async def test_keeps_fresh_sessions(self):
        now = [1000]
        cache = SessionCache(clock=lambda: now[0])
        cache.put("h", "u", "fake", _handle("fresh"))  # activity=1000

        async def closer(_entry):
            raise AssertionError("should not close")

        reaper = IdleReaper(
            cache=cache,
            session_idle_ms=500,
            daemon_idle_ms=10_000,
            close_session=closer,
            clock=lambda: now[0],
        )
        await reaper.tick()


class TestReaperDaemonIdle:
    @pytest.mark.anyio
    async def test_no_exit_while_sessions_alive(self):
        now = [1000]
        cache = SessionCache(clock=lambda: now[0])
        cache.put("h", "u", "fake", _handle("s"))

        reaper = IdleReaper(
            cache=cache,
            session_idle_ms=60_000,
            daemon_idle_ms=1,
            close_session=_dont_call,
            clock=lambda: now[0],
        )
        await reaper.tick()
        assert reaper.should_exit() is False

    @pytest.mark.anyio
    async def test_exits_after_grace_period_with_no_sessions(self):
        now = [1000]
        cache = SessionCache(clock=lambda: now[0])
        reaper = IdleReaper(
            cache=cache,
            session_idle_ms=500,
            daemon_idle_ms=200,
            close_session=_dont_call,
            clock=lambda: now[0],
        )
        await reaper.tick()  # starts the empty timer
        assert reaper.should_exit() is False
        now[0] = 1500  # 500ms later, past 200ms grace
        await reaper.tick()
        assert reaper.should_exit() is True

    @pytest.mark.anyio
    async def test_resets_grace_when_session_opens(self):
        now = [1000]
        cache = SessionCache(clock=lambda: now[0])
        reaper = IdleReaper(
            cache=cache,
            session_idle_ms=500,
            daemon_idle_ms=200,
            close_session=_dont_call,
            clock=lambda: now[0],
        )
        await reaper.tick()  # empty, grace timer armed
        cache.put("h", "u", "fake", _handle("s"))
        now[0] = 1500
        await reaper.tick()  # non-empty → grace disarmed
        assert reaper.should_exit() is False


async def _dont_call(_entry) -> None:
    raise AssertionError("close_session should not be called in this test")


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _await_point():
    await asyncio.sleep(0)
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/kvm/test_reaper.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement** — `src/redfish_mcp/kvm/daemon/reaper.py`

```python
"""Idle-session reaper + daemon self-exit trigger."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from redfish_mcp.kvm.daemon.cache import CacheEntry, SessionCache

Clock = Callable[[], int]
CloseSession = Callable[[CacheEntry], Awaitable[None]]


class IdleReaper:
    def __init__(
        self,
        *,
        cache: SessionCache,
        session_idle_ms: int,
        daemon_idle_ms: int,
        close_session: CloseSession,
        clock: Clock,
    ) -> None:
        self._cache = cache
        self._session_idle_ms = session_idle_ms
        self._daemon_idle_ms = daemon_idle_ms
        self._close = close_session
        self._clock = clock
        self._empty_since_ms: int | None = None
        self._should_exit = False

    async def tick(self) -> None:
        for entry in self._cache.idle_entries(threshold_ms=self._session_idle_ms):
            await self._close(entry)

        is_empty = not self._cache.snapshot()
        now = self._clock()
        if is_empty:
            if self._empty_since_ms is None:
                self._empty_since_ms = now
            elif now - self._empty_since_ms >= self._daemon_idle_ms:
                self._should_exit = True
        else:
            self._empty_since_ms = None

    def should_exit(self) -> bool:
        return self._should_exit
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/kvm/test_reaper.py -v
```

Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/redfish_mcp/kvm/daemon/reaper.py tests/kvm/test_reaper.py
git commit -m "feat(kvm): add IdleReaper with injectable clock and exit-trigger"
```

---

## Task 10 — Progress publisher

**Goal:** Fan out `ProgressEvent`s from a single producer (the open() coroutine) to zero-or-many async subscribers (the inbound connection waiting on progress).

**Files:**
- Create: `src/redfish_mcp/kvm/daemon/progress.py`
- Create: `tests/kvm/test_progress.py`

**Steps:**

- [ ] **Step 1: Write the failing test** — `tests/kvm/test_progress.py`

```python
"""Tests for the progress publisher."""

from __future__ import annotations

import asyncio

import pytest

from redfish_mcp.kvm.backend import ProgressEvent
from redfish_mcp.kvm.daemon.progress import ProgressPublisher


class TestProgressPublisher:
    @pytest.mark.anyio
    async def test_single_subscriber_sees_events(self):
        pub = ProgressPublisher()
        q = pub.subscribe("sess-1")
        await pub.publish("sess-1", ProgressEvent(stage="authenticating"))
        await pub.publish("sess-1", ProgressEvent(stage="ready"))
        ev1 = await asyncio.wait_for(q.get(), timeout=1)
        ev2 = await asyncio.wait_for(q.get(), timeout=1)
        assert ev1.stage == "authenticating"
        assert ev2.stage == "ready"

    @pytest.mark.anyio
    async def test_two_subscribers_both_see_events(self):
        pub = ProgressPublisher()
        a = pub.subscribe("sess-1")
        b = pub.subscribe("sess-1")
        await pub.publish("sess-1", ProgressEvent(stage="ready"))
        ea = await asyncio.wait_for(a.get(), timeout=1)
        eb = await asyncio.wait_for(b.get(), timeout=1)
        assert ea.stage == "ready"
        assert eb.stage == "ready"

    @pytest.mark.anyio
    async def test_unsubscribe_removes_queue(self):
        pub = ProgressPublisher()
        q = pub.subscribe("sess-1")
        pub.unsubscribe("sess-1", q)
        await pub.publish("sess-1", ProgressEvent(stage="ready"))
        assert q.empty()

    @pytest.mark.anyio
    async def test_complete_delivers_sentinel(self):
        pub = ProgressPublisher()
        q = pub.subscribe("sess-1")
        await pub.complete("sess-1")
        ev = await asyncio.wait_for(q.get(), timeout=1)
        assert ev is None

    @pytest.mark.anyio
    async def test_publish_to_unknown_session_is_silent(self):
        pub = ProgressPublisher()
        await pub.publish("missing", ProgressEvent(stage="x"))  # must not raise


@pytest.fixture
def anyio_backend():
    return "asyncio"
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/kvm/test_progress.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement** — `src/redfish_mcp/kvm/daemon/progress.py`

```python
"""Progress pub/sub: fan out ProgressEvents to N subscribers per session key."""

from __future__ import annotations

import asyncio

from redfish_mcp.kvm.backend import ProgressEvent


class ProgressPublisher:
    """Per-session fanout of progress events.

    ``None`` placed on a queue is the sentinel meaning "session open complete".
    """

    def __init__(self) -> None:
        self._by_session: dict[str, list[asyncio.Queue[ProgressEvent | None]]] = {}

    def subscribe(self, session_key: str) -> asyncio.Queue[ProgressEvent | None]:
        q: asyncio.Queue[ProgressEvent | None] = asyncio.Queue()
        self._by_session.setdefault(session_key, []).append(q)
        return q

    def unsubscribe(self, session_key: str, q: asyncio.Queue[ProgressEvent | None]) -> None:
        subs = self._by_session.get(session_key)
        if subs and q in subs:
            subs.remove(q)
            if not subs:
                self._by_session.pop(session_key, None)

    async def publish(self, session_key: str, event: ProgressEvent) -> None:
        for q in list(self._by_session.get(session_key, [])):
            await q.put(event)

    async def complete(self, session_key: str) -> None:
        for q in list(self._by_session.get(session_key, [])):
            await q.put(None)
        self._by_session.pop(session_key, None)
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/kvm/test_progress.py -v
```

Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/redfish_mcp/kvm/daemon/progress.py tests/kvm/test_progress.py
git commit -m "feat(kvm): add progress publisher with per-session fanout"
```

---

## Task 11 — Request router

**Goal:** Dispatch decoded `Request` envelopes to handler coroutines. Phase 1 wires six methods returning a stub `not_implemented` for now (wiring happens in Task 15 after the server is in place). Also covers unknown-method and exception paths.

**Files:**
- Create: `src/redfish_mcp/kvm/daemon/router.py`
- Create: `tests/kvm/test_router.py`

**Steps:**

- [ ] **Step 1: Write the failing test** — `tests/kvm/test_router.py`

```python
"""Tests for the request router."""

from __future__ import annotations

import pytest

from redfish_mcp.kvm.daemon.router import Router
from redfish_mcp.kvm.exceptions import AuthFailed
from redfish_mcp.kvm.protocol import Request, Response


class TestRouter:
    @pytest.mark.anyio
    async def test_dispatch_calls_registered_handler(self):
        r = Router()

        async def handle_echo(params):
            return {"echoed": params.get("value")}

        r.register("echo", handle_echo)
        resp = await r.dispatch(Request(id=1, method="echo", params={"value": 42}))
        assert isinstance(resp, Response)
        assert resp.id == 1
        assert resp.result == {"echoed": 42}
        assert resp.error is None

    @pytest.mark.anyio
    async def test_unknown_method_returns_error(self):
        r = Router()
        resp = await r.dispatch(Request(id=7, method="nope"))
        assert resp.error is not None
        assert resp.error.code == "method_not_found"
        assert resp.id == 7

    @pytest.mark.anyio
    async def test_kvm_error_in_handler_is_mapped(self):
        r = Router()

        async def bad(_params):
            raise AuthFailed("bad", stage="authenticating")

        r.register("bad", bad)
        resp = await r.dispatch(Request(id=9, method="bad"))
        assert resp.error is not None
        assert resp.error.code == "auth_failed"
        assert resp.error.stage == "authenticating"

    @pytest.mark.anyio
    async def test_unexpected_exception_is_mapped_to_internal(self):
        r = Router()

        async def boom(_params):
            raise RuntimeError("surprise")

        r.register("boom", boom)
        resp = await r.dispatch(Request(id=11, method="boom"))
        assert resp.error is not None
        assert resp.error.code == "internal"
        assert "surprise" in resp.error.message


@pytest.fixture
def anyio_backend():
    return "asyncio"
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/kvm/test_router.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement** — `src/redfish_mcp/kvm/daemon/router.py`

```python
"""Request router for the KVM daemon."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from redfish_mcp.kvm.exceptions import KVMError
from redfish_mcp.kvm.protocol import ErrorPayload, Request, Response

logger = logging.getLogger("redfish_mcp.kvm.router")

Handler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class Router:
    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}

    def register(self, method: str, handler: Handler) -> None:
        self._handlers[method] = handler

    async def dispatch(self, req: Request) -> Response:
        handler = self._handlers.get(req.method)
        if handler is None:
            return Response(
                id=req.id,
                error=ErrorPayload(code="method_not_found", message=f"unknown method {req.method!r}"),
            )
        try:
            result = await handler(req.params)
            return Response(id=req.id, result=result)
        except KVMError as exc:
            code = exc.reason or "kvm_error"
            return Response(
                id=req.id,
                error=ErrorPayload(code=code, message=str(exc), stage=exc.stage),
            )
        except Exception as exc:  # noqa: BLE001 — final catch for daemon robustness
            logger.exception("handler crash for method %s", req.method)
            return Response(
                id=req.id, error=ErrorPayload(code="internal", message=f"{type(exc).__name__}: {exc}")
            )
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/kvm/test_router.py -v
```

Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/redfish_mcp/kvm/daemon/router.py tests/kvm/test_router.py
git commit -m "feat(kvm): add request router with KVMError→ErrorPayload mapping"
```

---

## Task 12 — Observation logging helpers

**Goal:** Thin wrapper around `AgentStateStore.add_observation` with pre-canned KVM-specific kinds so the daemon doesn't string-sprinkle kind names.

**Files:**
- Create: `src/redfish_mcp/kvm/daemon/observations.py`
- Create: `tests/kvm/test_observations.py`

**Steps:**

The real `AgentStateStore.add_observation` signature is:

```python
add_observation(*, host_key, kind, summary, details, tags,
                confidence, reporter_id, ttl_hours) -> int
```

and `list_observations` requires `host_key` and returns `list[dict[str, Any]]` (not dataclass rows). The wrapper must call it correctly.

- [ ] **Step 1: Write the failing test** — `tests/kvm/test_observations.py`

```python
"""Tests for observation logging helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from redfish_mcp.agent_state_store import AgentStateStore
from redfish_mcp.kvm.daemon.observations import ObservationKind, ObservationLogger


@pytest.fixture
def store(tmp_path: Path):
    db = tmp_path / "state.sqlite3"
    s = AgentStateStore(db_path=db)
    yield s
    s.close()


class TestObservationLogger:
    def test_session_opened_roundtrip(self, store: AgentStateStore):
        log = ObservationLogger(store, reporter_id="kvm-daemon-test")
        log.session_opened(host="h1", user="u", backend="fake", session_id="s1")
        rows = store.list_observations(host_key="h1")
        matches = [r for r in rows if r["kind"] == ObservationKind.SESSION_OPENED.value]
        assert len(matches) == 1
        row = matches[0]
        assert "h1" in row["summary"]
        assert row["details"]["backend"] == "fake"
        assert row["details"]["session_id"] == "s1"

    def test_session_closed_roundtrip(self, store: AgentStateStore):
        log = ObservationLogger(store, reporter_id="kvm-daemon-test")
        log.session_closed(host="h1", user="u", backend="fake", session_id="s1", reason="reap")
        rows = store.list_observations(host_key="h1")
        row = next(r for r in rows if r["kind"] == ObservationKind.SESSION_CLOSED.value)
        assert row["details"]["reason"] == "reap"

    def test_keys_sent_roundtrip(self, store: AgentStateStore):
        log = ObservationLogger(store, reporter_id="kvm-daemon-test")
        log.keys_sent(host="h1", backend="fake", session_id="s1", n_chars=5)
        rows = store.list_observations(host_key="h1")
        row = next(r for r in rows if r["kind"] == ObservationKind.KEYS_SENT.value)
        assert row["details"]["n"] == 5

    def test_error_logged_with_stage(self, store: AgentStateStore):
        log = ObservationLogger(store, reporter_id="kvm-daemon-test")
        log.error(host="h1", stage="authenticating", reason="auth_failed", message="bad creds")
        rows = store.list_observations(host_key="h1")
        row = next(r for r in rows if r["kind"] == ObservationKind.ERROR.value)
        assert row["details"]["stage"] == "authenticating"
        assert row["details"]["reason"] == "auth_failed"

    def test_ttl_forwarded(self, store: AgentStateStore):
        log = ObservationLogger(store, reporter_id="kvm-daemon-test", default_ttl_hours=1)
        log.session_opened(host="h1", user="u", backend="fake", session_id="s1")
        rows = store.list_observations(host_key="h1")
        assert rows[0]["expires_at_ms"] is not None
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/kvm/test_observations.py -v
```

Expected: `ModuleNotFoundError` for `redfish_mcp.kvm.daemon.observations`.

- [ ] **Step 3: Implement** — `src/redfish_mcp/kvm/daemon/observations.py`

```python
"""Thin wrapper around AgentStateStore for KVM observation kinds."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from redfish_mcp.agent_state_store import AgentStateStore


class ObservationKind(StrEnum):
    SESSION_OPENED = "kvm_session_opened"
    SESSION_CLOSED = "kvm_session_closed"
    KEYS_SENT = "kvm_keys_sent"
    REAP = "kvm_reap"
    ERROR = "kvm_error"


@dataclass
class ObservationLogger:
    store: AgentStateStore
    reporter_id: str = "kvm-daemon"
    default_ttl_hours: int | None = None

    def session_opened(self, *, host: str, user: str, backend: str, session_id: str) -> None:
        self._add(
            host_key=host,
            kind=ObservationKind.SESSION_OPENED,
            summary=f"kvm session opened on {host}",
            details={"host": host, "user": user, "backend": backend, "session_id": session_id},
            tags=[host, backend],
        )

    def session_closed(
        self, *, host: str, user: str, backend: str, session_id: str, reason: str
    ) -> None:
        self._add(
            host_key=host,
            kind=ObservationKind.SESSION_CLOSED,
            summary=f"kvm session closed on {host} ({reason})",
            details={
                "host": host,
                "user": user,
                "backend": backend,
                "session_id": session_id,
                "reason": reason,
            },
            tags=[host, backend, reason],
        )

    def keys_sent(self, *, host: str, backend: str, session_id: str, n_chars: int) -> None:
        self._add(
            host_key=host,
            kind=ObservationKind.KEYS_SENT,
            summary=f"sent {n_chars} chars to {host}",
            details={"host": host, "backend": backend, "session_id": session_id, "n": n_chars},
            tags=[host, backend],
        )

    def error(self, *, host: str, stage: str, reason: str, message: str) -> None:
        self._add(
            host_key=host,
            kind=ObservationKind.ERROR,
            summary=f"kvm error on {host}: {reason}",
            details={"host": host, "stage": stage, "reason": reason, "message": message},
            tags=[host, reason],
        )

    def _add(
        self,
        *,
        host_key: str,
        kind: ObservationKind,
        summary: str,
        details: dict[str, Any],
        tags: list[str],
    ) -> None:
        self.store.add_observation(
            host_key=host_key,
            kind=str(kind),
            summary=summary,
            details=details,
            tags=tags,
            confidence=None,
            reporter_id=self.reporter_id,
            ttl_hours=self.default_ttl_hours,
        )
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/kvm/test_observations.py -v
```

Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/redfish_mcp/kvm/daemon/observations.py tests/kvm/test_observations.py
git commit -m "feat(kvm): add ObservationLogger wrapping AgentStateStore"
```

---

## Task 13 — Daemon server (asyncio UNIX socket) + entry point

**Goal:** Tie lifecycle + router together. Accept connections on the UNIX socket, read line-delimited `Request`s, dispatch, write `Response`s. `python -m redfish_mcp.kvm.daemon` entry point. Ticks the reaper on an interval.

**Files:**
- Create: `src/redfish_mcp/kvm/daemon/server.py`
- Create: `src/redfish_mcp/kvm/daemon/__main__.py`
- Create: `tests/kvm/test_server.py`

**Steps:**

- [ ] **Step 1: Write the failing test** — `tests/kvm/test_server.py`

```python
"""Tests for the asyncio UNIX-socket server."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from redfish_mcp.kvm.config import KVMConfig
from redfish_mcp.kvm.daemon.server import DaemonServer


def _cfg(dir_: Path) -> KVMConfig:
    return KVMConfig(
        socket_dir=dir_,
        session_idle_s=300,
        daemon_idle_s=1,  # short for test shutdown
        max_concurrent=4,
        backend="java",
        java_bin="java",
        jar_cache_dir=dir_ / "jars",
        log_level="INFO",
        daemon_path=None,
    )


@pytest.mark.anyio
async def test_server_starts_and_replies_to_ping(tmp_path: Path):
    cfg = _cfg(tmp_path)
    server = DaemonServer(cfg)

    async def handle_ping(_params):
        return {"pong": True}

    server.router.register("ping", handle_ping)
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(str(server.lifecycle.socket_path))
        writer.write(json.dumps({"id": 1, "method": "ping", "params": {}}).encode() + b"\n")
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=2)
        resp = json.loads(line)
        assert resp["id"] == 1
        assert resp["result"] == {"pong": True}
        writer.close()
        await writer.wait_closed()
    finally:
        await server.stop()


@pytest.mark.anyio
async def test_server_socket_permissions_0600(tmp_path: Path):
    cfg = _cfg(tmp_path)
    server = DaemonServer(cfg)
    await server.start()
    try:
        mode = server.lifecycle.socket_path.stat().st_mode & 0o777
        assert mode == 0o600
    finally:
        await server.stop()


@pytest.mark.anyio
async def test_server_writes_pid_file(tmp_path: Path):
    cfg = _cfg(tmp_path)
    server = DaemonServer(cfg)
    await server.start()
    try:
        pid_text = server.lifecycle.pid_path.read_text().strip()
        assert pid_text.isdigit()
    finally:
        await server.stop()


@pytest.mark.anyio
async def test_unknown_method_returns_method_not_found(tmp_path: Path):
    cfg = _cfg(tmp_path)
    server = DaemonServer(cfg)
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(str(server.lifecycle.socket_path))
        writer.write(json.dumps({"id": 5, "method": "zzz", "params": {}}).encode() + b"\n")
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=2)
        resp = json.loads(line)
        assert resp["id"] == 5
        assert resp["error"]["code"] == "method_not_found"
        writer.close()
        await writer.wait_closed()
    finally:
        await server.stop()


@pytest.fixture
def anyio_backend():
    return "asyncio"
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/kvm/test_server.py -v
```

Expected: `ModuleNotFoundError` for `redfish_mcp.kvm.daemon.server`.

- [ ] **Step 3: Implement** — `src/redfish_mcp/kvm/daemon/server.py`

```python
"""asyncio UNIX-socket daemon for the KVM feature."""

from __future__ import annotations

import asyncio
import logging
import os
import time

from redfish_mcp.kvm.config import KVMConfig
from redfish_mcp.kvm.daemon.cache import SessionCache
from redfish_mcp.kvm.daemon.lifecycle import DaemonLifecycle
from redfish_mcp.kvm.daemon.progress import ProgressPublisher
from redfish_mcp.kvm.daemon.reaper import IdleReaper
from redfish_mcp.kvm.daemon.router import Router
from redfish_mcp.kvm.protocol import (
    ProtocolError,
    Request,
    Response,
    decode_message,
    encode_message,
)

logger = logging.getLogger("redfish_mcp.kvm.daemon")


def _now_ms() -> int:
    return int(time.time() * 1000)


class DaemonServer:
    def __init__(self, config: KVMConfig) -> None:
        self.config = config
        self.lifecycle = DaemonLifecycle(config)
        self.cache = SessionCache(clock=_now_ms)
        self.progress = ProgressPublisher()
        self.router = Router()
        self.reaper = IdleReaper(
            cache=self.cache,
            session_idle_ms=config.session_idle_s * 1000,
            daemon_idle_ms=config.daemon_idle_s * 1000,
            close_session=self._close_entry_noop,
            clock=_now_ms,
        )
        self._server: asyncio.base_events.Server | None = None
        self._reaper_task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    async def _close_entry_noop(self, _entry) -> None:
        # Phase 1 scaffolding: no real backend yet. Task 15/16 wires real closing.
        return None

    async def start(self) -> None:
        self.config.socket_dir.mkdir(parents=True, exist_ok=True)
        if self.lifecycle.socket_path.exists():
            self.lifecycle.socket_path.unlink()

        self._server = await asyncio.start_unix_server(
            self._handle_client, path=str(self.lifecycle.socket_path)
        )
        os.chmod(self.lifecycle.socket_path, 0o600)
        self.lifecycle.write_pid(os.getpid())
        self._reaper_task = asyncio.create_task(self._reaper_loop())
        logger.info("kvm daemon listening on %s", self.lifecycle.socket_path)

    async def serve_forever(self) -> None:
        if self._server is None:
            raise RuntimeError("server not started")
        try:
            await self._stopping.wait()
        finally:
            await self.stop()

    async def stop(self) -> None:
        self._stopping.set()
        if self._reaper_task is not None:
            self._reaper_task.cancel()
            try:
                await self._reaper_task
            except asyncio.CancelledError:
                pass
            self._reaper_task = None
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self.lifecycle.clear()

    async def _reaper_loop(self) -> None:
        try:
            while not self._stopping.is_set():
                await self.reaper.tick()
                if self.reaper.should_exit():
                    self._stopping.set()
                    break
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            while not reader.at_eof():
                line = await reader.readline()
                if not line:
                    break
                try:
                    msg = decode_message(line)
                except ProtocolError as exc:
                    logger.warning("protocol error: %s", exc)
                    continue
                if not isinstance(msg, Request):
                    continue
                resp = await self.router.dispatch(msg)
                writer.write(encode_message(resp))
                await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            with suppress_close():
                writer.close()


class suppress_close:
    """Context manager that swallows exceptions from writer.close/wait_closed."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, tb) -> bool:
        return True


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = KVMConfig.load()
    server = DaemonServer(cfg)
    await server.start()
    await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Create `src/redfish_mcp/kvm/daemon/__main__.py`**

```python
"""Entry point for ``python -m redfish_mcp.kvm.daemon``."""

from __future__ import annotations

import asyncio

from redfish_mcp.kvm.daemon.server import main


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
```

- [ ] **Step 5: Run test to verify it passes**

```
uv run pytest tests/kvm/test_server.py -v
```

Expected: `4 passed`. Close remaining writer warnings are acceptable.

- [ ] **Step 6: Commit**

```bash
git add src/redfish_mcp/kvm/daemon/server.py src/redfish_mcp/kvm/daemon/__main__.py tests/kvm/test_server.py
git commit -m "feat(kvm): add asyncio UNIX-socket daemon server"
```

---

## Task 14 — Daemon client library

**Goal:** `DaemonClient` that opens the socket, sends a request, returns the response, and yields progress events. Used by MCP tool stubs and the CLI.

**Files:**
- Create: `src/redfish_mcp/kvm/client.py`
- Create: `tests/kvm/test_client.py`

**Steps:**

- [ ] **Step 1: Write the failing test** — `tests/kvm/test_client.py`

```python
"""Tests for DaemonClient."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from redfish_mcp.kvm.client import DaemonClient
from redfish_mcp.kvm.exceptions import AuthFailed, KVMError


class FakeServer:
    def __init__(self, socket_path: Path, responses: list[dict]) -> None:
        self.socket_path = socket_path
        self._responses = responses
        self._server: asyncio.base_events.Server | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_unix_server(self._handle, path=str(self.socket_path))

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        line = await reader.readline()
        req = json.loads(line)
        for item in self._responses:
            item_with_id = dict(item)
            item_with_id["id"] = req["id"]
            writer.write(json.dumps(item_with_id).encode() + b"\n")
            await writer.drain()
        writer.close()
        await writer.wait_closed()


@pytest.mark.anyio
async def test_request_returns_result(tmp_path: Path):
    sock = tmp_path / "srv.sock"
    server = FakeServer(sock, [{"result": {"ok": True, "val": 1}}])
    await server.start()
    try:
        client = DaemonClient(socket_path=sock)
        result = await client.request("ping", {})
        assert result == {"ok": True, "val": 1}
    finally:
        await server.stop()


@pytest.mark.anyio
async def test_request_error_maps_to_kvmerror(tmp_path: Path):
    sock = tmp_path / "srv.sock"
    server = FakeServer(
        sock,
        [{"error": {"code": "auth_failed", "message": "bad creds", "stage": "authenticating"}}],
    )
    await server.start()
    try:
        client = DaemonClient(socket_path=sock)
        with pytest.raises(AuthFailed) as exc_info:
            await client.request("open", {})
        assert exc_info.value.stage == "authenticating"
    finally:
        await server.stop()


@pytest.mark.anyio
async def test_unknown_error_code_falls_back_to_base(tmp_path: Path):
    sock = tmp_path / "srv.sock"
    server = FakeServer(sock, [{"error": {"code": "weird", "message": "x", "stage": None}}])
    await server.start()
    try:
        client = DaemonClient(socket_path=sock)
        with pytest.raises(KVMError) as exc_info:
            await client.request("open", {})
        # Unknown code is preserved as reason on the base KVMError.
        assert type(exc_info.value) is KVMError
        assert exc_info.value.reason == "weird"
    finally:
        await server.stop()


@pytest.mark.anyio
async def test_progress_stream_yields_events_until_result(tmp_path: Path):
    sock = tmp_path / "srv.sock"
    server = FakeServer(
        sock,
        [
            {"progress": {"stage": "authenticating", "detail": ""}},
            {"progress": {"stage": "ready", "detail": ""}},
            {"result": {"session_id": "s1"}},
        ],
    )
    await server.start()
    try:
        client = DaemonClient(socket_path=sock)
        events: list[dict] = []

        async def on_progress(ev: dict) -> None:
            events.append(ev)

        result = await client.request("open", {}, on_progress=on_progress)
        assert result == {"session_id": "s1"}
        assert [e["stage"] for e in events] == ["authenticating", "ready"]
    finally:
        await server.stop()


@pytest.fixture
def anyio_backend():
    return "asyncio"
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/kvm/test_client.py -v
```

Expected: `ModuleNotFoundError` for `redfish_mcp.kvm.client`.

- [ ] **Step 3: Implement** — `src/redfish_mcp/kvm/client.py`

```python
"""UNIX-socket client for the KVM daemon."""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from redfish_mcp.kvm.exceptions import (
    AuthFailed,
    BackendUnsupported,
    DaemonUnavailable,
    JarMismatch,
    JnlpUnavailable,
    KVMError,
    SessionLost,
    SlotBusy,
    StaleSession,
)
from redfish_mcp.kvm.protocol import (
    ErrorPayload,
    Request,
    Response,
    decode_message,
    encode_message,
)

ProgressHandler = Callable[[dict[str, Any]], Awaitable[None]]

_ERROR_CODE_TO_EXC: dict[str, type[KVMError]] = {
    "auth_failed": AuthFailed,
    "kvm_slot_busy": SlotBusy,
    "stale": StaleSession,
    "session_lost": SessionLost,
    "backend_unsupported": BackendUnsupported,
    "jar_mismatch": JarMismatch,
    "jnlp_unavailable": JnlpUnavailable,
    "daemon_unavailable": DaemonUnavailable,
}


class DaemonClient:
    """Async client that speaks line-framed JSON to a single daemon socket."""

    def __init__(self, *, socket_path: Path) -> None:
        self.socket_path = socket_path
        self._id_gen = itertools.count(1)

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        on_progress: ProgressHandler | None = None,
        timeout_s: float = 60.0,
    ) -> dict[str, Any]:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(str(self.socket_path)),
                timeout=timeout_s,
            )
        except (FileNotFoundError, ConnectionRefusedError) as exc:
            raise DaemonUnavailable(f"daemon socket unavailable: {exc}") from exc

        try:
            req = Request(id=next(self._id_gen), method=method, params=params or {})
            writer.write(encode_message(req))
            await writer.drain()

            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=timeout_s)
                if not line:
                    raise SessionLost("daemon closed connection before response")
                msg = decode_message(line)
                if not isinstance(msg, Response):
                    raise KVMError("unexpected request from daemon")
                if msg.id != req.id:
                    continue
                if msg.progress is not None:
                    if on_progress is not None:
                        await on_progress(msg.progress)
                    continue
                if msg.error is not None:
                    self._raise_from_error(msg.error)
                assert msg.result is not None
                return msg.result
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    @staticmethod
    def _raise_from_error(err: ErrorPayload) -> None:
        cls = _ERROR_CODE_TO_EXC.get(err.code, KVMError)
        raise cls(err.message, stage=err.stage, reason=err.code)
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/kvm/test_client.py -v
```

Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/redfish_mcp/kvm/client.py tests/kvm/test_client.py
git commit -m "feat(kvm): add DaemonClient with progress streaming and error mapping"
```

---

## Task 15 — Daemon autostart

**Goal:** `ensure_daemon_running(cfg)` starts the daemon via `subprocess.Popen(..., start_new_session=True)` if no live daemon is listening, then blocks until the socket is connectable (up to 3 seconds).

**Files:**
- Create: `src/redfish_mcp/kvm/autostart.py`
- Create: `tests/kvm/test_autostart.py`

**Steps:**

- [ ] **Step 1: Write the failing test** — `tests/kvm/test_autostart.py`

```python
"""Tests for ensure_daemon_running."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from redfish_mcp.kvm.autostart import ensure_daemon_running
from redfish_mcp.kvm.config import KVMConfig
from redfish_mcp.kvm.daemon.lifecycle import DaemonLifecycle


def _cfg(tmp: Path) -> KVMConfig:
    return KVMConfig(
        socket_dir=tmp,
        session_idle_s=300,
        daemon_idle_s=1,
        max_concurrent=4,
        backend="java",
        java_bin="java",
        jar_cache_dir=tmp / "jars",
        log_level="INFO",
        daemon_path=None,
    )


@pytest.mark.anyio
async def test_noop_when_daemon_alive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = _cfg(tmp_path)
    lc = DaemonLifecycle(cfg)
    lc.pid_path.write_text(f"{os.getpid()}\n")
    lc.socket_path.touch()

    called = {"spawned": False}

    def fake_spawn(_cfg):
        called["spawned"] = True

    monkeypatch.setattr("redfish_mcp.kvm.autostart._spawn_daemon", fake_spawn)
    monkeypatch.setattr("redfish_mcp.kvm.autostart._wait_for_socket", _true_async)

    await ensure_daemon_running(cfg)
    assert called["spawned"] is False


@pytest.mark.anyio
async def test_spawns_when_no_daemon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = _cfg(tmp_path)

    spawned = {"n": 0}

    def fake_spawn(_cfg):
        spawned["n"] += 1
        DaemonLifecycle(_cfg).pid_path.write_text(f"{os.getpid()}\n")
        DaemonLifecycle(_cfg).socket_path.touch()

    monkeypatch.setattr("redfish_mcp.kvm.autostart._spawn_daemon", fake_spawn)
    monkeypatch.setattr("redfish_mcp.kvm.autostart._wait_for_socket", _true_async)

    await ensure_daemon_running(cfg)
    assert spawned["n"] == 1


@pytest.mark.anyio
async def test_raises_when_socket_never_appears(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from redfish_mcp.kvm.exceptions import DaemonUnavailable

    cfg = _cfg(tmp_path)

    monkeypatch.setattr("redfish_mcp.kvm.autostart._spawn_daemon", lambda _c: None)
    monkeypatch.setattr("redfish_mcp.kvm.autostart._wait_for_socket", _false_async)

    with pytest.raises(DaemonUnavailable):
        await ensure_daemon_running(cfg, start_timeout_s=0.05)


async def _true_async(*_args, **_kwargs) -> bool:
    return True


async def _false_async(*_args, **_kwargs) -> bool:
    return False


@pytest.fixture
def anyio_backend():
    return "asyncio"
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/kvm/test_autostart.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement** — `src/redfish_mcp/kvm/autostart.py`

```python
"""Daemon autostart helper used by MCP tools and the CLI."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys

from redfish_mcp.kvm.config import KVMConfig
from redfish_mcp.kvm.daemon.lifecycle import DaemonLifecycle
from redfish_mcp.kvm.exceptions import DaemonUnavailable

logger = logging.getLogger("redfish_mcp.kvm.autostart")


async def ensure_daemon_running(cfg: KVMConfig, *, start_timeout_s: float = 3.0) -> None:
    lc = DaemonLifecycle(cfg)
    if lc.claimed_by_live_daemon() and lc.socket_path.exists():
        return

    _spawn_daemon(cfg)
    if not await _wait_for_socket(lc.socket_path, timeout_s=start_timeout_s):
        raise DaemonUnavailable(f"daemon did not start within {start_timeout_s}s")


def _spawn_daemon(cfg: KVMConfig) -> None:
    log_path = cfg.socket_dir / "kvm-daemon.log"
    cfg.socket_dir.mkdir(parents=True, exist_ok=True)
    log_fh = open(log_path, "a")  # noqa: SIM115 — intentionally long-lived
    cmd = [sys.executable, "-m", "redfish_mcp.kvm.daemon"]
    logger.info("spawning kvm daemon: %s", " ".join(cmd))
    subprocess.Popen(  # noqa: S603
        cmd,
        stdout=log_fh,
        stderr=log_fh,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )


async def _wait_for_socket(socket_path, *, timeout_s: float) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        if socket_path.exists():
            try:
                _, writer = await asyncio.open_unix_connection(str(socket_path))
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                return True
            except (ConnectionRefusedError, FileNotFoundError):
                pass
        await asyncio.sleep(0.05)
    return False
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/kvm/test_autostart.py -v
```

Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/redfish_mcp/kvm/autostart.py tests/kvm/test_autostart.py
git commit -m "feat(kvm): add ensure_daemon_running autostart helper"
```

---

## Task 16 — MCP tool stubs

**Goal:** Register six MCP tools that return `not_implemented`. Real implementations land in phases 2–3.

**Files:**
- Create: `src/redfish_mcp/kvm/tools.py`
- Modify: `src/redfish_mcp/mcp_server.py`
- Create: `tests/kvm/test_tools.py`

**Steps:**

- [ ] **Step 1: Write the failing test** — `tests/kvm/test_tools.py`

```python
"""Tests for MCP tool stubs."""

from __future__ import annotations

import pytest

from redfish_mcp.kvm.tools import (
    kvm_close,
    kvm_screen,
    kvm_sendkey,
    kvm_sendkeys,
    kvm_status,
    kvm_type_and_read,
)


class TestToolStubs:
    @pytest.mark.anyio
    async def test_screen_returns_not_implemented(self):
        result = await kvm_screen(host="h", user="u", password="p")
        assert result["ok"] is False
        assert result["error"] == "not_implemented"
        assert result["phase"] == 1

    @pytest.mark.anyio
    async def test_sendkey_returns_not_implemented(self):
        result = await kvm_sendkey(host="h", user="u", password="p", key="Enter")
        assert result == {"ok": False, "error": "not_implemented", "phase": 1}

    @pytest.mark.anyio
    async def test_sendkeys_returns_not_implemented(self):
        result = await kvm_sendkeys(host="h", user="u", password="p", text="hi")
        assert result == {"ok": False, "error": "not_implemented", "phase": 1}

    @pytest.mark.anyio
    async def test_type_and_read_returns_not_implemented(self):
        result = await kvm_type_and_read(host="h", user="u", password="p", keys="a")
        assert result == {"ok": False, "error": "not_implemented", "phase": 1}

    @pytest.mark.anyio
    async def test_close_returns_not_implemented(self):
        result = await kvm_close(host="h", user="u", password="p")
        assert result == {"ok": False, "error": "not_implemented", "phase": 1}

    @pytest.mark.anyio
    async def test_status_returns_not_implemented(self):
        result = await kvm_status()
        assert result == {"ok": False, "error": "not_implemented", "phase": 1}


@pytest.fixture
def anyio_backend():
    return "asyncio"
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/kvm/test_tools.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement** — `src/redfish_mcp/kvm/tools.py`

```python
"""MCP tool stubs for the KVM feature.

Phase 1 returns ``not_implemented``. Phase 2 (#64) wires ``kvm_screen`` to
the Java backend; phase 3 (#65) wires the input tools.
"""

from __future__ import annotations

from typing import Any

_STUB: dict[str, Any] = {"ok": False, "error": "not_implemented", "phase": 1}


async def kvm_screen(
    *,
    host: str,
    user: str,
    password: str,
    mode: str = "image",
    wait_for_ready: bool = False,
    timeout_s: int = 30,
) -> dict[str, Any]:
    """Capture the current KVM screen (scaffolding stub)."""
    return dict(_STUB)


async def kvm_sendkey(
    *,
    host: str,
    user: str,
    password: str,
    key: str,
    modifiers: list[str] | None = None,
) -> dict[str, Any]:
    """Send a single named key (scaffolding stub)."""
    return dict(_STUB)


async def kvm_sendkeys(
    *,
    host: str,
    user: str,
    password: str,
    text: str,
    press_enter_after: bool = False,
) -> dict[str, Any]:
    """Type a text string (scaffolding stub)."""
    return dict(_STUB)


async def kvm_type_and_read(
    *,
    host: str,
    user: str,
    password: str,
    keys: str,
    wait_ms: int = 500,
    mode: str = "text_only",
) -> dict[str, Any]:
    """Send keys, wait, capture, optionally OCR (scaffolding stub)."""
    return dict(_STUB)


async def kvm_close(*, host: str, user: str, password: str) -> dict[str, Any]:
    """Close an active KVM session (scaffolding stub)."""
    return dict(_STUB)


async def kvm_status() -> dict[str, Any]:
    """List active KVM sessions and daemon health (scaffolding stub)."""
    return dict(_STUB)
```

- [ ] **Step 4: Register stubs in the MCP server** — append to `src/redfish_mcp/mcp_server.py` (bottom of the file, near where other tools are registered; find the `tools["..."] = ...` and `mcp.tool(...)(_wrap(...))` pattern used for existing tools and follow it). Each stub needs both a `tools[...] = ...` entry for the dispatcher map and an `mcp.tool(...)(_wrap(...))` registration with `readOnlyHint=True` for `kvm_screen` / `kvm_status`, and `destructiveHint=False` for inputs (they don't mutate BMC state by themselves in phase 1 since they're stubs).

Exact code block to add (search for the final `tools["redfish_agent_get_host_stats"] = ...` block or similar, add immediately after the last existing `tools[...] = ...` registration):

```python
from redfish_mcp.kvm.tools import (
    kvm_close as _kvm_close,
    kvm_screen as _kvm_screen,
    kvm_sendkey as _kvm_sendkey,
    kvm_sendkeys as _kvm_sendkeys,
    kvm_status as _kvm_status,
    kvm_type_and_read as _kvm_type_and_read,
)

tools["redfish_kvm_screen"] = _kvm_screen
tools["redfish_kvm_sendkey"] = _kvm_sendkey
tools["redfish_kvm_sendkeys"] = _kvm_sendkeys
tools["redfish_kvm_type_and_read"] = _kvm_type_and_read
tools["redfish_kvm_close"] = _kvm_close
tools["redfish_kvm_status"] = _kvm_status

mcp.tool(
    annotations=ToolAnnotations(
        title="KVM: capture screen",
        readOnlyHint=True, destructiveHint=False, idempotentHint=True,
    )
)(_wrap(_kvm_screen))
mcp.tool(
    annotations=ToolAnnotations(
        title="KVM: send single key",
        readOnlyHint=False, destructiveHint=False, idempotentHint=False,
    )
)(_wrap(_kvm_sendkey))
mcp.tool(
    annotations=ToolAnnotations(
        title="KVM: send keystrokes",
        readOnlyHint=False, destructiveHint=False, idempotentHint=False,
    )
)(_wrap(_kvm_sendkeys))
mcp.tool(
    annotations=ToolAnnotations(
        title="KVM: send keys and read screen",
        readOnlyHint=False, destructiveHint=False, idempotentHint=False,
    )
)(_wrap(_kvm_type_and_read))
mcp.tool(
    annotations=ToolAnnotations(
        title="KVM: close session",
        readOnlyHint=False, destructiveHint=False, idempotentHint=True,
    )
)(_wrap(_kvm_close))
mcp.tool(
    annotations=ToolAnnotations(
        title="KVM: status",
        readOnlyHint=True, destructiveHint=False, idempotentHint=True,
    )
)(_wrap(_kvm_status))
```

If `ToolAnnotations` is already imported in `mcp_server.py` (it is, since other tools use it), do not re-import. If `_wrap` has a different name in the file (look for the async wrapper applied to every `redfish_*` tool), use that. No other modifications to `mcp_server.py` should be needed.

- [ ] **Step 5: Run tool-stub tests**

```
uv run pytest tests/kvm/test_tools.py -v
```

Expected: `6 passed`.

- [ ] **Step 6: Run full suite to catch registration regressions**

```
uv run pytest -q --no-header
```

Expected: all prior passing tests still pass; `test_netbox_resolves_ori_site` still fails (pre-existing, documented).

- [ ] **Step 7: Commit**

```bash
git add src/redfish_mcp/kvm/tools.py src/redfish_mcp/mcp_server.py tests/kvm/test_tools.py
git commit -m "feat(kvm): register redfish_kvm_* MCP tool stubs (not_implemented)"
```

---

## Task 17 — CLI subcommand stubs + docs

**Goal:** Add `redfish-cli kvm screen|send|type-and-read|close|status` returning `not_implemented`. Add a KVM feature doc stub and index update.

**Files:**
- Create: `src/redfish_mcp/kvm/cli_commands.py`
- Modify: `src/redfish_mcp/cli.py`
- Create: `tests/kvm/test_cli.py`
- Create: `docs/KVM_CONSOLE_FEATURE.md`
- Modify: `docs/DOCUMENTATION_INDEX.md`

**Steps:**

- [ ] **Step 1: Write the failing test** — `tests/kvm/test_cli.py`

```python
"""Tests for KVM CLI subcommand stubs."""

from __future__ import annotations

from typer.testing import CliRunner

from redfish_mcp.cli import app


runner = CliRunner()


class TestKvmCliStubs:
    def test_kvm_help(self):
        r = runner.invoke(app, ["kvm", "--help"])
        assert r.exit_code == 0
        assert "screen" in r.stdout
        assert "send" in r.stdout
        assert "type-and-read" in r.stdout
        assert "close" in r.stdout
        assert "status" in r.stdout

    def test_kvm_screen_not_implemented(self):
        r = runner.invoke(app, ["kvm", "screen", "10.0.0.1"])
        assert r.exit_code != 0
        assert "not_implemented" in (r.stdout + r.stderr)

    def test_kvm_status_not_implemented(self):
        r = runner.invoke(app, ["kvm", "status"])
        assert r.exit_code != 0
        assert "not_implemented" in (r.stdout + r.stderr)
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/kvm/test_cli.py -v
```

Expected: `typer.BadOptionUsage` / `No such command 'kvm'` errors.

- [ ] **Step 3: Implement** — `src/redfish_mcp/kvm/cli_commands.py`

```python
"""typer subcommands for ``redfish-cli kvm ...`` (phase 1 stubs)."""

from __future__ import annotations

import typer

app = typer.Typer(name="kvm", help="KVM console — read screen and send keyboard input.")

_NOT_IMPL_MSG = "not_implemented — phase 1 scaffolding only; see docs/KVM_CONSOLE_FEATURE.md"


@app.command("screen")
def screen(
    host: str = typer.Argument(..., help="BMC host or IP"),
    mode: str = typer.Option("text_only", "--mode", help="image|text_only|both|summary|analysis|diagnosis"),
    detach: bool = typer.Option(False, "--detach", help="Return task id and exit"),
) -> None:
    typer.echo(_NOT_IMPL_MSG, err=True)
    raise typer.Exit(code=2)


@app.command("send")
def send(
    host: str = typer.Argument(..., help="BMC host or IP"),
    keys_or_text: str = typer.Argument(..., help="A single key (e.g. Enter, F2, Ctrl+Alt+Del) or text"),
    enter: bool = typer.Option(False, "--enter", help="Press Enter after text"),
) -> None:
    typer.echo(_NOT_IMPL_MSG, err=True)
    raise typer.Exit(code=2)


@app.command("type-and-read")
def type_and_read(
    host: str = typer.Argument(..., help="BMC host or IP"),
    text: str = typer.Argument(..., help="Text to type"),
    wait_ms: int = typer.Option(500, "--wait-ms"),
    mode: str = typer.Option("text_only", "--mode"),
) -> None:
    typer.echo(_NOT_IMPL_MSG, err=True)
    raise typer.Exit(code=2)


@app.command("close")
def close(host: str = typer.Argument(..., help="BMC host or IP")) -> None:
    typer.echo(_NOT_IMPL_MSG, err=True)
    raise typer.Exit(code=2)


@app.command("status")
def status(
    task_id: str | None = typer.Argument(None, help="Optional task id to poll"),
) -> None:
    typer.echo(_NOT_IMPL_MSG, err=True)
    raise typer.Exit(code=2)
```

- [ ] **Step 4: Register the subcommand in `src/redfish_mcp/cli.py`** — find the `app = typer.Typer(...)` block at the top and add after `install_cli_exception_handler(app, project_repo=...)`:

```python
from redfish_mcp.kvm.cli_commands import app as _kvm_app
app.add_typer(_kvm_app, name="kvm")
```

- [ ] **Step 5: Run CLI stub tests**

```
uv run pytest tests/kvm/test_cli.py -v
```

Expected: `3 passed`.

- [ ] **Step 6: Create** — `docs/KVM_CONSOLE_FEATURE.md`

```markdown
# KVM Console Feature

Interactive KVM-over-IP: read the server screen and send keyboard input
via the BMC. See the design spec at
[`docs/superpowers/specs/2026-04-20-kvm-console-design.md`](./superpowers/specs/2026-04-20-kvm-console-design.md)
for full architecture.

## Status

Phase 1 (scaffolding) — merged. Tools return `not_implemented`.

- Phase 2 ([#64](https://github.com/vhspace/redfish-mcp/issues/64)) — Java iKVM backend + screen capture.
- Phase 3 ([#65](https://github.com/vhspace/redfish-mcp/issues/65)) — keyboard input + `type_and_read`.
- Phase 4 ([#66](https://github.com/vhspace/redfish-mcp/issues/66)) — status, reaper tuning, docs.

Epic: [#67](https://github.com/vhspace/redfish-mcp/issues/67).

## Runtime dependencies (required from phase 2)

- OpenJDK 17+
- Xvfb (package `xvfb`)
- x11vnc (package `x11vnc`)

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `REDFISH_KVM_SOCKET_DIR` | `$XDG_RUNTIME_DIR` (`/tmp` fallback) | Daemon socket location |
| `REDFISH_KVM_SESSION_IDLE_S` | `300` | Session reap threshold |
| `REDFISH_KVM_DAEMON_IDLE_S` | `600` | Daemon self-exit threshold |
| `REDFISH_KVM_MAX_CONCURRENT` | `4` | Global concurrent-session cap |
| `REDFISH_KVM_BACKEND` | `java` | `java` \| `playwright` \| `auto` |
| `REDFISH_KVM_JAVA_BIN` | `java` | JRE binary path |
| `REDFISH_KVM_JAR_CACHE_DIR` | `$XDG_CACHE_HOME/redfish-mcp/kvm/jars` | JAR cache |
| `REDFISH_KVM_LOG_LEVEL` | `INFO` | Daemon log verbosity |
```

- [ ] **Step 7: Update** — `docs/DOCUMENTATION_INDEX.md`: add a line under the existing list that references `KVM_CONSOLE_FEATURE.md`. If the existing format is a bulleted list, append:

```markdown
- [KVM Console Feature](KVM_CONSOLE_FEATURE.md) — read BMC screen and send keyboard input.
```

- [ ] **Step 8: Run the full test suite one more time**

```
uv run pytest -q --no-header
```

Expected: all KVM tests pass (roughly 60+ new tests); pre-existing `test_netbox_resolves_ori_site` still fails; everything else still passes.

- [ ] **Step 9: Commit**

```bash
git add src/redfish_mcp/kvm/cli_commands.py src/redfish_mcp/cli.py tests/kvm/test_cli.py docs/KVM_CONSOLE_FEATURE.md docs/DOCUMENTATION_INDEX.md
git commit -m "feat(kvm): add redfish-cli kvm subcommand stubs and feature doc"
```

---

## Final verification

- [ ] Run `uv run mypy src/redfish_mcp/kvm` — expect no errors.
- [ ] Run `uv run ruff check src/redfish_mcp/kvm tests/kvm` — expect no errors.
- [ ] Run `uv run pytest -q --no-header` — expect 60+ new passing tests; only the pre-existing `test_netbox_resolves_ori_site` failure remains.
- [ ] `git log --oneline feat/kvm-phase1-scaffolding ^feat/kvm-console` shows 17 focused commits (one per task).

## Open item for PR description

- Phase 1 PR should reference issue #63 in the body (`Closes #63`).
- Note the pre-existing `test_netbox_resolves_ori_site` failure in the PR so it's not mistaken as introduced here.
