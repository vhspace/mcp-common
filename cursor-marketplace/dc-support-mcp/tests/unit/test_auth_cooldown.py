"""Unit tests for auth cooldown, session probing, and cookie locking.

Covers the original issue #36 behaviour plus the issue #54 fixes:
  - Per-process-only cooldown (no cross-process via disk)
  - Cookie-validity check before launching browser auth
  - MCP tool error surfacing on auth failure
  - _read_last_auth_attempt is now a no-op
"""

import pickle
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
import responses

from dc_support_mcp.constants import AUTH_COOLDOWN, COOKIE_MAX_AGE, ORI_API_ENDPOINT, ORI_BASE_URL
from dc_support_mcp.vendors.iren import IrenVendorHandler
from dc_support_mcp.vendors.ori import OriVendorHandler


def _make_iren(mock_credentials, tmp_path):
    handler = IrenVendorHandler(
        email=mock_credentials["email"],
        password=mock_credentials["password"],
        use_cached_cookies=False,
        verbose=False,
    )
    handler.cookie_file = tmp_path / "iren_cookies.pkl"
    return handler


# ── Per-process cooldown (issue #54 fix 1) ────────────────────────────


@pytest.mark.unit
class TestAuthCooldown:
    """_guarded_authenticate refuses to login within AUTH_COOLDOWN window."""

    def test_first_auth_attempt_allowed(self, ori_handler):
        with (
            patch.object(ori_handler, "_load_cookies", return_value=False),
            patch.object(ori_handler, "_authenticate_with_browser", return_value=True) as mock,
        ):
            result = ori_handler._guarded_authenticate()
        assert result is True
        mock.assert_called_once()

    def test_second_auth_blocked_within_cooldown(self, ori_handler):
        ori_handler._last_auth_attempt = datetime.now()

        with patch.object(ori_handler, "_authenticate_with_browser") as mock:
            result = ori_handler._guarded_authenticate()

        assert result is False
        mock.assert_not_called()

    def test_cooldown_sets_last_error(self, ori_handler):
        ori_handler._last_auth_attempt = datetime.now()
        ori_handler._guarded_authenticate()
        assert ori_handler.last_error is not None
        assert "cooldown" in ori_handler.last_error.lower()

    def test_auth_allowed_after_cooldown_expires(self, ori_handler):
        ori_handler._last_auth_attempt = datetime.now() - AUTH_COOLDOWN - timedelta(seconds=1)

        with (
            patch.object(ori_handler, "_load_cookies", return_value=False),
            patch.object(ori_handler, "_authenticate_with_browser", return_value=True) as mock,
        ):
            result = ori_handler._guarded_authenticate()

        assert result is True
        mock.assert_called_once()

    def test_authenticate_with_browser_records_timestamp(self, mock_credentials, tmp_path):
        with patch.object(OriVendorHandler, "_authenticate_with_browser", return_value=True):
            handler = OriVendorHandler(
                email=mock_credentials["email"],
                password=mock_credentials["password"],
                use_cached_cookies=False,
            )
        handler.cookie_file = tmp_path / "cookies.pkl"

        assert handler._last_auth_attempt is None

        with patch("playwright.sync_api.sync_playwright") as mock_pw:
            mock_browser = MagicMock()
            mock_context = MagicMock()
            mock_page = MagicMock()
            mock_context.cookies.return_value = []
            mock_browser.new_context.return_value = mock_context
            mock_context.new_page.return_value = mock_page
            mock_pw.return_value.__enter__ = MagicMock(
                return_value=MagicMock(
                    chromium=MagicMock(launch=MagicMock(return_value=mock_browser))
                )
            )
            mock_pw.return_value.__exit__ = MagicMock(return_value=False)

            handler._authenticate_with_browser()

        assert handler._last_auth_attempt is not None
        assert (datetime.now() - handler._last_auth_attempt).total_seconds() < 5


# ── Successful auth does not self-block (issue #65) ──────────────────


