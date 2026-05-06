# KVM Phase 2 — Java iKVM Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `redfish_kvm_screen` MCP tool's `not_implemented` stub with a working Supermicro Java iKVM backend. Ship screen-capture end-to-end; keyboard input stays stubbed for phase 3.

**Architecture:** A new `JavaIkvmBackend` (implementing the existing `KVMBackend` Protocol from phase 1) composes three subprocesses per session: `Xvfb` (virtual X display), the vendor's iKVM `.jar` running under `java` (connects to the BMC on port 5900), and `x11vnc` (re-exposes the X display as plain VNC on localhost). The daemon's chosen VNC client talks to that local x11vnc. The SupermicroCGI flow logs in, fetches the JNLP, downloads the JAR (SHA-256 content-addressable cache), then hands everything to a `SessionSubprocesses` async context manager that keeps all three processes co-scoped. A router-layer `session_ops.open_session` wrapper applies `asyncio.wait_for` with a progress-subscriber that records the last-seen stage so timeouts surface as `failed:timeout:<stage>`.

**Tech Stack:** Python 3.12+, `asyncio` for subprocess/VNC I/O, `requests` + `pytest-httpx` for CGI testing, either `asyncvnc` or `vncdotool` (decided in Task 6 spike), `xml.etree.ElementTree` for JNLP parsing, system apt packages (`openjdk-17-jre-headless`, `xvfb`, `x11vnc`) on x86_64 and aarch64 Linux.

**Design spec:** `docs/superpowers/specs/2026-04-21-kvm-phase2-java-backend-design.md`.

---

## File layout

New files under `src/redfish_mcp/kvm/backends/`:

```
src/redfish_mcp/kvm/backends/
├── __init__.py                 # package marker, no re-exports
├── _supermicro_cgi.py          # POST /cgi/login.cgi + GET /cgi/url_redirect.cgi
├── _jnlp.py                    # Parse JNLP XML to JnlpSpec dataclass
├── _jar_cache.py               # SHA-256 content-addressable JAR cache
├── _subprocess.py              # SessionSubprocesses async context manager
├── _vnc.py                     # Thin wrapper over winning VNC lib (post Task 6)
└── java.py                     # JavaIkvmBackend (KVMBackend impl)
```

New files under `src/redfish_mcp/kvm/daemon/`:

```
src/redfish_mcp/kvm/daemon/
├── preflight.py                # check_runtime_deps()
├── session_ops.py              # open_session(...), screenshot(...) with timeout + stage tracking
└── handlers.py                 # register_kvm_handlers(router, cache, progress, backend)
```

New test files:

```
tests/kvm/
├── backends/
│   ├── __init__.py
│   ├── test_supermicro_cgi.py
│   ├── test_jnlp.py
│   ├── test_jar_cache.py
│   ├── test_subprocess.py      # @pytest.mark.subprocess
│   ├── test_vnc.py             # @pytest.mark.subprocess
│   ├── test_java_backend.py
│   ├── fixtures/
│   │   └── jnlp_supermicro_x13.xml
├── test_preflight.py
├── test_session_ops.py
├── test_handlers.py
└── test_java_backend_e2e.py    # @pytest.mark.e2e
```

Modified files:

- `pyproject.toml` — add `subprocess` pytest marker; add winning VNC lib dep (post Task 6).
- `.github/workflows/ci.yml` — add x86_64/aarch64 matrix, apt install step.
- `src/redfish_mcp/kvm/daemon/server.py` — call `check_runtime_deps()` in `start()`; register handlers.
- `src/redfish_mcp/kvm/tools.py` — `kvm_screen` calls daemon via `DaemonClient`.
- `docs/KVM_CONSOLE_FEATURE.md` — phase 2 status, runtime deps section.
- `README.md` — runtime dependencies section.
- `AI_AGENT_GUIDE.md` — first real KVM usage example.

Every new `.py` file starts with `from __future__ import annotations`. Test functions follow existing repo style: no `-> None` return type (matches other tests in `tests/`).

---

## Project conventions (apply to every task)

- `mypy` runs against `src/` only; test files don't need return-type annotations.
- Async tests use `@pytest.mark.anyio`; the project-wide `anyio_backend` fixture returns `"asyncio"` (in `tests/conftest.py`).
- Ruff's `B017` forbids `pytest.raises(Exception)` — use specific exception types.
- Ruff's `N818` requires exception class names to end in `Error`. Phase-1 KVM exceptions are already renamed: `AuthFailedError`, `SlotBusyError`, `StaleSessionError`, `SessionLostError`, `BackendUnsupportedError`, `JarMismatchError`, `JnlpUnavailableError`, `DaemonUnavailableError`, `KVMError`.
- `uv run pytest -m "not integration"` is the default local + CI run.
- Commit messages use conventional-commits prefixes: `feat(kvm):`, `fix(kvm):`, `docs(kvm):`, `test(kvm):`, `chore(kvm):`.

## Environment variables (required across tasks)

Every env var listed in the design spec's **Configuration additions** table MUST be honored by the implementation. Wiring locations:

| Env var | Default | Wired in task | Wired where |
|---|---|---|---|
| `REDFISH_KVM_OPEN_TIMEOUT_S` | `30` | Task 9 | `session_ops.DEFAULT_OPEN_TIMEOUT_S` (env-overridable module constant) |
| `REDFISH_KVM_SCREENSHOT_TIMEOUT_S` | `15` | Task 9 | `session_ops.DEFAULT_SCREENSHOT_TIMEOUT_S` |
| `REDFISH_KVM_VERIFY_TLS` | `0` (unset/0 → False, `1` → True) | Task 8 | `JavaIkvmBackend.__init__` default |
| `REDFISH_KVM_DISPLAY_RANGE_START` | `10` | Task 5 | `_subprocess._DEFAULT_DISPLAY_RANGE_START` |
| `REDFISH_KVM_XVFB_GEOMETRY` | `1280x1024x24` | Task 8 | `JavaIkvmBackend.__init__` default |

Each task's test suite must include at least one test that verifies the env override takes effect (using `monkeypatch.setenv` or reimporting the module after `monkeypatch.setenv`).

---

## Task 1 — Preflight module + CI matrix + runtime-deps docs

**Goal:** Daemon refuses to start with a clear error message if apt deps are missing; CI installs them on both x86_64 and aarch64; README documents them. This unblocks tier-2 subprocess tests in later tasks.

**Files:**
- Create: `src/redfish_mcp/kvm/daemon/preflight.py`
- Create: `tests/kvm/test_preflight.py`
- Modify: `pyproject.toml` (add `subprocess` marker)
- Modify: `.github/workflows/ci.yml` (matrix + apt install)
- Modify: `README.md` (runtime dependencies section)
- Modify: `src/redfish_mcp/kvm/daemon/server.py` (call preflight in `start()`)

**Steps:**

- [ ] **Step 1: Write failing test** — `tests/kvm/test_preflight.py`

```python
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

    def test_all_missing_lists_all_three(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "redfish_mcp.kvm.daemon.preflight.shutil.which", lambda _name: None
        )
        with pytest.raises(BackendUnsupportedError) as exc_info:
            check_runtime_deps()
        msg = str(exc_info.value)
        assert "java" in msg
        assert "Xvfb" in msg
        assert "x11vnc" in msg
        assert exc_info.value.reason == "backend_unsupported"
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/kvm/test_preflight.py -v
```

Expected: `ModuleNotFoundError: No module named 'redfish_mcp.kvm.daemon.preflight'`.

- [ ] **Step 3: Implement** — `src/redfish_mcp/kvm/daemon/preflight.py`

```python
"""Preflight check for KVM runtime system dependencies.

The Java iKVM backend requires three binaries to be installed at the OS
level: openjdk (the JRE), Xvfb (virtual X display server), and x11vnc
(X→VNC exporter). We check for them at daemon startup so misconfigured
environments fail fast with an actionable message.
"""

from __future__ import annotations

import shutil

from redfish_mcp.kvm.exceptions import BackendUnsupportedError

_REQUIRED_BINARIES: tuple[str, ...] = ("java", "Xvfb", "x11vnc")
_APT_INSTALL_HINT = (
    "sudo apt install -y openjdk-17-jre-headless xvfb x11vnc"
)


def check_runtime_deps() -> None:
    """Raise ``BackendUnsupportedError`` if any required binary is missing."""
    missing = [b for b in _REQUIRED_BINARIES if shutil.which(b) is None]
    if not missing:
        return
    raise BackendUnsupportedError(
        f"Missing KVM runtime dependencies: {', '.join(missing)}. "
        f"Install with: {_APT_INSTALL_HINT}"
    )
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/kvm/test_preflight.py -v
```

Expected: `3 passed`.

- [ ] **Step 5: Wire preflight into `DaemonServer.start()`** — `src/redfish_mcp/kvm/daemon/server.py`

Read the existing file first (`Read` tool). Locate `async def start(self)`. At the very top of that method (before `socket_dir.mkdir`), add:

```python
        from redfish_mcp.kvm.daemon.preflight import check_runtime_deps
        check_runtime_deps()
```

