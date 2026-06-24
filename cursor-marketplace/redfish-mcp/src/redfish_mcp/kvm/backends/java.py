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
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

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
    # Redfish auth token; None when legacy CGI login was used.
    x_auth_token: str | None = None
    # The iKVM interface value that was active before we switched to JAVA plug-in.
    # Stored so close() can restore it. None means no toggle was performed.
    prior_interface: str | None = None


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
        self._jar_cache = JarCache(root=jar_cache_root)
        self._java_bin = java_bin
        # xvfb_geometry: explicit kwarg wins; else env; else built-in default.
        self._xvfb_geometry = (
            xvfb_geometry or os.getenv("REDFISH_KVM_XVFB_GEOMETRY") or "1280x1024x24"
        )
        # verify_tls: explicit kwarg wins; else env ("1" → True); else False.
        if verify_tls is not None:
            self._verify_tls = verify_tls
        else:
            self._verify_tls = os.getenv("REDFISH_KVM_VERIFY_TLS", "0") == "1"
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

        # Attempt Redfish SessionService auth first (newer X13 firmware).
        # Fall back to legacy CGI login for older firmware.
        x_auth_token: str | None = None
        sid: str
        try:
            x_auth_token, sid = supermicro_cgi.login_via_redfish(
                host=host, user=user, password=password, verify_tls=self._verify_tls
            )
            logger.info("Redfish session auth succeeded for %s@%s", user, host)
        except supermicro_cgi.SupermicroCGIError as redfish_exc:
            logger.info(
                "Redfish session auth failed for %s@%s (%s); falling back to legacy CGI login",
                user,
                host,
                redfish_exc,
            )
            x_auth_token = None
            try:
                sid = supermicro_cgi.login(
                    host=host, user=user, password=password, verify_tls=self._verify_tls
                )
            except supermicro_cgi.SupermicroCGIError as exc:
                if isinstance(
                    exc.__cause__, (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout)
                ):
                    raise KVMError(
                        f"login request failed for {host}: {exc}",
                        stage="authenticating",
                    ) from exc
                raise AuthFailedError(
                    f"login failed for {user}@{host}: {exc}",
                    stage="authenticating",
                ) from exc

        # When Redfish auth was used, ensure the iKVM interface is set to JAVA plug-in.
        # Record the prior interface so close() can restore it.
        prior_interface: str | None = None
        if x_auth_token is not None:
            try:
                current = supermicro_cgi.get_current_interface(
                    host=host, x_auth_token=x_auth_token, verify_tls=self._verify_tls
                )
                if current != supermicro_cgi._JAVA_INTERFACE_VALUE:
                    supermicro_cgi.set_current_interface(
                        host=host,
                        x_auth_token=x_auth_token,
                        value=supermicro_cgi._JAVA_INTERFACE_VALUE,
                        verify_tls=self._verify_tls,
                    )
                    prior_interface = current
                    logger.info(
                        "Switched iKVM interface from %r to %r on %s",
                        current,
                        supermicro_cgi._JAVA_INTERFACE_VALUE,
                        host,
                    )
            except supermicro_cgi.SupermicroCGIError as exc:
                # Not fatal; older firmware may not expose this endpoint. Proceed.
                logger.debug("could not toggle iKVM interface: %s", exc)

        await progress(ProgressEvent(stage="fetching_jar"))
        try:
            jnlp_bytes = supermicro_cgi.fetch_jnlp(host=host, sid=sid, verify_tls=self._verify_tls)
        except supermicro_cgi.SupermicroCGIError as exc:
            if isinstance(
                exc.__cause__, (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout)
            ):
                raise KVMError(
                    f"network error fetching JNLP from {host}: {exc}",
                    stage="fetching_jar",
                ) from exc
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
        spawned = None
        vnc_session = None
        try:
            await progress(ProgressEvent(stage="launching_java"))
            await progress(ProgressEvent(stage="starting_vnc"))
            try:
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
                vnc_session = await vnc.connect("127.0.0.1", spawned.vnc_port, password_bytes)
            except Exception as exc:
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
                x_auth_token=x_auth_token,
                prior_interface=prior_interface,
            )
        except BaseException as exc:
            # Any failure (including CancelledError from router-level timeout)
            # after spawn must tear down the subprocess group.
            if vnc_session is not None:
                try:
                    await vnc_session.close()
                except Exception:
                    logger.warning("vnc close on cleanup failed", exc_info=True)
            if spawned is not None:
                try:
                    await subprocesses.__aexit__(type(exc), exc, None)
                except Exception:
                    logger.warning("subprocess cleanup failed", exc_info=True)
            raise

        await progress(ProgressEvent(stage="ready"))
        return self._make_handle(host=host, user=user, session_id=session_id)

    async def screenshot(self, session: SessionHandle) -> bytes:
        live = self._live.get(session.session_id)
        if live is None:
            raise StaleSessionError(f"session {session.session_id} not found", stage="ready")
        try:
            return await vnc.screenshot(live.vnc_session)
        except Exception as exc:
            raise StaleSessionError(f"screenshot failed: {exc}", stage="ready") from exc

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

        # Best-effort: restore the iKVM interface to the value it had before we opened.
        if live.prior_interface is not None and live.x_auth_token is not None:
            try:
                supermicro_cgi.set_current_interface(
                    host=live.host,
                    x_auth_token=live.x_auth_token,
                    value=live.prior_interface,
                    verify_tls=self._verify_tls,
                )
                logger.info("Restored iKVM interface to %r on %s", live.prior_interface, live.host)
            except Exception:
                logger.warning(
                    "could not restore iKVM interface on %s (best-effort, continuing)",
                    live.host,
                    exc_info=True,
                )

    async def health(self, session: SessionHandle) -> str:
        live = self._live.get(session.session_id)
        if live is None:
            return "dead"
        if live.spawned.java is not None and live.spawned.java.returncode is not None:
            return "failed"
        if live.spawned.x11vnc.returncode is not None:
            return "failed"
        return "ok"
