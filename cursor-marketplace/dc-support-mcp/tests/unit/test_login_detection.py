"""Tests for issue #24: ORI login failure and logged-out API response detection."""

from unittest.mock import MagicMock, patch

import pytest
import responses

from dc_support_mcp.constants import (
    ATLASSIAN_API_ENDPOINT,
    ATLASSIAN_SESSION_COOKIE_NAMES,
    ORI_BASE_URL,
)
from dc_support_mcp.vendors.atlassian_base import AtlassianServiceDeskHandler
from dc_support_mcp.vendors.ori import OriVendorHandler

# ── _is_logged_out_response ──────────────────────────────────────────


@pytest.mark.unit
class TestIsLoggedOutResponse:
    """Detect the _lout XSRF token suffix that Atlassian returns for anonymous sessions."""

    def test_lout_suffix_detected(self):
        data = {"xsrfToken": "abc123_lout"}
        assert AtlassianServiceDeskHandler._is_logged_out_response(data) is True

    def test_normal_token_passes(self):
        data = {"xsrfToken": "abc123_valid"}
        assert AtlassianServiceDeskHandler._is_logged_out_response(data) is False

    def test_missing_token_passes(self):
        assert AtlassianServiceDeskHandler._is_logged_out_response({}) is False

    def test_empty_token_passes(self):
        assert AtlassianServiceDeskHandler._is_logged_out_response({"xsrfToken": ""}) is False

    def test_non_string_token_passes(self):
        assert AtlassianServiceDeskHandler._is_logged_out_response({"xsrfToken": 123}) is False

    def test_lout_in_middle_not_detected(self):
        data = {"xsrfToken": "abc_lout_extra"}
        assert AtlassianServiceDeskHandler._is_logged_out_response(data) is False

    def test_bare_lout_detected(self):
        data = {"xsrfToken": "_lout"}
        assert AtlassianServiceDeskHandler._is_logged_out_response(data) is True


# ── _has_session_cookies ─────────────────────────────────────────────


@pytest.mark.unit
class TestHasSessionCookies:
    """Validate that captured cookies include real session tokens."""

    def test_cloud_session_token_found(self):
        cookies = [
            {"name": "cloud.session.token", "value": "abc", "domain": ".atlassian.net"},
            {"name": "tracking_cookie", "value": "xyz", "domain": ".atlassian.net"},
        ]
        assert AtlassianServiceDeskHandler._has_session_cookies(cookies) is True

    def test_tenant_session_token_found(self):
        cookies = [
            {"name": "tenant.session.token", "value": "abc", "domain": ".atlassian.net"},
        ]
        assert AtlassianServiceDeskHandler._has_session_cookies(cookies) is True

    def test_session_id_found(self):
        cookies = [
            {"name": "_session_id", "value": "abc", "domain": ".atlassian.net"},
        ]
        assert AtlassianServiceDeskHandler._has_session_cookies(cookies) is True

    def test_no_session_cookies(self):
        cookies = [
            {"name": "atlassian.xsrf.token", "value": "x", "domain": ".atlassian.net"},
            {"name": "_ga", "value": "y", "domain": ".atlassian.net"},
        ]
        assert AtlassianServiceDeskHandler._has_session_cookies(cookies) is False

    def test_empty_cookies(self):
        assert AtlassianServiceDeskHandler._has_session_cookies([]) is False

    def test_missing_name_key_ignored(self):
        cookies = [{"value": "orphan"}]
        assert AtlassianServiceDeskHandler._has_session_cookies(cookies) is False

    def test_constants_cover_expected_names(self):
        for name in ("cloud.session.token", "tenant.session.token", "_session_id"):
            assert name in ATLASSIAN_SESSION_COOKIE_NAMES


# ── _check_login_error ───────────────────────────────────────────────