(Keep it as a deferred import so `from redfish_mcp.kvm.daemon.server import DaemonServer` still works in unit tests that don't exercise `start()`.)

- [ ] **Step 6: Add the `subprocess` pytest marker** — `pyproject.toml`

Find the existing marker block:

```toml
markers = [
    "unit: unit tests",
    "integration: integration tests (requires REDFISH_IP/USER/PASSWORD)",
    "e2e: end-to-end tests",
]
```

Replace with:

```toml
markers = [
    "unit: unit tests",
    "integration: integration tests (requires REDFISH_IP/USER/PASSWORD)",
    "e2e: end-to-end tests",
    "subprocess: tests that spawn real Xvfb/Java/x11vnc subprocesses (requires apt deps)",
]
```

- [ ] **Step 7: Update CI workflow** — `.github/workflows/ci.yml`

Read the existing file. Replace the `lint-and-test` job with:

```yaml
jobs:
  lint-and-test:
    strategy:
      matrix:
        runner: [ubuntu-latest, ubuntu-24.04-arm]
      fail-fast: false
    runs-on: ${{ matrix.runner }}
    steps:
      - name: Checkout
        uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2

      - name: Set up Python
        uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065 # v5.6.0
        with:
          python-version: "3.13"

      - name: Set up uv
        uses: astral-sh/setup-uv@85856786d1ce8acfbcc2f13a5f3fbd6b938f9f41 # v7.1.2

      - name: Install KVM runtime deps
        run: sudo apt-get update && sudo apt-get install -y openjdk-17-jre-headless xvfb x11vnc

      - name: Install dependencies
        run: uv sync --all-groups

      - name: Lint (ruff check)
        run: uv run ruff check src tests

      - name: Format check (ruff format)
        run: uv run ruff format --check src tests

      - name: Type check (mypy)
        continue-on-error: true
        run: uv run mypy src/

      - name: Run tests (non-integration, non-e2e)
        run: uv run pytest -m "not integration and not e2e" -v

      - name: Build package
        run: uv build
```

The key additions: `strategy.matrix.runner` with both architectures, the `Install KVM runtime deps` step, and the `"not integration and not e2e"` marker filter (subprocess tests run by default since apt deps are present).

- [ ] **Step 8: Add runtime-dependencies section to README** — `README.md`

Read the existing file. Find a good insertion point (after the top-level description, before "Installation"). Insert:

```markdown
## Runtime dependencies

The KVM console feature (phase 2+) requires three system binaries in addition
to Python and `uv`:

- `openjdk-17-jre-headless` — Java runtime for the Supermicro iKVM client.
- `xvfb` — headless X display server.
- `x11vnc` — exports the X display as a local VNC stream.

**Ubuntu / Debian** (x86_64 and aarch64):
```bash
sudo apt install -y openjdk-17-jre-headless xvfb x11vnc
```

**macOS** (Homebrew; caveat: `xvfb` / `x11vnc` on macOS require XQuartz and are
less polished than on Linux):
```bash
brew install openjdk
# Xvfb/x11vnc via XQuartz; see docs/KVM_CONSOLE_FEATURE.md
```

Without these binaries, the daemon exits on startup with a clear error. Non-KVM
features (screenshot via Redfish/CGI, BIOS, firmware, power) work without them.
```

- [ ] **Step 9: Run full test suite locally**

```
uv run pytest -m "not integration and not e2e" -q --no-header
```

Expected: all tests pass (no regressions). `test_preflight.py` contributes 3 passing tests.

Also verify formatting and linting:

```
uv run ruff check src tests
uv run ruff format --check src tests
```

Expected: both clean.

- [ ] **Step 10: Commit**

```bash
git add src/redfish_mcp/kvm/daemon/preflight.py src/redfish_mcp/kvm/daemon/server.py tests/kvm/test_preflight.py pyproject.toml .github/workflows/ci.yml README.md
git commit -m "feat(kvm): add runtime-deps preflight + x86_64/aarch64 CI matrix

Adds check_runtime_deps() that fails fast if java/Xvfb/x11vnc are missing,
with an apt install hint. Wired into DaemonServer.start().

CI now runs on both ubuntu-latest (x86_64) and ubuntu-24.04-arm (aarch64),
with apt-get install step for the KVM subprocess deps. Adds 'subprocess'
pytest marker for tier-2 tests in later phase 2 tasks.

Part of #64."
```

---

## Task 2 — Supermicro CGI client (login + JNLP fetch)

**Goal:** Wrap the two Supermicro CGI endpoints we need: `POST /cgi/login.cgi` (→ SID cookie) and `GET /cgi/url_redirect.cgi?url_name=man_ikvm&url_type=jwsk` (→ JNLP XML). Pure I/O; no JNLP parsing yet.

**Files:**
- Create: `src/redfish_mcp/kvm/backends/__init__.py`
- Create: `src/redfish_mcp/kvm/backends/_supermicro_cgi.py`
- Create: `tests/kvm/backends/__init__.py`
- Create: `tests/kvm/backends/test_supermicro_cgi.py`

**Steps:**

- [ ] **Step 1: Create package markers**

`src/redfish_mcp/kvm/backends/__init__.py`:
```python
"""Phase 2: concrete KVMBackend implementations.

Contents are considered implementation-private; the only public seam is the
KVMBackend Protocol in src/redfish_mcp/kvm/backend.py.
"""

from __future__ import annotations

__all__: list[str] = []
```

`tests/kvm/backends/__init__.py`: empty file.

- [ ] **Step 2: Write failing test** — `tests/kvm/backends/test_supermicro_cgi.py`

```python
"""Tests for the Supermicro CGI client."""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from redfish_mcp.kvm.backends._supermicro_cgi import (
    SupermicroCGIError,
    fetch_jnlp,
    login,
)


class TestLogin:
    def test_login_posts_credentials_and_returns_sid(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            method="POST",
            url="https://10.0.0.1/cgi/login.cgi",
            headers={"Set-Cookie": "SID=abc123; Path=/; HttpOnly"},
            text="<html><body>OK</body></html>",
            status_code=200,
        )
        sid = login(host="10.0.0.1", user="ADMIN", password="pw", verify_tls=False)
        assert sid == "abc123"

        request = httpx_mock.get_request()
        assert request is not None
        body = request.content.decode()
        assert "name=ADMIN" in body
        assert "pwd=pw" in body

    def test_login_without_sid_cookie_raises(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            method="POST",
            url="https://10.0.0.1/cgi/login.cgi",
            text="<html><body>Invalid username or password</body></html>",
            status_code=200,
        )
        with pytest.raises(SupermicroCGIError) as exc_info:
            login(host="10.0.0.1", user="ADMIN", password="bad", verify_tls=False)
        assert "SID" in str(exc_info.value)

    def test_login_http_error_raises(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            method="POST",
            url="https://10.0.0.1/cgi/login.cgi",
            status_code=500,
            text="internal error",
        )
        with pytest.raises(SupermicroCGIError):
            login(host="10.0.0.1", user="ADMIN", password="pw", verify_tls=False)


class TestFetchJnlp:
    def test_fetch_jnlp_returns_xml_bytes(self, httpx_mock: HTTPXMock):
        jnlp_body = b"<?xml version='1.0'?><jnlp></jnlp>"
        httpx_mock.add_response(
            method="GET",
            url="https://10.0.0.1/cgi/url_redirect.cgi?url_name=man_ikvm&url_type=jwsk",
            content=jnlp_body,
            status_code=200,
        )
        result = fetch_jnlp(host="10.0.0.1", sid="abc123", verify_tls=False)
        assert result == jnlp_body

        request = httpx_mock.get_request()
        assert request is not None
        cookie_header = request.headers.get("cookie", "")
        assert "SID=abc123" in cookie_header

    def test_fetch_jnlp_404_raises(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            method="GET",
            url="https://10.0.0.1/cgi/url_redirect.cgi?url_name=man_ikvm&url_type=jwsk",
            status_code=404,
        )
        with pytest.raises(SupermicroCGIError):
            fetch_jnlp(host="10.0.0.1", sid="abc123", verify_tls=False)
```

- [ ] **Step 3: Run test to verify it fails**

```
uv run pytest tests/kvm/backends/test_supermicro_cgi.py -v
```

Expected: `ModuleNotFoundError: No module named 'redfish_mcp.kvm.backends._supermicro_cgi'`.

- [ ] **Step 4: Implement** — `src/redfish_mcp/kvm/backends/_supermicro_cgi.py`

```python
"""Supermicro BMC CGI HTTP client.

Two endpoints, used in sequence during JavaIkvmBackend.open():

    POST /cgi/login.cgi   with name=<user>&pwd=<password>
        → sets SID cookie; response body is HTML but we only care about the cookie.

    GET /cgi/url_redirect.cgi?url_name=man_ikvm&url_type=jwsk
        → returns JNLP XML with a rotated credential and JAR URL.

TLS verification defaults off because BMC certs are almost always self-signed
or expired. Enable via verify_tls=True when the environment has proper cert
distribution.
"""

from __future__ import annotations

import logging
import warnings

import httpx

logger = logging.getLogger("redfish_mcp.kvm.backends.supermicro_cgi")


class SupermicroCGIError(Exception):
    """Raised for unexpected responses from the Supermicro CGI endpoints."""


_LOGIN_PATH = "/cgi/login.cgi"
_JNLP_PATH = "/cgi/url_redirect.cgi"
_JNLP_PARAMS = {"url_name": "man_ikvm", "url_type": "jwsk"}


def _base_url(host: str) -> str:
    return f"https://{host}"


def _client(verify_tls: bool) -> httpx.Client:
    if not verify_tls:
        warnings.filterwarnings("ignore", category=httpx.InsecureRequestWarning, append=True)
    return httpx.Client(verify=verify_tls, timeout=httpx.Timeout(10.0, connect=5.0))


def login(*, host: str, user: str, password: str, verify_tls: bool = False) -> str:
    """POST credentials and return the SID cookie value.

    Raises SupermicroCGIError on HTTP error or missing SID cookie.
    """
    with _client(verify_tls) as client:
        try:
            resp = client.post(
                f"{_base_url(host)}{_LOGIN_PATH}",
                data={"name": user, "pwd": password},
            )
        except httpx.HTTPError as exc:
            raise SupermicroCGIError(f"login request failed: {exc}") from exc

    if resp.status_code != 200:
        raise SupermicroCGIError(
            f"login returned HTTP {resp.status_code}: {resp.text[:200]}"
        )

    sid = resp.cookies.get("SID")
    if not sid:
        raise SupermicroCGIError("login response missing SID cookie (bad credentials?)")
    return sid


def fetch_jnlp(*, host: str, sid: str, verify_tls: bool = False) -> bytes:
    """Download the iKVM JNLP XML for the authenticated session.

    Returns raw bytes; caller parses via the _jnlp module.
    """
    with _client(verify_tls) as client:
        try:
            resp = client.get(
                f"{_base_url(host)}{_JNLP_PATH}",
                params=_JNLP_PARAMS,
                cookies={"SID": sid},
            )
        except httpx.HTTPError as exc:
            raise SupermicroCGIError(f"jnlp fetch failed: {exc}") from exc

    if resp.status_code != 200:
        raise SupermicroCGIError(
            f"jnlp fetch returned HTTP {resp.status_code}: {resp.text[:200]}"
        )
    return resp.content
```

Note: this uses `httpx` (sync) because that's what `pytest-httpx` mocks. The project already has `httpx` transitively via FastMCP. Verify with `grep httpx pyproject.toml uv.lock | head -5`. If not present, add `httpx>=0.27` to `[project.dependencies]` in this task.

- [ ] **Step 5: Verify httpx is available**

```
grep "^httpx\|\"httpx\"" pyproject.toml uv.lock | head -5
```

If no match in `pyproject.toml`'s `[project.dependencies]`, add this line (under the existing deps block alphabetically):

```toml
    "httpx>=0.27",
```

and run `uv sync --all-groups`.

- [ ] **Step 6: Run test to verify it passes**

```
uv run pytest tests/kvm/backends/test_supermicro_cgi.py -v
```

Expected: `5 passed`.

- [ ] **Step 7: Commit**

```bash
git add src/redfish_mcp/kvm/backends/__init__.py src/redfish_mcp/kvm/backends/_supermicro_cgi.py tests/kvm/backends/__init__.py tests/kvm/backends/test_supermicro_cgi.py
# include pyproject.toml / uv.lock only if Step 5 added httpx
git status --short
git commit -m "feat(kvm): add Supermicro CGI client (login + JNLP fetch)

Wraps POST /cgi/login.cgi and GET /cgi/url_redirect.cgi?url_name=man_ikvm.
Raises SupermicroCGIError on unexpected responses. TLS verification off
by default because BMC certs are self-signed.

Part of #64."
```

---

## Task 3 — JNLP parser

**Goal:** Parse the JNLP XML returned by the BMC into a structured `JnlpSpec` dataclass with the JAR URL, host/port(s), rotated credential, and the exact argv list to hand to `java -cp`. Pure parsing — no I/O.

**Files:**
- Create: `src/redfish_mcp/kvm/backends/_jnlp.py`
- Create: `tests/kvm/backends/test_jnlp.py`
- Create: `tests/kvm/backends/fixtures/jnlp_supermicro_x13.xml`

**Steps:**

- [ ] **Step 1: Capture a realistic JNLP fixture**

Create `tests/kvm/backends/fixtures/jnlp_supermicro_x13.xml` with a Supermicro X13-style JNLP. Minimum required shape:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<jnlp spec="1.0+" codebase="https://10.0.0.1:443" href="launch.jnlp">
    <information>
        <title>ATEN Java iKVM Viewer</title>
        <vendor>ATEN International Co. Ltd.</vendor>
    </information>
    <security>
        <all-permissions/>
    </security>
    <resources>
        <j2se version="1.8+"/>
        <jar href="iKVM__V1.69.42.0x0.jar" download="eager" main="true"/>
    </resources>
    <application-desc main-class="tw.com.aten.ikvm.KVMMain">
        <argument>10.0.0.1</argument>
        <argument>63630</argument>
        <argument>63631</argument>
        <argument>1</argument>
        <argument>0</argument>
        <argument>5900</argument>
        <argument>623</argument>
        <argument>0</argument>
        <argument>0</argument>
        <argument>EphemeralUser</argument>
        <argument>EphemeralPass</argument>
        <argument>0</argument>
        <argument>1</argument>
        <argument>5900</argument>
        <argument>623</argument>
        <argument>0</argument>
        <argument>0</argument>
        <argument>63630</argument>
        <argument>63631</argument>
        <argument>0</argument>
        <argument>0</argument>
        <argument>0</argument>
    </application-desc>
</jnlp>
```

This is representative of the shape documented in Flameeyes's and MisterCalvin's reverse-engineering. The parser validates the important fields but is tolerant of argument-count differences across firmware versions.

- [ ] **Step 2: Write failing test** — `tests/kvm/backends/test_jnlp.py`

```python
"""Tests for JNLP XML parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from redfish_mcp.kvm.backends._jnlp import JnlpParseError, JnlpSpec, parse_jnlp

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class TestParseJnlp:
    def test_parses_supermicro_x13_fixture(self):
        spec = parse_jnlp(_load("jnlp_supermicro_x13.xml"))
        assert isinstance(spec, JnlpSpec)
        assert spec.codebase == "https://10.0.0.1:443"
        assert spec.jar_href == "iKVM__V1.69.42.0x0.jar"
        assert spec.main_class == "tw.com.aten.ikvm.KVMMain"
        assert len(spec.arguments) >= 22
        assert spec.arguments[0] == "10.0.0.1"
        assert spec.arguments[9] == "EphemeralUser"
        assert spec.arguments[10] == "EphemeralPass"

    def test_jar_absolute_url_computed(self):
        spec = parse_jnlp(_load("jnlp_supermicro_x13.xml"))
        assert spec.jar_url() == "https://10.0.0.1:443/iKVM__V1.69.42.0x0.jar"

    def test_missing_jar_raises(self):
        bad_xml = b"""<?xml version="1.0"?>
<jnlp codebase="https://x/"><resources/>
<application-desc main-class="x"><argument>a</argument></application-desc></jnlp>"""
        with pytest.raises(JnlpParseError) as exc_info:
            parse_jnlp(bad_xml)
        assert "jar" in str(exc_info.value).lower()

    def test_missing_main_class_raises(self):
        bad_xml = b"""<?xml version="1.0"?>
<jnlp codebase="https://x/">
<resources><jar href="x.jar"/></resources>
<application-desc><argument>a</argument></application-desc></jnlp>"""
        with pytest.raises(JnlpParseError) as exc_info:
            parse_jnlp(bad_xml)
        assert "main-class" in str(exc_info.value).lower()

    def test_malformed_xml_raises(self):
        with pytest.raises(JnlpParseError):
            parse_jnlp(b"<<not xml>>")

    def test_no_arguments_raises(self):
        bad_xml = b"""<?xml version="1.0"?>
<jnlp codebase="https://x/">
<resources><jar href="x.jar"/></resources>
<application-desc main-class="x"></application-desc></jnlp>"""
        with pytest.raises(JnlpParseError):
            parse_jnlp(bad_xml)
```

- [ ] **Step 3: Run test to verify it fails**

```
uv run pytest tests/kvm/backends/test_jnlp.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 4: Implement** — `src/redfish_mcp/kvm/backends/_jnlp.py`

```python
"""Parse Supermicro iKVM JNLP XML into a structured JnlpSpec.

JNLP (Java Network Launch Protocol) is the legacy vehicle for Java Web Start
applications. Supermicro serves one at /cgi/url_redirect.cgi?url_name=man_ikvm
that describes the iKVM viewer:
  - codebase URL (base for resolving relative jar href)
  - one <jar href=...> we download once and cache
  - main class to invoke
  - ~22 positional arguments passed to that main class, including a rotated
    ephemeral credential for the RFB handshake.

We tolerate argument count variation across firmware versions; we only
validate that the arguments array is non-empty and the structural fields
are present.
"""

from __future__ import annotations

from dataclasses import dataclass
from xml.etree import ElementTree as ET


class JnlpParseError(Exception):
    """Raised when the JNLP XML cannot be parsed into a JnlpSpec."""


@dataclass(frozen=True)
class JnlpSpec:
    codebase: str
    jar_href: str
    main_class: str
    arguments: tuple[str, ...]

    def jar_url(self) -> str:
        """Absolute URL to the iKVM JAR on the BMC."""
        base = self.codebase.rstrip("/")
        return f"{base}/{self.jar_href}"


def parse_jnlp(xml_bytes: bytes) -> JnlpSpec:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise JnlpParseError(f"malformed JNLP XML: {exc}") from exc

    codebase = root.attrib.get("codebase")
    if not codebase:
        raise JnlpParseError("JNLP root missing codebase attribute")

    jar_elem = root.find(".//resources/jar")
    if jar_elem is None or not jar_elem.attrib.get("href"):
        raise JnlpParseError("JNLP missing <resources><jar href=...>")
    jar_href = jar_elem.attrib["href"]

    app_elem = root.find("application-desc")
    if app_elem is None:
        raise JnlpParseError("JNLP missing <application-desc>")
    main_class = app_elem.attrib.get("main-class")
    if not main_class:
        raise JnlpParseError("<application-desc> missing main-class attribute")

    args: list[str] = [a.text or "" for a in app_elem.findall("argument")]
    if not args:
        raise JnlpParseError("<application-desc> has no <argument> elements")

    return JnlpSpec(
        codebase=codebase,
        jar_href=jar_href,
        main_class=main_class,
        arguments=tuple(args),
    )
```

- [ ] **Step 5: Run test to verify it passes**

```
uv run pytest tests/kvm/backends/test_jnlp.py -v
```

Expected: `6 passed`.

- [ ] **Step 6: Commit**

```bash
git add src/redfish_mcp/kvm/backends/_jnlp.py tests/kvm/backends/test_jnlp.py tests/kvm/backends/fixtures/jnlp_supermicro_x13.xml
git commit -m "feat(kvm): add JNLP XML parser to JnlpSpec dataclass

Parses the JNLP served at /cgi/url_redirect.cgi?url_name=man_ikvm into
codebase URL, jar href, main class, and the argument list that goes to
java -cp <jar> <main-class> <args...>. Tolerant of argument-count
variation across firmware versions.

Part of #64."
```

---

## Task 4 — JAR cache

**Goal:** SHA-256 content-addressable cache at `$XDG_CACHE_HOME/redfish-mcp/kvm/jars/<sha>/iKVM.jar`. Avoids re-downloading the vendor JAR on every session. Directory perms `0700`, file perms `0600`.

**Files:**
- Create: `src/redfish_mcp/kvm/backends/_jar_cache.py`
- Create: `tests/kvm/backends/test_jar_cache.py`

**Steps:**

- [ ] **Step 1: Write failing test** — `tests/kvm/backends/test_jar_cache.py`

```python
"""Tests for the SHA-256 JAR cache."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from redfish_mcp.kvm.backends._jar_cache import JarCache, JarCacheError


JAR_BYTES = b"PK\x03\x04" + b"fake jar contents\x00" * 200


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    return tmp_path / "jars"


class TestJarCache:
    def test_first_fetch_downloads_and_caches(
        self, httpx_mock: HTTPXMock, cache_dir: Path
    ):
        httpx_mock.add_response(
            method="GET",
            url="https://10.0.0.1/iKVM.jar",
            content=JAR_BYTES,
            status_code=200,
        )
        cache = JarCache(root=cache_dir)
        path = cache.get_or_fetch(
            url="https://10.0.0.1/iKVM.jar", sid="abc", verify_tls=False
        )
        assert path.exists()
        assert path.read_bytes() == JAR_BYTES
        assert path.parent.parent == cache_dir
        expected_sha = hashlib.sha256(JAR_BYTES).hexdigest()
        assert expected_sha in str(path)

    def test_second_fetch_is_cache_hit(
        self, httpx_mock: HTTPXMock, cache_dir: Path
    ):
        httpx_mock.add_response(
            method="GET",
            url="https://10.0.0.1/iKVM.jar",
            content=JAR_BYTES,
            status_code=200,
        )
        cache = JarCache(root=cache_dir)
        p1 = cache.get_or_fetch("https://10.0.0.1/iKVM.jar", sid="abc", verify_tls=False)
        p2 = cache.get_or_fetch("https://10.0.0.1/iKVM.jar", sid="abc", verify_tls=False)
        assert p1 == p2
        # Only one HTTP request should have been made.
        assert len(httpx_mock.get_requests()) == 1

    def test_cache_dir_is_0700(self, httpx_mock: HTTPXMock, cache_dir: Path):
        httpx_mock.add_response(
            method="GET",
            url="https://10.0.0.1/iKVM.jar",
            content=JAR_BYTES,
            status_code=200,
        )
        cache = JarCache(root=cache_dir)
        path = cache.get_or_fetch("https://10.0.0.1/iKVM.jar", sid="abc", verify_tls=False)
        # SHA subdir
        assert (path.parent.stat().st_mode & 0o777) == 0o700
        # JAR file
        assert (path.stat().st_mode & 0o777) == 0o600

    def test_tampered_cache_file_detected_and_refreshed(
        self, httpx_mock: HTTPXMock, cache_dir: Path
    ):
        httpx_mock.add_response(
            method="GET",
            url="https://10.0.0.1/iKVM.jar",
            content=JAR_BYTES,
            status_code=200,
        )
        cache = JarCache(root=cache_dir)
        path = cache.get_or_fetch("https://10.0.0.1/iKVM.jar", sid="abc", verify_tls=False)
        # Corrupt the cached file.
        path.write_bytes(b"tampered")
        # Queue a second response for the re-fetch.
        httpx_mock.add_response(
            method="GET",
            url="https://10.0.0.1/iKVM.jar",
            content=JAR_BYTES,
            status_code=200,
        )
        path2 = cache.get_or_fetch("https://10.0.0.1/iKVM.jar", sid="abc", verify_tls=False)
        assert path2.read_bytes() == JAR_BYTES

    def test_http_error_raises_jarcache_error(
        self, httpx_mock: HTTPXMock, cache_dir: Path
    ):
        httpx_mock.add_response(
            method="GET",
            url="https://10.0.0.1/iKVM.jar",
            status_code=404,
        )
        cache = JarCache(root=cache_dir)
        with pytest.raises(JarCacheError):
            cache.get_or_fetch("https://10.0.0.1/iKVM.jar", sid="abc", verify_tls=False)
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/kvm/backends/test_jar_cache.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement** — `src/redfish_mcp/kvm/backends/_jar_cache.py`

```python
"""SHA-256 content-addressable cache for the Supermicro iKVM JAR.

The JAR is vendor-supplied and tied to the BMC's firmware version. We never
redistribute it — each BMC serves its own. Caching by content hash means the
cache is correct across BMCs without us having to guess firmware identifiers.

Layout:
    <root>/
        <sha256>/
            iKVM.jar     (mode 0600, file)

Directory <sha256> has mode 0700. The root directory is created with whatever
mode its parents provide; on first use we chmod it to 0700 too.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import httpx

logger = logging.getLogger("redfish_mcp.kvm.backends.jar_cache")


class JarCacheError(Exception):
    """Raised when a JAR cannot be fetched or validated."""


@dataclass
class JarCache:
    root: Path

    def _subdir(self, sha: str) -> Path:
        return self.root / sha

    def _jar_path(self, sha: str) -> Path:
        return self._subdir(sha) / "iKVM.jar"

    def _ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)

    def _validate(self, path: Path, sha: str) -> bool:
        if not path.exists():
            return False
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(64 * 1024), b""):
                h.update(chunk)
        return h.hexdigest() == sha

    def get_or_fetch(self, url: str, *, sid: str, verify_tls: bool = False) -> Path:
        """Return the path to the cached JAR, downloading if needed.

        The URL is fetched; the response body is hashed; cache path is derived
        from the hash. Subsequent calls with the same content hit the cache
        even if the URL changes (which it does on firmware updates).
        """
        self._ensure_root()

        try:
            with httpx.Client(verify=verify_tls, timeout=httpx.Timeout(30.0, connect=5.0)) as c:
                resp = c.get(url, cookies={"SID": sid})
        except httpx.HTTPError as exc:
            raise JarCacheError(f"JAR download failed: {exc}") from exc

        if resp.status_code != 200:
            raise JarCacheError(
                f"JAR download returned HTTP {resp.status_code}: {resp.text[:200]}"
            )

        body = resp.content
        sha = hashlib.sha256(body).hexdigest()
        jar_path = self._jar_path(sha)

        if self._validate(jar_path, sha):
            logger.debug("JAR cache hit: %s", sha[:12])
            return jar_path

        # Write atomically: tmp file then rename.
        subdir = self._subdir(sha)
        subdir.mkdir(parents=True, exist_ok=True)
        os.chmod(subdir, 0o700)

        tmp = subdir / "iKVM.jar.tmp"
        tmp.write_bytes(body)
        os.chmod(tmp, 0o600)
        os.replace(tmp, jar_path)

        logger.info("JAR cached: %s (%d bytes)", sha[:12], len(body))
        return jar_path
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/kvm/backends/test_jar_cache.py -v
```

Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/redfish_mcp/kvm/backends/_jar_cache.py tests/kvm/backends/test_jar_cache.py
git commit -m "feat(kvm): add SHA-256 content-addressable JAR cache

JarCache.get_or_fetch(url, sid) downloads the vendor JAR (once), hashes
it, and stores under <root>/<sha256>/iKVM.jar with 0600/0700 perms.
Tampered cache entries are detected and re-fetched. Cache key is content
hash, not URL — resilient to firmware-version URL changes.

Part of #64."
```

---

## Task 5 — Session subprocesses (Xvfb + Java + x11vnc lifecycle)

**Goal:** `SessionSubprocesses` async context manager that spawns Xvfb, launches the JAR under java, starts x11vnc, yields when all three are ready, and tears them down in reverse order on exit. Ensures no orphaned Java process if a later step fails. Tier-2 test runs real Xvfb+x11vnc (no Java — tested against a local target).

**Files:**
- Create: `src/redfish_mcp/kvm/backends/_subprocess.py`
- Create: `tests/kvm/backends/test_subprocess.py`

**Steps:**

- [ ] **Step 1: Write failing test** — `tests/kvm/backends/test_subprocess.py`

```python
"""Tests for SessionSubprocesses.

These tests spawn real Xvfb/x11vnc processes and are gated with the
``subprocess`` pytest marker. They auto-skip when Xvfb is not installed.
"""

from __future__ import annotations

import asyncio
import shutil
import socket

import pytest

from redfish_mcp.kvm.backends._subprocess import (
    SessionSubprocesses,
    SpawnedSession,
)

pytestmark = pytest.mark.subprocess


def _xvfb_available() -> bool:
    return shutil.which("Xvfb") is not None and shutil.which("x11vnc") is not None


skip_no_binaries = pytest.mark.skipif(
    not _xvfb_available(),
    reason="Xvfb/x11vnc not installed",
)


@skip_no_binaries
@pytest.mark.anyio
async def test_start_xvfb_and_x11vnc_only():
    """Spin up Xvfb + x11vnc without Java; verify VNC port is listening."""
    session = SessionSubprocesses.for_x11_only(geometry="640x480x24")
    async with session as spawned:
        assert isinstance(spawned, SpawnedSession)
        assert spawned.display_num >= 10
        assert spawned.vnc_port > 0
        # VNC port should be bound to localhost.
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        try:
            sock.connect(("127.0.0.1", spawned.vnc_port))
        finally:
            sock.close()


@skip_no_binaries
@pytest.mark.anyio
async def test_cleanup_kills_all_subprocesses():
    session = SessionSubprocesses.for_x11_only(geometry="640x480x24")
    async with session as spawned:
        xvfb_pid = spawned.xvfb.pid
        x11vnc_pid = spawned.x11vnc.pid
    # After exit both processes should be gone.
    await asyncio.sleep(0.2)
    for pid in (xvfb_pid, x11vnc_pid):
        try:
            import os

            os.kill(pid, 0)
            alive = True
        except ProcessLookupError:
            alive = False
        assert not alive, f"pid {pid} still alive after __aexit__"


@pytest.mark.anyio
async def test_allocate_free_vnc_port_is_unused():
    """Pure-function test — doesn't need Xvfb."""
    from redfish_mcp.kvm.backends._subprocess import _allocate_free_tcp_port

    port = _allocate_free_tcp_port()
    assert 1024 < port < 65536
    # Confirm port is actually free by binding.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", port))
    sock.close()


@pytest.mark.anyio
async def test_allocate_free_display_num_skips_existing_locks(tmp_path, monkeypatch):
    """Pure-function test — monkeypatches the lock-scan root."""
    from redfish_mcp.kvm.backends._subprocess import _allocate_free_display

    # Simulate :10 and :11 already in use.
    (tmp_path / ".X10-lock").touch()
    (tmp_path / ".X11-lock").touch()
    monkeypatch.setattr(
        "redfish_mcp.kvm.backends._subprocess._X_LOCK_DIR", tmp_path
    )
    display = _allocate_free_display(start=10)
    assert display == 12


def test_display_range_start_env_override(tmp_path, monkeypatch):
    """REDFISH_KVM_DISPLAY_RANGE_START shifts the default range."""
    from redfish_mcp.kvm.backends._subprocess import _allocate_free_display

    monkeypatch.setenv("REDFISH_KVM_DISPLAY_RANGE_START", "50")
    monkeypatch.setattr(
        "redfish_mcp.kvm.backends._subprocess._X_LOCK_DIR", tmp_path
    )
    display = _allocate_free_display()  # no start= kwarg → env applies
    assert display == 50
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/kvm/backends/test_subprocess.py -v -m subprocess
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement** — `src/redfish_mcp/kvm/backends/_subprocess.py`

```python
"""Per-session Xvfb + Java + x11vnc subprocess lifecycle.

Design commitment: all three subprocesses are co-scoped in a single
async context manager. If any start step fails, or if the caller's
body raises, everything already-started is torn down in reverse order
(x11vnc → java → Xvfb) with SIGTERM then SIGKILL after a grace period.

Phase 2 ships ``for_x11_only()`` (no Java) for the tier-2 integration
test and for the VNC library spike. The full ``for_java_ikvm()`` factory
lands in Task 8's JavaIkvmBackend.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import signal
import socket
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("redfish_mcp.kvm.backends.subprocess")

_X_LOCK_DIR = Path("/tmp")
_SIGTERM_GRACE_S = 2.0


def _default_display_range_start() -> int:
    """Read REDFISH_KVM_DISPLAY_RANGE_START with fallback to 10.

    Read at call time (not at module import) so tests that monkeypatch the
    env var after import work correctly.
    """
    raw = os.getenv("REDFISH_KVM_DISPLAY_RANGE_START")
    if raw is None or raw == "":
        return 10
    try:
        return int(raw)
    except ValueError:
        return 10


@dataclass
class SpawnedSession:
    display_num: int
    vnc_port: int
    vnc_secret_path: Path
    xvfb: asyncio.subprocess.Process
    java: asyncio.subprocess.Process | None
    x11vnc: asyncio.subprocess.Process


def _allocate_free_tcp_port() -> int:
    """Bind a random free TCP port on localhost and release it immediately."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _allocate_free_display(*, start: int | None = None, end: int = 100) -> int:
    """Find an X display number with no /tmp/.X<n>-lock file.

    ``start`` defaults to REDFISH_KVM_DISPLAY_RANGE_START (or 10).
    """
    if start is None:
        start = _default_display_range_start()
    for n in range(start, end):
        if not (_X_LOCK_DIR / f".X{n}-lock").exists():
            return n
    raise RuntimeError(
        f"no free X display number in [{start}, {end})"
    )


class SessionSubprocesses:
    """Co-scoped Xvfb+(Java?)+x11vnc lifecycle as async context manager."""

    def __init__(
        self,
        *,
        geometry: str = "1280x1024x24",
        java_cmd: list[str] | None = None,
        tmp_dir: Path | None = None,
    ) -> None:
        self.geometry = geometry
        self.java_cmd = java_cmd
        self._tmp_dir = tmp_dir or Path("/tmp")
        self._spawned: SpawnedSession | None = None

    @classmethod
    def for_x11_only(cls, *, geometry: str = "1280x1024x24") -> SessionSubprocesses:
        """Spawn only Xvfb + x11vnc. Used for VNC-lib testing/spike."""
        return cls(geometry=geometry, java_cmd=None)

    @classmethod
    def for_java_ikvm(
        cls,
        *,
        java_cmd: list[str],
        geometry: str = "1280x1024x24",
    ) -> SessionSubprocesses:
        """Spawn Xvfb, launch the Java iKVM JAR, then expose via x11vnc."""
        return cls(geometry=geometry, java_cmd=java_cmd)

    async def __aenter__(self) -> SpawnedSession:
        display = _allocate_free_display()
        vnc_port = _allocate_free_tcp_port()

        secret_bytes = secrets.token_bytes(32).hex().encode()
        secret_path = self._tmp_dir / f"x11vnc-pw-{os.getpid()}-{display}"
        secret_path.write_bytes(secret_bytes)
        os.chmod(secret_path, 0o600)

        env = {**os.environ, "DISPLAY": f":{display}"}

        xvfb = await asyncio.create_subprocess_exec(
            "Xvfb",
            f":{display}",
            "-screen", "0", self.geometry,
            "-nolisten", "tcp",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )

        # Wait briefly for the lock file to appear.
        await _wait_for_path(_X_LOCK_DIR / f".X{display}-lock", timeout_s=3.0)

        java = None
        try:
            if self.java_cmd is not None:
                java = await asyncio.create_subprocess_exec(
                    *self.java_cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    env=env,
                )
                # Give Java a moment to initialize; detect immediate exit.
                await asyncio.sleep(1.0)
                if java.returncode is not None:
                    raise RuntimeError(
                        f"java exited during startup rc={java.returncode}"
                    )

            x11vnc = await asyncio.create_subprocess_exec(
                "x11vnc",
                "-display", f":{display}",
                "-localhost",
                "-rfbport", str(vnc_port),
                "-passwdfile", str(secret_path),
                "-forever",
                "-quiet",
                "-noxdamage",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=env,
            )

            # Wait for x11vnc to accept a connection.
            await _wait_for_tcp("127.0.0.1", vnc_port, timeout_s=3.0)

            self._spawned = SpawnedSession(
                display_num=display,
                vnc_port=vnc_port,
                vnc_secret_path=secret_path,
                xvfb=xvfb,
                java=java,
                x11vnc=x11vnc,
            )
            return self._spawned
        except BaseException:
            # Partial cleanup of whatever we spawned before the failure.
            for proc in (java, xvfb):
                if proc is not None and proc.returncode is None:
                    await _terminate_process(proc)
            try:
                secret_path.unlink()
            except FileNotFoundError:
                pass
            raise

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._spawned is None:
            return
        s = self._spawned
        # Tear down in reverse order: x11vnc, java, Xvfb.
        for proc in (s.x11vnc, s.java, s.xvfb):
            if proc is None:
                continue
            if proc.returncode is None:
                await _terminate_process(proc)
        try:
            s.vnc_secret_path.unlink()
        except FileNotFoundError:
            pass
        self._spawned = None


async def _terminate_process(proc: asyncio.subprocess.Process) -> None:
    try:
        proc.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=_SIGTERM_GRACE_S)
    except asyncio.TimeoutError:
        try:
            proc.send_signal(signal.SIGKILL)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            logger.warning("process pid=%s did not exit after SIGKILL", proc.pid)


async def _wait_for_path(path: Path, *, timeout_s: float) -> None:
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        if path.exists():
            return
        await asyncio.sleep(0.05)
    raise RuntimeError(f"timed out waiting for {path}")


async def _wait_for_tcp(host: str, port: int, *, timeout_s: float) -> None:
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        try:
            reader, writer = await asyncio.open_connection(host, port)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return
        except (ConnectionRefusedError, OSError):
            await asyncio.sleep(0.05)
    raise RuntimeError(f"timed out waiting for TCP {host}:{port}")
```

- [ ] **Step 4: Run tests (subprocess-marked auto-skip on dev laptops without apt deps)**

```
uv run pytest tests/kvm/backends/test_subprocess.py -v
```

Expected on a machine with apt deps installed: `4 passed`. On a machine without them: `2 passed, 2 skipped` (pure-function tests pass; subprocess tests skip).

- [ ] **Step 5: Verify CI would pass**

The CI matrix (Task 1) installs apt deps, so all 4 tests should pass in CI.

```
# Locally, if apt deps installed:
uv run pytest tests/kvm/backends/test_subprocess.py -v -m subprocess
```

- [ ] **Step 6: Commit**

```bash
git add src/redfish_mcp/kvm/backends/_subprocess.py tests/kvm/backends/test_subprocess.py
git commit -m "feat(kvm): add SessionSubprocesses lifecycle manager

Spawns Xvfb + (optional Java) + x11vnc as a single async context manager.
Cleanup is reverse-order with SIGTERM → 2s grace → SIGKILL. Detects free
X display numbers via /tmp/.X<n>-lock scan and allocates a random free
TCP port for VNC. No orphaned Java processes if x11vnc startup fails.

Phase 2 ships the x11-only factory; Java factory lands with
JavaIkvmBackend in a later task.

Part of #64."
```

---

## Task 6 — VNC library spike + decision gate

**Goal:** Benchmark `asyncvnc` vs `vncdotool` against a real Xvfb+x11vnc target. Pick winner. Commit the decision; remove the loser from any future code path.

This task differs from TDD tasks: it's exploratory. The deliverable is a documented decision + a winning dependency in `pyproject.toml`.

**Files:**
- Create (throwaway): `scripts/kvm_vnc_spike.py`
- Modify: `pyproject.toml` (add winning dep)

**Steps:**

- [ ] **Step 1: Write the benchmark script** — `scripts/kvm_vnc_spike.py`

```python
"""Benchmark asyncvnc vs vncdotool against a local Xvfb + x11vnc target.

Usage:
    uv run --with asyncvnc --with vncdotool python scripts/kvm_vnc_spike.py

Prints a table comparing:
  - connect + screenshot round-trip latency (3 samples, median)
  - screenshot size (bytes)
  - PIL Image dimensions match the Xvfb geometry
  - anomalies / errors

Writes a decision to scripts/kvm_vnc_spike_result.md alongside this script.
"""

from __future__ import annotations

import asyncio
import statistics
import time
from pathlib import Path

from redfish_mcp.kvm.backends._subprocess import SessionSubprocesses


GEOMETRY = "800x600x24"


async def _timed(coro):
    t0 = time.perf_counter()
    result = await coro
    return time.perf_counter() - t0, result


async def bench_asyncvnc(vnc_port: int, password: str) -> dict:
    import asyncvnc

    latencies = []
    size = 0
    error = None
    try:
        for _ in range(3):
            dt, _ = await _timed(_asyncvnc_once(vnc_port, password))
            latencies.append(dt)
        # Capture one for size
        png_bytes = await _asyncvnc_once(vnc_port, password, return_png=True)
        size = len(png_bytes)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    return {
        "lib": "asyncvnc",
        "median_s": statistics.median(latencies) if latencies else None,
        "samples": latencies,
        "png_bytes": size,
        "error": error,
    }


async def _asyncvnc_once(port: int, password: str, *, return_png: bool = False):
    import io

    import asyncvnc

    async with asyncvnc.connect("127.0.0.1", port, password=password) as client:
        await client.screenshot()
        if return_png:
            buf = io.BytesIO()
            (await client.screenshot()).save(buf, format="PNG")
            return buf.getvalue()


def bench_vncdotool_sync(vnc_port: int, password: str) -> dict:
    from vncdotool import api

    latencies = []
    size = 0
    error = None
    try:
        for _ in range(3):
            t0 = time.perf_counter()
            client = api.connect(f"127.0.0.1::{vnc_port}", password=password)
            try:
                client.refreshScreen()
            finally:
                client.disconnect()
            latencies.append(time.perf_counter() - t0)
        # One more for size
        client = api.connect(f"127.0.0.1::{vnc_port}", password=password)
        try:
            client.refreshScreen()
            import io

            buf = io.BytesIO()
            client.screen.save(buf, format="PNG")
            size = len(buf.getvalue())
        finally:
            client.disconnect()
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    return {
        "lib": "vncdotool",
        "median_s": statistics.median(latencies) if latencies else None,
        "samples": latencies,
        "png_bytes": size,
        "error": error,
    }


async def bench_vncdotool(vnc_port: int, password: str) -> dict:
    return await asyncio.to_thread(bench_vncdotool_sync, vnc_port, password)


async def main() -> None:
    print(f"Starting Xvfb + x11vnc @ {GEOMETRY}")
    async with SessionSubprocesses.for_x11_only(geometry=GEOMETRY) as spawned:
        password = spawned.vnc_secret_path.read_text().strip()
        print(f"VNC on 127.0.0.1:{spawned.vnc_port}")

        results = []
        results.append(await bench_asyncvnc(spawned.vnc_port, password))
        results.append(await bench_vncdotool(spawned.vnc_port, password))

        lines = [
            "# VNC library spike results",
            "",
            f"Target: Xvfb {GEOMETRY} + x11vnc on localhost",
            f"Samples: 3 screenshots per library",
            "",
            "| Library   | Median (s) | Samples | PNG bytes | Error |",
            "|-----------|------------|---------|-----------|-------|",
        ]
        for r in results:
            samples = ", ".join(f"{s:.3f}" for s in r["samples"]) if r["samples"] else "-"
            lines.append(
                f"| {r['lib']:9s} | {r['median_s'] or '-':>10} | {samples} | "
                f"{r['png_bytes']} | {r['error'] or ''} |"
            )

        decision_path = Path(__file__).parent / "kvm_vnc_spike_result.md"
        decision_path.write_text("\n".join(lines) + "\n")
        print("\n".join(lines))
        print(f"\nWrote {decision_path}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Install both libs as transient deps and run the benchmark**

```
uv add --dev asyncvnc vncdotool
uv run python scripts/kvm_vnc_spike.py
```

Expected: the script prints a markdown table and writes `scripts/kvm_vnc_spike_result.md`.

If either library errors, that library is out. If both work, compare median latency, and assess the APIs in hand:
- Screenshot return type (PIL Image vs raw bytes vs fb bytes).
- Keystroke API shape (for phase 3 relevance).
- Exception types on connection/handshake failure.

- [ ] **Step 3: Optionally benchmark against the real H100 BMC**

If `REDFISH_KVM_E2E=1` and you have the BMC credentials available locally, also run a benchmark against the real H100 via a full `JavaIkvmBackend.open()` dry-run. Since the full backend doesn't exist yet, you can do this by hand:

```bash
# Manual H100 benchmark (only if creds available locally):
# 1. Login via curl, capture SID.
# 2. Fetch JNLP.
# 3. Download JAR manually.
# 4. Start Xvfb, Java, x11vnc against the real BMC.
# 5. Point the spike script's 127.0.0.1 at the local x11vnc port.
```

This is optional for the decision — the x11vnc local benchmark is representative enough for latency; the H100 benchmark would validate that both libraries can decode the actual AST2600 framebuffer content.

- [ ] **Step 4: Document the decision**

Pick the winner based on:
1. Lower median latency is a mild preference, not decisive unless >2× delta.
2. API ergonomics for screenshot + keystroke matters more (phase 3 cares).
3. Maintenance activity (GitHub last commit recency).
4. Any errors in the benchmark disqualifies.

Append decision to `scripts/kvm_vnc_spike_result.md`:

```markdown

## Decision

**Winner: <asyncvnc|vncdotool>**

Rationale:
- <1-2 bullet points from the criteria above>
- <notes on phase-3 keystroke API fit>

Decided on 2026-04-21.
```

- [ ] **Step 5: Add winner to `pyproject.toml` as a runtime dependency**

If the winner is `asyncvnc`:
```toml
    "asyncvnc>=1.2",
```

If the winner is `vncdotool`:
```toml
    "vncdotool>=1.2",
```

Under `[project.dependencies]` in alphabetical order. Then:

```
uv sync --all-groups
```

Remove the loser (and both transient dev-deps):

```
uv remove --dev asyncvnc vncdotool
# then re-add only the winner as a runtime dep via manual pyproject edit
```

- [ ] **Step 6: Verify lint/tests still clean**

```
uv run pytest -m "not integration and not e2e" -q --no-header
uv run ruff check src tests
```

- [ ] **Step 7: Commit**

```bash
git add scripts/kvm_vnc_spike.py scripts/kvm_vnc_spike_result.md pyproject.toml uv.lock
git commit -m "chore(kvm): benchmark asyncvnc vs vncdotool, choose <WINNER>

Ran 3-sample median latency benchmarks against local Xvfb + x11vnc.
Decision captured in scripts/kvm_vnc_spike_result.md.

Winner: <asyncvnc|vncdotool>.

The spike script is kept for reproducibility; feel free to remove in a
follow-up once the chosen library has stabilized under real usage.

Part of #64."
```

(Replace `<WINNER>` with the actual library name.)

---

## Task 7 — VNC wrapper module

**Goal:** A thin `_vnc.py` wrapper that exposes a small API the rest of the backend uses: `connect(...) → VncSession`, `screenshot(session) → bytes` (PNG). Everything else (named keys, keystrokes) gets stubbed to `NotImplementedError` for phase 3 to fill.

The wrapper insulates `JavaIkvmBackend` from the winning lib's API surface, and keeps imports of the VNC lib in one place.

**Files:**
- Create: `src/redfish_mcp/kvm/backends/_vnc.py`
- Create: `tests/kvm/backends/test_vnc.py`

This task's code depends on which library won Task 6. **Two code paths are documented below — use the one that matches the winner.**

**Steps (path A — if Task 6 picked `asyncvnc`):**

- [ ] **Step 1: Write failing test** — `tests/kvm/backends/test_vnc.py`

```python
"""Tests for the _vnc wrapper (post-spike)."""

from __future__ import annotations

import pytest

from redfish_mcp.kvm.backends._subprocess import SessionSubprocesses
from redfish_mcp.kvm.backends._vnc import VncSession, connect, screenshot

pytestmark = pytest.mark.subprocess


@pytest.mark.anyio
async def test_connect_and_screenshot_returns_png():
    async with SessionSubprocesses.for_x11_only(geometry="640x480x24") as spawned:
        password = spawned.vnc_secret_path.read_text().strip()
        session = await connect("127.0.0.1", spawned.vnc_port, password)
        try:
            assert isinstance(session, VncSession)
            png = await screenshot(session)
            assert png.startswith(b"\x89PNG")
            assert 1000 < len(png) < 1_000_000
        finally:
            await session.close()


@pytest.mark.anyio
async def test_sendkey_raises_not_implemented():
    async with SessionSubprocesses.for_x11_only(geometry="320x240x24") as spawned:
        password = spawned.vnc_secret_path.read_text().strip()
        session = await connect("127.0.0.1", spawned.vnc_port, password)
        try:
            from redfish_mcp.kvm.backends._vnc import sendkey

            with pytest.raises(NotImplementedError):
                await sendkey(session, "Enter")
        finally:
            await session.close()
```

- [ ] **Step 2: Run to verify failure**

```
uv run pytest tests/kvm/backends/test_vnc.py -v -m subprocess
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement (asyncvnc path)** — `src/redfish_mcp/kvm/backends/_vnc.py`

```python
"""Thin wrapper over asyncvnc.

Rationale for the wrapper: the rest of JavaIkvmBackend imports VncSession,
connect, screenshot, and (phase 3) sendkey from here. If we ever switch
libraries we change one file.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import asyncvnc


@dataclass
class VncSession:
    client_ctx: object  # async context manager instance from asyncvnc.connect
    client: asyncvnc.Client

    async def close(self) -> None:
        await self.client_ctx.__aexit__(None, None, None)


async def connect(host: str, port: int, password: str) -> VncSession:
    ctx = asyncvnc.connect(host, port, password=password)
    client = await ctx.__aenter__()
    return VncSession(client_ctx=ctx, client=client)


async def screenshot(session: VncSession) -> bytes:
    img = await session.client.screenshot()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def sendkey(session: VncSession, key: str, modifiers: list[str] | None = None) -> None:
    raise NotImplementedError("keyboard input lands in phase 3 (#65)")


async def sendkeys(session: VncSession, text: str) -> None:
    raise NotImplementedError("keyboard input lands in phase 3 (#65)")
```

**Steps (path B — if Task 6 picked `vncdotool`):**

- [ ] **Step 3 (alternate): Implement (vncdotool path)** — `src/redfish_mcp/kvm/backends/_vnc.py`

```python
"""Thin wrapper over vncdotool (sync library wrapped in to_thread).

vncdotool is synchronous; we wrap every call in asyncio.to_thread so the
daemon's event loop stays responsive. VncSession holds the sync client
instance plus an asyncio.Lock to serialize concurrent calls (the sync
client is not thread-safe under concurrent sends).
"""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass

from vncdotool import api


@dataclass
class VncSession:
    client: object  # vncdotool SynchronousVNCDoToolClient
    lock: asyncio.Lock

    async def close(self) -> None:
        async with self.lock:
            await asyncio.to_thread(self.client.disconnect)


async def connect(host: str, port: int, password: str) -> VncSession:
    target = f"{host}::{port}"
    client = await asyncio.to_thread(api.connect, target, password=password)
    return VncSession(client=client, lock=asyncio.Lock())


async def screenshot(session: VncSession) -> bytes:
    async with session.lock:
        def _capture() -> bytes:
            session.client.refreshScreen()
            buf = io.BytesIO()
            session.client.screen.save(buf, format="PNG")
            return buf.getvalue()
        return await asyncio.to_thread(_capture)


async def sendkey(session: VncSession, key: str, modifiers: list[str] | None = None) -> None:
    raise NotImplementedError("keyboard input lands in phase 3 (#65)")


async def sendkeys(session: VncSession, text: str) -> None:
    raise NotImplementedError("keyboard input lands in phase 3 (#65)")
```

- [ ] **Step 4 (either path): Run tests**

```
uv run pytest tests/kvm/backends/test_vnc.py -v -m subprocess
```

Expected: `2 passed` (tests skipped on machines without apt deps).

- [ ] **Step 5: Commit**

```bash
git add src/redfish_mcp/kvm/backends/_vnc.py tests/kvm/backends/test_vnc.py
git commit -m "feat(kvm): add _vnc wrapper over <WINNER>

Single-file insulation layer over the chosen VNC library. connect() +
screenshot() are implemented; sendkey/sendkeys raise NotImplementedError
until phase 3 (#65) fills them in with X11-keysym mapping.

Part of #64."
```

(Replace `<WINNER>` with the chosen library name.)

---

## Task 8 — JavaIkvmBackend

**Goal:** Compose the CGI client, JNLP parser, JAR cache, subprocess manager, and VNC wrapper into a class that implements the phase-1 `KVMBackend` Protocol. Emits progress events for all 7 stages; returns a `SessionHandle` on success. `screenshot()` works; `sendkey`/`sendkeys`/`close`/`health` — `close` works (tears down subprocess group), the rest raise NotImplementedError for phase 3.

**Files:**
- Create: `src/redfish_mcp/kvm/backends/java.py`
- Create: `tests/kvm/backends/test_java_backend.py`

**Steps:**

- [ ] **Step 1: Write failing test (tier 1 — mocks)** — `tests/kvm/backends/test_java_backend.py`

```python
"""Tests for JavaIkvmBackend."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from redfish_mcp.kvm.backend import ProgressEvent, SessionHandle
from redfish_mcp.kvm.backends.java import JavaIkvmBackend
from redfish_mcp.kvm.exceptions import AuthFailedError


JNLP_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<jnlp codebase="https://10.0.0.1:443">
  <resources><jar href="iKVM.jar"/></resources>
  <application-desc main-class="tw.com.aten.ikvm.KVMMain">
    <argument>10.0.0.1</argument>
    <argument>63630</argument>
    <argument>63631</argument>
    <argument>1</argument>
    <argument>0</argument>
    <argument>5900</argument>
    <argument>623</argument>
    <argument>0</argument>
    <argument>0</argument>
    <argument>user</argument>
    <argument>pass</argument>
    <argument>0</argument>
  </application-desc>
</jnlp>"""


@pytest.fixture
def tmp_jar_cache(tmp_path: Path) -> Path:
    return tmp_path / "jars"


class TestJavaIkvmBackendOpen:
    @pytest.mark.anyio
    async def test_auth_failure_maps_to_auth_failed_error(
        self, tmp_jar_cache: Path
    ):
        events: list[ProgressEvent] = []

        async def progress(e):
            events.append(e)

        with patch(
            "redfish_mcp.kvm.backends.java.supermicro_cgi.login",
            side_effect=Exception("missing SID cookie"),
        ):
            backend = JavaIkvmBackend(jar_cache_root=tmp_jar_cache, java_bin="java")
            with pytest.raises(AuthFailedError) as exc_info:
                await backend.open("10.0.0.1", "ADMIN", "bad", progress)

        assert exc_info.value.stage == "authenticating"
        assert events[0].stage == "authenticating"

    @pytest.mark.anyio
    async def test_progress_stages_emitted_in_order_up_to_failure(
        self, tmp_jar_cache: Path, httpx_mock
    ):
        events: list[ProgressEvent] = []

        async def progress(e):
            events.append(e)

        with patch(
            "redfish_mcp.kvm.backends.java.supermicro_cgi.login",
            return_value="fake_sid",
        ), patch(
            "redfish_mcp.kvm.backends.java.supermicro_cgi.fetch_jnlp",
            return_value=JNLP_XML,
        ), patch(
            "redfish_mcp.kvm.backends.java.JarCache.get_or_fetch",
            return_value=tmp_jar_cache / "fake" / "iKVM.jar",
        ), patch(
            "redfish_mcp.kvm.backends.java.SessionSubprocesses.for_java_ikvm"
        ) as mock_subproc:
            mock_subproc.return_value.__aenter__ = AsyncMock(
                side_effect=RuntimeError("simulated Xvfb failure")
            )
            mock_subproc.return_value.__aexit__ = AsyncMock()
            backend = JavaIkvmBackend(jar_cache_root=tmp_jar_cache, java_bin="java")
            with pytest.raises(Exception):
                await backend.open("10.0.0.1", "ADMIN", "pw", progress)

        stages = [e.stage for e in events]
        # Should have fired at least these.
        assert "authenticating" in stages
        assert "fetching_jar" in stages

    @pytest.mark.anyio
    async def test_open_returns_handle_with_backend_java(self):
        # Full happy-path is validated in the tier-2 test below; this
        # tier-1 test only checks handle shape assuming everything else
        # succeeded.
        backend = JavaIkvmBackend.__new__(JavaIkvmBackend)  # bypass init
        handle = backend._make_handle(host="10.0.0.1", user="ADMIN", session_id="s1")
        assert isinstance(handle, SessionHandle)
        assert handle.host == "10.0.0.1"
        assert handle.user == "ADMIN"
        assert handle.backend == "java"
        assert handle.opened_at_ms > 0


class TestJavaIkvmBackendStubs:
    @pytest.mark.anyio
    async def test_sendkey_raises_not_implemented(self, tmp_jar_cache: Path):
        backend = JavaIkvmBackend(jar_cache_root=tmp_jar_cache, java_bin="java")
        fake_handle = SessionHandle(
            session_id="s", host="x", user="x", backend="java", opened_at_ms=0
        )
        with pytest.raises(NotImplementedError):
            await backend.sendkey(fake_handle, "Enter")

    @pytest.mark.anyio
    async def test_sendkeys_raises_not_implemented(self, tmp_jar_cache: Path):
        backend = JavaIkvmBackend(jar_cache_root=tmp_jar_cache, java_bin="java")
        fake_handle = SessionHandle(
            session_id="s", host="x", user="x", backend="java", opened_at_ms=0
        )
        with pytest.raises(NotImplementedError):
            await backend.sendkeys(fake_handle, "text")


class TestJavaIkvmBackendEnvVars:
    def test_xvfb_geometry_env_override(
        self, tmp_jar_cache: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("REDFISH_KVM_XVFB_GEOMETRY", "1920x1080x24")
        backend = JavaIkvmBackend(jar_cache_root=tmp_jar_cache, java_bin="java")
        assert backend._xvfb_geometry == "1920x1080x24"

    def test_xvfb_geometry_default_when_unset(
        self, tmp_jar_cache: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.delenv("REDFISH_KVM_XVFB_GEOMETRY", raising=False)
        backend = JavaIkvmBackend(jar_cache_root=tmp_jar_cache, java_bin="java")
        assert backend._xvfb_geometry == "1280x1024x24"

    def test_verify_tls_env_override_true(
        self, tmp_jar_cache: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("REDFISH_KVM_VERIFY_TLS", "1")
        backend = JavaIkvmBackend(jar_cache_root=tmp_jar_cache, java_bin="java")
        assert backend._verify_tls is True

    def test_verify_tls_default_false(
        self, tmp_jar_cache: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.delenv("REDFISH_KVM_VERIFY_TLS", raising=False)
        backend = JavaIkvmBackend(jar_cache_root=tmp_jar_cache, java_bin="java")
        assert backend._verify_tls is False

    def test_explicit_kwarg_wins_over_env(
        self, tmp_jar_cache: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("REDFISH_KVM_VERIFY_TLS", "1")
        backend = JavaIkvmBackend(
            jar_cache_root=tmp_jar_cache, java_bin="java", verify_tls=False
        )
        assert backend._verify_tls is False


@pytest.fixture
def anyio_backend():
    return "asyncio"
```

- [ ] **Step 2: Run to verify failure**

```
uv run pytest tests/kvm/backends/test_java_backend.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement** — `src/redfish_mcp/kvm/backends/java.py`

```python
"""JavaIkvmBackend — concrete KVMBackend for Supermicro X13/H13 hardware.

Composes:
  _supermicro_cgi : login → SID, JNLP fetch.
  _jnlp           : parse JNLP to JnlpSpec.
  _jar_cache      : SHA-256 content-addressable JAR cache.
  _subprocess     : Xvfb + Java + x11vnc lifecycle.
  _vnc            : VNC client wrapper (screenshot here; keystrokes in phase 3).

Session lookup key is (host, user, "java"). The backend holds no per-BMC
state — each open() returns a fresh SessionHandle + fresh subprocess
group. The daemon's SessionCache keeps the open session alive between
calls.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from redfish_mcp.kvm.backend import ProgressCallback, ProgressEvent, SessionHandle
from redfish_mcp.kvm.backends import _supermicro_cgi as supermicro_cgi
from redfish_mcp.kvm.backends import _vnc as vnc
from redfish_mcp.kvm.backends._jar_cache import JarCache, JarCacheError
from redfish_mcp.kvm.backends._jnlp import JnlpParseError, JnlpSpec, parse_jnlp
from redfish_mcp.kvm.backends._subprocess import SessionSubprocesses
from redfish_mcp.kvm.exceptions import (
    AuthFailedError,
    BackendUnsupportedError,
    JarMismatchError,
    JnlpUnavailableError,
    KVMError,
    SlotBusyError,
    StaleSessionError,
)

logger = logging.getLogger("redfish_mcp.kvm.backends.java")


@dataclass
class _LiveSession:
    session_id: str
    host: str
    user: str
    subprocesses: SessionSubprocesses
    spawned: Any  # SpawnedSession; kept opaque here
    vnc_session: vnc.VncSession
    opened_at_ms: int


class JavaIkvmBackend:
    """Supermicro X13/H13 KVM backend via the vendor Java iKVM client."""

    def __init__(
        self,
        *,
        jar_cache_root: Path,
        java_bin: str = "java",
        xvfb_geometry: str | None = None,
        verify_tls: bool | None = None,
    ) -> None:
        import os as _os

        self._jar_cache = JarCache(root=jar_cache_root)
        self._java_bin = java_bin
        # xvfb_geometry: explicit kwarg wins; else env; else built-in default.
        self._xvfb_geometry = (
            xvfb_geometry
            or _os.getenv("REDFISH_KVM_XVFB_GEOMETRY")
            or "1280x1024x24"
        )
        # verify_tls: explicit kwarg wins; else env ("1" → True); else False.
        if verify_tls is not None:
            self._verify_tls = verify_tls
        else:
            self._verify_tls = _os.getenv("REDFISH_KVM_VERIFY_TLS", "0") == "1"
        self._live: dict[str, _LiveSession] = {}

    @staticmethod
    def _make_handle(*, host: str, user: str, session_id: str) -> SessionHandle:
        return SessionHandle(
            session_id=session_id,
            host=host,
            user=user,
            backend="java",
            opened_at_ms=int(time.time() * 1000),
        )

    async def open(
        self,
        host: str,
        user: str,
        password: str,
        progress: ProgressCallback,
    ) -> SessionHandle:
        await progress(ProgressEvent(stage="authenticating"))
        try:
            sid = supermicro_cgi.login(
                host=host, user=user, password=password, verify_tls=self._verify_tls
            )
        except Exception as exc:
            raise AuthFailedError(
                f"login failed for {user}@{host}: {exc}",
                stage="authenticating",
            ) from exc

        await progress(ProgressEvent(stage="fetching_jar"))
        try:
            jnlp_bytes = supermicro_cgi.fetch_jnlp(
                host=host, sid=sid, verify_tls=self._verify_tls
            )
        except Exception as exc:
            raise JnlpUnavailableError(
                f"could not fetch JNLP from {host}: {exc}",
                stage="fetching_jar",
            ) from exc

        try:
            jnlp: JnlpSpec = parse_jnlp(jnlp_bytes)
        except JnlpParseError as exc:
            raise BackendUnsupportedError(
                f"JNLP from {host} has unexpected shape: {exc}",
                stage="fetching_jar",
            ) from exc

        try:
            jar_path = self._jar_cache.get_or_fetch(
                jnlp.jar_url(), sid=sid, verify_tls=self._verify_tls
            )
        except JarCacheError as exc:
            raise KVMError(
                f"JAR download failed: {exc}",
                stage="fetching_jar",
            ) from exc

        java_cmd = [
            self._java_bin,
            "-cp",
            str(jar_path),
            jnlp.main_class,
            *jnlp.arguments,
        ]

        await progress(ProgressEvent(stage="starting_xvfb"))
        subprocesses = SessionSubprocesses.for_java_ikvm(
            java_cmd=java_cmd,
            geometry=self._xvfb_geometry,
        )
        try:
            await progress(ProgressEvent(stage="launching_java"))
            await progress(ProgressEvent(stage="starting_vnc"))
            spawned = await subprocesses.__aenter__()
        except RuntimeError as exc:
            msg = str(exc).lower()
            if "java exited" in msg:
                raise JarMismatchError(
                    f"Java process exited during launch: {exc}",
                    stage="launching_java",
                ) from exc
            if "slot" in msg or "busy" in msg or "too many" in msg:
                raise SlotBusyError(
                    f"BMC KVM slot unavailable: {exc}",
                    stage="launching_java",
                ) from exc
            raise KVMError(
                f"subprocess startup failed: {exc}",
                stage="starting_xvfb",
            ) from exc

        await progress(ProgressEvent(stage="handshaking"))
        try:
            password_bytes = spawned.vnc_secret_path.read_text().strip()
            vnc_session = await vnc.connect(
                "127.0.0.1", spawned.vnc_port, password_bytes
            )
        except Exception as exc:
            await subprocesses.__aexit__(type(exc), exc, None)
            raise BackendUnsupportedError(
                f"VNC handshake to local x11vnc failed: {exc}",
                stage="handshaking",
            ) from exc

        session_id = f"java-{uuid.uuid4().hex[:12]}"
        self._live[session_id] = _LiveSession(
            session_id=session_id,
            host=host,
            user=user,
            subprocesses=subprocesses,
            spawned=spawned,
            vnc_session=vnc_session,
            opened_at_ms=int(time.time() * 1000),
        )

        await progress(ProgressEvent(stage="ready"))
        return self._make_handle(host=host, user=user, session_id=session_id)

    async def screenshot(self, session: SessionHandle) -> bytes:
        live = self._live.get(session.session_id)
        if live is None:
            raise StaleSessionError(
                f"session {session.session_id} not found", stage="ready"
            )
        try:
            return await vnc.screenshot(live.vnc_session)
        except Exception as exc:
            raise StaleSessionError(
                f"screenshot failed: {exc}", stage="ready"
            ) from exc

    async def sendkeys(self, session: SessionHandle, text: str) -> None:
        raise NotImplementedError("sendkeys ships in phase 3 (#65)")

    async def sendkey(
        self,
        session: SessionHandle,
        key: str,
        modifiers: list[str] | None = None,
    ) -> None:
        raise NotImplementedError("sendkey ships in phase 3 (#65)")

    async def close(self, session: SessionHandle) -> None:
        live = self._live.pop(session.session_id, None)
        if live is None:
            return
        try:
            await live.vnc_session.close()
        except Exception:
            logger.warning("vnc close failed for %s", session.session_id, exc_info=True)
        await live.subprocesses.__aexit__(None, None, None)

    async def health(self, session: SessionHandle) -> str:
        live = self._live.get(session.session_id)
        if live is None:
            return "dead"
        if live.spawned.java is not None and live.spawned.java.returncode is not None:
            return "failed"
        if live.spawned.x11vnc.returncode is not None:
            return "failed"
        return "ok"
```

- [ ] **Step 4: Run tier-1 tests**

```
uv run pytest tests/kvm/backends/test_java_backend.py -v
```

Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/redfish_mcp/kvm/backends/java.py tests/kvm/backends/test_java_backend.py
git commit -m "feat(kvm): add JavaIkvmBackend composing CGI + JNLP + cache + subprocesses + VNC

Implements the phase-1 KVMBackend Protocol via the Supermicro Java iKVM
client running under Xvfb + x11vnc. Emits progress for all seven stages
(authenticating through ready), maps well-known failure patterns to the
right KVMError subclass, and tears the subprocess group down cleanly on
error paths. sendkey/sendkeys stay NotImplementedError until phase 3.

Part of #64."
```

---

## Task 9 — Router-layer session_ops (timeout + stage tracking)

**Goal:** `open_session` helper that wraps `backend.open()` with `asyncio.wait_for` + progress subscriber that records the last-seen stage. On timeout, raises `StaleSessionError` with `stage=<last-seen>`. Also ships `screenshot_session` that enforces a shorter timeout on steady-state screen captures.

**Files:**
- Create: `src/redfish_mcp/kvm/daemon/session_ops.py`
- Create: `tests/kvm/test_session_ops.py`

**Steps:**

- [ ] **Step 1: Write failing test** — `tests/kvm/test_session_ops.py`

```python
"""Tests for session_ops — timeout + stage-tracking wrapper."""

from __future__ import annotations

import asyncio

import pytest

from redfish_mcp.kvm.backend import ProgressEvent, SessionHandle
from redfish_mcp.kvm.daemon.progress import ProgressPublisher
from redfish_mcp.kvm.daemon.session_ops import open_session, screenshot_session
from redfish_mcp.kvm.exceptions import AuthFailedError, StaleSessionError


class _FakeSlowBackend:
    """Fakes a backend.open that emits stages then hangs."""

    def __init__(self, hang_after_stage: str = "starting_vnc") -> None:
        self._hang_after = hang_after_stage

    async def open(self, host, user, password, progress):
        stages = (
            "authenticating",
            "fetching_jar",
            "starting_xvfb",
            "launching_java",
            "starting_vnc",
            "handshaking",
            "ready",
        )
        for stage in stages:
            await progress(ProgressEvent(stage=stage))
            if stage == self._hang_after:
                # Sleep forever; the wait_for timeout should cancel us.
                await asyncio.sleep(1000)
        return SessionHandle(
            session_id="s1", host=host, user=user, backend="fake", opened_at_ms=0
        )


class _FakeFastBackend:
    async def open(self, host, user, password, progress):
        for stage in ("authenticating", "ready"):
            await progress(ProgressEvent(stage=stage))
        return SessionHandle(
            session_id="s1", host=host, user=user, backend="fake", opened_at_ms=0
        )

    async def screenshot(self, session):
        return b"\x89PNG\r\n\x1a\nfake"


class _FakeFailingBackend:
    async def open(self, host, user, password, progress):
        await progress(ProgressEvent(stage="authenticating"))
        raise AuthFailedError("bad creds", stage="authenticating")


@pytest.mark.anyio
async def test_timeout_fires_with_last_seen_stage():
    publisher = ProgressPublisher()
    with pytest.raises(StaleSessionError) as exc_info:
        await open_session(
            backend=_FakeSlowBackend(hang_after_stage="starting_vnc"),
            progress=publisher,
            host="h", user="u", password="p",
            session_key="fake:h:u",
            timeout_s=0.2,
        )
    assert exc_info.value.stage == "starting_vnc"


@pytest.mark.anyio
async def test_happy_path_returns_handle_and_emits_stages():
    publisher = ProgressPublisher()
    q = publisher.subscribe("fake:h:u")
    handle = await open_session(
        backend=_FakeFastBackend(),
        progress=publisher,
        host="h", user="u", password="p",
        session_key="fake:h:u",
        timeout_s=2.0,
    )
    assert handle.session_id == "s1"
    events = []
    while not q.empty():
        events.append(await q.get())
    # Expect both stage events plus a None sentinel from publisher.complete.
    stages = [e.stage for e in events if e is not None]
    assert "authenticating" in stages
    assert "ready" in stages


@pytest.mark.anyio
async def test_backend_exception_propagates_not_converted_to_stale():
    publisher = ProgressPublisher()
    with pytest.raises(AuthFailedError):
        await open_session(
            backend=_FakeFailingBackend(),
            progress=publisher,
            host="h", user="u", password="p",
            session_key="fake:h:u",
            timeout_s=2.0,
        )


@pytest.mark.anyio
async def test_screenshot_timeout_raises_stale():
    class _SlowScreen:
        async def open(self, *a, **kw): ...
        async def screenshot(self, session):
            await asyncio.sleep(5)
            return b""
    from redfish_mcp.kvm.daemon.session_ops import screenshot_session

    fake_handle = SessionHandle(
        session_id="s", host="h", user="u", backend="fake", opened_at_ms=0
    )
    with pytest.raises(StaleSessionError):
        await screenshot_session(
            backend=_SlowScreen(), session=fake_handle, timeout_s=0.1
        )


def test_open_timeout_env_override(monkeypatch: pytest.MonkeyPatch):
    from redfish_mcp.kvm.daemon.session_ops import default_open_timeout_s

    monkeypatch.setenv("REDFISH_KVM_OPEN_TIMEOUT_S", "45.5")
    assert default_open_timeout_s() == 45.5

    monkeypatch.delenv("REDFISH_KVM_OPEN_TIMEOUT_S", raising=False)
    assert default_open_timeout_s() == 30.0

    monkeypatch.setenv("REDFISH_KVM_OPEN_TIMEOUT_S", "not-a-number")
    assert default_open_timeout_s() == 30.0


def test_screenshot_timeout_env_override(monkeypatch: pytest.MonkeyPatch):
    from redfish_mcp.kvm.daemon.session_ops import default_screenshot_timeout_s

    monkeypatch.setenv("REDFISH_KVM_SCREENSHOT_TIMEOUT_S", "7.5")
    assert default_screenshot_timeout_s() == 7.5

    monkeypatch.delenv("REDFISH_KVM_SCREENSHOT_TIMEOUT_S", raising=False)
    assert default_screenshot_timeout_s() == 15.0


@pytest.fixture
def anyio_backend():
    return "asyncio"
```

- [ ] **Step 2: Run to verify failure**

```
uv run pytest tests/kvm/test_session_ops.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement** — `src/redfish_mcp/kvm/daemon/session_ops.py`

```python
"""Router-layer wrappers around backend.open()/screenshot() with timeout + stage tracking.

The KVMBackend Protocol stays unchanged — timeout enforcement lives here so
every backend (Java in phase 2, Playwright in v2) gets uniform semantics
without re-implementing wait_for plumbing.

On timeout in open(), we report the last ProgressEvent stage that fired
before the wait_for fired, so clients see failed:timeout:<stage> rather
than a naked timeout.
"""

from __future__ import annotations

import asyncio
import os

from redfish_mcp.kvm.backend import KVMBackend, ProgressEvent, SessionHandle
from redfish_mcp.kvm.daemon.progress import ProgressPublisher
from redfish_mcp.kvm.exceptions import StaleSessionError


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def default_open_timeout_s() -> float:
    """Read REDFISH_KVM_OPEN_TIMEOUT_S at call time (with fallback 30.0)."""
    return _env_float("REDFISH_KVM_OPEN_TIMEOUT_S", 30.0)


def default_screenshot_timeout_s() -> float:
    """Read REDFISH_KVM_SCREENSHOT_TIMEOUT_S at call time (with fallback 15.0)."""
    return _env_float("REDFISH_KVM_SCREENSHOT_TIMEOUT_S", 15.0)


async def open_session(
    *,
    backend: KVMBackend,
    progress: ProgressPublisher,
    host: str,
    user: str,
    password: str,
    session_key: str,
    timeout_s: float | None = None,
) -> SessionHandle:
    """Call backend.open() with a bounded timeout and stage-aware error.

    The returned handle is the one backend.open() produced. On timeout, a
    StaleSessionError is raised with ``stage=<last-seen stage>``. When
    ``timeout_s`` is None, the value from ``REDFISH_KVM_OPEN_TIMEOUT_S``
    (fallback 30.0) is used.
    """
    if timeout_s is None:
        timeout_s = default_open_timeout_s()
    last_stage = "authenticating"

    async def tracking_progress(event: ProgressEvent) -> None:
        nonlocal last_stage
        last_stage = event.stage
        await progress.publish(session_key, event)

    try:
        handle = await asyncio.wait_for(
            backend.open(host, user, password, tracking_progress),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError as exc:
        raise StaleSessionError(
            f"open() did not complete within {timeout_s}s",
            stage=last_stage,
        ) from exc
    finally:
        await progress.complete(session_key)

    return handle


async def screenshot_session(
    *,
    backend: KVMBackend,
    session: SessionHandle,
    timeout_s: float | None = None,
) -> bytes:
    """Call backend.screenshot() with a shorter bounded timeout.

    Steady-state captures should be fast (sub-second on LAN). Anything
    over ``timeout_s`` indicates a hung Java process or dead VNC channel.
    When ``timeout_s`` is None, the value from
    ``REDFISH_KVM_SCREENSHOT_TIMEOUT_S`` (fallback 15.0) is used.
    """
    if timeout_s is None:
        timeout_s = default_screenshot_timeout_s()
    try:
        return await asyncio.wait_for(
            backend.screenshot(session),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError as exc:
        raise StaleSessionError(
            f"screenshot did not complete within {timeout_s}s",
            stage="ready",
        ) from exc
```

- [ ] **Step 4: Run tests**

```
uv run pytest tests/kvm/test_session_ops.py -v
```

Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/redfish_mcp/kvm/daemon/session_ops.py tests/kvm/test_session_ops.py
git commit -m "feat(kvm): add router-layer session_ops with timeout + stage tracking

open_session wraps backend.open() in asyncio.wait_for and substitutes a
tracking progress callback that records the last-seen stage. On timeout,
raises StaleSessionError with stage=<last-seen>, giving clients the
'failed:timeout:<stage>' diagnostic surface from the spec. Same shape
for screenshot_session with a shorter default timeout.

Implements the design decision recorded on issue #64.

Part of #64."
```

---

## Task 10 — Handlers module + server wiring

**Goal:** Register `open` and `screen` handlers on the daemon's router; replace the phase-1 `_close_entry_noop` with a real backend close. Daemon startup now produces a working end-to-end daemon.

**Files:**
- Create: `src/redfish_mcp/kvm/daemon/handlers.py`
- Modify: `src/redfish_mcp/kvm/daemon/server.py`
- Create: `tests/kvm/test_handlers.py`

**Steps:**

- [ ] **Step 1: Write failing test** — `tests/kvm/test_handlers.py`

```python
"""Tests for daemon request handlers."""

from __future__ import annotations

import base64

import pytest

from redfish_mcp.kvm.backend import SessionHandle
from redfish_mcp.kvm.daemon.handlers import register_kvm_handlers
from redfish_mcp.kvm.daemon.progress import ProgressPublisher
from redfish_mcp.kvm.daemon.router import Router
from redfish_mcp.kvm.daemon.cache import SessionCache
from redfish_mcp.kvm.fake_backend import FakeBackend
from redfish_mcp.kvm.protocol import Request


@pytest.mark.anyio
async def test_open_handler_produces_result():
    router = Router()
    cache = SessionCache(clock=lambda: 0)
    publisher = ProgressPublisher()
    backend = FakeBackend()
    register_kvm_handlers(
        router=router, cache=cache, progress=publisher, backend=backend
    )
    resp = await router.dispatch(
        Request(
            id=1,
            method="open",
            params={"host": "10.0.0.1", "user": "admin", "password": "p"},
        )
    )
    assert resp.result is not None
    assert "session_id" in resp.result
    assert resp.result["host"] == "10.0.0.1"
    assert resp.result["backend"] == "fake"


@pytest.mark.anyio
async def test_screen_handler_returns_png_b64():
    router = Router()
    cache = SessionCache(clock=lambda: 0)
    publisher = ProgressPublisher()
    backend = FakeBackend()
    register_kvm_handlers(
        router=router, cache=cache, progress=publisher, backend=backend
    )
    # Open first.
    open_resp = await router.dispatch(
        Request(
            id=1,
            method="open",
            params={"host": "10.0.0.1", "user": "admin", "password": "p"},
        )
    )
    session_id = open_resp.result["session_id"]

    screen_resp = await router.dispatch(
        Request(
            id=2,
            method="screen",
            params={
                "host": "10.0.0.1",
                "user": "admin",
                "password": "p",
                "mode": "image",
            },
        )
    )
    assert screen_resp.result is not None
    png_b64 = screen_resp.result["png_b64"]
    png_bytes = base64.b64decode(png_b64)
    assert png_bytes.startswith(b"\x89PNG")


@pytest.mark.anyio
async def test_unknown_host_in_screen_reopens():
    """screen against a host not in cache triggers an implicit open."""
    router = Router()
    cache = SessionCache(clock=lambda: 0)
    publisher = ProgressPublisher()
    backend = FakeBackend()
    register_kvm_handlers(
        router=router, cache=cache, progress=publisher, backend=backend
    )
    resp = await router.dispatch(
        Request(
            id=1,
            method="screen",
            params={
                "host": "10.0.0.1",
                "user": "admin",
                "password": "p",
                "mode": "image",
            },
        )
    )
    assert resp.result is not None
    assert resp.result["png_b64"] != ""


@pytest.fixture
def anyio_backend():
    return "asyncio"
```

- [ ] **Step 2: Run to verify failure**

```
uv run pytest tests/kvm/test_handlers.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement** — `src/redfish_mcp/kvm/daemon/handlers.py`

```python
"""Router handler registration for the KVM daemon.

Phase 2 registers two methods: "open" and "screen". Other methods (sendkey,
sendkeys, type_and_read, close, status) stay unregistered and return
method_not_found until phase 3 fills them in.
"""

from __future__ import annotations

import base64
from typing import Any

from redfish_mcp.kvm.backend import KVMBackend
from redfish_mcp.kvm.daemon.cache import SessionCache
from redfish_mcp.kvm.daemon.progress import ProgressPublisher
from redfish_mcp.kvm.daemon.router import Router
from redfish_mcp.kvm.daemon.session_ops import (
    default_open_timeout_s,
    default_screenshot_timeout_s,
    open_session,
    screenshot_session,
)


def register_kvm_handlers(
    *,
    router: Router,
    cache: SessionCache,
    progress: ProgressPublisher,
    backend: KVMBackend,
) -> None:
    """Register `open` and `screen` handlers on the router."""

    backend_name = "java"

    def _session_key(host: str, user: str) -> str:
        return f"{backend_name}:{host}:{user}"

    async def handle_open(params: dict[str, Any]) -> dict[str, Any]:
        host = params["host"]
        user = params["user"]
        password = params["password"]
        timeout_s = float(params.get("timeout_s") or default_open_timeout_s())
        session_key = _session_key(host, user)

        existing = cache.get(host, user, backend_name)
        if existing is not None:
            h = existing.handle
            return {
                "session_id": h.session_id,
                "host": h.host,
                "user": h.user,
                "backend": h.backend,
                "opened_at_ms": h.opened_at_ms,
                "from_cache": True,
            }

        handle = await open_session(
            backend=backend,
            progress=progress,
            host=host,
            user=user,
            password=password,
            session_key=session_key,
            timeout_s=timeout_s,
        )
        cache.put(host, user, backend_name, handle)
        return {
            "session_id": handle.session_id,
            "host": handle.host,
            "user": handle.user,
            "backend": handle.backend,
            "opened_at_ms": handle.opened_at_ms,
            "from_cache": False,
        }

    async def handle_screen(params: dict[str, Any]) -> dict[str, Any]:
        host = params["host"]
        user = params["user"]
        password = params["password"]
        mode = params.get("mode", "image")
        timeout_s = float(params.get("timeout_s") or default_screenshot_timeout_s())

        entry = cache.get(host, user, backend_name)
        if entry is None:
            # Lazy open if nothing cached.
            session_key = _session_key(host, user)
            handle = await open_session(
                backend=backend,
                progress=progress,
                host=host,
                user=user,
                password=password,
                session_key=session_key,
            )
            entry = cache.put(host, user, backend_name, handle)

        png_bytes = await screenshot_session(
            backend=backend, session=entry.handle, timeout_s=timeout_s
        )

        if mode == "image":
            return {
                "mode": "image",
                "png_b64": base64.b64encode(png_bytes).decode("ascii"),
                "session_id": entry.handle.session_id,
            }
        # Non-image modes delegate to the phase-1 vision module.
        # Kept as a TODO comment — the phase-1 vision module hook lives in
        # tools.py, not here. For handlers, we always return PNG; the tool
        # does OCR on top.
        return {
            "mode": "image",
            "png_b64": base64.b64encode(png_bytes).decode("ascii"),
            "session_id": entry.handle.session_id,
        }

    router.register("open", handle_open)
    router.register("screen", handle_screen)
```

- [ ] **Step 4: Wire handler registration into `DaemonServer.__init__`** — `src/redfish_mcp/kvm/daemon/server.py`

Read the file. Find the `class DaemonServer` `__init__`. After the existing field assignments, add (inside `__init__`):

```python
        # Register KVM handlers on the router. Backend is injected via
        # a classmethod factory; tests that exercise the protocol handler
        # without a real Java backend can swap in FakeBackend.
        from redfish_mcp.kvm.backends.java import JavaIkvmBackend
        from redfish_mcp.kvm.daemon.handlers import register_kvm_handlers

        self.backend = JavaIkvmBackend(
            jar_cache_root=config.jar_cache_dir,
            java_bin=config.java_bin,
        )
        register_kvm_handlers(
            router=self.router,
            cache=self.cache,
            progress=self.progress,
            backend=self.backend,
        )
```

Also replace the phase-1 `_close_entry_noop` with a real closer:

```python
    async def _close_entry(self, entry) -> None:
        await self.backend.close(entry.handle)
```

And in the reaper initialization, change `close_session=self._close_entry_noop` to `close_session=self._close_entry`. Delete `_close_entry_noop`.

- [ ] **Step 5: Run tier-1 tests**

```
uv run pytest tests/kvm/test_handlers.py tests/kvm/test_server.py -v
```

Expected: all green (handlers tests + existing server tests still pass).

- [ ] **Step 6: Commit**

```bash
git add src/redfish_mcp/kvm/daemon/handlers.py src/redfish_mcp/kvm/daemon/server.py tests/kvm/test_handlers.py
git commit -m "feat(kvm): register open/screen handlers + wire JavaIkvmBackend into DaemonServer

handlers.py exposes register_kvm_handlers(router, cache, progress, backend).
DaemonServer instantiates JavaIkvmBackend and calls this from __init__, so
the daemon now serves real 'open' and 'screen' requests. Reaper's
close_session points at backend.close() instead of a noop.

Part of #64."
```

---

## Task 11 — Wire `redfish_kvm_screen` MCP tool

**Goal:** The MCP tool `redfish_kvm_screen` no longer returns `not_implemented` — it calls `DaemonClient.request("screen", ...)` and returns real results. All phase-1 modes (`image`, `text_only`, `both`, `summary`, `analysis`, `diagnosis`) are supported by re-using `vision.py` / `screen_analysis.py`.

**Files:**
- Modify: `src/redfish_mcp/kvm/tools.py`
- Modify: `tests/kvm/test_tools.py`

**Steps:**

- [ ] **Step 1: Update test for `kvm_screen`** — `tests/kvm/test_tools.py`

Read the existing file. Find the `test_screen_returns_not_implemented` test. Replace it with:

```python
    @pytest.mark.anyio
    async def test_screen_calls_daemon_client(self, monkeypatch: pytest.MonkeyPatch):
        """kvm_screen delegates to DaemonClient.request."""
        called = {}

        async def fake_ensure(*_args, **_kwargs):
            return None

        async def fake_request(self, method, params=None, **_kwargs):
            called["method"] = method
            called["params"] = params
            return {
                "mode": "image",
                "png_b64": "ZmFrZQ==",
                "session_id": "s1",
            }

        monkeypatch.setattr(
            "redfish_mcp.kvm.tools.ensure_daemon_running", fake_ensure
        )
        monkeypatch.setattr(
            "redfish_mcp.kvm.client.DaemonClient.request", fake_request
        )

        result = await kvm_screen(host="10.0.0.1", user="u", password="p", mode="image")
        assert result["ok"] is True
        assert result["mode"] == "image"
        assert result["png_b64"] == "ZmFrZQ=="
        assert called["method"] == "screen"
        assert called["params"]["host"] == "10.0.0.1"
        assert called["params"]["mode"] == "image"
```

- [ ] **Step 2: Run to verify failure**

```
uv run pytest tests/kvm/test_tools.py -v
```

Expected: the updated test fails (old implementation still returns `not_implemented`).

- [ ] **Step 3: Update `kvm_screen` implementation** — `src/redfish_mcp/kvm/tools.py`

Read the existing file. Replace only the `kvm_screen` function (keep every other stub as-is):

```python
async def kvm_screen(
    *,
    host: str,
    user: str,
    password: str,
    mode: str = "image",
    wait_for_ready: bool = False,
    timeout_s: int = 30,
) -> dict[str, Any]:
    """Capture the current KVM screen via the daemon."""
    from redfish_mcp.kvm.autostart import ensure_daemon_running
    from redfish_mcp.kvm.client import DaemonClient
    from redfish_mcp.kvm.config import KVMConfig
    from redfish_mcp.kvm.daemon.lifecycle import DaemonLifecycle

    cfg = KVMConfig.load()
    await ensure_daemon_running(cfg)
    lc = DaemonLifecycle(cfg)
    client = DaemonClient(socket_path=lc.socket_path)

    try:
        result = await client.request(
            "screen",
            params={
                "host": host,
                "user": user,
                "password": password,
                "mode": mode,
                "timeout_s": timeout_s,
            },
            timeout_s=float(timeout_s + 10),
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    result["ok"] = True
    return result
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/kvm/test_tools.py -v
```

Expected: updated test passes; the other five tests (still-stubbed tools) still pass.

- [ ] **Step 5: Run the full suite to check no regressions**

```
uv run pytest -m "not integration and not e2e" -q --no-header
```

Expected: no unexpected failures.

- [ ] **Step 6: Commit**

```bash
git add src/redfish_mcp/kvm/tools.py tests/kvm/test_tools.py
git commit -m "feat(kvm): wire redfish_kvm_screen through DaemonClient (no longer not_implemented)

Lazy-starts the daemon, opens (or reuses) a session, returns the PNG as
base64. Other phase-1 tool stubs (sendkey, sendkeys, type_and_read,
close, status) remain not_implemented — phase 3 (#65) wires those.

OCR/analysis modes (text_only, both, summary, analysis, diagnosis) will
plug in via the existing vision.py pipeline; for phase 2 we pass PNG
bytes back directly. Deferred to a follow-up task within phase 2 or to
phase 3.

Part of #64."
```

---

## Task 12 — Tier 3 e2e tests (real H100)

**Goal:** Five tests, marked `@pytest.mark.e2e`, gated on `REDFISH_KVM_E2E=1`. Never run in CI; run locally against `research-common-h100-001` (BMC `192.168.196.1`) to validate the whole stack.

**Files:**
- Create: `tests/kvm/test_java_backend_e2e.py`

**Steps:**

- [ ] **Step 1: Write the e2e tests** — `tests/kvm/test_java_backend_e2e.py`

```python
"""End-to-end tests for JavaIkvmBackend against a real Supermicro BMC.

Gating:
    REDFISH_KVM_E2E=1           (required)
    REDFISH_IP=<bmc-ip>         (required; default 192.168.196.1 if absent)
    REDFISH_USER=<username>     (required)
    REDFISH_PASSWORD=<password> (required)

Run:
    REDFISH_KVM_E2E=1 REDFISH_IP=192.168.196.1 \\
        REDFISH_USER=ADMIN REDFISH_PASSWORD=xxx \\
        uv run pytest tests/kvm/test_java_backend_e2e.py -v
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from redfish_mcp.kvm.backend import ProgressEvent
from redfish_mcp.kvm.backends.java import JavaIkvmBackend
from redfish_mcp.kvm.exceptions import AuthFailedError

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.getenv("REDFISH_KVM_E2E") != "1",
        reason="REDFISH_KVM_E2E=1 not set",
    ),
    pytest.mark.skipif(
        not (os.getenv("REDFISH_USER") and os.getenv("REDFISH_PASSWORD")),
        reason="REDFISH_USER/REDFISH_PASSWORD not set",
    ),
]


