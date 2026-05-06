"""
Shared Atlassian Service Desk handler base class.

Provides authentication, cookie management, ticket CRUD, and comment
operations for any Atlassian Service Desk portal. Vendor-specific
subclasses only need to set class-level config attributes.
"""

import html
import json
import os
import pickle
import re
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

import requests

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from collections.abc import Iterator

    from playwright.sync_api import Page
from bs4 import BeautifulSoup

from ..constants import (
    API_TIMEOUT,
    ATLASSIAN_API_ENDPOINT,
    ATLASSIAN_SESSION_COOKIE_NAMES,
    AUTH_COOLDOWN,
    BROWSER_COOKIE_BANNER_TIMEOUT,
    BROWSER_LOGIN_ERROR_TIMEOUT,
    BROWSER_LOGIN_STEP_TIMEOUT,
    BROWSER_NAVIGATION_TIMEOUT,
    BROWSER_POST_LOGIN_WAIT,
    COOKIE_MAX_AGE,
    HTTP_CREATED,
    HTTP_FORBIDDEN,
    HTTP_OK,
    HTTP_UNAUTHORIZED,
    SESSION_PROBE_TIMEOUT,
)
from ..decorators import verbose_log
from ..formatting import markdown_to_wiki, sanitize_for_vendor
from ..types import CommentData, CookieData, SimplifiedTicketData, TicketData
from ..vendor_handler import VendorHandler