@pytest.mark.unit
class TestSuccessfulAuthDoesNotSelfBlock:
    """After a successful auth, re-auth is allowed within the cooldown window."""

    def test_successful_auth_allows_reauth_within_cooldown(self, ori_handler):
        """Core issue #65 fix: successful login must not block subsequent re-auth."""
        ori_handler._last_auth_attempt = datetime.now()
        ori_handler._last_auth_succeeded = True

        with (
            patch.object(ori_handler, "_load_cookies", return_value=False),
            patch.object(ori_handler, "_probe_session", return_value=False),
            patch.object(ori_handler, "_authenticate_with_browser", return_value=True) as mock,
        ):
            result = ori_handler._guarded_authenticate()

        assert result is True
        mock.assert_called_once()

    def test_failed_auth_still_blocks_within_cooldown(self, ori_handler):
        """Failed auth must still trigger the cooldown (lockout prevention)."""
        ori_handler._last_auth_attempt = datetime.now()
        ori_handler._last_auth_succeeded = False

        with patch.object(ori_handler, "_authenticate_with_browser") as mock:
            result = ori_handler._guarded_authenticate()

        assert result is False
        mock.assert_not_called()

    def test_guarded_authenticate_tracks_success(self, ori_handler):
        """_guarded_authenticate sets _last_auth_succeeded = True on success."""
        with (
            patch.object(ori_handler, "_load_cookies", return_value=False),
            patch.object(ori_handler, "_probe_session", return_value=False),
            patch.object(ori_handler, "_authenticate_with_browser", return_value=True),
        ):
            ori_handler._guarded_authenticate()

        assert ori_handler._last_auth_succeeded is True

    def test_guarded_authenticate_tracks_failure(self, ori_handler):
        """_guarded_authenticate sets _last_auth_succeeded = False on failure."""
        with (
            patch.object(ori_handler, "_load_cookies", return_value=False),
            patch.object(ori_handler, "_probe_session", return_value=False),
            patch.object(ori_handler, "_authenticate_with_browser", return_value=False),
        ):
            ori_handler._guarded_authenticate()

        assert ori_handler._last_auth_succeeded is False

    def test_cold_start_double_auth_not_blocked(self, ori_handler):
        """Simulates the cold-start scenario from issue #65.

        1. First auth succeeds (sets _last_auth_attempt + _last_auth_succeeded=True)
        2. Second auth triggered by session expiry within cooldown window
        3. Second auth must NOT be blocked
        """

        def fake_browser_auth_success():
            ori_handler._last_auth_attempt = datetime.now()
            return True

        with (
            patch.object(ori_handler, "_load_cookies", return_value=False),
            patch.object(ori_handler, "_probe_session", return_value=False),
            patch.object(
                ori_handler, "_authenticate_with_browser", side_effect=fake_browser_auth_success
            ),
        ):
            ori_handler._guarded_authenticate()

        assert ori_handler._last_auth_succeeded is True
        assert ori_handler._last_auth_attempt is not None

        with (
            patch.object(ori_handler, "_load_cookies", return_value=False),
            patch.object(ori_handler, "_probe_session", return_value=False),
            patch.object(ori_handler, "_authenticate_with_browser", return_value=True) as mock2,
        ):
            result = ori_handler._guarded_authenticate()

        assert result is True
        mock2.assert_called_once()

    def test_iren_successful_auth_allows_reauth(self, mock_credentials, tmp_path):
        """IREN: successful auth must not block subsequent re-auth (issue #65)."""
        handler = _make_iren(mock_credentials, tmp_path)
        handler._last_auth_attempt = datetime.now()
        handler._last_auth_succeeded = True

        with (
            patch.object(handler, "_load_cookies", return_value=False),
            patch.object(handler, "_authenticate_with_browser", return_value=True) as mock,
        ):
            result = handler._guarded_authenticate()

        assert result is True
        mock.assert_called_once()

    def test_iren_failed_auth_still_blocks(self, mock_credentials, tmp_path):
        """IREN: failed auth must still trigger the cooldown."""
        handler = _make_iren(mock_credentials, tmp_path)
        handler._last_auth_attempt = datetime.now()
        handler._last_auth_succeeded = False

        with patch.object(handler, "_authenticate_with_browser") as mock:
            result = handler._guarded_authenticate()

        assert result is False
        mock.assert_not_called()

    def test_iren_guarded_authenticate_tracks_result(self, mock_credentials, tmp_path):
        """IREN: _guarded_authenticate records success/failure in _last_auth_succeeded."""
        handler = _make_iren(mock_credentials, tmp_path)

        with (
            patch.object(handler, "_load_cookies", return_value=False),
            patch.object(handler, "_authenticate_with_browser", return_value=True),
        ):
            handler._guarded_authenticate()
        assert handler._last_auth_succeeded is True

        handler._last_auth_attempt = None
        with (
            patch.object(handler, "_load_cookies", return_value=False),
            patch.object(handler, "_authenticate_with_browser", return_value=False),
        ):
            handler._guarded_authenticate()
        assert handler._last_auth_succeeded is False


# ── Disk cooldown is no longer used (issue #54 fix 1) ────────────────