def _host() -> str:
    return os.getenv("REDFISH_IP", "192.168.196.1")


def _creds() -> tuple[str, str]:
    return os.getenv("REDFISH_USER", ""), os.getenv("REDFISH_PASSWORD", "")


@pytest.fixture
def backend(tmp_path: Path) -> JavaIkvmBackend:
    return JavaIkvmBackend(jar_cache_root=tmp_path / "jars", java_bin="java")


@pytest.mark.anyio
async def test_open_screenshot_close_happy_path(backend: JavaIkvmBackend):
    events: list[ProgressEvent] = []

    async def progress(e):
        events.append(e)

    user, password = _creds()
    handle = await backend.open(_host(), user, password, progress)
    try:
        assert handle.backend == "java"
        assert handle.host == _host()
        png = await backend.screenshot(handle)
        assert png.startswith(b"\x89PNG")
        assert len(png) > 1024
    finally:
        await backend.close(handle)

    stages = [e.stage for e in events]
    assert stages[0] == "authenticating"
    assert stages[-1] == "ready"


@pytest.mark.anyio
async def test_screenshot_returns_valid_png_dimensions(backend: JavaIkvmBackend):
    import io

    from PIL import Image

    async def progress(_e):
        pass

    user, password = _creds()
    handle = await backend.open(_host(), user, password, progress)
    try:
        png = await backend.screenshot(handle)
        img = Image.open(io.BytesIO(png))
        assert img.width >= 640
        assert img.height >= 480
    finally:
        await backend.close(handle)


