# KVM Console Feature — Design Spec

**Date:** 2026-04-20
**Author:** mballew
**Status:** Draft (brainstorming output)
**Target repo:** vhspace/redfish-mcp

## Summary

Add interactive KVM console capability to redfish-mcp: read the server's video output and send keyboard input through the BMC's KVM-over-IP facility. Today redfish-mcp can capture single screenshots (via Supermicro DumpService / CGI CapturePreview / iDRAC sysmgmt) but has no keyboard input and no persistent interactive session. This feature closes that gap.

Primary use case is an LLM agent driving a server through scenarios where SSH is unavailable or irrelevant: graphical BIOS interaction, OS installer prompts, rescue-mode operations, crash-state observation with keyboard recovery. Text-console interaction via IPMI SOL is **not** an option in this environment — the hosts are not configured for serial console redirection.

## Background

### Why this is hard

Supermicro (the dominant platform in this fleet, including the H100-SXM-8x test target) exposes KVM through a proprietary ATEN-flavor RFB/VNC stream with custom `AST2100` video encoding that no stock VNC client decodes. There is no `sendkey.cgi` endpoint. Two viable paths exist:

1. **Java JNLP iKVM** — vendor JAR (`tw.com.aten.ikvm.KVMMain`) handles the proprietary RFB and AST2100 internally. Run it headlessly inside `Xvfb` and re-expose the X display via `x11vnc` as plain VNC, which any client can drive. Proven by `MisterCalvin/supermicro-java-ikvm` (92⭐) and `internap/docker-kvm-console-supermicro` (48⭐).
2. **Browser automation of the HTML5 iKVM SPA** — Playwright drives the iKVM canvas, `page.screenshot()` for reads, `page.keyboard` for input. Proven by `MagnaCapax/mcxBMCView` for AMI/ASRock but not for Supermicro.

Supermicro X13/H13 (current H100 platform) ships both paths. X14+ may drop the Java path. HTML5 is the declared direction.

### Why not sidecar containers or an in-process supervisor

redfish-mcp runs in two modes: stdio MCP server (long-lived) and `redfish-cli` (one-shot subprocess). An in-process supervisor dies with the CLI. A Docker sidecar adds a runtime dependency and port-management burden. A **local supervisor daemon** over a UNIX socket serves both modes with one mechanism.

### What exists today in redfish-mcp to reuse

- `screen_capture.py` — vendor-specific capture methods, MIME sniffing.
- `screenshot_cache.py` — SHA-256 change detection, LRU cache, `no_change` short-circuit.
- `vision.py` + `screen_analysis.py` — Together AI OCR and analysis modes (`summary`, `analysis`, `diagnosis`).
- `agent_state_store.py` — SQLite observation and stats store.
- `_create_background_task()` — MCP async task wrapper already used for firmware updates and BIOS changes.
- Per-BMC concurrency limiter (1 concurrent request per BMC).

## Goals

- Interactive KVM (read screen, send keys) on Supermicro X13/H13 hardware.
- Pluggable backend interface so Playwright (HTML5) can replace or complement Java later without API churn.
- Persistent sessions that survive across stdio MCP tool calls **and** across `redfish-cli` one-shot invocations.
- Automatic idle session reaping — the agent does not manage session lifecycle.
- First-class OCR/analysis output by reusing the existing `vision.py` pipeline.
- MCP async-task support so cold-start (5–15s) does not block the agent.
- CLI parity with progress updates during cold start.

## Non-goals (v1)

- Real-time video streaming, recording, or playback.
- Mouse input (can be added later; the BIOS/installer flows this targets are keyboard-driven).
- Multi-agent coordination on a single BMC — assume the current agent holds an exclusive lock.
- Virtual-media mounting — a natural v2 once the backend plumbing is proven.
- Non-Supermicro vendors in v1 — Playwright backend (v2) opens the door to ASRock/AMI boards.
- Writing a pure-Python AST2100 RFB client (deferred indefinitely; only pursue if Java and Playwright both fail).

## Architecture

Four layers, each with one responsibility:

```
┌─────────────────────────────────────────────────────────┐
│ Layer 4: MCP tools + CLI commands                        │
│   redfish_kvm_screen / sendkey / sendkeys /              │
│   type_and_read / close / status                         │
└─────────────────────────────────────────────────────────┘
                        │ JSON over UNIX socket
┌─────────────────────────────────────────────────────────┐
│ Layer 3: Local KVM daemon (the supervisor)               │
│   Session cache, idle reaper, progress publisher         │
└─────────────────────────────────────────────────────────┘
                        │ backend interface
┌─────────────────────────────────────────────────────────┐
│ Layer 2: KVMBackend implementations                      │
│   JavaIkvmBackend (v1)                                   │
│   PlaywrightBackend (v2)                                 │
└─────────────────────────────────────────────────────────┘
                        │ subprocesses / browser
┌─────────────────────────────────────────────────────────┐
│ Layer 1: Vendor substrate                                │
│   Xvfb + iKVM*.jar + x11vnc + VNC client   OR            │
│   Chromium + HTML5 iKVM SPA                              │
└─────────────────────────────────────────────────────────┘
```

### Layer 1 — Vendor substrate (Java backend)

Per session:

1. `POST https://<bmc>/cgi/login.cgi` with credentials → `SID` cookie.
2. `GET /cgi/url_redirect.cgi?url_name=man_ikvm&url_type=jwsk` → JNLP XML with ephemeral creds + ports + JAR URL.
3. Download `iKVM__V*.jar` and native libraries from the BMC directly (no redistribution problem).
4. `Xvfb :<display> -screen 0 1280x1024x24` — virtual X display.
5. `java -cp iKVM*.jar tw.com.aten.ikvm.KVMMain <22 args from JNLP>` bound to `:<display>`.
6. `x11vnc -display :<display> -localhost -rfbport <local-port> -passwdfile <random-secret>` — exposes plain VNC to localhost only.
7. Daemon's VNC client (`asyncvnc` or `vncdotool`) connects to `localhost:<local-port>`.

Display number, VNC port, and x11vnc secret are allocated per session with no collisions across concurrent sessions.

### Layer 2 — Backend interface

```python
class KVMBackend(Protocol):
    async def open(self, host: str, user: str, password: str,
                   progress: ProgressCallback) -> SessionHandle: ...
    async def screenshot(self, session: SessionHandle) -> bytes: ...  # PNG
    async def sendkeys(self, session: SessionHandle, text: str) -> None: ...
    async def sendkey(self, session: SessionHandle, key: str,
                      modifiers: list[str] = []) -> None: ...
    async def close(self, session: SessionHandle) -> None: ...
    async def health(self, session: SessionHandle) -> HealthStatus: ...
```

`ProgressCallback(stage: str, detail: str = "")` emits to the daemon's pub/sub channel.

Stages (contract):
`authenticating` → `fetching_jar` → `starting_xvfb` → `launching_java` → `starting_vnc` → `handshaking` → `ready`

Terminal failures: `failed:<stage>:<reason>` where `<reason>` is a short machine-readable token (`auth_failed`, `kvm_slot_busy`, `jnlp_unavailable`, `jar_mismatch`, `timeout`, `backend_unsupported`).

### Layer 3 — Local KVM daemon

One daemon per user. Auto-started lazily by the first tool call that needs KVM.