@pytest.mark.unit
class TestCrossProcessCooldownRemoved:
    """Verify that cross-process cooldown via the cookie file is gone."""

    def test_disk_timestamp_does_not_block_auth(self, ori_handler, tmp_path):
        """Even when the pickle has a recent last_auth_attempt, auth proceeds."""
        ori_handler.cookie_file = tmp_path / "cookies.pkl"
        recent_ts = datetime.now() - timedelta(seconds=30)
        cookie_data = {
            "cookies": [],
            "timestamp": datetime.now(),
            "last_auth_attempt": recent_ts,
        }
        with open(ori_handler.cookie_file, "wb") as f:
            pickle.dump(cookie_data, f)

        with (
            patch.object(ori_handler, "_load_cookies", return_value=False),
            patch.object(ori_handler, "_authenticate_with_browser", return_value=True) as mock,
        ):
            result = ori_handler._guarded_authenticate()

        assert result is True
        mock.assert_called_once()

    def test_save_cookies_does_not_persist_last_auth_attempt(self, mock_credentials, tmp_path):
        with patch.object(OriVendorHandler, "_authenticate_with_browser"):
            handler = OriVendorHandler(
                email=mock_credentials["email"],
                password=mock_credentials["password"],
                use_cached_cookies=False,
            )
        handler.cookie_file = tmp_path / "cookies.pkl"
        handler._last_auth_attempt = datetime.now() - timedelta(seconds=30)

        handler._save_cookies([{"name": "s", "value": "v", "domain": ".x.net", "path": "/"}])

        with open(handler.cookie_file, "rb") as f:
            data = pickle.load(f)

        assert "last_auth_attempt" not in data

    def test_iren_save_cookies_does_not_persist_last_auth_attempt(self, mock_credentials, tmp_path):
        handler = _make_iren(mock_credentials, tmp_path)
        handler._last_auth_attempt = datetime.now() - timedelta(seconds=10)

        handler._save_cookies([{"name": "s", "value": "v", "domain": ".iren.com", "path": "/"}])

        with open(handler.cookie_file, "rb") as f:
            data = pickle.load(f)

        assert "last_auth_attempt" not in data

    def test_two_independent_handlers_have_independent_cooldown(self, mock_credentials, tmp_path):
        """Two handler instances (simulating two processes) do not share cooldown."""
        with patch.object(OriVendorHandler, "_authenticate_with_browser"):
            h1 = OriVendorHandler(
                email=mock_credentials["email"],
                password=mock_credentials["password"],
                use_cached_cookies=False,
            )
            h2 = OriVendorHandler(
                email=mock_credentials["email"],
                password=mock_credentials["password"],
                use_cached_cookies=False,
            )
        h1.cookie_file = tmp_path / "cookies.pkl"
        h2.cookie_file = tmp_path / "cookies.pkl"

        h1._last_auth_attempt = datetime.now()

        with (
            patch.object(h2, "_load_cookies", return_value=False),
            patch.object(h2, "_authenticate_with_browser", return_value=True) as mock,
        ):
            result = h2._guarded_authenticate()

        assert result is True
        mock.assert_called_once()


# ── Multi-process auth isolation (issue #53) ─────────────────────────


@pytest.mark.unit
class TestMultiProcessAuthIsolation:
    """Verify that concurrent processes sharing a cookie file cannot deadlock.

    Issue #53: when cooldown was persisted to the shared cookie pickle,
    any process's auth attempt would reset the cooldown for ALL processes,
    creating an infinite loop.  Now cooldown is purely in-memory, so each
    process (handler instance) tracks its own cooldown independently.
    """

    @staticmethod
    def _fake_browser_fail(handler):
        """Side-effect that mimics _authenticate_with_browser on failure."""
        def _side_effect():
            handler._last_auth_attempt = datetime.now()
            return False
        return _side_effect

    def test_failed_auth_in_one_handler_does_not_block_another_ori(
        self, mock_credentials, tmp_path
    ):
        """ORI: process A fails auth -> process B can still authenticate."""
        cookie_file = tmp_path / "shared_cookies.pkl"

        with patch.object(OriVendorHandler, "_authenticate_with_browser"):
            proc_a = OriVendorHandler(
                email=mock_credentials["email"],
                password=mock_credentials["password"],
                use_cached_cookies=False,
            )
            proc_b = OriVendorHandler(
                email=mock_credentials["email"],
                password=mock_credentials["password"],
                use_cached_cookies=False,
            )
        proc_a.cookie_file = cookie_file
        proc_b.cookie_file = cookie_file

        # Process A tries auth and fails — enters cooldown
        with (
            patch.object(proc_a, "_load_cookies", return_value=False),
            patch.object(
                proc_a, "_authenticate_with_browser",
                side_effect=self._fake_browser_fail(proc_a),
            ),
        ):
            proc_a._guarded_authenticate()

        assert proc_a._last_auth_succeeded is False
        assert proc_a._last_auth_attempt is not None

        # Process A is now blocked by its own cooldown
        assert proc_a._guarded_authenticate() is False

        # Process B must NOT be blocked — it has no cooldown of its own
        with (
            patch.object(proc_b, "_load_cookies", return_value=False),
            patch.object(proc_b, "_authenticate_with_browser", return_value=True) as mock_b,
        ):
            result = proc_b._guarded_authenticate()

        assert result is True
        mock_b.assert_called_once()

    def test_failed_auth_in_one_handler_does_not_block_another_iren(
        self, mock_credentials, tmp_path
    ):
        """IREN: process A fails auth -> process B can still authenticate."""
        cookie_file = tmp_path / "shared_iren_cookies.pkl"

        proc_a = _make_iren(mock_credentials, tmp_path)
        proc_b = _make_iren(mock_credentials, tmp_path)
        proc_a.cookie_file = cookie_file
        proc_b.cookie_file = cookie_file

        with (
            patch.object(proc_a, "_load_cookies", return_value=False),
            patch.object(
                proc_a, "_authenticate_with_browser",
                side_effect=self._fake_browser_fail(proc_a),
            ),
        ):
            proc_a._guarded_authenticate()

        assert proc_a._last_auth_succeeded is False
        assert proc_a._guarded_authenticate() is False

        with (
            patch.object(proc_b, "_load_cookies", return_value=False),
            patch.object(proc_b, "_authenticate_with_browser", return_value=True) as mock_b,
        ):
            result = proc_b._guarded_authenticate()

        assert result is True
        mock_b.assert_called_once()

    def test_rapid_alternating_auth_does_not_deadlock(self, mock_credentials, tmp_path):
        """Simulates the rapid fire scenario from issue #53.

        Multiple handlers share a cookie file and attempt auth in quick
        succession.  None should be blocked by another's cooldown.
        """
        cookie_file = tmp_path / "shared_cookies.pkl"

        with patch.object(OriVendorHandler, "_authenticate_with_browser"):
            handlers = [
                OriVendorHandler(
                    email=mock_credentials["email"],
                    password=mock_credentials["password"],
                    use_cached_cookies=False,
                )
                for _ in range(3)
            ]
        for h in handlers:
            h.cookie_file = cookie_file

        # Handler 0 fails, enters cooldown
        with (
            patch.object(handlers[0], "_load_cookies", return_value=False),
            patch.object(
                handlers[0], "_authenticate_with_browser",
                side_effect=self._fake_browser_fail(handlers[0]),
            ),
        ):
            handlers[0]._guarded_authenticate()

        # Handlers 1 and 2 must still be able to attempt auth
        for h in handlers[1:]:
            with (
                patch.object(h, "_load_cookies", return_value=False),
                patch.object(h, "_authenticate_with_browser", return_value=True) as mock_auth,
            ):
                result = h._guarded_authenticate()
            assert result is True
            mock_auth.assert_called_once()

    def test_cookie_file_written_by_one_process_usable_by_another(
        self, mock_credentials, tmp_path
    ):
        """Process A saves cookies; process B loads them without needing browser auth."""
        cookie_file = tmp_path / "shared_cookies.pkl"

        with patch.object(OriVendorHandler, "_authenticate_with_browser"):
            proc_a = OriVendorHandler(
                email=mock_credentials["email"],
                password=mock_credentials["password"],
                use_cached_cookies=False,
            )
            proc_b = OriVendorHandler(
                email=mock_credentials["email"],
                password=mock_credentials["password"],
                use_cached_cookies=False,
            )
        proc_a.cookie_file = cookie_file
        proc_b.cookie_file = cookie_file

        # Process A saves valid cookies
        proc_a._save_cookies(
            [{"name": "session", "value": "abc", "domain": ".atlassian.net", "path": "/"}]
        )

        # Process B picks them up via _load_cookies + _probe_session
        with (
            patch.object(proc_b, "_probe_session", return_value=True),
            patch.object(proc_b, "_authenticate_with_browser") as mock_browser,
        ):
            result = proc_b._guarded_authenticate()

        assert result is True
        mock_browser.assert_not_called()