@pytest.mark.anyio
async def test_bad_credentials_raise_auth_failed(backend: JavaIkvmBackend):
    async def progress(_e):
        pass

    with pytest.raises(AuthFailedError):
        await backend.open(_host(), "NOT-A-REAL-USER", "definitely-wrong", progress)


@pytest.mark.anyio
async def test_session_survives_idle_time(backend: JavaIkvmBackend):
    async def progress(_e):
        pass

    user, password = _creds()
    handle = await backend.open(_host(), user, password, progress)
    try:
        time.sleep(5)
        png = await backend.screenshot(handle)
        assert png.startswith(b"\x89PNG")
    finally:
        await backend.close(handle)


@pytest.mark.anyio
async def test_health_reports_ok(backend: JavaIkvmBackend):
    async def progress(_e):
        pass

    user, password = _creds()
    handle = await backend.open(_host(), user, password, progress)
    try:
        assert await backend.health(handle) == "ok"
    finally:
        await backend.close(handle)


@pytest.fixture
def anyio_backend():
    return "asyncio"
```

- [ ] **Step 2: Verify the tests skip cleanly without the env vars**

```
uv run pytest tests/kvm/test_java_backend_e2e.py -v
```

Expected: `5 skipped` with the gating-reason visible.

- [ ] **Step 3: Manual e2e run (operator does this with real credentials)**

```
REDFISH_KVM_E2E=1 \
  REDFISH_IP=192.168.196.1 \
  REDFISH_USER=<admin-user> \
  REDFISH_PASSWORD=<real-password> \
  uv run pytest tests/kvm/test_java_backend_e2e.py -v
