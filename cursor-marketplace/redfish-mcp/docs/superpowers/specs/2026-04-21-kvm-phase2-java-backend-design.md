# KVM Phase 2 — Java iKVM Backend Design Spec

**Date:** 2026-04-21
**Author:** mballew
**Status:** Draft (brainstorming output)
**Target issue:** vhspace/redfish-mcp#64
**Epic:** vhspace/redfish-mcp#67
**Phase 1 (merged):** vhspace/redfish-mcp#77 (design `docs/superpowers/specs/2026-04-20-kvm-console-design.md`)

## Summary

Replace the `not_implemented` stub of `redfish_kvm_screen` with a real implementation backed by the Supermicro Java iKVM client. This spec covers the `JavaIkvmBackend` implementation of the `KVMBackend` Protocol already defined in phase 1, its subprocess substrate (`Xvfb` + vendor JAR + `x11vnc`), the Supermicro CGI flow (login → JNLP → JAR), and the router-layer timeout-with-stage-tracking wrapper.

**Scope of phase 2:** screen capture only. Keyboard input ships in phase 3 (#65).

## Background

### What phase 1 left in place

- `src/redfish_mcp/kvm/backend.py` — `KVMBackend` Protocol with async `open`, `screenshot`, `sendkeys`, `sendkey`, `close`, `health`. `SessionHandle`, `ProgressEvent`, `ProgressCallback`.
- `src/redfish_mcp/kvm/daemon/server.py` — `DaemonServer` wiring `router`/`cache`/`reaper`/`progress`/`lifecycle`. Starts, accepts connections, dispatches requests to handlers registered on the router. **No handlers registered in phase 1.**
- `src/redfish_mcp/kvm/daemon/progress.py` — `ProgressPublisher` per-session fanout. Ready to carry real progress events.
- `src/redfish_mcp/kvm/client.py` — `DaemonClient` with progress streaming and error-code → exception mapping.
- `src/redfish_mcp/kvm/tools.py` — six MCP tool stubs returning `not_implemented`. Phase 2 makes `kvm_screen` real; the other five stay stubs until phase 3.
- `src/redfish_mcp/kvm/daemon/observations.py` — `ObservationLogger` ready to record `kvm_session_opened`/`_closed`/`_error` events.

### Decisions locked during brainstorming

1. **VNC client library:** benchmark `asyncvnc` vs `vncdotool` against the real H100 BMC in an early task. Losing lib drops out of dependencies. Benchmark criteria: screenshot latency, API ergonomics, correctness against a reference frame.
2. **Runtime dependency packaging:** system apt packages (`openjdk-17-jre-headless xvfb x11vnc`). Daemon preflight check at startup; clear error message with install command if missing. Docker deferred to a future issue. Cursor MCP config stays `uv run redfish-mcp`.
3. **JAR caching:** SHA-256 content-addressable at `$XDG_CACHE_HOME/redfish-mcp/kvm/jars/<sha>/`. Fetched from the BMC at runtime (no redistribution, follows `MisterCalvin/supermicro-java-ikvm`'s pattern).
4. **Timeout enforcement:** router layer only. `KVMBackend` Protocol unchanged. Timeout uses `asyncio.wait_for`; progress subscriber records last-seen stage so timeout errors report `failed:timeout:<stage>`. Documented on issue #64.
5. **Test tiers:** (1) unit against HTTP stubs, (2) integration against real Xvfb+Java+x11vnc subprocesses with stubbed BMC HTTP, (3) e2e against real H100 gated on `REDFISH_KVM_E2E=1`. CI runs tiers 1+2 (adds one apt step); e2e runs locally.

## Goals

- `redfish_kvm_screen` returns a real PNG (or OCR/analysis, re-using phase-1's `vision.py`) from a Supermicro X13/H13 BMC.
- Progress events (`authenticating → fetching_jar → starting_xvfb → launching_java → starting_vnc → handshaking → ready`) flow through the daemon to the client per the phase-1 progress-publisher contract.
- Session cold-start completes in ≤ 30s against a warm BMC (once JAR is cached), ≤ 60s cold (first-ever download). Timeouts raise `StaleSessionError` with the last-seen stage.
- Concurrent sessions to different BMCs work; concurrent sessions to the same BMC are serialized by the existing per-BMC concurrency limit.
- Cancellation-clean: if `DaemonServer.stop()` fires mid-open, all subprocesses for in-flight sessions are killed within 5 seconds.

## Non-goals (phase 2)

- Keyboard input, key combos, `type_and_read` — phase 3 (#65).
- Mouse input.
- Virtual media.
- Playwright HTML5 backend — a separate future issue.
- Container packaging — separate future issue.
- `REDFISH_KVM_BACKEND=auto` dispatch logic — phase 3 or later when more than one backend exists.

## Architecture

### Layer diagram (phase 1 layers shown greyed; phase 2 additions in bold)

```
┌──────────────────────────────────────────────────────────┐
│ Tool surface (phase 1)                                   │
│   redfish_kvm_screen (no longer a stub in phase 2)       │
└──────────────────────────────────────────────────────────┘
                       │ DaemonClient (phase 1)
┌──────────────────────────────────────────────────────────┐
│ Daemon (phase 1 skeleton, phase 2 wiring)                │
│   router, cache, reaper, progress (phase 1 structures)   │
│   ** open_session handler (phase 2, router-registered) **│
│   ** session_ops.py: timeout + stage-tracking wrapper ** │
└──────────────────────────────────────────────────────────┘
                       │ KVMBackend Protocol (phase 1)
┌──────────────────────────────────────────────────────────┐
│ ** JavaIkvmBackend (phase 2) **                          │
│   ** _supermicro_cgi (login, JNLP fetch) **              │
│   ** _jnlp (parse vendor JNLP XML) **                    │
│   ** _jar_cache (SHA-256 content-addressable) **         │
│   ** _subprocess (Xvfb + java + x11vnc lifecycle) **     │
│   ** _vnc (wraps winning VNC lib) **                     │
└──────────────────────────────────────────────────────────┘
                       │ localhost VNC
┌──────────────────────────────────────────────────────────┐
│ Subprocess group (per session, phase 2)                  │
│   Xvfb :N ─ display ─ Java iKVM JAR ─ TLS ─ BMC (:5900)  │
│        ▲                                                  │
│        └─ display ─ x11vnc :(5900+N) ── local VNC        │
└──────────────────────────────────────────────────────────┘
```

### Module layout

New files under `src/redfish_mcp/kvm/backends/`:

```
src/redfish_mcp/kvm/backends/
├── __init__.py
├── java.py                 # JavaIkvmBackend (KVMBackend impl)
├── _supermicro_cgi.py      # POST /cgi/login.cgi + GET /cgi/url_redirect.cgi
├── _jnlp.py                # JNLP XML parse (jar URL, ports, credentials, arg list)
├── _jar_cache.py           # SHA-256 cache
├── _subprocess.py          # SessionSubprocesses async context manager
└── _vnc.py                 # thin wrapper over the chosen VNC lib
```

New files under `src/redfish_mcp/kvm/daemon/`:

```
src/redfish_mcp/kvm/daemon/
├── preflight.py            # check_runtime_deps()
├── session_ops.py          # open_session(...) with timeout + stage tracking
└── handlers.py             # router handler registration (open, screen, ...)
```

Touched phase-1 files:

- `src/redfish_mcp/kvm/daemon/server.py` — register handlers from `handlers.py`; call preflight in `start()`; wire `_close_entry_noop` replacement.
- `src/redfish_mcp/kvm/tools.py` — `kvm_screen` calls into `DaemonClient`; other stubs unchanged.
- `pyproject.toml` — add the winning VNC lib + `requests` (already present) + any JAR-download dep (probably just `requests`).

Everything under `backends/` is implementation-private. The only public seam is the existing `KVMBackend` Protocol.

### Data flow — cold `open`

```
MCP tool → DaemonClient.request("open", host, user, password)
  ↓
DaemonServer.router.dispatch → open_session(host, user, password, timeout_s=30)
  ↓
session_ops.open_session:
    progress_sub = progress.subscribe(session_key)
    last_stage = None
    async def track(event):
        last_stage = event.stage
        await progress.publish(session_key, event)
    try:
        handle = await asyncio.wait_for(
            backend.open(host, user, password, progress=track),
            timeout_s,
        )
    except asyncio.TimeoutError:
        raise StaleSessionError(f"open timed out ({timeout_s}s)", stage=last_stage)
  ↓
JavaIkvmBackend.open:
    stage="authenticating":   supermicro_cgi.login(host, user, password)     → SID
    stage="fetching_jar":     jnlp_xml = supermicro_cgi.fetch_jnlp(host, SID)
                              jar_path, sha = jar_cache.get_or_fetch(host, jnlp_xml, SID)
    stage="starting_xvfb":    SessionSubprocesses.start_xvfb()
    stage="launching_java":   SessionSubprocesses.start_java(jar_path, jnlp_xml.args)
    stage="starting_vnc":     SessionSubprocesses.start_x11vnc()
    stage="handshaking":      await vnc.connect(localhost, vnc_port, secret)
    stage="ready":            return SessionHandle(session_id, host, user, backend="java", ...)
```

### Data flow — `screenshot`

```
MCP tool → DaemonClient.request("screen", host, user, password, mode)
  ↓
DaemonServer → session_ops.screenshot(host, user, password, mode):
    handle = cache.get_or_open(...)   # reuses existing session if warm
    png_bytes = await backend.screenshot(handle)
    cache.touch(handle)
    if mode in {image}:       return {"png_b64": base64(png_bytes)}
    if mode in {text_only, ...}: return vision.ocr_or_analyze(png_bytes, mode)
  ↓
JavaIkvmBackend.screenshot(handle):
    png = await self._vnc.screenshot(handle.session_id)  # cached VNC client per session
    return png
```

The existing `screenshot_cache.py` / `vision.py` / `screen_analysis.py` modules plug in unchanged; the JAR-backed path is a new source of PNG bytes but the post-processing pipeline is identical to the Redfish/CGI snapshot path.

### Subprocess lifecycle — `SessionSubprocesses`

Key design commitment: **one `async with` context manager per session owns all three subprocesses**. No orphan Java processes if VNC fails to come up.

```python
@dataclass
class SpawnedSession:
    display_num: int
    vnc_port: int
    vnc_secret_path: Path
    xvfb: asyncio.subprocess.Process
    java: asyncio.subprocess.Process
    x11vnc: asyncio.subprocess.Process


class SessionSubprocesses:
    async def __aenter__(self) -> SpawnedSession: ...
    async def __aexit__(self, exc_type, exc, tb) -> None: ...
    # Cleanup order: x11vnc → java → Xvfb.
    # Each step: SIGTERM, wait ≤2s, SIGKILL process group.
```

Display-number allocation: scan `/tmp/.X*-lock` to find an unused `:N`, starting from `:10`. Rate-limited retries if collisions.

VNC port allocation: `socket.bind(("127.0.0.1", 0))` to grab a random free port, close, pass that number to x11vnc's `-rfbport`.

x11vnc password: generate 32 random bytes, write to a mode-0600 tempfile, pass via `-passwdfile`. Same value used by the VNC client to authenticate locally.

### Router-layer timeout + stage tracking

Lives in `daemon/session_ops.py`:

```python
async def open_session(
    *,
    backend: KVMBackend,
    progress: ProgressPublisher,
    host: str, user: str, password: str,
    session_key: str,
    timeout_s: float = 30.0,
) -> SessionHandle:
    last_stage = "authenticating"

    async def tracking_progress(event: ProgressEvent) -> None:
        nonlocal last_stage
        last_stage = event.stage
        await progress.publish(session_key, event)

    try:
        handle = await asyncio.wait_for(
            backend.open(host, user, password, progress=tracking_progress),
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
```

Default `timeout_s=30` for `open`. Default `timeout_s=15` for `screenshot` (surfaces slow VNC or hung Java). Both configurable via env or per-call kwarg on the MCP tool.

## Error handling (phase 2 additions)

| Condition | Detection | Surface |
|---|---|---|
| BMC rejects `/cgi/login.cgi` | non-200 or missing SID cookie | `AuthFailedError` (stage `authenticating`) |
| Network / DNS failure to BMC | `requests.ConnectionError` | base `KVMError` with message; stage reflects where |
| JNLP response malformed / unexpected shape | XML parse fails or required fields missing | `BackendUnsupportedError` (stage `fetching_jar`) |
| JAR download fails | HTTP error or SHA mismatch on retry | base `KVMError` (stage `fetching_jar`) |
| Xvfb fails to start | non-zero exit within 2s | base `KVMError` (stage `starting_xvfb`) with captured stderr |
| Java process exits during launch | non-zero exit within 10s | `JarMismatchError` (stage `launching_java`) with captured stderr |
| x11vnc fails to bind | non-zero exit within 2s | base `KVMError` (stage `starting_vnc`) |
| VNC handshake fails | `asyncvnc`/`vncdotool` raises | `BackendUnsupportedError` (stage `handshaking`) |
| Another user holds BMC KVM slot | Java stderr pattern match | `SlotBusyError` (stage `launching_java`) |
| Total open time exceeds `timeout_s` | `asyncio.TimeoutError` in `session_ops` | `StaleSessionError` with `stage=<last-seen>` |
| VNC screenshot fails mid-session | VNC client raises | `StaleSessionError` (stage `ready`) |

All errors surface to the client as `ErrorPayload(code=<reason>, stage=<stage>, message=<detail>)` per the phase-1 wire protocol, and are logged as observations (`kvm_error` kind) in the existing SQLite store.

## Security

- `_supermicro_cgi.login` uses `verify=False` by default because BMC certs are almost always self-signed / invalid. Expose `REDFISH_KVM_VERIFY_TLS=1` override for environments with proper cert distribution. Warn once per daemon lifetime on first insecure request.
- SID cookie held only in-memory on the backend instance; never persisted.
- JAR cache dir is `0700`, cached JARs are `0600`. SHA-256 verification on cache hits rejects tampering.
- x11vnc `-passwdfile` uses 32 random bytes, `-localhost` binds to 127.0.0.1 only, password deleted when session closes.
- VNC password + session SID never logged. Stage events are the only visibility into an `open()` call.

## Configuration additions

All optional.

| Variable | Default | Purpose |
|---|---|---|
| `REDFISH_KVM_OPEN_TIMEOUT_S` | `30` | Router-layer timeout for `open` |
| `REDFISH_KVM_SCREENSHOT_TIMEOUT_S` | `15` | Router-layer timeout for `screenshot` |
| `REDFISH_KVM_VERIFY_TLS` | `0` | Verify BMC TLS certs |
| `REDFISH_KVM_DISPLAY_RANGE_START` | `10` | First X display number to try |
| `REDFISH_KVM_XVFB_GEOMETRY` | `1280x1024x24` | Virtual display geometry |

## Dependencies

**Winning VNC lib (decided in task 6, benchmark):** one of `asyncvnc` or `vncdotool`. Whichever is picked is added to `pyproject.toml` (`[project.dependencies]`, not an optional extra since it's required whenever the Java backend is usable, which is the only backend in phase 2).

**Already present:** `requests` (for CGI/JAR HTTP), `Pillow` (transitive, used by both VNC libs for screenshot decoding).

**System (apt):** `openjdk-17-jre-headless`, `xvfb`, `x11vnc`. All three are available as first-class Debian/Ubuntu packages on both **x86_64 and aarch64** — no architecture-specific handling needed in code. Documented in README + feature doc. Enforced at daemon startup by `preflight.check_runtime_deps()`.

**Architecture support:** x86_64 and aarch64 Linux are both first-class targets. The Supermicro iKVM JAR is platform-independent bytecode; the JRE itself is the arch-specific bit and apt picks the right one. Apple Silicon and Ampere-based servers are both supported without code changes.

**CI:** `.github/workflows/ci.yml` gains one step before `uv sync`, and the test job runs on **both** x86_64 and aarch64 runners via a matrix:
```yaml
strategy:
  matrix:
    runner: [ubuntu-latest, ubuntu-24.04-arm]
runs-on: ${{ matrix.runner }}
steps:
  - name: Install KVM runtime deps
    run: sudo apt-get update && sudo apt-get install -y openjdk-17-jre-headless xvfb x11vnc
  # ... rest of job
```
Both matrix legs run lint, unit, and subprocess-tier tests — so an arm64 regression in subprocess handling (e.g., x11vnc flag differences, Xvfb glyph behaviour) fails CI instead of production. CI time doubles nominally but runs in parallel so wall time is unchanged.

## Testing

### Tier 1 — unit (pytest default, fast)
- `test_supermicro_cgi.py` — login request shape, SID cookie parsing, JNLP endpoint URL construction (stubs via `pytest-httpx`).
- `test_jnlp.py` — parses sample JNLP fixtures (happy path + malformed); asserts extracted jar URL, port, argv list.
- `test_jar_cache.py` — cache miss → fetch → cache hit → SHA mismatch → re-fetch; dir perms 0700, file perms 0600.
- `test_session_ops.py` — timeout fires, reports last-seen stage; successful open returns handle; progress events fan out via publisher.
- `test_preflight.py` — monkeypatches `shutil.which` to simulate missing binaries; asserts `BackendUnsupportedError` with correct message.

### Tier 2 — integration (subprocesses, real Xvfb/Java/x11vnc, stubbed BMC)
- Marked `@pytest.mark.subprocess`. Skipped automatically if `shutil.which("Xvfb")` is None (dev laptops without deps).
- `test_session_subprocesses.py` — spin up Xvfb + x11vnc (no JAR) against an empty display; verify VNC client can connect. Exercises the subprocess-lifecycle context manager.
- `test_java_backend_stubbed.py` — run the full `JavaIkvmBackend.open()` against an HTTP mock that serves login, JNLP, and a tiny valid JAR (a test-fixture JAR that just sleeps forever); verify progress stages fire in order; verify clean cancellation if the test times out mid-open.

### Tier 3 — e2e (real H100 BMC)
- Marked `@pytest.mark.e2e`. Gated on `REDFISH_KVM_E2E=1` + `REDFISH_IP`, `REDFISH_USER`, `REDFISH_PASSWORD`.
- `test_java_backend_e2e.py` — 5 tests:
  1. `open → screenshot → close` happy path on H100.
  2. `screenshot` returns valid PNG of expected dimensions.
  3. bad creds → `AuthFailedError`.
  4. BMC slot already held (set up by pre-opening iKVM via browser) → `SlotBusyError`.
  5. open → wait 5s → screenshot → close; verify idle reaper doesn't prematurely close an active session.

### CI behavior

`.github/workflows/ci.yml` `lint-and-test` job grows to an x86_64 + aarch64 matrix:
```yaml
lint-and-test:
  strategy:
    matrix:
      runner: [ubuntu-latest, ubuntu-24.04-arm]
  runs-on: ${{ matrix.runner }}
  steps:
    - uses: actions/checkout@v4
    - name: Install apt deps
      run: sudo apt-get update && sudo apt-get install -y openjdk-17-jre-headless xvfb x11vnc
    - name: Install project
      run: uv sync --all-groups
    - name: Lint
      run: uv run ruff check src tests && uv run ruff format --check src tests
    - name: Tests (non-e2e)
      run: uv run pytest -m "not e2e and not integration"
```

Both matrix legs must pass for the check to be green. Lint runs on both so any arch-specific formatting drift is caught; unit and subprocess-tier tests run on both so any architecture-sensitive behaviour (e.g., x11vnc startup timing differences on arm64) fails CI.

Existing non-KVM integration tests in the repo already skip when `REDFISH_IP` is absent. Phase 2 adds the `subprocess` marker for tier 2 which runs by default (apt deps are present in CI on both architectures).

## Rollout — phase 2 PR shape

One PR targeting `main`. Tasks, in implementation order:

1. **Preflight module + CI apt step + x86_64/aarch64 matrix + runtime-deps docs.** `src/redfish_mcp/kvm/daemon/preflight.py`, CI workflow edit adding the `ubuntu-latest` / `ubuntu-24.04-arm` matrix + apt install step, README section covering both architectures.
2. **`_supermicro_cgi.py`** — login, JNLP fetch. Tier 1 tests.
3. **`_jnlp.py`** — parse JNLP XML to `JnlpSpec` dataclass (jar URL, ports, credentials, Java argv). Tier 1 tests.
4. **`_jar_cache.py`** — SHA-256 content-addressable cache. Tier 1 tests.
5. **`_subprocess.py`** — `SessionSubprocesses` async context manager. Tier 2 integration test that spins up Xvfb + x11vnc with no Java (verifies cleanup path).
6. **VNC spike + decision gate.** Temporary scratch module under `backends/_spike_vnc.py` that benchmarks `asyncvnc` and `vncdotool` against the local x11vnc (and against H100 if `REDFISH_KVM_E2E=1`). Decision recorded in a commit message; losing lib's code deleted; winning lib added to `pyproject.toml`.
7. **`_vnc.py`** — thin wrapper around the winner. Exposes `connect(host, port, secret) → VncSession`, `screenshot(session) → bytes`. Tier 1 tests against a local x11vnc fixture.
8. **`JavaIkvmBackend`** (`backends/java.py`) — composes all the above, implements `open()` + `screenshot()`. `sendkeys`/`sendkey` raise `NotImplementedError` (phase 3). `close()` calls the subprocess context manager's `__aexit__`. Tier 1 tests with mocks; tier 2 test against stubbed BMC + real subprocesses.
9. **`daemon/session_ops.py`** — router-layer `open_session` and `screenshot` helpers with timeout + stage-tracking. Tier 1 tests.
10. **`daemon/handlers.py`** — router handler registration: `open`, `screen`. Wires `DaemonServer.__init__` to register them. Tier 1 test that `DaemonServer` starts with handlers registered and `_close_entry_noop` replaced with a real close.
11. **Wire `redfish_kvm_screen`** — `src/redfish_mcp/kvm/tools.py`: `kvm_screen` calls `DaemonClient.request("screen", ...)`, supports all the phase-1 `mode` values (reuses `vision.py`). Integration test via `tests/test_mcp_tools.py` confirms the tool no longer returns `not_implemented`.
12. **Tier 3 e2e tests** — `tests/kvm/test_java_backend_e2e.py`. Not run in CI.
13. **Docs** — `docs/KVM_CONSOLE_FEATURE.md` status update, runtime deps section in `README.md`, AGENT_GUIDE entry with the first real example.

Estimated: ~14 commits, similar shape to phase 1. Closes #64.

## Open items for implementation

- **JRE flags for unsigned Supermicro JARs.** Modern JREs may block unsigned JARs without `-Djava.security.manager -Djava.security.policy=<allow-all.policy>`. Resolved during task 5/8 against real firmware; document in feature doc.
- **X13/H13 JNLP variant.** If the H100's firmware emits a JNLP that differs from the reference shape (e.g., new args, different URL structure), task 3 surfaces this and we update the parser. Baseline assumption: matches the shape documented in Flameeyes 2012 + MisterCalvin's current reverse-engineering.
- **`host.docker.internal` vs routable IP.** Not an issue since we're not containerizing in phase 2, but noting that future Docker work (separate issue) will need to think about BMC reachability from container network namespaces.

## References

- Phase 1 design: `docs/superpowers/specs/2026-04-20-kvm-console-design.md`
- Phase 1 implementation plan: `docs/superpowers/plans/2026-04-20-kvm-phase1-scaffolding.md`
- Issue #64 with timeout decision: https://github.com/vhspace/redfish-mcp/issues/64
- Flameeyes 2012 Supermicro iKVM reverse engineering: https://flameeyes.blog/2012/07/03/more-on-the-supermicro-ikvm/
- MisterCalvin/supermicro-java-ikvm: https://github.com/MisterCalvin/supermicro-java-ikvm
- asyncvnc: https://github.com/barneygale/asyncvnc
- vncdotool: https://github.com/sibson/vncdotool