# ── Cookie-validity check before browser auth (issue #54 fix 2) ──────


@pytest.mark.unit
class TestCookieValidityBeforeAuth:
    """_guarded_authenticate checks cookies before launching Playwright."""

    def test_skips_browser_when_cookies_valid(self, ori_handler):
        """If _load_cookies + _probe_session pass, browser auth is skipped."""
        with (
            patch.object(ori_handler, "_load_cookies", return_value=True),
            patch.object(ori_handler, "_probe_session", return_value=True),
            patch.object(ori_handler, "_authenticate_with_browser") as mock_browser,
        ):
            result = ori_handler._guarded_authenticate()

        assert result is True
        mock_browser.assert_not_called()
        assert ori_handler.last_error is None

    def test_falls_through_to_browser_when_probe_fails(self, ori_handler):
        with (
            patch.object(ori_handler, "_load_cookies", return_value=True),
            patch.object(ori_handler, "_probe_session", return_value=False),
            patch.object(
                ori_handler, "_authenticate_with_browser", return_value=True
            ) as mock_browser,
        ):
            result = ori_handler._guarded_authenticate()

        assert result is True
        mock_browser.assert_called_once()

    def test_falls_through_to_browser_when_no_cookies(self, ori_handler):
        with (
            patch.object(ori_handler, "_load_cookies", return_value=False),
            patch.object(
                ori_handler, "_authenticate_with_browser", return_value=True
            ) as mock_browser,
        ):
            result = ori_handler._guarded_authenticate()

        assert result is True
        mock_browser.assert_called_once()

    def test_iren_skips_browser_when_cookies_valid(self, mock_credentials, tmp_path):
        handler = _make_iren(mock_credentials, tmp_path)
        with (
            patch.object(handler, "_load_cookies", return_value=True),
            patch.object(handler, "_authenticate_with_browser") as mock_browser,
        ):
            result = handler._guarded_authenticate()

        assert result is True
        mock_browser.assert_not_called()


# ── Session probing ──────────────────────────────────────────────────