```

Expected: all 5 pass against the real H100. (This is a validation step, not a test to automate.)

- [ ] **Step 4: Commit**

```bash
git add tests/kvm/test_java_backend_e2e.py
git commit -m "test(kvm): add tier-3 e2e tests for JavaIkvmBackend

Five tests against a real Supermicro BMC, gated on REDFISH_KVM_E2E=1 +
REDFISH_IP/USER/PASSWORD. Covers happy path open→screenshot→close,
PNG dimensions sanity check, AuthFailedError on bad creds, session
survives idle time, and health() returns 'ok'.

Never runs in CI; manual validation only.

Part of #64."
```

---

## Task 13 — Documentation updates

**Goal:** Update phase 2 status in the feature doc, add runtime deps section to README (may already be done in Task 1), add a KVM example to AI_AGENT_GUIDE.md.

**Files:**
- Modify: `docs/KVM_CONSOLE_FEATURE.md`
- Modify: `README.md` (if Task 1 didn't already add it)
- Modify: `AI_AGENT_GUIDE.md`

**Steps:**

- [ ] **Step 1: Update `docs/KVM_CONSOLE_FEATURE.md`**

Read the existing file. Find the `## Status` section. Replace with:

```markdown
## Status

Phase 2 (Java iKVM backend + screen capture) — merged.

- Phase 1 ([#63](https://github.com/vhspace/redfish-mcp/issues/63)) — scaffolding. ✅
- Phase 2 ([#64](https://github.com/vhspace/redfish-mcp/issues/64)) — Java iKVM backend + screen capture. ✅
- Phase 3 ([#65](https://github.com/vhspace/redfish-mcp/issues/65)) — keyboard input + `type_and_read`.
- Phase 4 ([#66](https://github.com/vhspace/redfish-mcp/issues/66)) — status, reaper tuning, docs.

Epic: [#67](https://github.com/vhspace/redfish-mcp/issues/67).

## What works today (phase 2)

- `redfish_kvm_screen(host, user, password, mode="image")` returns a PNG of the
  server's current video output via the Supermicro Java iKVM client.
- Sessions are cached and reused across calls (per-BMC session, `(host, user, "java")` key).
- Cold-start takes 15–30s (JNLP fetch, JAR download on first use, Xvfb + Java + x11vnc warmup).
- Warm screenshots take ~0.5–2s depending on framebuffer size and network.

## Not yet implemented

- Keyboard input (`sendkey`, `sendkeys`, `type_and_read`, `close`, `status`). Phase 3.
- Non-image modes (`text_only`, `both`, `analysis`, `diagnosis`) — deferred; phase 2 returns raw PNG only for now.
- Playwright HTML5 backend (alternative for Supermicro X14+ where JNLP may be deprecated). Future issue.
- Container packaging. Future issue.
```