@pytest.mark.unit
class TestCheckLoginError:
    """Detect login error messages after form submission."""

    def _make_handler(self, tmp_path):
        with patch.object(OriVendorHandler, "_authenticate_with_browser"):
            handler = OriVendorHandler(
                email="test@example.com",
                password="bad_password",
                use_cached_cookies=False,
                verbose=False,
            )
        handler.cookie_file = tmp_path / "cookies.pkl"
        return handler

    def test_detects_error_message_class(self, tmp_path):
        handler = self._make_handler(tmp_path)
        page = MagicMock()
        error_el = MagicMock()
        error_el.is_visible.return_value = True
        error_el.text_content.return_value = "Incorrect password. Try again"

        def locator_side_effect(selector):
            mock = MagicMock()
            if selector == ".error-message":
                mock.first = error_el
            else:
                mock.first = MagicMock(is_visible=MagicMock(return_value=False))
            return mock

        page.locator = locator_side_effect
        result = handler._check_login_error(page)
        assert result == "Incorrect password. Try again"

    def test_detects_form_error_testid(self, tmp_path):
        handler = self._make_handler(tmp_path)
        page = MagicMock()

        def locator_side_effect(selector):
            mock = MagicMock()
            if selector == '[data-testid="form-error"]':
                el = MagicMock()
                el.is_visible.return_value = True
                el.text_content.return_value = "Invalid credentials"
                mock.first = el
            elif selector == "body":
                el = MagicMock()
                el.text_content.return_value = ""
                mock.first = el
                return mock
            else:
                el = MagicMock()
                el.is_visible.return_value = False
                mock.first = el
            return mock

        page.locator = locator_side_effect
        result = handler._check_login_error(page)
        assert result == "Invalid credentials"

    def test_detects_incorrect_password_in_body(self, tmp_path):
        handler = self._make_handler(tmp_path)
        page = MagicMock()

        def locator_side_effect(selector):
            mock = MagicMock()
            if selector == "body":
                mock.text_content.return_value = "Some text with Incorrect password in the page"
                return mock
            else:
                el = MagicMock()
                el.is_visible.return_value = False
                mock.first = el
            return mock

        page.locator = locator_side_effect
        result = handler._check_login_error(page)
        assert result == "Incorrect password"

    def test_no_error_returns_none(self, tmp_path):
        handler = self._make_handler(tmp_path)
        page = MagicMock()

        def locator_side_effect(selector):
            mock = MagicMock()
            if selector == "body":
                mock.text_content.return_value = "Welcome to the portal"
                return mock
            else:
                el = MagicMock()
                el.is_visible.return_value = False
                mock.first = el
            return mock

        page.locator = locator_side_effect
        result = handler._check_login_error(page)
        assert result is None


# ── _dismiss_cookie_banner ───────────────────────────────────────────


@pytest.mark.unit
class TestDismissCookieBanner:
    """Click cookie consent banner if present."""

    def _make_handler(self, tmp_path):
        with patch.object(OriVendorHandler, "_authenticate_with_browser"):
            handler = OriVendorHandler(
                email="test@example.com",
                password="testpass",
                use_cached_cookies=False,
                verbose=True,
            )
        handler.cookie_file = tmp_path / "cookies.pkl"
        return handler

    def test_clicks_accept_all_when_visible(self, tmp_path):
        handler = self._make_handler(tmp_path)
        page = MagicMock()
        btn = MagicMock()
        btn.is_visible.return_value = True
        page.get_by_role.return_value = btn

        handler._dismiss_cookie_banner(page)
        btn.click.assert_called_once()

    def test_falls_back_to_css_selectors(self, tmp_path):
        from playwright.sync_api import Error as PlaywrightError

        handler = self._make_handler(tmp_path)
        page = MagicMock()
        page.get_by_role.side_effect = PlaywrightError("not found")

        btn = MagicMock()
        btn.is_visible.return_value = True

        def locator_side_effect(selector):
            mock = MagicMock()
            if "Accept all" in selector:
                mock.first = btn
            else:
                invisible = MagicMock()
                invisible.is_visible.return_value = False
                mock.first = invisible
            return mock

        page.locator = locator_side_effect
        handler._dismiss_cookie_banner(page)
        btn.click.assert_called_once()

    def test_noop_when_no_banner(self, tmp_path):
        from playwright.sync_api import Error as PlaywrightError

        handler = self._make_handler(tmp_path)
        page = MagicMock()
        page.get_by_role.side_effect = PlaywrightError("not found")

        def locator_side_effect(selector):
            mock = MagicMock()
            el = MagicMock()
            el.is_visible.return_value = False
            mock.first = el
            return mock

        page.locator = locator_side_effect
        page.evaluate.return_value = False
        handler._dismiss_cookie_banner(page)

    def test_handles_playwright_error(self, tmp_path):
        from playwright.sync_api import Error as PlaywrightError

        handler = self._make_handler(tmp_path)
        page = MagicMock()
        page.get_by_role.side_effect = PlaywrightError("not found")

        def locator_side_effect(selector):
            raise PlaywrightError("element not found")

        page.locator = locator_side_effect
        page.evaluate.side_effect = PlaywrightError("evaluate failed")
        handler._dismiss_cookie_banner(page)  # should not raise


# ── _make_api_request with logged-out detection ──────────────────────