@pytest.mark.unit
class TestSessionProbe:
    """_probe_session validates cookies with a lightweight server request."""

    @responses.activate
    def test_probe_returns_true_on_200(self, ori_handler):
        responses.add(
            responses.GET,
            f"{ORI_BASE_URL}/servicedesk/customer/portals",
            status=200,
        )
        assert ori_handler._probe_session() is True

    @responses.activate
    def test_probe_returns_false_on_401(self, ori_handler):
        responses.add(
            responses.GET,
            f"{ORI_BASE_URL}/servicedesk/customer/portals",
            status=401,
        )
        assert ori_handler._probe_session() is False

    @responses.activate
    def test_probe_returns_false_on_403(self, ori_handler):
        responses.add(
            responses.GET,
            f"{ORI_BASE_URL}/servicedesk/customer/portals",
            status=403,
        )
        assert ori_handler._probe_session() is False

    @responses.activate
    def test_probe_returns_false_on_login_redirect(self, ori_handler):
        responses.add(
            responses.GET,
            f"{ORI_BASE_URL}/servicedesk/customer/portals",
            status=302,
            headers={"Location": "/servicedesk/customer/user/login?dest=..."},
        )
        assert ori_handler._probe_session() is False

    @responses.activate
    def test_probe_returns_true_on_non_login_redirect(self, ori_handler):
        responses.add(
            responses.GET,
            f"{ORI_BASE_URL}/servicedesk/customer/portals",
            status=302,
            headers={"Location": "/servicedesk/customer/portal/3"},
        )
        assert ori_handler._probe_session() is True

    def test_probe_returns_true_on_network_error(self, ori_handler):
        """Network errors don't prove auth failure; let the real call decide."""
        import requests

        with patch.object(
            ori_handler.session, "get", side_effect=requests.ConnectionError("timeout")
        ):
            assert ori_handler._probe_session() is True


# ── Cookie load with probe ───────────────────────────────────────────


@pytest.mark.unit
class TestCookieLoadWithProbe:
    """_load_cookies validates server-side via _probe_session for stale cookies."""

    def test_load_cookies_clears_on_server_rejection(self, mock_credentials, tmp_path):
        """Cookies >1h old are probed; if server rejects, cookies are cleared."""
        with patch.object(OriVendorHandler, "_authenticate_with_browser"):
            handler = OriVendorHandler(
                email=mock_credentials["email"],
                password=mock_credentials["password"],
                use_cached_cookies=False,
            )
        handler.cookie_file = tmp_path / "cookies.pkl"

        cookie_data = {
            "cookies": [
                {"name": "session", "value": "abc", "domain": ".atlassian.net", "path": "/"}
            ],
            "timestamp": datetime.now() - timedelta(hours=2),
        }
        with open(handler.cookie_file, "wb") as f:
            pickle.dump(cookie_data, f)

        with patch.object(handler, "_probe_session", return_value=False):
            result = handler._load_cookies()

        assert result is False
        assert len(handler.session.cookies) == 0

    def test_load_cookies_succeeds_when_probe_passes(self, mock_credentials, tmp_path):
        with patch.object(OriVendorHandler, "_authenticate_with_browser"):
            handler = OriVendorHandler(
                email=mock_credentials["email"],
                password=mock_credentials["password"],
                use_cached_cookies=False,
            )
        handler.cookie_file = tmp_path / "cookies.pkl"

        cookie_data = {
            "cookies": [
                {"name": "session", "value": "abc", "domain": ".atlassian.net", "path": "/"}
            ],
            "timestamp": datetime.now() - timedelta(hours=2),
        }
        with open(handler.cookie_file, "wb") as f:
            pickle.dump(cookie_data, f)

        with patch.object(handler, "_probe_session", return_value=True):
            result = handler._load_cookies()

        assert result is True
        assert handler.session.cookies.get("session") == "abc"

    def test_fresh_cookies_skip_probe(self, mock_credentials, tmp_path):
        """Cookies <1h old skip the network probe entirely."""
        with patch.object(OriVendorHandler, "_authenticate_with_browser"):
            handler = OriVendorHandler(
                email=mock_credentials["email"],
                password=mock_credentials["password"],
                use_cached_cookies=False,
            )
        handler.cookie_file = tmp_path / "cookies.pkl"

        cookie_data = {
            "cookies": [
                {"name": "session", "value": "fresh", "domain": ".atlassian.net", "path": "/"}
            ],
            "timestamp": datetime.now() - timedelta(minutes=30),
        }
        with open(handler.cookie_file, "wb") as f:
            pickle.dump(cookie_data, f)

        with patch.object(handler, "_probe_session") as mock_probe:
            result = handler._load_cookies()

        assert result is True
        mock_probe.assert_not_called()


# ── Sliding window refresh ───────────────────────────────────────────


@pytest.mark.unit
class TestSlidingWindowRefresh:
    """_refresh_cookie_timestamp slides the expiry forward on success."""

    def test_timestamp_updated_on_refresh(self, mock_credentials, tmp_path):
        with patch.object(OriVendorHandler, "_authenticate_with_browser"):
            handler = OriVendorHandler(
                email=mock_credentials["email"],
                password=mock_credentials["password"],
                use_cached_cookies=False,
            )
        handler.cookie_file = tmp_path / "cookies.pkl"

        old_ts = datetime.now() - timedelta(hours=1)
        cookie_data = {
            "cookies": [{"name": "s", "value": "v", "domain": ".x.net", "path": "/"}],
            "timestamp": old_ts,
        }
        with open(handler.cookie_file, "wb") as f:
            pickle.dump(cookie_data, f)

        handler._refresh_cookie_timestamp()

        with open(handler.cookie_file, "rb") as f:
            data = pickle.load(f)

        assert data["timestamp"] > old_ts
        assert (datetime.now() - data["timestamp"]).total_seconds() < 5

    def test_refresh_noop_when_no_cookie_file(self, mock_credentials, tmp_path):
        with patch.object(OriVendorHandler, "_authenticate_with_browser"):
            handler = OriVendorHandler(
                email=mock_credentials["email"],
                password=mock_credentials["password"],
                use_cached_cookies=False,
            )
        handler.cookie_file = tmp_path / "nonexistent_cookies.pkl"
        handler._refresh_cookie_timestamp()  # should not raise