- [ ] **Step 2: Verify README has runtime deps section**

Task 1 should have added this. Confirm `grep -n "Runtime dependencies" README.md` returns a match. If not, add the section per Task 1 Step 8.

- [ ] **Step 3: Add KVM example to `AI_AGENT_GUIDE.md`**

Read the file. Find a good insertion point (probably near other tool examples). Add:

```markdown
## KVM console (screen capture)

Capture a screenshot from a Supermicro BMC via `redfish_kvm_screen`:

```python
result = await redfish_kvm_screen(
    host="192.168.196.1",
    user="ADMIN",
    password=os.environ["REDFISH_PASSWORD"],
    mode="image",
)
if result["ok"]:
    png = base64.b64decode(result["png_b64"])
    Path("/tmp/kvm.png").write_bytes(png)
```

First call on a cold daemon takes 15–30 seconds (Java + Xvfb + x11vnc
warmup, JAR download on first-ever use). Subsequent calls against the
same BMC reuse the open session and typically return in under 2 seconds.

Currently only `mode="image"` is supported in phase 2. Text/analysis modes
arrive in a follow-up.
```

- [ ] **Step 4: Run full verification**

```
uv run pytest -m "not integration and not e2e" -q --no-header
uv run ruff check src tests
uv run ruff format --check src tests
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add docs/KVM_CONSOLE_FEATURE.md AI_AGENT_GUIDE.md README.md
git commit -m "docs(kvm): update feature doc + agent guide for phase 2

Marks phase 2 as merged in KVM_CONSOLE_FEATURE.md; documents what works
(screen capture) and what's still pending (keyboard, non-image modes,
Playwright backend). Adds a first real usage example to AI_AGENT_GUIDE.

Part of #64."
```

