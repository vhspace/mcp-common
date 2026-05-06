# KVM Console Feature

Interactive KVM-over-IP: read the server screen and send keyboard input
via the BMC. See the design spec at
[`docs/superpowers/specs/2026-04-20-kvm-console-design.md`](./superpowers/specs/2026-04-20-kvm-console-design.md)
for full architecture.

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

## Runtime dependencies (required from phase 2)

- OpenJDK 17+ (`openjdk-17-jre-headless` package)
- OpenJDK 11 JDK (`openjdk-11-jdk` package) — supplies `unpack200`, required to decode the compressed iKVM JAR that newer Supermicro X13 firmware serves (`jnlp.packEnabled=true`). The tool was removed in Java 14+, so we pin to Java 11 for this purpose even though the JRE we actually run the JAR under is Java 17.
- Xvfb (package `xvfb`)
- x11vnc (package `x11vnc`)

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `REDFISH_KVM_SOCKET_DIR` | `$XDG_RUNTIME_DIR` (`/tmp` fallback) | Daemon socket location |
| `REDFISH_KVM_SESSION_IDLE_S` | `300` | Session reap threshold |
| `REDFISH_KVM_DAEMON_IDLE_S` | `600` | Daemon self-exit threshold |
| `REDFISH_KVM_MAX_CONCURRENT` | `4` | Global concurrent-session cap |
| `REDFISH_KVM_BACKEND` | `java` | Backend selector: `java`, `playwright`, or `auto` |
| `REDFISH_KVM_JAVA_BIN` | `java` | JRE binary path |
| `REDFISH_KVM_JAR_CACHE_DIR` | `$XDG_CACHE_HOME/redfish-mcp/kvm/jars` | JAR cache |
| `REDFISH_KVM_LOG_LEVEL` | `INFO` | Daemon log verbosity |
| `REDFISH_KVM_DAEMON_PATH` | _unset_ | Override daemon executable path (default: `python -m redfish_mcp.kvm.daemon`) |