class AtlassianServiceDeskHandler(VendorHandler):
    """
    Base handler for Atlassian Service Desk portals.

    Authenticates via Playwright browser automation to capture httpOnly cookies,
    then uses requests library for fast API operations.

    Subclasses must set these class attributes:
        VENDOR_NAME: str            -- e.g. "ori", "hypertec"
        BASE_URL: str               -- e.g. "https://oriindustries.atlassian.net"
        PORTAL_ID: int              -- Service Desk portal number
        TICKET_ID_PREFIX: str       -- e.g. "SUPP", "HTCSR"
        COOKIE_FILE_NAME: str       -- e.g. ".ori_session_cookies.pkl"

    Optionally override:
        HELP_CENTER_ARI: str | None -- Atlassian resource identifier (None to omit)
    """

    VENDOR_NAME: ClassVar[str]
    BASE_URL: ClassVar[str]
    PORTAL_ID: ClassVar[int]
    TICKET_ID_PREFIX: ClassVar[str]
    COOKIE_FILE_NAME: ClassVar[str]
    HELP_CENTER_ARI: ClassVar[str | None] = None

    _STATUS_MAP: ClassVar[dict[str, str]] = {
        "open": "OPEN_REQUESTS",
        "closed": "CLOSED_REQUESTS",
        "all": "ALL_REQUESTS",
    }

    def __init__(
        self, email: str, password: str, use_cached_cookies: bool = True, verbose: bool = True
    ):
        self.email = email
        self.password = password
        self.verbose = verbose
        self.cookie_file = Path.home() / self.COOKIE_FILE_NAME
        self._ticket_pattern = re.compile(rf"{self.TICKET_ID_PREFIX}-\d+")
        self._last_auth_attempt: datetime | None = None
        self._last_auth_succeeded: bool = False

        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Content-Type": "application/json",
                "Accept": "*/*",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{self.BASE_URL}/servicedesk/customer/portal/{self.PORTAL_ID}",
            }
        )

        if use_cached_cookies and self._load_cookies():
            if self.verbose:
                sys.stderr.write(f"✓ Using cached {self.VENDOR_NAME} session cookies\n")
        else:
            if self.verbose:
                sys.stderr.write(
                    f"→ Authenticating to {self.VENDOR_NAME} via browser automation...\n"
                )
            self._guarded_authenticate()

    def _validate_ticket_id(self, ticket_id: str) -> None:
        """Validate ticket ID matches this vendor's expected format."""
        if not re.match(rf"^{self.TICKET_ID_PREFIX}-\d+$", ticket_id):
            raise ValueError(
                f"Invalid ticket ID format: {ticket_id}. "
                f"Expected format: {self.TICKET_ID_PREFIX}-####"
            )

    def _load_cookies(self) -> bool:
        """Load cookies from cache file if valid both locally and server-side."""
        if not self.cookie_file.exists():
            return False

        try:
            with self._cookie_lock(exclusive=False):
                with open(self.cookie_file, "rb") as f:
                    data = pickle.load(f)

            cookies = data.get("cookies")
            timestamp = data.get("timestamp")

            cookie_age = datetime.now() - timestamp
            if cookie_age > COOKIE_MAX_AGE:
                if self.verbose:
                    sys.stderr.write(
                        f"  Cached cookies expired (age: {cookie_age.total_seconds() / 3600:.1f}h)\n"
                    )
                return False

            for cookie in cookies:
                self.session.cookies.set(
                    cookie["name"],
                    cookie["value"],
                    domain=cookie.get("domain"),
                    path=cookie.get("path"),
                )

            if cookie_age > timedelta(hours=1) and not self._probe_session():
                if self.verbose:
                    sys.stderr.write("  Cached cookies rejected by server\n")
                self.session.cookies.clear()
                return False

            return True

        except (OSError, pickle.PickleError, KeyError) as e:
            if self.verbose:
                sys.stderr.write(f"  Failed to load cached cookies: {e}\n")
            return False

    def _save_cookies(self, cookies: list[CookieData]) -> None:
        """Save cookies to cache file with exclusive lock.

        Uses atomic write (temp file + os.replace) so a crash mid-write
        cannot corrupt the cookie cache.
        """
        try:
            data = {
                "cookies": cookies,
                "timestamp": datetime.now(),
            }
            with self._cookie_lock(exclusive=True):
                self._atomic_pickle_write(data)
            if self.verbose:
                sys.stderr.write(f"  Saved {len(cookies)} cookies to cache\n")
        except (OSError, pickle.PickleError) as e:
            if self.verbose:
                sys.stderr.write(f"  Warning: Failed to save cookies: {e}\n")

    def _refresh_cookie_timestamp(self) -> None:
        """Slide the cookie expiry window forward after a successful API call."""
        if not self.cookie_file.exists():
            return
        try:
            with self._cookie_lock(exclusive=True):
                with open(self.cookie_file, "rb") as f:
                    data = pickle.load(f)
                data["timestamp"] = datetime.now()
                self._atomic_pickle_write(data)
        except (OSError, pickle.PickleError):
            pass

    def _atomic_pickle_write(self, data: dict[str, Any]) -> None:
        """Write *data* to ``self.cookie_file`` atomically.

        Writes to a temp file in the same directory, then replaces the
        target in one ``os.replace`` call so a crash mid-write never
        leaves a truncated/corrupt pickle on disk.
        """
        fd, tmp_path = tempfile.mkstemp(
            dir=self.cookie_file.parent,
            prefix=f".{self.cookie_file.stem}.",
            suffix=".tmp",
        )
        try:
            with open(fd, "wb") as f:
                pickle.dump(data, f)
            os.replace(tmp_path, self.cookie_file)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    # ── Session probing & file locking ────────────────────────────────

    def _probe_session(self) -> bool:
        """Lightweight check: can the current cookies reach the portal?

        Returns True if the session is still valid server-side, False if
        the server redirects to login or returns an auth error.
        """
        probe_url = f"{self.BASE_URL}/servicedesk/customer/portals"
        try:
            r = self.session.get(probe_url, timeout=SESSION_PROBE_TIMEOUT, allow_redirects=False)
            if r.status_code in (HTTP_UNAUTHORIZED, HTTP_FORBIDDEN):
                return False
            location = r.headers.get("Location", "")
            if "/user/login" in location:
                return False
            if r.status_code >= 300 and r.status_code < 400:
                return "/user/login" not in location
            return r.status_code == HTTP_OK
        except requests.RequestException:
            return True  # network error ≠ auth failure; let the real call decide

    @contextmanager
    def _cookie_lock(self, *, exclusive: bool = False) -> "Iterator[None]":
        """Advisory flock on a vendor-specific lock file.

        Degrades gracefully to a no-op on platforms without ``fcntl``
        (e.g. Windows) so the tool remains usable everywhere.
        """
        if fcntl is None:
            yield
            return

        lockfile = self.cookie_file.with_suffix(".lock")
        fd = open(lockfile, "a+")
        try:
            mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            try:
                fcntl.flock(fd, mode)
            except OSError:
                pass
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            fd.close()

    # ── Authentication ────────────────────────────────────────────────

    def authenticate(self) -> bool:
        """Authenticate with the portal."""
        return bool(self._guarded_authenticate())

    def _guarded_authenticate(self) -> bool:
        """Authenticate with cooldown protection to prevent account lockout.

        Refuses to run a Playwright login if one was attempted within the
        last AUTH_COOLDOWN window *by this process* AND that attempt failed.
        Successful auth does not trigger the cooldown — the protection only
        prevents repeated failed logins that could lock the account (see
        issue #65).

        The cooldown is tracked only in-memory so that concurrent processes
        sharing the same cookie file do not deadlock each other (issue #54).

        Before launching the browser, checks whether another process has
        already stored valid cookies (via ``_load_cookies``).  If so, the
        expensive Playwright auth is skipped entirely.

        TODO(issue-54): extract shared auth mixin — this cooldown logic is
        duplicated in IrenVendorHandler._guarded_authenticate.
        """
        now = datetime.now()
        last = self._last_auth_attempt
        if last and not self._last_auth_succeeded and (now - last) < AUTH_COOLDOWN:
            elapsed = (now - last).total_seconds()
            cooldown_remaining = AUTH_COOLDOWN.total_seconds() - elapsed
            sys.stderr.write(
                f"  ⚠ {self.VENDOR_NAME}: auth cooldown active "
                f"({cooldown_remaining:.0f}s remaining, last attempt "
                f"{elapsed:.0f}s ago). Skipping browser login to prevent lockout.\n"
            )
            self.last_error = (
                f"Auth cooldown active ({cooldown_remaining:.0f}s remaining). "
                "Skipping browser login to prevent account lockout."
            )
            return False

        if self._load_cookies() and self._probe_session():
            if self.verbose:
                sys.stderr.write(
                    f"  ✓ {self.VENDOR_NAME}: another process refreshed cookies, "
                    "skipping browser auth\n"
                )
            self.last_error = None
            return True

        try:
            result = self._authenticate_with_browser()
        except Exception:
            self._last_auth_succeeded = False
            raise
        self._last_auth_succeeded = result
        return result

    @verbose_log("Authenticating via browser automation", "Authentication completed")
    def _authenticate_with_browser(self) -> bool:
        """Use Playwright to authenticate and extract cookies."""
        self._last_auth_attempt = datetime.now()
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise ImportError(
                "Playwright required for authentication.\n"
                "Install with: uv pip install playwright && playwright install chromium"
            ) from e

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()

            try:
                login_url = f"{self.BASE_URL}/servicedesk/customer/user/login"
                page.goto(
                    login_url, wait_until="domcontentloaded", timeout=BROWSER_NAVIGATION_TIMEOUT
                )

                self._fill_login_form(page)

                login_error = self._check_login_error(page)
                if login_error:
                    if self.verbose:
                        sys.stderr.write(f"  ✗ Login failed: {login_error}\n")
                    self.last_error = f"Browser login failed: {login_error}"
                    return False

                try:
                    page.wait_for_url(
                        "**/servicedesk/customer/**", timeout=BROWSER_NAVIGATION_TIMEOUT
                    )
                except PlaywrightError as e:
                    if self.verbose:
                        sys.stderr.write(f"  Warning: Redirect timeout: {e}\n")

                page.wait_for_timeout(BROWSER_POST_LOGIN_WAIT)

                page.goto(f"{self.BASE_URL}/servicedesk/customer/portals")
                page.wait_for_load_state("domcontentloaded")

                cookies = context.cookies()

                if not self._has_session_cookies(cookies):  # type: ignore[arg-type]
                    if self.verbose:
                        names = sorted({c["name"] for c in cookies})
                        sys.stderr.write(
                            f"  ⚠ No session cookies captured (got: {names}). "
                            "Login may have failed silently.\n"
                        )

                for cookie in cookies:
                    self.session.cookies.set(
                        cookie["name"],
                        cookie["value"],
                        domain=cookie.get("domain"),
                        path=cookie.get("path"),
                    )

                self._save_cookies(cookies)  # type: ignore[arg-type]
                self.last_error = None

                return True

            finally:
                browser.close()

    def _fill_login_form(self, page: "Page") -> None:
        """Fill the Atlassian login form (email -> Next -> password -> Continue)."""
        from playwright.sync_api import Error as PlaywrightError

        self._dismiss_cookie_banner(page)

        try:
            page.get_by_label("Email address").fill(self.email)
        except PlaywrightError:
            page.locator("input").first.fill(self.email)

        try:
            page.get_by_role("button", name="Next").click()
        except PlaywrightError:
            page.locator("button").first.click()

        # Wait for the password field instead of networkidle — Atlassian
        # keeps background XHR alive so networkidle never fires (#56).
        try:
            page.get_by_label("Password").wait_for(
                state="visible", timeout=BROWSER_LOGIN_STEP_TIMEOUT
            )
        except PlaywrightError:
            page.locator('input[type="password"]').wait_for(
                state="visible", timeout=BROWSER_LOGIN_STEP_TIMEOUT
            )

        self._dismiss_cookie_banner(page)

        try:
            page.get_by_label("Password").fill(self.password)
        except PlaywrightError:
            page.locator('input[type="password"]').fill(self.password)

        try:
            page.get_by_role("button", name="Continue").click()
        except PlaywrightError:
            try:
                page.get_by_role("button", name="Log in").click()
            except PlaywrightError:
                page.locator('button[type="submit"]').click()

        page.wait_for_timeout(BROWSER_POST_LOGIN_WAIT)

    def _dismiss_cookie_banner(self, page: "Page") -> None:
        """Click cookie consent banner if present, preventing it from blocking form interactions."""
        from playwright.sync_api import Error as PlaywrightError

        try:
            btn = page.get_by_role("button", name="Accept all")
            if btn.is_visible(timeout=BROWSER_COOKIE_BANNER_TIMEOUT):
                btn.click()
                if self.verbose:
                    sys.stderr.write("  Dismissed cookie consent banner\n")
                return
        except PlaywrightError:
            pass

        selectors = [
            'button:has-text("Accept all")',
            'button:has-text("Accept All")',
            'button:has-text("Accept cookies")',
            'button[id*="accept"]',
            'button[data-testid="cookie-accept"]',
        ]
        for selector in selectors:
            try:
                btn = page.locator(selector).first
                if btn.is_visible(timeout=BROWSER_COOKIE_BANNER_TIMEOUT):
                    btn.click()
                    if self.verbose:
                        sys.stderr.write("  Dismissed cookie consent banner\n")
                    return
            except PlaywrightError:
                continue

        try:
            dismissed = page.evaluate(
                """() => {
                    for (const btn of document.querySelectorAll('button')) {
                        if (btn.textContent.trim().toLowerCase().includes('accept all')) {
                            btn.click();
                            return true;
                        }
                    }
                    return false;
                }"""
            )
            if dismissed and self.verbose:
                sys.stderr.write("  Dismissed cookie consent banner (JS fallback)\n")
        except PlaywrightError:
            pass

    def _check_login_error(self, page: "Page") -> str | None:
        """Check for login error messages after form submission.

        Returns the error message text if found, None if no error detected.
        Ignores non-error alerts (e.g. "Cookie preferences saved").
        """
        from playwright.sync_api import Error as PlaywrightError

        non_error_phrases = ("cookie", "preferences saved")

        error_selectors = [
            ".error-message",
            '[data-testid="form-error"]',
            '[role="alert"]',
            "#login-error",
        ]
        for selector in error_selectors:
            try:
                el = page.locator(selector).first
                if el.is_visible(timeout=BROWSER_LOGIN_ERROR_TIMEOUT):
                    text = (el.text_content() or "").strip()
                    if text and not any(p in text.lower() for p in non_error_phrases):
                        return text
            except PlaywrightError:
                continue

        # Fallback: check for known error text anywhere on the page
        try:
            body_text = page.locator("body").text_content() or ""
            for phrase in ("Incorrect password", "invalid credentials", "login failed"):
                if phrase.lower() in body_text.lower():
                    return phrase
        except PlaywrightError:
            pass

        return None

    @staticmethod
    def _is_logged_out_response(data: dict[str, Any]) -> bool:
        """Detect a logged-out API response by the ``_lout`` XSRF token suffix.

        Atlassian returns HTTP 200 with ``{"xsrfToken": "..._lout"}`` when the
        session is not authenticated, instead of a proper 401/403.
        """
        xsrf = data.get("xsrfToken", "")
        return isinstance(xsrf, str) and xsrf.endswith("_lout")

    @staticmethod
    def _has_session_cookies(cookies: list[CookieData]) -> bool:
        """Check that captured cookies include real session tokens.

        Returns True if at least one cookie name matches the expected
        Atlassian session cookie names, False if only anonymous/tracking
        cookies were captured.
        """
        cookie_names = {c.get("name", "") for c in cookies}
        return bool(cookie_names & ATLASSIAN_SESSION_COOKIE_NAMES)

    @staticmethod
    def _is_login_redirect(response: requests.Response) -> bool:
        """Detect when Atlassian silently redirects to the login page."""
        return "/user/login" in response.url

    def _request_with_reauth(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> requests.Response | None:
        """Make an HTTP request, re-authenticating once on 401/403
        or when the response is a silent redirect to the login page.

        Uses cooldown-guarded authentication to prevent account lockout
        from rapid successive login attempts.  On success, slides the
        cookie-cache timestamp forward to extend its useful life.
        """
        kwargs.setdefault("timeout", API_TIMEOUT)
        try:
            response: requests.Response = getattr(self.session, method)(url, **kwargs)

            needs_reauth = response.status_code in (
                HTTP_UNAUTHORIZED,
                HTTP_FORBIDDEN,
            ) or self._is_login_redirect(response)
            if needs_reauth:
                if self.verbose:
                    sys.stderr.write(
                        f"  {self.VENDOR_NAME} session expired, re-authenticating...\n"
                    )
                if not self._guarded_authenticate():
                    return response  # return the original failed response
                response = getattr(self.session, method)(url, **kwargs)

            if response.status_code == HTTP_OK:
                self._refresh_cookie_timestamp()

            return response

        except (requests.RequestException, ValueError) as e:
            if self.verbose:
                sys.stderr.write(f"  Request failed: {e}\n")
            return None

    def _build_api_context(self) -> dict[str, str]:
        """Build the context dict for API requests.

        Subclasses with a HELP_CENTER_ARI get it included automatically.
        """
        ctx: dict[str, str] = {
            "clientBasePath": f"{self.BASE_URL}/servicedesk/customer",
        }
        if self.HELP_CENTER_ARI:
            ctx["helpCenterAri"] = self.HELP_CENTER_ARI
        return ctx

    def _make_api_request(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Make API request with current session cookies.

        Detects logged-out responses (XSRF token ending in ``_lout``) and
        triggers re-authentication before retrying once.
        """
        api_url = f"{self.BASE_URL}{ATLASSIAN_API_ENDPOINT}"
        response = self._request_with_reauth("post", api_url, json=payload)

        if response is None:
            return None
        if response.status_code == HTTP_OK:
            data = dict(response.json())
            if self._is_logged_out_response(data):
                if self.verbose:
                    sys.stderr.write(
                        f"  ✗ {self.VENDOR_NAME}: API returned logged-out token, "
                        "re-authenticating...\n"
                    )
                if self._guarded_authenticate():
                    retry = self._request_with_reauth("post", api_url, json=payload)
                    if retry is not None and retry.status_code == HTTP_OK:
                        retry_data = dict(retry.json())
                        if not self._is_logged_out_response(retry_data):
                            return retry_data
                if self.verbose:
                    sys.stderr.write(f"  ✗ {self.VENDOR_NAME}: still logged out after re-auth\n")
                return None
            return data

        if self.verbose:
            sys.stderr.write(f"  API error: {response.status_code}\n")
        return None

    # ── Ticket operations ──────────────────────────────────────────────────────

    def get_ticket(self, ticket_id: str) -> dict[str, Any] | None:
        """Fetch a ticket by ID using fast API call."""
        self._validate_ticket_id(ticket_id)

        payload = {
            "options": {
                "reqDetails": {"key": ticket_id, "portalId": self.PORTAL_ID},
                "portalId": self.PORTAL_ID,
            },
            "models": ["reqDetails"],
            "context": self._build_api_context(),
        }

        data = self._make_api_request(payload)

        if not data or "reqDetails" not in data:
            return None

        return dict(self._parse_ticket_data(data["reqDetails"]))

    def _parse_ticket_data(self, req_details: dict[str, Any]) -> TicketData:
        """Parse ticket data from API response."""
        issue = req_details.get("issue", {})

        comments: list[CommentData] = []
        for activity in issue.get("activityStream", []):
            if activity.get("type") in ["requester-comment", "worker-comment"]:
                comments.append(
                    {
                        "author": activity.get("author", "Unknown"),
                        "date": activity.get("friendlyDate", "Unknown"),
                        "comment": activity.get("rawComment", ""),
                        "type": activity.get("type", "unknown"),
                    }
                )

        return {
            "id": issue.get("key", ""),
            "summary": issue.get("summary", ""),
            "status": issue.get("status", "Unknown"),
            "reporter": issue.get("reporter", {}).get("displayName", "Unknown"),
            "assignee": issue.get("assignee", {}).get("displayName", "Unassigned"),
            "created": issue.get("friendlyDate", "Unknown"),
            "comments": comments,
            "url": (
                f"{self.BASE_URL}/servicedesk/customer"
                f"/portal/{self.PORTAL_ID}/{issue.get('key', '')}"
            ),
        }

    def list_tickets(self, status: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        """List tickets via the Service Desk REST API."""
        result = self._list_requests_api(status=status or "open", limit=limit)
        if result is not None:
            return [dict(t) for t in result]

        if self.verbose:
            sys.stderr.write("  REST API failed, falling back to HTML scraping\n")
        fallback = self._list_requests_html(status=status or "open", limit=limit)
        return [dict(t) for t in fallback] if fallback else []

    def _list_requests_api(
        self, status: str = "open", limit: int = 50
    ) -> list[SimplifiedTicketData] | None:
        """List requests via the internal customer models API."""
        api_status = self._STATUS_MAP.get(status, "open")
        payload = {
            "options": {
                "allReqFilter": {
                    "portalId": self.PORTAL_ID,
                    "status": api_status,
                    "reporter": "all",
                    "page": 1,
                },
                "portalId": self.PORTAL_ID,
            },
            "models": ["allReqFilter"],
            "context": self._build_api_context(),
        }

        data = self._make_api_request(payload)
        if not data or "allReqFilter" not in data:
            return None

        request_list = data["allReqFilter"].get("requestList", [])
        tickets: list[SimplifiedTicketData] = []
        for req in request_list[:limit]:
            ticket_id = req.get("key", "")
            if not self._ticket_pattern.match(ticket_id):
                continue
            portal_base = req.get(
                "portalBaseUrl",
                f"/servicedesk/customer/portal/{self.PORTAL_ID}",
            )
            tickets.append(
                {
                    "id": ticket_id,
                    "summary": req.get("summary", ""),
                    "status": req.get("status", "Unknown"),
                    "created": req.get("friendlyDate", "Unknown"),
                    "assignee": req.get("assignee", "Unknown"),
                    "url": f"{self.BASE_URL}{portal_base}/{ticket_id}",
                }
            )
        return tickets

    def _list_requests_html(
        self, page: int = 1, status: str = "open", reporter: str = "all", limit: int = 50
    ) -> list[SimplifiedTicketData]:
        """Fallback: list requests by scraping the customer portal HTML."""
        url = f"{self.BASE_URL}/servicedesk/customer/user/requests"
        params = {"page": page, "reporter": reporter, "statuses": status}

        response = self._request_with_reauth("get", url, params=params)
        if response is None or response.status_code != HTTP_OK:
            if self.verbose and response is not None:
                sys.stderr.write(f"  Failed to list requests: {response.status_code}\n")
            return []

        if self._is_login_redirect(response):
            if self.verbose:
                sys.stderr.write("  Still on login page after re-auth, giving up\n")
            return []

        try:
            tickets = self._parse_requests_json(response.text, limit)
            if tickets is not None:
                return tickets

            if self.verbose:
                sys.stderr.write("  Embedded JSON not found, falling back to link scraping\n")
            return self._parse_requests_links(response.text, status, limit)

        except (ValueError, AttributeError, KeyError) as e:
            if self.verbose:
                sys.stderr.write(f"  Error parsing requests page: {e}\n")
            return []

    def _parse_requests_json(self, page_html: str, limit: int) -> list[SimplifiedTicketData] | None:
        """Extract ticket list from the embedded JSON state blob."""
        idx = page_html.find("allReqFilter")
        if idx < 0:
            return None

        bracket_idx = page_html.rfind(">{", 0, idx)
        if bracket_idx < 0:
            return None

        close_tag = page_html.find("</", bracket_idx + 1)
        if close_tag < 0:
            return None

        encoded = page_html[bracket_idx + 1 : close_tag]
        decoded = html.unescape(encoded)
        data = json.loads(decoded)

        request_list = data.get("allReqFilter", {}).get("requestList", [])
        tickets: list[SimplifiedTicketData] = []
        for req in request_list[:limit]:
            ticket_id = req.get("key", "")
            if not self._ticket_pattern.match(ticket_id):
                continue
            portal_base = req.get("portalBaseUrl", f"/servicedesk/customer/portal/{self.PORTAL_ID}")
            tickets.append(
                {
                    "id": ticket_id,
                    "summary": req.get("summary", ""),
                    "status": req.get("status", "Unknown"),
                    "created": "Unknown",
                    "assignee": "Unknown",
                    "url": f"{self.BASE_URL}{portal_base}/{ticket_id}",
                }
            )
        return tickets

    def _parse_requests_links(
        self, page_html: str, status: str, limit: int
    ) -> list[SimplifiedTicketData]:
        """Legacy fallback: scrape ``<a>`` tags for ticket links."""
        soup = BeautifulSoup(page_html, "html.parser")
        tickets: list[SimplifiedTicketData] = []

        link_pattern = re.compile(rf"/portal/{self.PORTAL_ID}/{self.TICKET_ID_PREFIX}-")
        for link in soup.find_all("a", href=link_pattern):
            ticket_match = self._ticket_pattern.search(link["href"])
            if ticket_match:
                ticket_id = ticket_match.group()
                summary = link.get_text(strip=True)

                if any(t["id"] == ticket_id for t in tickets):
                    continue

                tickets.append(
                    {
                        "id": ticket_id,
                        "summary": summary,
                        "status": status,
                        "created": "Unknown",
                        "assignee": "Unknown",
                        "url": (
                            f"{self.BASE_URL}/servicedesk/customer"
                            f"/portal/{self.PORTAL_ID}/{ticket_id}"
                        ),
                    }
                )

                if len(tickets) >= limit:
                    break

        return tickets

    def add_comment(
        self, ticket_id: str, comment: str, public: bool = True
    ) -> dict[str, Any] | None:
        """Add a comment to a ticket.

        The comment body is automatically sanitized (internal ticket IDs,
        hostnames, URLs, and customer names are stripped) and converted
        from Markdown to Atlassian wiki markup before posting.
        """
        self._validate_ticket_id(ticket_id)

        safe_body = markdown_to_wiki(sanitize_for_vendor(comment))
        comment_url = f"{self.BASE_URL}/rest/servicedeskapi/request/{ticket_id}/comment"
        payload = {"body": safe_body, "public": public}

        response = self._request_with_reauth("post", comment_url, json=payload)
        if response is None:
            return None

        if response.status_code in (HTTP_OK, HTTP_CREATED):
            return dict(response.json())

        if self.verbose:
            sys.stderr.write(f"  Failed to add comment: {response.status_code}\n")
            sys.stderr.write(f"  Response: {response.text[:200]}\n")
        return None

    # ── Status transitions ──────────────────────────────────────────────────────

    def update_ticket_status(self, ticket_id: str, target_status: str) -> dict[str, Any] | None:
        """Transition a Service Desk ticket to *target_status*.

        Uses the Service Desk customer transitions API:
          1. GET available transitions for the ticket
          2. Find the transition whose name contains *target_status* (case-insensitive)
          3. POST the transition

        Returns dict with ok/ticket_id/new_status on success, None on failure.
        """
        self._validate_ticket_id(ticket_id)

        transitions_url = f"{self.BASE_URL}/rest/servicedeskapi/request/{ticket_id}/transition"
        response = self._request_with_reauth("get", transitions_url)
        if response is None or response.status_code != HTTP_OK:
            if self.verbose:
                status = response.status_code if response else "no response"
                sys.stderr.write(f"  Failed to fetch transitions for {ticket_id}: {status}\n")
            return None

        transitions = response.json().get("values", [])
        target_lower = target_status.lower()

        # Map friendly names to stems that match Jira transition names
        # (e.g. "resolved" → "resolve" matches "Resolve this issue")
        _STATUS_STEMS: dict[str, str] = {
            "resolved": "resolve",
            "closed": "close",
        }
        search_term = _STATUS_STEMS.get(target_lower, target_lower)

        matched_transition = None
        for t in transitions:
            if search_term in t.get("name", "").lower():
                matched_transition = t
                break

        if not matched_transition:
            available = [t.get("name", "?") for t in transitions]
            if self.verbose:
                sys.stderr.write(
                    f"  No transition matching '{target_status}' for {ticket_id}. "
                    f"Available: {available}\n"
                )
            return None

        transition_payload = {"id": str(matched_transition["id"])}
        post_response = self._request_with_reauth("post", transitions_url, json=transition_payload)
        if post_response is None:
            return None

        if post_response.status_code in (HTTP_OK, HTTP_CREATED, 204):
            if self.verbose:
                sys.stderr.write(f"✓ Transitioned {ticket_id} → {matched_transition['name']}\n")
            return {
                "ok": True,
                "ticket_id": ticket_id,
                "new_status": matched_transition["name"],
            }

        if self.verbose:
            sys.stderr.write(
                f"  Transition failed for {ticket_id}: {post_response.status_code}\n"
                f"  Response: {post_response.text[:200]}\n"
            )
        return None

    def create_service_desk_request(
        self,
        summary: str,
        description: str,
        request_type_id: str | int = "7",
        extra_fields: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Create a ticket via the Service Desk REST API.

        Both ``summary`` and ``description`` are automatically sanitized
        to remove internal references before submission.

        Args:
            summary: Short title for the ticket
            description: Detailed issue description
            request_type_id: Service Desk request type ID
            extra_fields: Additional ``requestFieldValues`` entries

        Returns:
            Dict with ``issueKey`` and ``issueId`` on success, None on failure.
        """
        safe_summary = sanitize_for_vendor(summary)
        safe_desc = sanitize_for_vendor(description)

        fields: dict[str, Any] = {
            "summary": safe_summary,
            "description": safe_desc,
        }
        if extra_fields:
            fields.update(extra_fields)

        payload = {
            "serviceDeskId": str(self.PORTAL_ID),
            "requestTypeId": str(request_type_id),
            "requestFieldValues": fields,
        }

        url = f"{self.BASE_URL}/rest/servicedeskapi/request"
        response = self._request_with_reauth("post", url, json=payload)
        if response is None:
            self.last_error = "No response from server (network error or auth failure)"
            sys.stderr.write(f"  {self.VENDOR_NAME}: {self.last_error}\n")
            return None

        if response.status_code in (HTTP_OK, HTTP_CREATED):
            self.last_error = None
            data = response.json()
            ticket_key = data.get("issueKey", "")
            if self.verbose:
                sys.stderr.write(f"✓ Created {ticket_key}\n")
            return dict(data)

        body = response.text[:500]
        self.last_error = f"HTTP {response.status_code}: {body}"
        sys.stderr.write(
            f"  {self.VENDOR_NAME}: Failed to create request: {response.status_code}\n"
            f"  Response: {body}\n"
        )
        return None
