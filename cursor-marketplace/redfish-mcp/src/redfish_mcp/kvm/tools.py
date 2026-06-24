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