- **Socket path:** `$XDG_RUNTIME_DIR/redfish-mcp-kvm-<uid>.sock`, fallback `/tmp/redfish-mcp-kvm-<uid>.sock`.
- **PID file:** `$XDG_RUNTIME_DIR/redfish-mcp-kvm-<uid>.pid`. Stale-detection: check `/proc/<pid>`; if dead, unlink the socket and start a fresh daemon.
- **Startup:** `python -m redfish_mcp.kvm.daemon` via `subprocess.Popen(..., start_new_session=True, stdout=log, stderr=log)`. Daemon writes a "ready" byte to the log; the client tails until ready (3s timeout, then error).
- **Protocol:** line-delimited JSON. Request `{"id": N, "method": "...", "params": {...}}`, response `{"id": N, "result": ...}` or `{"id": N, "error": {...}}`. Streaming responses for progress use `{"id": N, "progress": {...}}`.
- **Methods:** `open`, `screenshot`, `sendkeys`, `sendkey`, `type_and_read`, `close`, `status`, `subscribe_progress`.
- **Session cache:** keyed by `(host, user, backend)`. Reuses warm sessions; cold-starts the rest.
- **Idle reaper:** background task. Per-session last-activity timestamp updated on every op. Sessions idle > `KVM_SESSION_IDLE_S` (default 300) are closed. Daemon self-exits when it holds zero sessions for `KVM_DAEMON_IDLE_S` (default 600).
- **Per-BMC exclusive lock:** serializes concurrent requests for the same BMC. Different BMCs run in parallel (bounded by a configurable global cap, default 4).
- **Health checks:** keepalive frame expected every ≤60s; a session with no frames for 60s is killed and marked `failed:stale`.
- **Observations logged:** `kvm_session_opened`, `kvm_session_closed`, `kvm_reap`, `kvm_error` into the existing `agent_state.sqlite3`.

### Layer 4 — MCP tool + CLI surface

All MCP tools take `host`, `user`, `password` like existing redfish tools.

#### Tools

| Tool | Purpose |
|---|---|
| `redfish_kvm_screen(host, user, password, mode="image", wait_for_ready=False, timeout_s=30)` | Capture current screen. Modes match existing `redfish_capture_screenshot`: `image`, `text_only`, `both`, `summary`, `analysis`, `diagnosis`. |
| `redfish_kvm_sendkey(host, user, password, key, modifiers=[])` | Single named key (`Enter`, `Escape`, `F2`, etc.) with optional modifiers. Accepts `"Ctrl+Alt+Del"` as a single combo string too. |
| `redfish_kvm_sendkeys(host, user, password, text, press_enter_after=False)` | Type an arbitrary text string. |
| `redfish_kvm_type_and_read(host, user, password, keys, wait_ms=500, mode="text_only")` | Send keys → wait → capture → return. Single MCP round-trip. **Primary interactive tool.** |
| `redfish_kvm_close(host, user, password)` | Explicit session teardown. |
| `redfish_kvm_status()` | List active sessions, idle time, backend, daemon health. |

#### Async task contract

- Warm session → tool returns synchronously.
- Cold session → tool returns an MCP async task immediately (agent polls via standard task-get). Final task result is the originally-requested operation's result (screenshot bytes / OCR text / confirmation).
- `wait_for_ready=True` → tool blocks in-process up to `timeout_s` instead of returning a task. For CLI convenience and for agents that prefer blocking calls.

#### Cache semantics for screen captures

- Reuse `screenshot_cache.py` keyed by `(backend, host)` — KVM and Redfish-CGI snapshots never collide.
- **Single-key presses force a fresh capture** — `sendkey` and single-character `sendkeys` (e.g., agent answering `y` to a prompt) bypass the `no_change` short-circuit on the subsequent read. The agent must be able to see "nothing visibly changed" as a real result, not a stale cached frame.
- Multi-character `sendkeys` and `type_and_read` with multi-character input use default cache semantics (a visible change is almost always present).
- Explicit `force=True` available on `redfish_kvm_screen` for agents that want to disable short-circuiting regardless.

#### CLI parity

```
redfish-cli kvm screen <host> [--mode text_only] [--detach]
redfish-cli kvm send   <host> <keys-or-text> [--enter]
redfish-cli kvm type-and-read <host> <text> [--wait-ms 500] [--mode text_only]
redfish-cli kvm close  <host>
redfish-cli kvm status [<task-id>]
```

- **Default on cold session:** poll-and-wait. Prints the task ID on the first line, then emits stage updates every 2 seconds to stderr. Final result on stdout. This gives both the "agent can reference a task ID if it disconnects" and the "humans get a blocking answer" properties in one default.
- `--detach`: print the task ID and exit. Agent/human polls later with `redfish-cli kvm status <task-id>`.

## Data flow

### `type_and_read` — the hot path