# ── _request_with_reauth cooldown ────────────────────────────────────


@pytest.mark.unit
class TestRequestWithReauthCooldown:
    """_request_with_reauth respects cooldown on re-auth."""

    @responses.activate
    def test_reauth_uses_guarded_authenticate(self, ori_handler):
        """On 401, _request_with_reauth calls _guarded_authenticate, not raw auth."""
        responses.add(
            responses.POST,
            f"{ORI_BASE_URL}{ORI_API_ENDPOINT}",
            status=401,
        )
        responses.add(
            responses.POST,
            f"{ORI_BASE_URL}{ORI_API_ENDPOINT}",
            json={"ok": True},
            status=200,
        )

        with patch.object(ori_handler, "_guarded_authenticate", return_value=True) as mock_guard:
            with patch.object(ori_handler, "_refresh_cookie_timestamp"):
                result = ori_handler._request_with_reauth(
                    "post", f"{ORI_BASE_URL}{ORI_API_ENDPOINT}", json={}
                )

        mock_guard.assert_called_once()
        assert result is not None
        assert result.status_code == 200

    @responses.activate
    def test_returns_original_response_when_cooldown_blocks_reauth(self, ori_handler):
        """When cooldown blocks re-auth, the original 401 response is returned."""
        ori_handler._last_auth_attempt = datetime.now()

        responses.add(
            responses.POST,
            f"{ORI_BASE_URL}{ORI_API_ENDPOINT}",
            status=401,
        )

        with patch.object(ori_handler, "_refresh_cookie_timestamp"):
            result = ori_handler._request_with_reauth(
                "post", f"{ORI_BASE_URL}{ORI_API_ENDPOINT}", json={}
            )

        assert result is not None
        assert result.status_code == 401

    @responses.activate
    def test_successful_request_refreshes_timestamp(self, ori_handler):
        responses.add(
            responses.POST,
            f"{ORI_BASE_URL}{ORI_API_ENDPOINT}",
            json={"ok": True},
            status=200,
        )

        with patch.object(ori_handler, "_refresh_cookie_timestamp") as mock_refresh:
            ori_handler._request_with_reauth("post", f"{ORI_BASE_URL}{ORI_API_ENDPOINT}", json={})

        mock_refresh.assert_called_once()


# ── Cookie max age ───────────────────────────────────────────────────


@pytest.mark.unit
class TestCookieMaxAgeIncreased:
    """Verify COOKIE_MAX_AGE is now 8 hours."""

    def test_cookie_max_age_is_8_hours(self):
        assert COOKIE_MAX_AGE == timedelta(hours=8)

    def test_7_hour_old_cookies_still_valid(self, mock_credentials, tmp_path):
        with patch.object(OriVendorHandler, "_authenticate_with_browser"):
            handler = OriVendorHandler(
                email=mock_credentials["email"],
                password=mock_credentials["password"],
                use_cached_cookies=False,
            )
        handler.cookie_file = tmp_path / "cookies.pkl"

        cookie_data = {
            "cookies": [{"name": "s", "value": "v", "domain": ".x.net", "path": "/"}],
            "timestamp": datetime.now() - timedelta(hours=7),
        }
        with open(handler.cookie_file, "wb") as f:
            pickle.dump(cookie_data, f)

        with patch.object(handler, "_probe_session", return_value=True):
            assert handler._load_cookies() is True

    def test_9_hour_old_cookies_rejected(self, mock_credentials, tmp_path):
        with patch.object(OriVendorHandler, "_authenticate_with_browser"):
            handler = OriVendorHandler(
                email=mock_credentials["email"],
                password=mock_credentials["password"],
                use_cached_cookies=False,
            )
        handler.cookie_file = tmp_path / "cookies.pkl"

        cookie_data = {
            "cookies": [{"name": "s", "value": "v", "domain": ".x.net", "path": "/"}],
            "timestamp": datetime.now() - timedelta(hours=9),
        }
        with open(handler.cookie_file, "wb") as f:
            pickle.dump(cookie_data, f)

        assert handler._load_cookies() is False


# ── IREN cooldown tests ──────────────────────────────────────────────


