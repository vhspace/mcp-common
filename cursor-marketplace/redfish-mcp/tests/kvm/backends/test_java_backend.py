"""Tests for JavaIkvmBackend."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from redfish_mcp.kvm.backend import ProgressEvent, SessionHandle
from redfish_mcp.kvm.backends._supermicro_cgi import SupermicroCGIError
from redfish_mcp.kvm.backends.java import JavaIkvmBackend
from redfish_mcp.kvm.exceptions import AuthFailedError, KVMError

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

# Convenience: patch login_via_redfish to fail so tests that only patch
# the legacy login() path get the right fallback behaviour.
_REDFISH_LOGIN_FAILS = patch(
    "redfish_mcp.kvm.backends.java.supermicro_cgi.login_via_redfish",
    side_effect=SupermicroCGIError("no Redfish on this firmware"),
)


@pytest.fixture
def tmp_jar_cache(tmp_path: Path) -> Path:
    return tmp_path / "jars"


class TestJavaIkvmBackendOpen:
    @pytest.mark.anyio
    async def test_auth_failure_maps_to_auth_failed_error(self, tmp_jar_cache: Path):
        events: list[ProgressEvent] = []

        async def progress(e: ProgressEvent) -> None:
            events.append(e)

        with (
            _REDFISH_LOGIN_FAILS,
            patch(
                "redfish_mcp.kvm.backends.java.supermicro_cgi.login",
                side_effect=SupermicroCGIError(
                    "login response missing SID cookie (bad credentials?)"
                ),
            ),
        ):
            backend = JavaIkvmBackend(jar_cache_root=tmp_jar_cache, java_bin="java")
            with pytest.raises(AuthFailedError) as exc_info:
                await backend.open("10.0.0.1", "ADMIN", "bad", progress)

        assert exc_info.value.stage == "authenticating"
        assert events[0].stage == "authenticating"

    @pytest.mark.anyio
    async def test_progress_stages_emitted_in_order_up_to_failure(self, tmp_jar_cache: Path):
        events: list[ProgressEvent] = []

        async def progress(e: ProgressEvent) -> None:
            events.append(e)

        with (
            _REDFISH_LOGIN_FAILS,
            patch(
                "redfish_mcp.kvm.backends.java.supermicro_cgi.login",
                return_value="fake_sid",
            ),
            patch(
                "redfish_mcp.kvm.backends.java.supermicro_cgi.fetch_jnlp",
                return_value=JNLP_XML,
            ),
            patch(
                "redfish_mcp.kvm.backends.java.JarCache.get_or_fetch",
                return_value=tmp_jar_cache / "fake" / "iKVM.jar",
            ),
            patch(
                "redfish_mcp.kvm.backends.java.SessionSubprocesses.for_java_ikvm"
            ) as mock_subproc,
        ):
            mock_subproc.return_value.__aenter__ = AsyncMock(
                side_effect=RuntimeError("simulated Xvfb failure")
            )
            mock_subproc.return_value.__aexit__ = AsyncMock()
            backend = JavaIkvmBackend(jar_cache_root=tmp_jar_cache, java_bin="java")
            with pytest.raises(Exception):  # noqa: B017 - accepts any KVMError subclass
                await backend.open("10.0.0.1", "ADMIN", "pw", progress)

        stages = [e.stage for e in events]
        assert "authenticating" in stages
        assert "fetching_jar" in stages

    @pytest.mark.anyio
    async def test_open_returns_handle_with_backend_java(self, tmp_jar_cache: Path):
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
    def test_xvfb_geometry_env_override(self, tmp_jar_cache: Path, monkeypatch: pytest.MonkeyPatch):
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

    def test_verify_tls_default_false(self, tmp_jar_cache: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("REDFISH_KVM_VERIFY_TLS", raising=False)
        backend = JavaIkvmBackend(jar_cache_root=tmp_jar_cache, java_bin="java")
        assert backend._verify_tls is False

    def test_explicit_kwarg_wins_over_env(
        self, tmp_jar_cache: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("REDFISH_KVM_VERIFY_TLS", "1")
        backend = JavaIkvmBackend(jar_cache_root=tmp_jar_cache, java_bin="java", verify_tls=False)
        assert backend._verify_tls is False


class TestJavaIkvmBackendCancellation:
    @pytest.mark.anyio
    async def test_cancellation_mid_handshake_cleans_subprocess(self, tmp_jar_cache: Path):
        """CancelledError during VNC handshake must NOT leak subprocesses."""
        events: list[ProgressEvent] = []

        async def progress(e: ProgressEvent) -> None:
            events.append(e)

        cleanup_called = {"count": 0}

        class _FakeSubprocessCtx:
            async def __aenter__(self) -> Any:
                # Simulate successfully-started subprocesses.
                from types import SimpleNamespace

                return SimpleNamespace(
                    display_num=10,
                    vnc_port=5910,
                    vnc_secret_path=tmp_jar_cache / "secret",
                    xvfb=SimpleNamespace(returncode=None, pid=1),
                    java=SimpleNamespace(returncode=None, pid=2),
                    x11vnc=SimpleNamespace(returncode=None, pid=3),
                )

            async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
                cleanup_called["count"] += 1
                return None

        # Write the secret file so the read doesn't fail.
        (tmp_jar_cache).mkdir(parents=True, exist_ok=True)
        (tmp_jar_cache / "secret").write_text("password")

        async def _cancelling_connect(*_args: Any, **_kwargs: Any) -> None:
            raise asyncio.CancelledError()

        with (
            _REDFISH_LOGIN_FAILS,
            patch(
                "redfish_mcp.kvm.backends.java.supermicro_cgi.login",
                return_value="fake_sid",
            ),
            patch(
                "redfish_mcp.kvm.backends.java.supermicro_cgi.fetch_jnlp",
                return_value=JNLP_XML,
            ),
            patch(
                "redfish_mcp.kvm.backends.java.JarCache.get_or_fetch",
                return_value=tmp_jar_cache / "fake.jar",
            ),
            patch(
                "redfish_mcp.kvm.backends.java.SessionSubprocesses.for_java_ikvm",
                return_value=_FakeSubprocessCtx(),
            ),
            patch(
                "redfish_mcp.kvm.backends.java.vnc.connect",
                side_effect=_cancelling_connect,
            ),
        ):
            backend = JavaIkvmBackend(jar_cache_root=tmp_jar_cache, java_bin="java")
            with pytest.raises(asyncio.CancelledError):
                await backend.open("10.0.0.1", "ADMIN", "pw", progress)

        assert cleanup_called["count"] == 1, "subprocess __aexit__ not called on cancellation"


class TestJavaIkvmBackendCancellationWaitFor:
    @pytest.mark.anyio
    async def test_cancellation_during_open_cleans_up_subprocesses(self, tmp_jar_cache: Path):
        """asyncio.wait_for timeout during open() must not orphan subprocesses."""

        async def progress(e: ProgressEvent) -> None:
            pass

        cleanup_called = {"count": 0}

        class _FakeSubprocessCtx:
            async def __aenter__(self) -> Any:
                from types import SimpleNamespace

                return SimpleNamespace(
                    display_num=10,
                    vnc_port=5910,
                    vnc_secret_path=tmp_jar_cache / "secret",
                    xvfb=SimpleNamespace(returncode=None, pid=1),
                    java=SimpleNamespace(returncode=None, pid=2),
                    x11vnc=SimpleNamespace(returncode=None, pid=3),
                )

            async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
                cleanup_called["count"] += 1
                return None

        (tmp_jar_cache).mkdir(parents=True, exist_ok=True)
        (tmp_jar_cache / "secret").write_text("password")

        async def _sleep_forever(*_args: Any, **_kwargs: Any) -> None:
            await asyncio.sleep(9999)

        with (
            _REDFISH_LOGIN_FAILS,
            patch(
                "redfish_mcp.kvm.backends.java.supermicro_cgi.login",
                return_value="fake_sid",
            ),
            patch(
                "redfish_mcp.kvm.backends.java.supermicro_cgi.fetch_jnlp",
                return_value=JNLP_XML,
            ),
            patch(
                "redfish_mcp.kvm.backends.java.JarCache.get_or_fetch",
                return_value=tmp_jar_cache / "fake.jar",
            ),
            patch(
                "redfish_mcp.kvm.backends.java.SessionSubprocesses.for_java_ikvm",
                return_value=_FakeSubprocessCtx(),
            ),
            patch(
                "redfish_mcp.kvm.backends.java.vnc.connect",
                side_effect=_sleep_forever,
            ),
        ):
            backend = JavaIkvmBackend(jar_cache_root=tmp_jar_cache, java_bin="java")
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(
                    backend.open("10.0.0.1", "ADMIN", "pw", progress),
                    timeout=0.2,
                )

        assert cleanup_called["count"] == 1, "subprocess __aexit__ not called when wait_for cancels"


class TestJavaIkvmBackendNetworkErrorClassification:
    @pytest.mark.anyio
    async def test_network_error_during_login_is_not_auth_failed_error(self, tmp_jar_cache: Path):
        """ConnectError during login must raise base KVMError, not AuthFailedError."""

        async def progress(e: ProgressEvent) -> None:
            pass

        connect_error = httpx.ConnectError("connection refused")
        cgi_error = SupermicroCGIError("login request failed: connection refused")
        cgi_error.__cause__ = connect_error

        with (
            _REDFISH_LOGIN_FAILS,
            patch(
                "redfish_mcp.kvm.backends.java.supermicro_cgi.login",
                side_effect=cgi_error,
            ),
        ):
            backend = JavaIkvmBackend(jar_cache_root=tmp_jar_cache, java_bin="java")
            with pytest.raises(KVMError) as exc_info:
                await backend.open("10.0.0.1", "ADMIN", "pw", progress)

        assert type(exc_info.value) is KVMError, "network error must not be AuthFailedError"
        assert exc_info.value.stage == "authenticating"

    @pytest.mark.anyio
    async def test_auth_failure_is_still_auth_failed_error(self, tmp_jar_cache: Path):
        """Missing SID (bad credentials) must raise AuthFailedError."""

        async def progress(e: ProgressEvent) -> None:
            pass

        cgi_error = SupermicroCGIError("login response missing SID cookie (bad credentials?)")
        # No __cause__ — this is the HTTP-layer / missing-cookie case.

        with (
            _REDFISH_LOGIN_FAILS,
            patch(
                "redfish_mcp.kvm.backends.java.supermicro_cgi.login",
                side_effect=cgi_error,
            ),
        ):
            backend = JavaIkvmBackend(jar_cache_root=tmp_jar_cache, java_bin="java")
            with pytest.raises(AuthFailedError) as exc_info:
                await backend.open("10.0.0.1", "ADMIN", "bad_pw", progress)

        assert exc_info.value.stage == "authenticating"


class TestJavaIkvmBackendRedfishAuthAndInterfaceToggle:
    """Tests for Redfish SessionService auth + JAVA/HTML5 interface toggle."""

    @pytest.mark.anyio
    async def test_open_with_redfish_auth_calls_set_current_interface(
        self, tmp_jar_cache: Path
    ) -> None:
        """open() with successful Redfish auth should switch interface to JAVA plug-in
        and on close() should restore the prior interface."""

        async def progress(e: ProgressEvent) -> None:
            pass

        # Simulate: Redfish login succeeds, interface is HTML 5
        mock_get_interface = MagicMock(return_value="HTML 5")
        mock_set_interface = MagicMock()

        class _FakeSubprocessCtx:
            async def __aenter__(self) -> Any:
                from types import SimpleNamespace

                return SimpleNamespace(
                    display_num=10,
                    vnc_port=5910,
                    vnc_secret_path=tmp_jar_cache / "secret",
                    xvfb=SimpleNamespace(returncode=None, pid=1),
                    java=SimpleNamespace(returncode=None, pid=2),
                    x11vnc=SimpleNamespace(returncode=None, pid=3),
                )

            async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
                return None

        (tmp_jar_cache).mkdir(parents=True, exist_ok=True)
        (tmp_jar_cache / "secret").write_text("password")

        mock_vnc_session = MagicMock()
        mock_vnc_session.close = AsyncMock()

        with (
            patch(
                "redfish_mcp.kvm.backends.java.supermicro_cgi.login_via_redfish",
                return_value=("tok-abc", "sid-xyz"),
            ),
            patch(
                "redfish_mcp.kvm.backends.java.supermicro_cgi.get_current_interface",
                mock_get_interface,
            ),
            patch(
                "redfish_mcp.kvm.backends.java.supermicro_cgi.set_current_interface",
                mock_set_interface,
            ),
            patch(
                "redfish_mcp.kvm.backends.java.supermicro_cgi.fetch_jnlp",
                return_value=JNLP_XML,
            ),
            patch(
                "redfish_mcp.kvm.backends.java.JarCache.get_or_fetch",
                return_value=tmp_jar_cache / "fake.jar",
            ),
            patch(
                "redfish_mcp.kvm.backends.java.SessionSubprocesses.for_java_ikvm",
                return_value=_FakeSubprocessCtx(),
            ),
            patch(
                "redfish_mcp.kvm.backends.java.vnc.connect",
                return_value=mock_vnc_session,
            ),
            patch(
                "redfish_mcp.kvm.backends.java.vnc.screenshot",
                return_value=b"\x89PNG fake",
            ),
        ):
            backend = JavaIkvmBackend(jar_cache_root=tmp_jar_cache, java_bin="java")
            handle = await backend.open("10.0.0.1", "ADMIN", "pw", progress)

            # set_current_interface must have been called with JAVA plug-in during open()
            mock_set_interface.assert_called_once_with(
                host="10.0.0.1",
                x_auth_token="tok-abc",
                value="JAVA plug-in",
                verify_tls=False,
            )

            # Prior interface should be stored
            live = backend._live[handle.session_id]
            assert live.prior_interface == "HTML 5"
            assert live.x_auth_token == "tok-abc"

            # Now close — should restore the interface
            mock_set_interface.reset_mock()
            await backend.close(handle)

            # set_current_interface must be called with the original value on close()
            mock_set_interface.assert_called_once_with(
                host="10.0.0.1",
                x_auth_token="tok-abc",
                value="HTML 5",
                verify_tls=False,
            )

    @pytest.mark.anyio
    async def test_open_no_toggle_when_already_java(self, tmp_jar_cache: Path) -> None:
        """If iKVM interface is already JAVA plug-in, set_current_interface is NOT called
        and close() does not attempt to restore."""

        async def progress(e: ProgressEvent) -> None:
            pass

        mock_set_interface = MagicMock()

        class _FakeSubprocessCtx:
            async def __aenter__(self) -> Any:
                from types import SimpleNamespace

                return SimpleNamespace(
                    display_num=10,
                    vnc_port=5910,
                    vnc_secret_path=tmp_jar_cache / "secret",
                    xvfb=SimpleNamespace(returncode=None, pid=1),
                    java=SimpleNamespace(returncode=None, pid=2),
                    x11vnc=SimpleNamespace(returncode=None, pid=3),
                )

            async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
                return None

        (tmp_jar_cache).mkdir(parents=True, exist_ok=True)
        (tmp_jar_cache / "secret").write_text("password")

        mock_vnc_session = MagicMock()
        mock_vnc_session.close = AsyncMock()

        with (
            patch(
                "redfish_mcp.kvm.backends.java.supermicro_cgi.login_via_redfish",
                return_value=("tok-abc", "sid-xyz"),
            ),
            patch(
                "redfish_mcp.kvm.backends.java.supermicro_cgi.get_current_interface",
                return_value="JAVA plug-in",
            ),
            patch(
                "redfish_mcp.kvm.backends.java.supermicro_cgi.set_current_interface",
                mock_set_interface,
            ),
            patch(
                "redfish_mcp.kvm.backends.java.supermicro_cgi.fetch_jnlp",
                return_value=JNLP_XML,
            ),
            patch(
                "redfish_mcp.kvm.backends.java.JarCache.get_or_fetch",
                return_value=tmp_jar_cache / "fake.jar",
            ),
            patch(
                "redfish_mcp.kvm.backends.java.SessionSubprocesses.for_java_ikvm",
                return_value=_FakeSubprocessCtx(),
            ),
            patch(
                "redfish_mcp.kvm.backends.java.vnc.connect",
                return_value=mock_vnc_session,
            ),
        ):
            backend = JavaIkvmBackend(jar_cache_root=tmp_jar_cache, java_bin="java")
            handle = await backend.open("10.0.0.1", "ADMIN", "pw", progress)

            # set_current_interface must NOT have been called (already correct)
            mock_set_interface.assert_not_called()

            live = backend._live[handle.session_id]
            assert live.prior_interface is None

            await backend.close(handle)
            # Still not called on close either
            mock_set_interface.assert_not_called()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