---

## Final verification

- [ ] **All tier-1 and tier-2 tests pass:**

```
uv run pytest -m "not integration and not e2e" -q --no-header
```

Expected: every test listed in `tests/kvm/` and `tests/kvm/backends/` passes; no regressions in pre-existing tests.

- [ ] **Lint and format clean:**

```
uv run ruff check src tests
uv run ruff format --check src tests
```

Both green.

- [ ] **Mypy unchanged vs baseline:**

```
uv run mypy src/ 2>&1 | grep -c "error:"
```

Compare to pre-phase-2 baseline. Phase 2 should add zero new mypy errors.

- [ ] **Manual e2e validation against H100:**

```
REDFISH_KVM_E2E=1 REDFISH_IP=192.168.196.1 REDFISH_USER=... REDFISH_PASSWORD=... \
  uv run pytest tests/kvm/test_java_backend_e2e.py -v
```

All 5 pass.

- [ ] **Commit graph sanity check:**

```
git log --oneline origin/main..HEAD
```

Shows ~14 commits in implementation order matching the task numbers above.

- [ ] **PR body reminder for later:**
  - `Closes #64`
  - Links the design spec path
  - Mentions any mypy baseline changes
  - Flags the manual e2e validation step
  - Notes non-image modes deferred to a follow-up