@pytest.mark.unit
class TestIrenAuthCooldown:
    """IREN _guarded_authenticate mirrors the Atlassian cooldown behaviour."""

    def test_first_auth_allowed(self, mock_credentials, tmp_path):
        handler = _make_iren(mock_credentials, tmp_path)
        with (
            patch.object(handler, "_load_cookies", return_value=False),
            patch.object(handler, "_authenticate_with_browser", return_value=True) as mock,
        ):
            result = handler._guarded_authenticate()
        assert result is True
        mock.assert_called_once()

    def test_second_auth_blocked_within_cooldown(self, mock_credentials, tmp_path):
        handler = _make_iren(mock_credentials, tmp_path)
        handler._last_auth_attempt = datetime.now()

        with patch.object(handler, "_authenticate_with_browser") as mock:
            result = handler._guarded_authenticate()

        assert result is False
        mock.assert_not_called()

    def test_auth_allowed_after_cooldown(self, mock_credentials, tmp_path):
        handler = _make_iren(mock_credentials, tmp_path)
        handler._last_auth_attempt = datetime.now() - AUTH_COOLDOWN - timedelta(seconds=1)

        with (
            patch.object(handler, "_load_cookies", return_value=False),
            patch.object(handler, "_authenticate_with_browser", return_value=True) as mock,
        ):
            result = handler._guarded_authenticate()

        assert result is True
        mock.assert_called_once()

    def test_disk_timestamp_does_not_block_iren_auth(self, mock_credentials, tmp_path):
        """Disk last_auth_attempt no longer blocks IREN auth (issue #54)."""
        handler = _make_iren(mock_credentials, tmp_path)

        recent_ts = datetime.now() - timedelta(seconds=30)
        cookie_data = {
            "cookies": [],
            "timestamp": datetime.now(),
            "last_auth_attempt": recent_ts,
        }
        with open(handler.cookie_file, "wb") as f:
            pickle.dump(cookie_data, f)

        with (
            patch.object(handler, "_load_cookies", return_value=False),
            patch.object(handler, "_authenticate_with_browser", return_value=True) as mock,
        ):
            result = handler._guarded_authenticate()

        assert result is True
        mock.assert_called_once()

    def test_authenticate_with_browser_records_timestamp(self, mock_credentials, tmp_path):
        handler = _make_iren(mock_credentials, tmp_path)
        assert handler._last_auth_attempt is None

        with patch.object(handler, "_ensure_browser_context"):
            handler._page = MagicMock()
            handler._browser_context = MagicMock()
            handler._page.locator.return_value.count.return_value = 1
            handler._authenticate_with_browser()

        assert handler._last_auth_attempt is not None


# ── Atomic write tests ───────────────────────────────────────────────


@pytest.mark.unit
class TestAtomicCookieWrites:
    """_atomic_pickle_write uses temp + os.replace for crash safety."""

    def test_atomic_write_produces_valid_pickle(self, mock_credentials, tmp_path):
        with patch.object(OriVendorHandler, "_authenticate_with_browser"):
            handler = OriVendorHandler(
                email=mock_credentials["email"],
                password=mock_credentials["password"],
                use_cached_cookies=False,
            )
        handler.cookie_file = tmp_path / "cookies.pkl"

        payload = {"cookies": [{"name": "a", "value": "b"}], "timestamp": datetime.now()}
        handler._atomic_pickle_write(payload)

        with open(handler.cookie_file, "rb") as f:
            loaded = pickle.load(f)
        assert loaded["cookies"] == payload["cookies"]

    def test_atomic_write_no_leftover_temp_files(self, mock_credentials, tmp_path):
        with patch.object(OriVendorHandler, "_authenticate_with_browser"):
            handler = OriVendorHandler(
                email=mock_credentials["email"],
                password=mock_credentials["password"],
                use_cached_cookies=False,
            )
        handler.cookie_file = tmp_path / "cookies.pkl"

        handler._atomic_pickle_write({"cookies": [], "timestamp": datetime.now()})

        tmp_files = [f for f in tmp_path.iterdir() if f.suffix == ".tmp"]
        assert len(tmp_files) == 0

    def test_atomic_write_cleans_up_on_failure(self, mock_credentials, tmp_path):
        with patch.object(OriVendorHandler, "_authenticate_with_browser"):
            handler = OriVendorHandler(
                email=mock_credentials["email"],
                password=mock_credentials["password"],
                use_cached_cookies=False,
            )
        handler.cookie_file = tmp_path / "cookies.pkl"

        handler._atomic_pickle_write(
            {"cookies": [{"name": "original"}], "timestamp": datetime.now()}
        )

        with pytest.raises((TypeError, AttributeError, pickle.PicklingError)):
            handler._atomic_pickle_write({"bad": lambda: None})

        with open(handler.cookie_file, "rb") as f:
            data = pickle.load(f)
        assert data["cookies"] == [{"name": "original"}]

        tmp_files = [f for f in tmp_path.iterdir() if f.suffix == ".tmp"]
        assert len(tmp_files) == 0

    def test_iren_atomic_write(self, mock_credentials, tmp_path):
        handler = _make_iren(mock_credentials, tmp_path)
        payload = {"cookies": [{"name": "x"}], "timestamp": datetime.now()}
        handler._atomic_pickle_write(payload)

        with open(handler.cookie_file, "rb") as f:
            loaded = pickle.load(f)
        assert loaded["cookies"] == payload["cookies"]


# ── MCP tool error surfacing (issue #54 fix 3) ──────────────────────