```
MCP tool / CLI
    │
    ▼
daemon.type_and_read(host, user, password, keys, wait_ms, mode)
    │
    ├─ session warm? ───── no ──► open() → progress("authenticating"..."ready")
    │                              │
    │                              ▼
    ├─ backend.sendkeys(session, keys)  (or sendkey for single)
    │
    ├─ await sleep(wait_ms)
    │
    ├─ backend.screenshot(session) → PNG bytes
    │
    ├─ screenshot_cache.put(key, png, force=single_key_heuristic)
    │
    ├─ if mode in {text_only, both, analysis, diagnosis, summary}:
    │       vision.ocr_or_analyze(png, mode) → text/struct
    │
    └─ return { png?, text?, analysis?, session_id, idle_s, backend }
```

### Progress subscription

```
CLI                           daemon                      backend
 │                              │                           │
 ├─ kvm.screen(host)  ─────────►│                           │
 │                              ├─ session cold?            │
 │                              ├─ subscribe_progress(key)  │
 │                              ├─ task_id = spawn open()──►│
 │◄─ {task_id, progress stream} │                           │
 │                              │  ◄─ stage="authenticating"│
 │  print stage every 2s        │  ◄─ stage="fetching_jar"  │
 │                              │  ◄─ stage="ready"         │
 │                              ├─ run screenshot ─────────►│
 │◄─ final result               │                           │
```

## Error handling

| Condition | Surface | Recovery hint |
|---|---|---|
| BMC rejects credentials | `failed:authenticating:auth_failed` | Agent prompts user for creds via credentials provider. |
| Another user holds KVM | `failed:launching_java:kvm_slot_busy` | Retry after N seconds, or force-close via out-of-band. |
| JAR version mismatch | `failed:launching_java:jar_mismatch` + captured stderr | Try Playwright backend (v2); for v1 surface to user. |
| Firmware doesn't expose JNLP | `failed:fetching_jar:jnlp_unavailable` | Try Playwright backend (v2). |
| Daemon socket dead mid-call | client unlinks + restarts, returns `session_lost` | Next call re-opens. |
| Session wedged (no frames 60s) | reaper kills, marks `failed:stale` | Next call re-opens. |
| Tool timeout elapsed | task result = `failed:timeout:<last-stage>` | Agent retries or inspects daemon logs. |

All errors return as structured MCP results (not exceptions) with the `redfish-mcp` convention via `ResponseBuilder.error(...)`.

## Security

- Credentials: reuse the existing `credentials.py` provider (1Password, env, elicitation).
- Daemon socket: `0600` permissions, owned by the invoking user.
- VNC socket: bound to `127.0.0.1` only, password-protected with a per-session random secret.
- No credentials written to disk beyond the SID cookie in the daemon's in-memory session record.
- JAR cache directory (`$XDG_CACHE_HOME/redfish-mcp/kvm/jars/<sha256>/`) permits sharing across sessions without risk — JARs are already downloaded-from-vendor bytes.

## Configuration

Environment variables (all optional with sensible defaults):

| Variable | Default | Purpose |
|---|---|---|
| `REDFISH_KVM_DAEMON_PATH` | auto | Override daemon executable. |
| `REDFISH_KVM_SOCKET_DIR` | `$XDG_RUNTIME_DIR` | Daemon socket location. |
| `REDFISH_KVM_SESSION_IDLE_S` | `300` | Session reap threshold. |
| `REDFISH_KVM_DAEMON_IDLE_S` | `600` | Daemon self-exit threshold. |
| `REDFISH_KVM_MAX_CONCURRENT` | `4` | Global concurrent-session cap. |
| `REDFISH_KVM_BACKEND` | `java` | Backend selector (`java`\|`playwright`\|`auto`). |
| `REDFISH_KVM_JAVA_BIN` | `java` | JRE binary path. |
| `REDFISH_KVM_JAR_CACHE_DIR` | `$XDG_CACHE_HOME/redfish-mcp/kvm/jars` | JAR cache. |
| `REDFISH_KVM_LOG_LEVEL` | `INFO` | Daemon log verbosity. |

## Dependencies