@pytest.mark.unit
class TestMakeApiRequestLoggedOutDetection:
    """_make_api_request detects _lout tokens and triggers re-auth."""

    @responses.activate
    def test_lout_triggers_reauth_and_retries(self, ori_handler):
        api_url = f"{ORI_BASE_URL}{ATLASSIAN_API_ENDPOINT}"

        responses.add(
            responses.POST,
            api_url,
            json={"xsrfToken": "abc_lout", "allReqFilter": {}},
            status=200,
        )
        responses.add(
            responses.POST,
            api_url,
            json={"xsrfToken": "real_token", "allReqFilter": {"requestList": []}},
            status=200,
        )

        with (
            patch.object(ori_handler, "_guarded_authenticate", return_value=True),
            patch.object(ori_handler, "_refresh_cookie_timestamp"),
        ):
            result = ori_handler._make_api_request({"models": ["allReqFilter"]})

        assert result is not None
        assert result["xsrfToken"] == "real_token"

    @responses.activate
    def test_lout_reauth_fails_returns_none(self, ori_handler):
        api_url = f"{ORI_BASE_URL}{ATLASSIAN_API_ENDPOINT}"

        responses.add(
            responses.POST,
            api_url,
            json={"xsrfToken": "abc_lout"},
            status=200,
        )

        with (
            patch.object(ori_handler, "_guarded_authenticate", return_value=False),
            patch.object(ori_handler, "_refresh_cookie_timestamp"),
        ):
            result = ori_handler._make_api_request({"models": []})

        assert result is None

    @responses.activate
    def test_normal_response_returned_directly(self, ori_handler):
        api_url = f"{ORI_BASE_URL}{ATLASSIAN_API_ENDPOINT}"

        responses.add(
            responses.POST,
            api_url,
            json={"xsrfToken": "valid_token", "data": "ok"},
            status=200,
        )

        with patch.object(ori_handler, "_refresh_cookie_timestamp"):
            result = ori_handler._make_api_request({"models": []})

        assert result is not None
        assert result["data"] == "ok"

    @responses.activate
    def test_lout_retry_still_lout_returns_none(self, ori_handler):
        """If re-auth succeeds but retry still returns _lout, give up."""
        api_url = f"{ORI_BASE_URL}{ATLASSIAN_API_ENDPOINT}"

        responses.add(
            responses.POST,
            api_url,
            json={"xsrfToken": "first_lout"},
            status=200,
        )
        responses.add(
            responses.POST,
            api_url,
            json={"xsrfToken": "still_lout"},
            status=200,
        )

        with (
            patch.object(ori_handler, "_guarded_authenticate", return_value=True),
            patch.object(ori_handler, "_refresh_cookie_timestamp"),
        ):
            result = ori_handler._make_api_request({"models": []})

        assert result is None


# ── _authenticate_with_browser integration with new checks ───────────


@pytest.mark.unit
class TestAuthenticateWithBrowserLoginDetection:
    """_authenticate_with_browser uses _check_login_error and _has_session_cookies."""

    def test_login_error_returns_false(self, mock_credentials, tmp_path):
        with patch.object(OriVendorHandler, "_authenticate_with_browser", return_value=True):
            handler = OriVendorHandler(
                email=mock_credentials["email"],
                password=mock_credentials["password"],
                use_cached_cookies=False,
            )
        handler.cookie_file = tmp_path / "cookies.pkl"
        handler.verbose = True

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

            with patch.object(handler, "_check_login_error", return_value="Incorrect password"):
                result = handler._authenticate_with_browser()

        assert result is False

    def test_no_session_cookies_still_returns_true_with_warning(self, mock_credentials, tmp_path):
        """Login succeeds but warns if no session cookies were captured."""
        with patch.object(OriVendorHandler, "_authenticate_with_browser", return_value=True):
            handler = OriVendorHandler(
                email=mock_credentials["email"],
                password=mock_credentials["password"],
                use_cached_cookies=False,
            )
        handler.cookie_file = tmp_path / "cookies.pkl"
        handler.verbose = True

        anon_cookies = [
            {
                "name": "atlassian.xsrf.token",
                "value": "tok",
                "domain": ".atlassian.net",
                "path": "/",
            },
        ]

        with patch("playwright.sync_api.sync_playwright") as mock_pw:
            mock_browser = MagicMock()
            mock_context = MagicMock()
            mock_page = MagicMock()
            mock_context.cookies.return_value = anon_cookies
            mock_browser.new_context.return_value = mock_context
            mock_context.new_page.return_value = mock_page
            mock_pw.return_value.__enter__ = MagicMock(
                return_value=MagicMock(
                    chromium=MagicMock(launch=MagicMock(return_value=mock_browser))
                )
            )
            mock_pw.return_value.__exit__ = MagicMock(return_value=False)

            with patch.object(handler, "_check_login_error", return_value=None):
                result = handler._authenticate_with_browser()

        assert result is True