@pytest.mark.unit
class TestMcpToolErrorSurfacing:
    """list_vendor_tickets returns error dict when auth fails."""

    @staticmethod
    def _make_handler_with_auth_error(method_name, return_value, error_msg):
        """Create a mock handler whose *method_name* sets last_error on call.

        This simulates real behaviour: last_error is cleared at tool
        start, then set during the handler method when auth fails.
        """
        mock_handler = MagicMock()
        mock_handler.last_error = None

        def side_effect(*args, **kwargs):
            mock_handler.last_error = error_msg
            return return_value

        getattr(mock_handler, method_name).side_effect = side_effect
        return mock_handler

    def test_list_vendor_tickets_surfaces_auth_error(self):
        from dc_support_mcp.mcp_server import list_vendor_tickets

        mock_handler = self._make_handler_with_auth_error(
            "list_tickets", [], "Auth cooldown active (270s remaining)."
        )
        with patch("dc_support_mcp.mcp_server._get_handler", return_value=mock_handler):
            result = list_vendor_tickets(vendor="ori", status="open", limit=20)

        assert "error" in result
        assert "Auth failure" in result["error"]

    def test_list_vendor_tickets_returns_empty_when_no_auth_error(self):
        from dc_support_mcp.mcp_server import list_vendor_tickets

        mock_handler = MagicMock()
        mock_handler.list_tickets.return_value = []
        mock_handler.last_error = None

        with patch("dc_support_mcp.mcp_server._get_handler", return_value=mock_handler):
            result = list_vendor_tickets(vendor="ori", status="open", limit=20)

        assert "error" not in result
        assert result["tickets"] == []
        assert result["count"] == 0

    def test_get_vendor_ticket_surfaces_auth_error(self):
        from dc_support_mcp.mcp_server import get_vendor_ticket

        mock_handler = self._make_handler_with_auth_error(
            "get_ticket", None, "Browser login failed: Incorrect password"
        )
        with patch("dc_support_mcp.mcp_server._get_handler", return_value=mock_handler):
            result = get_vendor_ticket(ticket_id="SUPP-1234", vendor="ori")

        assert "error" in result
        assert "Auth failure" in result["error"]

    def test_get_vendor_ticket_normal_not_found(self):
        from dc_support_mcp.mcp_server import get_vendor_ticket

        mock_handler = MagicMock()
        mock_handler.get_ticket.return_value = None
        mock_handler.last_error = None

        with patch("dc_support_mcp.mcp_server._get_handler", return_value=mock_handler):
            result = get_vendor_ticket(ticket_id="SUPP-9999", vendor="ori")

        assert "error" in result
        assert "not found" in result["error"]

    def test_add_vendor_comment_surfaces_auth_error(self):
        from dc_support_mcp.mcp_server import add_vendor_comment

        mock_handler = self._make_handler_with_auth_error(
            "add_comment", None, "Auth cooldown active (270s remaining)."
        )
        with patch("dc_support_mcp.mcp_server._get_handler", return_value=mock_handler):
            result = add_vendor_comment(ticket_id="SUPP-1234", comment="test", vendor="ori")

        assert "error" in result
        assert "Auth failure" in result["error"]
        assert "remediation" in result

    def test_update_vendor_ticket_status_surfaces_auth_error(self):
        from dc_support_mcp.mcp_server import update_vendor_ticket_status

        mock_handler = self._make_handler_with_auth_error(
            "update_ticket_status", None, "Auth cooldown active (270s remaining)."
        )
        with patch("dc_support_mcp.mcp_server._get_handler", return_value=mock_handler):
            result = update_vendor_ticket_status(
                ticket_id="SUPP-1234", status="resolved", vendor="ori"
            )

        assert "error" in result
        assert "Auth failure" in result["error"]
        assert "remediation" in result

    def test_search_vendor_kb_surfaces_auth_error(self):
        from dc_support_mcp.mcp_server import search_vendor_kb

        mock_handler = self._make_handler_with_auth_error(
            "search_knowledge_base", None, "Auth cooldown active (270s remaining)."
        )
        with patch("dc_support_mcp.mcp_server._get_handler", return_value=mock_handler):
            result = search_vendor_kb(query="test", vendor="iren")

        assert "error" in result
        assert "Auth failure" in result["error"]
        assert "remediation" in result

    def test_get_vendor_kb_article_surfaces_auth_error(self):
        from dc_support_mcp.mcp_server import get_vendor_kb_article

        mock_handler = self._make_handler_with_auth_error(
            "get_kb_article", None, "Auth cooldown active (270s remaining)."
        )
        with patch("dc_support_mcp.mcp_server._get_handler", return_value=mock_handler):
            result = get_vendor_kb_article(article_id="12345", vendor="iren")

        assert "error" in result
        assert "Auth failure" in result["error"]
        assert "remediation" in result

    def test_auth_error_includes_remediation(self):
        from dc_support_mcp.mcp_server import list_vendor_tickets

        mock_handler = self._make_handler_with_auth_error(
            "list_tickets", [], "Auth cooldown active (270s remaining)."
        )
        with patch("dc_support_mcp.mcp_server._get_handler", return_value=mock_handler):
            result = list_vendor_tickets(vendor="ori", status="open", limit=20)

        assert "error" in result
        assert "remediation" in result
        assert "Wait 5 minutes" in result["remediation"]

    def test_last_error_cleared_at_tool_start(self):
        from dc_support_mcp.mcp_server import get_vendor_ticket

        mock_handler = MagicMock()
        mock_handler.last_error = "stale error from previous call"
        mock_handler.get_ticket.return_value = {"id": "SUPP-1", "summary": "ok"}

        with patch("dc_support_mcp.mcp_server._get_handler", return_value=mock_handler):
            result = get_vendor_ticket(ticket_id="SUPP-1", vendor="ori")

        assert "error" not in result
        assert result["id"] == "SUPP-1"