**New runtime:**
- OpenJDK 17+ (system package).
- `Xvfb` (system package, `xvfb`).
- `x11vnc` (system package, `x11vnc`).
- `asyncvnc` (Python, async VNC client) — preferred over `vncdotool` for async support.

**New dev:**
- `pytest-asyncio` (already in project).
- A tiny VNC test server for unit tests.

All listed in `pyproject.toml` under a `[project.optional-dependencies.kvm]` extra so users who don't want KVM don't pay the dep cost.

## Testing

### Unit
- Backend protocol conformance tests with a `FakeBackend` recorder.
- Daemon protocol tests: request routing, progress fanout, session cache hits/misses, reaper scheduling (using `freezegun`/pytest time mocking).
- Socket lifecycle: stale-detection, restart-on-crash, permission bits.

### Integration
- Daemon end-to-end against a mock backend: start daemon → open → screenshot → idle → reap.
- VNC client against a local `x11vnc` on a dummy Xvfb (no Java needed).
- CLI command → daemon round-trip under `pexpect`.

### e2e (gated on `REDFISH_KVM_E2E=1`)
- Target: `research-common-h100-001` (BMC `192.168.196.1`).
- **Read-only first:** open session, capture screen, OCR, close. No input.
- **Harmless-input second (requires user-provided creds):** send `Esc` at a known idle prompt, capture, verify screen still sane.
- No destructive input tests in CI; reserved for manual runs.

## Rollout plan

Delivered as a series of PRs against `vhspace/redfish-mcp`:

1. **Scaffolding** — `kvm/` module, `KVMBackend` protocol, daemon skeleton (no backend), socket protocol, CLI stubs, config env vars, doc stubs. No Java yet. CI green on unit tests.
2. **Java backend, screenshot-only** — `JavaIkvmBackend.open/screenshot/close`. Wire up `redfish_kvm_screen` + `redfish-cli kvm screen`. e2e gated test against the H100.
3. **Input** — `sendkey`, `sendkeys`, `type_and_read`. Cache semantics for single-key presses. e2e harmless-input test.
4. **Polish** — `status`, idle reaper tuning, progress stages nailed down, docs, AGENTS.md update, AI_AGENT_GUIDE.md entries.
5. **(v2) Playwright backend** — `PlaywrightBackend`, `REDFISH_KVM_BACKEND=auto` detection logic, tested against Supermicro HTML5 SPA and optionally an AMI/ASRock board.

## Open questions to revisit during implementation

- Does `asyncvnc` handle the `x11vnc` quirks cleanly, or do we need `vncdotool` fallback? (Smoke test early.)
- Java 17 vs 11 vs 21 — Supermicro iKVM JARs are old; unsigned JARs block on modern JREs without `-Djava.security.policy` override. Determine the minimum-friction JRE config during PR #2.
- Do we want a `redfish_kvm_wait_for(pattern, timeout_s)` helper that polls OCR text until a regex matches? Natural follow-on to `type_and_read` for multi-step flows. Deferred to post-v1 based on actual usage.

## References

- Flameeyes 2012 — RFB/ATEN reverse engineering: https://flameeyes.blog/2012/07/03/more-on-the-supermicro-ikvm/
- kelleyk/noVNC (AST2100 decoder): https://github.com/kelleyk/noVNC/tree/bmc-support
- MisterCalvin/supermicro-java-ikvm: https://github.com/MisterCalvin/supermicro-java-ikvm
- internap/docker-kvm-console-supermicro: https://github.com/internap/docker-kvm-console-supermicro
- MagnaCapax/mcxBMCView (Playwright pattern for AMI): https://wiki.pulsedmedia.com/wiki/McxBMCView
- dalehamel — Supermicro iKVM preview scraper: https://gist.github.com/dalehamel/a5fa04c918b67f36aa75bb7a913416bd
- Supermicro BMC User's Manual X13/H13: https://www.supermicro.com/manuals/other/BMC_IPMI_X13_H13.pdf
- Supermicro Redfish Reference Guide: https://www.supermicro.com/manuals/other/RedfishRefGuide.pdf
- Related closed redfish-mcp issues: #28, #39, #41