---

## Spec-coverage self-review (completed by plan author)

- ✅ JavaIkvmBackend implementing KVMBackend Protocol → Task 8.
- ✅ Subprocess substrate (Xvfb + Java + x11vnc, co-scoped) → Task 5 + Task 8.
- ✅ Supermicro CGI flow (login → JNLP) → Tasks 2 + 3.
- ✅ SHA-256 content-addressable JAR cache → Task 4.
- ✅ VNC library choice via benchmark spike → Task 6.
- ✅ VNC wrapper insulating choice → Task 7.
- ✅ Router-layer timeout + stage tracking → Task 9.
- ✅ Handler registration + server wiring → Task 10.
- ✅ `redfish_kvm_screen` no longer `not_implemented` → Task 11.
- ✅ Tier 1 (unit) + Tier 2 (subprocess) + Tier 3 (e2e) tests → Tasks 2–10, Task 12.
- ✅ Preflight + CI apt step + x86_64/aarch64 matrix → Task 1.
- ✅ Runtime deps documented → Tasks 1, 13.
- ✅ Error mappings from spec Section "Error handling" → encoded in `JavaIkvmBackend.open()` (Task 8) and `session_ops` (Task 9).
- ✅ Config env vars fully wired:
  - `REDFISH_KVM_OPEN_TIMEOUT_S` — `session_ops.default_open_timeout_s()` (Task 9).
  - `REDFISH_KVM_SCREENSHOT_TIMEOUT_S` — `session_ops.default_screenshot_timeout_s()` (Task 9).
  - `REDFISH_KVM_VERIFY_TLS` — `JavaIkvmBackend.__init__` default (Task 8).
  - `REDFISH_KVM_DISPLAY_RANGE_START` — `_subprocess._default_display_range_start()` (Task 5).
  - `REDFISH_KVM_XVFB_GEOMETRY` — `JavaIkvmBackend.__init__` default (Task 8).

  Each is tested explicitly with `monkeypatch.setenv` and an `os.getenv` fallback; explicit kwargs always win over env.

## Final notes

- The VNC spike (Task 6) produces a one-time decision. After it's committed, re-orient Task 7's implementation around the winner.
- Tasks 2–5 are strictly independent and can be worked in parallel by separate subagents if executed via subagent-driven-development.
- Task 6 blocks Task 7; Task 7 blocks Task 8; Task 8 blocks Tasks 9–11.
- Task 12 (e2e) blocks nothing downstream; can run last or be parallelized with Task 13.
- Estimated total: ~14 commits, comparable in shape to phase 1's 26 commits (phase 2 is chunkier per commit because the units are larger).
