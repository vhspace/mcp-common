"""
IREN vendor handler - Freshdesk integration.

Uses the Freshdesk REST API for ticket operations (list, get, create,
add comment) with Playwright browser scraping as a fallback for read
operations.  Knowledge-base discovery still uses Playwright.

Auth for the REST API: Freshdesk API key as Basic-auth username,
literal ``"X"`` as password.  The API key is read from the
``IREN_PORTAL_PASSWORD`` environment variable; the requester e-mail
comes from ``IREN_PORTAL_USERNAME``.
"""

import json
import logging
import os
import pickle
import re
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

import requests as http_requests
from requests import RequestException

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from playwright.sync_api import Browser, BrowserContext, Page, Playwright

from ..constants import (
    API_TIMEOUT,
    AUTH_COOLDOWN,
    BROWSER_NAVIGATION_TIMEOUT,
    BROWSER_WAIT_TIMEOUT,
    COOKIE_MAX_AGE,
    HTTP_CREATED,
    HTTP_OK,
)
from ..decorators import verbose_log
from ..formatting import sanitize_for_vendor
from ..types import CommentData, CookieData, SimplifiedTicketData, TicketData
from ..vendor_handler import VendorHandler

logger = logging.getLogger(__name__)

FRESHDESK_STATUS_OPEN = 2
FRESHDESK_STATUS_PENDING = 3
FRESHDESK_STATUS_RESOLVED = 4
FRESHDESK_STATUS_CLOSED = 5

FRESHDESK_STATUS_NAMES: dict[int, str] = {
    FRESHDESK_STATUS_OPEN: "Open",
    FRESHDESK_STATUS_PENDING: "Pending",
    FRESHDESK_STATUS_RESOLVED: "Resolved",
    FRESHDESK_STATUS_CLOSED: "Closed",
}

FRESHDESK_STATUS_MAP: dict[str, int] = {
    name.lower(): code for code, name in FRESHDESK_STATUS_NAMES.items()
}


class KBAttachment(TypedDict):
    """Structure for a knowledge base article attachment."""

    name: str
    url: str
    content_type: str | None
    size: int | None


class KnowledgeBaseArticle(TypedDict):
    """Structure for knowledge base article."""

    id: str
    title: str
    url: str
    category: str | None
    last_modified: str | None
    content: str | None
    attachments: list[KBAttachment]


class KnowledgeBaseCache(TypedDict):
    """Structure for knowledge base cache."""

    articles: list[KnowledgeBaseArticle]
    cached_at: datetime
    last_modified: str | None


# Freshdesk priority values: 1=Low, 2=Medium, 3=High, 4=Urgent
FRESHDESK_PRIORITY_MAP: dict[str | int, int] = {
    "P1": 4,
    "P2": 3,
    "P3": 2,
    "P4": 1,
    "P5": 1,
    "Critical": 4,
    "High": 3,
    "Moderate": 2,
    "Medium": 2,
    "Low": 1,
    "Lowest": 1,
    1: 1,
    2: 2,
    3: 3,
    4: 4,
}

FRESHDESK_STATUS_OPEN = 2

_EXT_CONTENT_TYPES: dict[str, str] = {
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "zip": "application/zip",
}


class IrenVendorHandler(VendorHandler):
    """
    IREN vendor handler.

    Uses the Freshdesk REST API for ticket operations (list, get,
    create, add comment) and falls back to Playwright browser
    scraping when the API is unavailable.  Knowledge-base discovery
    still uses Playwright.

    Call close() when done to release the browser process.
    """

    VENDOR_NAME = "iren"
    BASE_URL = "https://support.iren.com"
    API_BASE_URL = "https://support.iren.com"
    COOKIE_FILE_NAME = ".iren_session_cookies.pkl"
    KB_CACHE_FILE_NAME = ".iren_kb_cache.json"
    KB_CACHE_MAX_AGE = timedelta(hours=24)

    def __init__(
        self, email: str, password: str, use_cached_cookies: bool = True, verbose: bool = True
    ):
        self.email = email
        self.password = password
        self.verbose = verbose
        self.cookie_file = Path.home() / self.COOKIE_FILE_NAME
        self.kb_cache_file = Path.home() / self.KB_CACHE_FILE_NAME
        self._browser: Browser | None = None
        self._browser_context: BrowserContext | None = None
        self._page: Page | None = None
        self._playwright: Playwright | None = None
        self._authenticated = False
        self._kb_cache: KnowledgeBaseCache | None = None
        self._last_auth_attempt: datetime | None = None
        self._last_auth_succeeded: bool = False

        self._api_key = os.environ.get("IREN_FRESHDESK_API_KEY", "").strip()
        self._api_url = os.environ.get(
            "IREN_FRESHDESK_URL", "https://iren.freshdesk.com"
        ).rstrip("/")

        if use_cached_cookies and self._load_cookies():
            if self.verbose:
                sys.stderr.write("✓ Using cached IREN session cookies\n")
            self._authenticated = True
        else:
            if self.verbose:
                sys.stderr.write("→ Will authenticate to IREN on first operation...\n")

        if self._api_key and self.verbose:
            sys.stderr.write(f"✓ IREN Freshdesk API key configured ({self._api_url})\n")

    # ── Cookie I/O with locking ─────────────────────────────────────────

    @contextmanager
    def _cookie_lock(self, *, exclusive: bool = False):
        """Advisory flock on a vendor-specific lock file.

        Degrades to a no-op on platforms without ``fcntl``.
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

    def _atomic_pickle_write(self, data: dict[str, Any]) -> None:
        """Write *data* to ``self.cookie_file`` atomically via temp + replace."""
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

    def _load_cookies(self) -> bool:
        """Load cookies from cache file if they exist and aren't expired."""
        if not self.cookie_file.exists():
            return False

        try:
            with self._cookie_lock(exclusive=False):
                with open(self.cookie_file, "rb") as f:
                    data = pickle.load(f)

            cookies = data.get("cookies")
            timestamp = data.get("timestamp")

            if datetime.now() - timestamp > COOKIE_MAX_AGE:
                if self.verbose:
                    sys.stderr.write("  Cached cookies expired\n")
                return False

            self._cached_cookies = cookies
            return True

        except (OSError, pickle.PickleError, KeyError) as e:
            if self.verbose:
                sys.stderr.write(f"  Failed to load cached cookies: {e}\n")
            return False

    def _save_cookies(self, cookies: list[CookieData]) -> None:
        """Save cookies to cache file atomically with exclusive lock."""
        try:
            data = {
                "cookies": cookies,
                "timestamp": datetime.now(),
            }
            with self._cookie_lock(exclusive=True):
                self._atomic_pickle_write(data)
            if self.verbose:
                sys.stderr.write(f"  Saved {len(cookies)} IREN cookies to cache\n")
        except (OSError, pickle.PickleError) as e:
            if self.verbose:
                sys.stderr.write(f"  Warning: Failed to save cookies: {e}\n")

    def _ensure_browser_context(self) -> None:
        """Ensure we have a browser context ready, launching one if needed."""
        if self._browser_context is not None and self._page is not None:
            return

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise ImportError(
                "Playwright required for IREN support.\n"
                "Install with: uv pip install playwright && playwright install chromium"
            ) from e

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=True)
        self._browser_context = self._browser.new_context()

        if hasattr(self, "_cached_cookies") and self._cached_cookies:
            self._browser_context.add_cookies(self._cached_cookies)

        self._page = self._browser_context.new_page()

    def close(self) -> None:
        """Release browser resources. Safe to call multiple times."""
        if self._page:
            try:
                self._page.close()
            except Exception:
                pass
            self._page = None

        if self._browser_context:
            try:
                self._browser_context.close()
            except Exception:
                pass
            self._browser_context = None

        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None

        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    def __del__(self) -> None:
        """Best-effort cleanup on garbage collection."""
        self.close()

    def authenticate(self) -> bool:
        """Authenticate with the IREN portal."""
        return bool(self._guarded_authenticate())

    def _guarded_authenticate(self) -> bool:
        """Authenticate with cooldown protection to prevent account lockout.

        Cooldown is tracked only in-memory per process to avoid the
        cross-process deadlock described in issue #54.  Only enforced after
        a FAILED auth attempt — successful auth does not block subsequent
        re-auth when the session expires (issue #65).

        IREN uses API-key auth for REST operations, not session cookies.
        Browser cookies are only injected into the Playwright context for
        read operations — there is no server-side session to probe, so
        ``_probe_session()`` is intentionally skipped (unlike Atlassian).

        Before launching the browser, checks whether another process has
        already stored valid cookies (via ``_load_cookies``).

        TODO(issue-54): extract shared auth mixin — this cooldown logic is
        duplicated in AtlassianServiceDeskHandler._guarded_authenticate.
        """
        now = datetime.now()
        last = self._last_auth_attempt
        if last and not self._last_auth_succeeded and (now - last) < AUTH_COOLDOWN:
            elapsed = (now - last).total_seconds()
            cooldown_remaining = AUTH_COOLDOWN.total_seconds() - elapsed
            sys.stderr.write(
                f"  ⚠ iren: auth cooldown active "
                f"({cooldown_remaining:.0f}s remaining, last attempt "
                f"{elapsed:.0f}s ago). Skipping browser login to prevent lockout.\n"
            )
            self.last_error = (
                f"Auth cooldown active ({cooldown_remaining:.0f}s remaining). "
                "Skipping browser login to prevent account lockout."
            )
            return False

        if self._load_cookies():
            if self.verbose:
                sys.stderr.write(
                    "  ✓ iren: another process refreshed cookies, skipping browser auth\n"
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

    @verbose_log("Authenticating to IREN portal", "IREN authentication completed")
    def _authenticate_with_browser(self) -> bool:
        """Use Playwright to authenticate and extract cookies."""
        self._last_auth_attempt = datetime.now()
        self._ensure_browser_context()
        assert self._page is not None
        assert self._browser_context is not None

        try:
            login_url = f"{self.BASE_URL}/support/tickets"
            self._page.goto(
                login_url, wait_until="domcontentloaded", timeout=BROWSER_NAVIGATION_TIMEOUT
            )
            self._page.wait_for_load_state("networkidle")

            if self._is_logged_in():
                if self.verbose:
                    sys.stderr.write("  Already authenticated via cached cookies\n")
                self._authenticated = True
                return True

            try:
                self._page.fill(
                    'input[type="email"], input[name="username"], input[name="email"]', self.email
                )
                self._page.fill('input[type="password"], input[name="password"]', self.password)
                self._page.click(
                    'button[type="submit"], input[type="submit"], button:has-text("Log in")'
                )
                self._page.wait_for_load_state("networkidle", timeout=BROWSER_WAIT_TIMEOUT)

            except Exception as e:
                if self.verbose:
                    sys.stderr.write(f"  Login form interaction failed: {e}\n")
                return False

            if not self._is_logged_in():
                if self.verbose:
                    sys.stderr.write("  Login verification failed\n")
                return False

            cookies = self._browser_context.cookies()
            self._save_cookies(cookies)  # type: ignore[arg-type]

            self._authenticated = True
            return True

        except Exception as e:
            if self.verbose:
                sys.stderr.write(f"  Authentication error: {e}\n")
            return False

    def _is_logged_in(self) -> bool:
        """Check if we're currently logged in by looking for Freshdesk session indicators."""
        if self._page is None:
            return False
        try:
            has_sign_out = self._page.locator('a:has-text("Sign out")').count() > 0
            has_tickets_nav = self._page.locator('a[href="/support/tickets"]').count() > 0
            has_ticket_list = self._page.locator("ul.fw-tickets-list").count() > 0
            return has_sign_out or (has_tickets_nav and has_ticket_list)
        except Exception:
            return False

    def _ensure_authenticated(self) -> bool:
        """Ensure we're authenticated, attempting login if needed."""
        if not self._authenticated:
            return self.authenticate()
        return True

    def _navigate_with_auth(self, url: str) -> bool:
        """Navigate to a URL, re-authenticating if the session expired.

        Uses cooldown-guarded auth to prevent account lockout.
        """
        self._ensure_browser_context()
        assert self._page is not None

        self._page.goto(url, wait_until="domcontentloaded", timeout=BROWSER_NAVIGATION_TIMEOUT)
        self._page.wait_for_load_state("networkidle")

        if not self._is_logged_in():
            if self.verbose:
                sys.stderr.write("  Session expired, re-authenticating...\n")
            if not self._guarded_authenticate():
                return False
            self._page.goto(url, wait_until="domcontentloaded", timeout=BROWSER_NAVIGATION_TIMEOUT)
            self._page.wait_for_load_state("networkidle")

        return True

    def get_ticket(self, ticket_id: str) -> dict[str, Any] | None:
        """Fetch a ticket by ID via Freshdesk REST API (if key configured) or browser."""
        if self._api_key:
            try:
                return self._get_ticket_via_api(ticket_id)
            except (RuntimeError, RequestException) as exc:
                logger.warning(
                    "REST API get_ticket failed for %s, falling back to browser: %s",
                    ticket_id,
                    exc,
                )
                if self.verbose:
                    sys.stderr.write(
                        f"  REST API get_ticket failed ({exc}), trying browser…\n"
                    )
        return self._get_ticket_via_browser(ticket_id)

    def _get_ticket_via_api(self, ticket_id: str) -> dict[str, Any] | None:
        """Fetch a single ticket and its conversations via the Freshdesk REST API."""
        data = self._freshdesk_get(
            f"/api/v2/tickets/{ticket_id}?include=requester"
        )

        requester = data.get("requester") or {}
        reporter = requester.get("name") or requester.get("email") or "Unknown"
        assignee = str(data.get("responder_id") or "Unassigned")

        description = data.get("description_text") or ""
        if not description:
            html_desc = data.get("description", "")
            description = re.sub(r"<[^>]+>", "", html_desc).strip()

        comments = self._fetch_conversations(ticket_id)

        ticket_url = f"{self.BASE_URL}/support/tickets/{ticket_id}"
        return {
            "id": str(data["id"]),
            "summary": data.get("subject", "Unknown"),
            "status": FRESHDESK_STATUS_NAMES.get(data.get("status"), "Unknown"),
            "reporter": reporter,
            "assignee": assignee,
            "created": data.get("created_at", "Unknown"),
            "description": description,
            "comments": comments,
            "url": ticket_url,
        }

    def _fetch_conversations(self, ticket_id: str) -> list[CommentData]:
        """Fetch all conversations for a ticket, paginating if needed."""
        comments: list[CommentData] = []
        page = 1
        while True:
            try:
                entries = self._freshdesk_get(
                    f"/api/v2/tickets/{ticket_id}/conversations?per_page=100&page={page}"
                )
            except Exception:
                break

            if not isinstance(entries, list) or not entries:
                break

            for entry in entries:
                comment_type = "customer-reply" if entry.get("incoming") else "agent-reply"
                comments.append(
                    {
                        "author": str(
                            entry.get("from_email")
                            or entry.get("user_id")
                            or "Unknown"
                        ),
                        "date": entry.get("created_at", "Unknown"),
                        "comment": entry.get("body_text", ""),
                        "type": comment_type,
                    }
                )

            if len(entries) < 100:
                break
            page += 1

        return comments

    def _get_ticket_via_browser(self, ticket_id: str) -> dict[str, Any] | None:
        """Fetch a ticket by scraping the Freshdesk portal (legacy fallback)."""
        if not self._ensure_authenticated():
            return None

        try:
            ticket_url = f"{self.BASE_URL}/support/tickets/{ticket_id}"
            if not self._navigate_with_auth(ticket_url):
                return None

            ticket_data = self._parse_ticket_from_page()

            if ticket_data:
                ticket_data["id"] = ticket_id
                ticket_data["url"] = ticket_url
                return dict(ticket_data)

            return None

        except Exception as e:
            if self.verbose:
                sys.stderr.write(f"  Error fetching ticket {ticket_id}: {e}\n")
            return None

    _EXTRACT_TICKET_JS = """
    () => {
        const result = {
            summary: '',
            status: '',
            created: '',
            reporter: '',
            assignee: '',
            description: '',
            comments: []
        };

        // --- Summary (h1) ---
        const h1 = document.querySelector('h1');
        if (h1) result.summary = h1.textContent.trim();

        // --- Status ---
        const badge = document.querySelector('.fw-status-badge, [class*="status-badge"]');
        if (badge) {
            result.status = badge.textContent.trim();
        } else {
            const statusLabel = [...document.querySelectorAll('label, span, div')]
                .find(el => el.textContent.trim() === 'Status');
            if (statusLabel) {
                const sibling = statusLabel.nextElementSibling;
                if (sibling) result.status = sibling.textContent.trim();
            }
        }

        // --- Created date (text below h1 matching "Created on ...") ---
        const allText = document.querySelectorAll('p, span, div, time');
        for (const el of allText) {
            const t = el.textContent.trim();
            if (t.startsWith('Created on ') && el.children.length <= 1) {
                result.created = t;
                break;
            }
        }

        // --- Reporter from the page header ---
        // Assignee is not exposed on the Freshdesk customer portal;
        // the REST API path populates it from responder_id instead.
        // The reporter is typically the first name in "X reported ... ago"
        const headerEls = document.querySelectorAll('p, span, div');
        for (const el of headerEls) {
            const t = el.textContent.trim();
            const match = t.match(/^(.+?)\\s+reported\\s/);
            if (match) {
                result.reporter = match[1].trim();
                break;
            }
        }

        // --- Description / ticket body ---
        const descSelectors = [
            '.fw-ticket-description',
            '.ticket-description',
            '.ticket-body',
            '.fr-view',
            '[class*="ticket-description"]',
            '[class*="ticket-body"]',
        ];
        for (const sel of descSelectors) {
            const el = document.querySelector(sel);
            if (el && el.textContent.trim()) {
                result.description = el.textContent.trim();
                break;
            }
        }
        // Fallback: first communication-body if no dedicated description element
        if (!result.description) {
            const firstBody = document.querySelector(
                '.communication-body, .content-body, .reply-content'
            );
            if (firstBody && firstBody.textContent.trim()) {
                result.description = firstBody.textContent.trim();
            }
        }

        // --- Comments / Conversations ---
        // Strategy 1: Find elements with conversation/communication classes
        const conversationSelectors = [
            '.communication-item',
            '.conversation-item',
            '.fw-communication-item',
            '.communication > .communication-item',
            '[class*="communication-item"]',
            '[class*="conversation-item"]',
            '.fw-reply-content',
            '.fw-conversation-content',
        ];

        let items = [];
        for (const sel of conversationSelectors) {
            items = [...document.querySelectorAll(sel)];
            if (items.length > 0) break;
        }

        // Strategy 2: Look for container with "communication" in class name
        if (items.length === 0) {
            const containers = document.querySelectorAll(
                '[class*="communication"], [class*="conversation"], ' +
                '[class*="Communications"], [class*="Conversations"]'
            );
            for (const container of containers) {
                const children = [...container.children].filter(
                    c => c.tagName !== 'SCRIPT' && c.tagName !== 'STYLE'
                );
                if (children.length >= 1) {
                    items = children;
                    break;
                }
            }
        }

        // Strategy 3: Find by data attributes
        if (items.length === 0) {
            items = [...document.querySelectorAll(
                '[data-conversation-id], [data-reply-id], [data-note-id]'
            )];
        }

        for (const item of items) {
            const entry = { author: '', date: '', comment: '', type: 'comment' };

            // Author extraction: try multiple patterns
            const authorSelectors = [
                '.author-name', '.name', 'strong', '.fw-user-name',
                '[class*="author"]', '[class*="name"]:not([class*="file"])',
                '.communication-author', '.user-name',
            ];
            for (const sel of authorSelectors) {
                const el = item.querySelector(sel);
                if (el && el.textContent.trim()) {
                    entry.author = el.textContent.trim();
                    break;
                }
            }

            // Date extraction: try time element first, then text patterns
            const timeEl = item.querySelector('time');
            if (timeEl) {
                entry.date = timeEl.getAttribute('datetime') ||
                             timeEl.getAttribute('title') ||
                             timeEl.textContent.trim();
            } else {
                const dateSelectors = [
                    '[class*="time"]', '[class*="date"]', '[class*="ago"]',
                    '.timestamp', '.meta',
                ];
                for (const sel of dateSelectors) {
                    const el = item.querySelector(sel);
                    if (el && el.textContent.trim()) {
                        entry.date = el.textContent.trim();
                        break;
                    }
                }
                // Fallback: look for "X ago" text pattern
                if (!entry.date) {
                    const spans = item.querySelectorAll('span, p, div');
                    for (const s of spans) {
                        if (/\\d+\\s+(minute|hour|day|week|month)s?\\s+ago/.test(s.textContent)) {
                            entry.date = s.textContent.trim();
                            break;
                        }
                    }
                }
            }

            // Body extraction: try content containers, fall back to full text
            const bodySelectors = [
                '.communication-body', '.reply-content', '.fw-reply-text',
                '.content-body', '[class*="body"]', '[class*="content"]:not([class*="header"])',
                '.message-body', '.note-body',
            ];
            let bodyText = '';
            for (const sel of bodySelectors) {
                const el = item.querySelector(sel);
                if (el && el.textContent.trim()) {
                    bodyText = el.textContent.trim();
                    break;
                }
            }
            if (!bodyText) {
                bodyText = item.textContent.trim();
                // Only strip author/date from the beginning, not arbitrary substrings
                if (entry.author && bodyText.startsWith(entry.author)) {
                    bodyText = bodyText.slice(entry.author.length).trim();
                }
                if (entry.date && bodyText.startsWith(entry.date)) {
                    bodyText = bodyText.slice(entry.date.length).trim();
                }
            }
            entry.comment = bodyText;

            // Type detection: customer reply vs agent reply
            const classes = item.className || '';
            const parentClasses = (item.parentElement && item.parentElement.className) || '';
            if (classes.includes('note') || parentClasses.includes('note')) {
                entry.type = 'note';
            } else if (classes.includes('incoming') || item.textContent.includes('reported')) {
                entry.type = 'customer-reply';
            } else {
                entry.type = 'agent-reply';
            }

            if (entry.comment) {
                result.comments.push(entry);
            }
        }

        return result;
    }
    """

    def _parse_ticket_from_page(self) -> TicketData | None:
        """Parse ticket data from the Freshdesk ticket detail page.

        Uses JavaScript DOM traversal via page.evaluate() for robust
        extraction that doesn't depend on specific CSS class names.
        Falls back to Playwright locators for basic fields.
        """
        if self._page is None:
            return None
        try:
            extracted: dict[str, Any] = self._page.evaluate(self._EXTRACT_TICKET_JS)

            summary = extracted.get("summary", "Unknown") or "Unknown"
            status = extracted.get("status", "Unknown") or "Unknown"
            created = extracted.get("created", "Unknown") or "Unknown"
            if isinstance(created, str):
                if created.startswith("Created"):
                    created = re.sub(r"^Created\s+(?:on\s+|by\s+\S+\s+on\s+)?", "", created)
                    created = re.sub(r"\s*-\s*via\s+.*$", "", created)
            reporter = extracted.get("reporter", "Unknown") or "Unknown"
            assignee = extracted.get("assignee", "Unknown") or "Unknown"

            # If JS extraction missed status, try locator fallback
            if status == "Unknown":
                status_elem = self._page.locator(".fw-status-badge")
                if status_elem.count() > 0:
                    status = status_elem.first.inner_text().strip()

            description = extracted.get("description", "") or ""

            BROWSER_TYPE_MAP = {"note": "agent-reply", "comment": "agent-reply"}
            comments: list[CommentData] = []
            for raw in extracted.get("comments", []):
                raw_type = raw.get("type") or "comment"
                comment_type = BROWSER_TYPE_MAP.get(raw_type, raw_type)
                comments.append(
                    {
                        "author": raw.get("author") or "Unknown",
                        "date": raw.get("date") or "Unknown",
                        "comment": raw.get("comment") or "",
                        "type": comment_type,
                    }
                )

            return {
                "id": "",
                "summary": summary.strip(),
                "status": status,
                "reporter": reporter,
                "assignee": assignee,
                "created": created,
                "description": description,
                "comments": comments,
                "url": "",
            }

        except Exception as e:
            if self.verbose:
                sys.stderr.write(f"  Error parsing ticket data: {e}\n")
            return None

    def list_tickets(self, status: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        """List tickets via Freshdesk REST API (if key configured) or browser."""
        if self._api_key:
            try:
                return self._list_tickets_via_api(status=status, limit=limit)
            except (RuntimeError, RequestException) as exc:
                logger.warning("REST API list_tickets failed, falling back to browser: %s", exc)
                if self.verbose:
                    sys.stderr.write(f"  REST API list_tickets failed ({exc}), trying browser…\n")
        return self._list_tickets_via_browser(status=status, limit=limit)

    def _list_tickets_via_api(
        self, status: str | None = None, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Fetch ticket list using the Freshdesk REST API with pagination."""
        max_per_page = 100
        collected: list[dict[str, Any]] = []
        page = 1

        status_code: int | None = None
        if status:
            status_code = FRESHDESK_STATUS_MAP.get(status.lower())

        while len(collected) < limit:
            per_page = min(max_per_page, limit - len(collected))
            path = f"/api/v2/tickets?per_page={per_page}&page={page}"
            if status_code is not None:
                path += f"&status={status_code}"

            data = self._freshdesk_get(path)
            if not isinstance(data, list) or not data:
                break

            for ticket in data:
                collected.append(
                    {
                        "id": str(ticket["id"]),
                        "summary": ticket.get("subject", "Unknown"),
                        "status": FRESHDESK_STATUS_NAMES.get(
                            ticket.get("status"), "Unknown"
                        ),
                        "created": ticket.get("created_at", "Unknown"),
                        "assignee": str(ticket.get("responder_id") or "Unassigned"),
                        "url": f"{self.BASE_URL}/support/tickets/{ticket['id']}",
                    }
                )
                if len(collected) >= limit:
                    break

            if len(data) < per_page:
                break
            page += 1

        return collected

    def _list_tickets_via_browser(
        self, status: str | None = None, limit: int = 10
    ) -> list[dict[str, Any]]:
        """List tickets by scraping the Freshdesk portal HTML (legacy fallback)."""
        if not self._ensure_authenticated():
            return []

        try:
            tickets_url = f"{self.BASE_URL}/support/tickets"
            if not self._navigate_with_auth(tickets_url):
                return []

            assert self._page is not None
            tickets: list[SimplifiedTicketData] = []
            ticket_elements = self._page.locator("ul.fw-tickets-list > li").all()

            for ticket_elem in ticket_elements[:limit]:
                try:
                    ticket_link = ticket_elem.locator('a[href^="/support/tickets/"]').first
                    if ticket_link.count() == 0:
                        continue

                    href = ticket_link.get_attribute("href")
                    ticket_id_match = re.search(r"/tickets/(\d+)", href or "")
                    if not ticket_id_match:
                        continue

                    ticket_id = ticket_id_match.group(1)

                    summary_elem = ticket_elem.locator("p.line-clamp-2").first
                    summary = (
                        summary_elem.inner_text().strip() if summary_elem.count() > 0 else "Unknown"
                    )

                    status_elem = ticket_elem.locator(".fw-status-badge").first
                    ticket_status = (
                        status_elem.inner_text().strip() if status_elem.count() > 0 else "Unknown"
                    )

                    meta_elem = ticket_elem.locator("p.fw-meta-info").first
                    created = meta_elem.inner_text().strip() if meta_elem.count() > 0 else "Unknown"

                    tickets.append(
                        {
                            "id": ticket_id,
                            "summary": summary,
                            "status": ticket_status,
                            "created": created,
                            "assignee": "Unknown",
                            "url": f"{self.BASE_URL}{href}",
                        }
                    )

                except Exception as e:
                    if self.verbose:
                        sys.stderr.write(f"  Error parsing ticket row: {e}\n")
                    continue

            return [dict(t) for t in tickets]

        except Exception as e:
            if self.verbose:
                sys.stderr.write(f"  Error listing tickets: {e}\n")
            return []

    def _load_kb_cache(self) -> bool:
        """Load knowledge base cache from disk if fresh."""
        if not self.kb_cache_file.exists():
            return False

        try:
            with open(self.kb_cache_file) as f:
                data = json.load(f)

            cached_at_str = data.get("cached_at")
            if not cached_at_str:
                return False

            cached_at = datetime.fromisoformat(cached_at_str)

            if datetime.now() - cached_at > self.KB_CACHE_MAX_AGE:
                if self.verbose:
                    sys.stderr.write("  KB cache expired\n")
                return False

            self._kb_cache = {
                "articles": data.get("articles", []),
                "cached_at": cached_at,
                "last_modified": data.get("last_modified"),
            }

            if self.verbose:
                article_count = len(self._kb_cache["articles"])
                age = datetime.now() - cached_at
                sys.stderr.write(
                    f"✓ Loaded KB cache: {article_count} articles (age: {age.seconds // 3600}h)\n"
                )

            return True

        except (OSError, json.JSONDecodeError, KeyError, ValueError) as e:
            if self.verbose:
                sys.stderr.write(f"  Failed to load KB cache: {e}\n")
            return False

    def _save_kb_cache(
        self, articles: list[KnowledgeBaseArticle], last_modified: str | None = None
    ) -> None:
        """Save knowledge base cache to disk."""
        try:
            cache_data = {
                "articles": articles,
                "cached_at": datetime.now().isoformat(),
                "last_modified": last_modified,
            }

            with open(self.kb_cache_file, "w") as f:
                json.dump(cache_data, f, indent=2)

            if self.verbose:
                sys.stderr.write(f"✓ Saved KB cache: {len(articles)} articles\n")

        except (OSError, TypeError) as e:
            if self.verbose:
                sys.stderr.write(f"  Warning: Failed to save KB cache: {e}\n")

    @verbose_log("Fetching knowledge base articles", "KB fetch completed")
    def fetch_knowledge_base(self, force_refresh: bool = False) -> list[KnowledgeBaseArticle]:
        """Fetch knowledge base articles, using disk cache when possible.

        Tries the Freshdesk REST API first (categories -> folders -> articles)
        for deeper discovery, then falls back to browser scraping.
        """
        if not force_refresh and self._load_kb_cache():
            assert self._kb_cache is not None
            return self._kb_cache["articles"]

        articles = self._fetch_kb_via_api()
        if articles:
            self._save_kb_cache(articles)
            self._kb_cache = {
                "articles": articles,
                "cached_at": datetime.now(),
                "last_modified": None,
            }
            return articles

        return self._fetch_kb_via_browser()

    def _fetch_kb_via_api(self) -> list[KnowledgeBaseArticle]:
        """Discover all KB articles via Freshdesk REST API: categories -> folders -> articles."""
        cat_resp = self._freshdesk_request("get", "/api/v2/solutions/categories")
        if cat_resp is None or cat_resp.status_code != HTTP_OK:
            return []

        articles: list[KnowledgeBaseArticle] = []
        seen_ids: set[str] = set()

        try:
            categories = cat_resp.json()
        except (ValueError, AttributeError):
            return []

        for cat in categories:
            cat_id = cat.get("id")
            cat_name = cat.get("name", "")
            if not cat_id:
                continue

            folder_resp = self._freshdesk_request(
                "get", f"/api/v2/solutions/categories/{cat_id}/folders"
            )
            if folder_resp is None or folder_resp.status_code != HTTP_OK:
                continue

            try:
                folders = folder_resp.json()
            except (ValueError, AttributeError):
                continue

            for folder in folders:
                folder_id = folder.get("id")
                folder_name = folder.get("name", "")
                if not folder_id:
                    continue

                page = 1
                while True:
                    art_resp = self._freshdesk_request(
                        "get",
                        f"/api/v2/solutions/folders/{folder_id}/articles?page={page}",
                    )
                    if art_resp is None or art_resp.status_code != HTTP_OK:
                        break

                    try:
                        page_articles = art_resp.json()
                    except (ValueError, AttributeError):
                        break

                    if not page_articles:
                        break

                    for art in page_articles:
                        art_id = str(art.get("id", ""))
                        if not art_id or art_id in seen_ids:
                            continue
                        seen_ids.add(art_id)

                        category_label = cat_name
                        if folder_name:
                            category_label = f"{cat_name} / {folder_name}"

                        articles.append(
                            {
                                "id": art_id,
                                "title": art.get("title", ""),
                                "url": f"{self.BASE_URL}/support/solutions/articles/{art_id}",
                                "category": category_label,
                                "last_modified": art.get("updated_at"),
                                "content": None,
                                "attachments": [],
                            }
                        )

                    page += 1

        if self.verbose and articles:
            sys.stderr.write(f"  REST API discovery: {len(articles)} articles found\n")

        return articles

    def _fetch_kb_via_browser(self) -> list[KnowledgeBaseArticle]:
        """Fallback: scrape the KB index page and follow category/folder links."""
        if not self._ensure_authenticated():
            return []

        self._ensure_browser_context()
        assert self._page is not None

        try:
            kb_url = f"{self.BASE_URL}/support/solutions"
            if not self._navigate_with_auth(kb_url):
                return []

            articles: list[KnowledgeBaseArticle] = []
            seen_ids: set[str] = set()

            self._scrape_article_links(articles, seen_ids, category=None)

            folder_links: list[str] = []
            folder_elems = self._page.locator('a[href*="/support/solutions/folders/"]').all()
            for elem in folder_elems:
                href = elem.get_attribute("href")
                if href:
                    folder_links.append(
                        href if href.startswith("http") else f"{self.BASE_URL}{href}"
                    )

            for folder_url in folder_links:
                try:
                    self._page.goto(
                        folder_url,
                        wait_until="domcontentloaded",
                        timeout=BROWSER_NAVIGATION_TIMEOUT,
                    )
                    self._page.wait_for_load_state("networkidle")

                    heading = self._page.locator("h1, h2").first
                    cat_label = heading.inner_text().strip() if heading.count() > 0 else None
                    self._scrape_article_links(articles, seen_ids, category=cat_label)
                except Exception as e:
                    if self.verbose:
                        sys.stderr.write(f"  Error scraping folder {folder_url}: {e}\n")

            self._save_kb_cache(articles)
            self._kb_cache = {
                "articles": articles,
                "cached_at": datetime.now(),
                "last_modified": None,
            }

            return articles

        except Exception as e:
            if self.verbose:
                sys.stderr.write(f"  Error fetching KB: {e}\n")
            return []

    def _scrape_article_links(
        self,
        articles: list[KnowledgeBaseArticle],
        seen_ids: set[str],
        category: str | None,
    ) -> None:
        """Scrape article links from the current page into *articles*."""
        assert self._page is not None
        article_links = self._page.locator('a[href*="/support/solutions/articles/"]').all()

        for link in article_links:
            try:
                href = link.get_attribute("href")
                if not href:
                    continue

                article_id_match = re.search(r"/articles/(\d+)", href)
                if not article_id_match:
                    continue

                article_id = article_id_match.group(1)
                if article_id in seen_ids:
                    continue
                seen_ids.add(article_id)

                title = link.inner_text().strip()
                url = href if href.startswith("http") else f"{self.BASE_URL}{href}"

                articles.append(
                    {
                        "id": article_id,
                        "title": title,
                        "url": url,
                        "category": category,
                        "last_modified": None,
                        "content": None,
                        "attachments": [],
                    }
                )

            except Exception as e:
                if self.verbose:
                    sys.stderr.write(f"  Error parsing KB article: {e}\n")
                continue

    def search_knowledge_base(self, query: str, limit: int = 10) -> list[KnowledgeBaseArticle]:
        """Search knowledge base articles by title (uses cache)."""
        articles = self.fetch_knowledge_base()
        query_lower = query.lower()
        matches = [a for a in articles if query_lower in a["title"].lower()]
        return matches[:limit]

    @staticmethod
    def _extract_article_id(article_id_or_url: str) -> str:
        """Extract a numeric article ID from a full URL or bare ID string."""
        m = re.search(r"/articles/(\d+)", article_id_or_url)
        if m:
            return m.group(1)
        digits = re.sub(r"\D", "", article_id_or_url)
        return digits

    def _fetch_article_direct(self, article_id: str) -> KnowledgeBaseArticle | None:
        """Fetch a single article via Freshdesk REST API (bypasses cache/index).

        Falls back to browser scraping if the REST API fails or is unavailable.
        """
        resp = self._freshdesk_request("get", f"/api/v2/solutions/articles/{article_id}")
        if resp is not None and resp.status_code == HTTP_OK:
            data = resp.json()
            description_html = data.get("description", "") or data.get("description_text", "") or ""

            attachments: list[KBAttachment] = []
            for att in data.get("attachments", []):
                attachments.append(
                    {
                        "name": att.get("name", ""),
                        "url": att.get("attachment_url", ""),
                        "content_type": att.get("content_type"),
                        "size": att.get("size"),
                    }
                )

            content_text = re.sub(r"<[^>]+>", "", description_html)
            content_text = "\n".join(
                line.strip() for line in content_text.split("\n") if line.strip()
            )

            folder_id = data.get("folder_id")
            category_id = data.get("category_id")
            category_label = None
            if category_id:
                category_label = f"category/{category_id}"
                if folder_id:
                    category_label += f"/folder/{folder_id}"

            return {
                "id": str(data.get("id", article_id)),
                "title": data.get("title", ""),
                "url": f"{self.BASE_URL}/support/solutions/articles/{article_id}",
                "category": category_label,
                "last_modified": data.get("updated_at"),
                "content": content_text,
                "attachments": attachments,
            }

        return self._fetch_article_via_browser(article_id)

    def _fetch_article_via_browser(self, article_id: str) -> KnowledgeBaseArticle | None:
        """Fetch article content by browser-scraping the portal page."""
        if not self._ensure_authenticated():
            return None

        self._ensure_browser_context()
        assert self._page is not None

        article_url = f"{self.BASE_URL}/support/solutions/articles/{article_id}"
        try:
            self._page.goto(
                article_url, wait_until="domcontentloaded", timeout=BROWSER_NAVIGATION_TIMEOUT
            )
            self._page.wait_for_load_state("networkidle")

            title_elem = self._page.locator("h1, .article-title").first
            title = title_elem.inner_text().strip() if title_elem.count() > 0 else ""

            content_text: str | None = None
            content_elem = self._page.locator(".fw-content--single-article").first
            if content_elem.count() > 0:
                raw = content_elem.inner_text()
                content_text = "\n".join(line.strip() for line in raw.split("\n") if line.strip())

            last_modified: str | None = None
            meta_elem = self._page.locator(':text-matches("Modified on", "i")').first
            if meta_elem.count() > 0:
                last_modified = meta_elem.inner_text()

            attachments = self._parse_attachments_from_page()

            return {
                "id": article_id,
                "title": title,
                "url": article_url,
                "category": None,
                "last_modified": last_modified,
                "content": content_text,
                "attachments": attachments,
            }
        except Exception as e:
            if self.verbose:
                sys.stderr.write(f"  Error fetching article {article_id} via browser: {e}\n")
            return None

    def _parse_attachments_from_page(self) -> list[KBAttachment]:
        """Extract attachment links from the current article page."""
        if self._page is None:
            return []
        attachments: list[KBAttachment] = []
        try:
            links = self._page.locator(
                '.fw-content--single-article a[href$=".pdf"], '
                '.fw-content--single-article a[href$=".doc"], '
                '.fw-content--single-article a[href$=".docx"], '
                '.fw-content--single-article a[href$=".xls"], '
                '.fw-content--single-article a[href$=".xlsx"], '
                '.fw-content--single-article a[href$=".png"], '
                '.fw-content--single-article a[href$=".jpg"], '
                '.fw-content--single-article a[href$=".jpeg"], '
                '.fw-content--single-article a[href$=".zip"], '
                ".attachment-item a, "
                "a.attachment-link"
            ).all()
            for link in links:
                href = link.get_attribute("href") or ""
                name = link.inner_text().strip() or href.rsplit("/", 1)[-1]
                if href:
                    url = href if href.startswith("http") else f"{self.BASE_URL}{href}"
                    ext = url.rsplit(".", 1)[-1].lower() if "." in url else ""
                    content_type = _EXT_CONTENT_TYPES.get(ext)
                    attachments.append(
                        {
                            "name": name,
                            "url": url,
                            "content_type": content_type,
                            "size": None,
                        }
                    )
        except Exception as e:
            if self.verbose:
                sys.stderr.write(f"  Error parsing attachments: {e}\n")
        return attachments

    def get_kb_article(self, article_id: str) -> KnowledgeBaseArticle | None:
        """Get a specific knowledge base article, fetching full content if needed.

        Accepts a numeric ID or a full Freshdesk article URL.
        Tries the REST API first (no index required), then falls back to cache.
        """
        article_id = self._extract_article_id(article_id)
        if not article_id:
            return None

        direct = self._fetch_article_direct(article_id)
        if direct is not None:
            return direct

        articles = self.fetch_knowledge_base()
        cached_article = next((a for a in articles if a["id"] == article_id), None)

        if not cached_article:
            return None

        if cached_article.get("content") is not None:
            return cached_article

        if not self._ensure_authenticated():
            return cached_article

        self._ensure_browser_context()
        assert self._page is not None

        try:
            article_url = cached_article["url"]
            self._page.goto(
                article_url, wait_until="domcontentloaded", timeout=BROWSER_NAVIGATION_TIMEOUT
            )
            self._page.wait_for_load_state("networkidle")

            content_elem = self._page.locator(".fw-content--single-article").first
            if content_elem.count() > 0:
                content_text = content_elem.inner_text()
                content_text = "\n".join(
                    line.strip() for line in content_text.split("\n") if line.strip()
                )
                cached_article["content"] = content_text

                if self.verbose:
                    sys.stderr.write(f"  Extracted {len(content_text)} chars of content\n")

            meta_elem = self._page.locator(':text-matches("Modified on", "i")').first
            if meta_elem.count() > 0:
                cached_article["last_modified"] = meta_elem.inner_text()

            cached_article["attachments"] = self._parse_attachments_from_page()

        except Exception as e:
            if self.verbose:
                sys.stderr.write(f"  Error fetching article content: {e}\n")

        return cached_article

    # ── Freshdesk REST API helpers ────────────────────────────────────

    def _freshdesk_request(
        self,
        method: str,
        path: str,
        json_body: dict[str, Any] | None = None,
    ) -> http_requests.Response | None:
        """Make an authenticated request to the Freshdesk REST API.

        Uses ``self._api_key`` (from ``IREN_FRESHDESK_API_KEY``) as the
        Freshdesk API key with Basic auth (key as username, ``"X"`` as
        password).  Falls back to ``self.password`` for backward
        compatibility (ticket creation / KB still use portal password).
        """
        api_key = self._api_key or self.password
        url = f"{self._api_url}{path}"
        try:
            resp: http_requests.Response = getattr(http_requests, method)(
                url,
                json=json_body,
                auth=(api_key, "X"),
                headers={"Content-Type": "application/json"},
                timeout=API_TIMEOUT,
            )
            return resp
        except http_requests.RequestException as exc:
            logger.warning("Freshdesk %s %s failed: %s", method.upper(), path, exc)
            if self.verbose:
                sys.stderr.write(f"  Freshdesk API error: {exc}\n")
            return None

    def _freshdesk_get(self, path: str) -> Any:
        """GET a Freshdesk endpoint and return parsed JSON, or raise on failure."""
        resp = self._freshdesk_request("get", path)
        if resp is None:
            raise RuntimeError(f"Freshdesk GET {path}: no response (network error)")
        if resp.status_code != HTTP_OK:
            raise RuntimeError(
                f"Freshdesk GET {path}: HTTP {resp.status_code}: {resp.text[:200]}"
            )
        return resp.json()

    # ── Ticket creation ────────────────────────────────────────────────

    def create_ticket(
        self,
        summary: str,
        description: str,
        cause: str = "",
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        """Create a support ticket via the Freshdesk REST API.

        The summary and description are sanitized to remove internal
        references before submission.

        Args:
            summary: Short ticket subject line.
            description: Detailed HTML/text description.
            cause: Appended to description if provided.
            **kwargs: Optional overrides — ``priority`` (P1-P5 string,
                name, or Freshdesk int 1-4), ``ticket_type``.

        Returns:
            Dict with ``id``, ``summary``, ``url``, ``status`` on
            success, or ``None`` on failure.
        """
        safe_summary = sanitize_for_vendor(summary)
        safe_description = sanitize_for_vendor(description)
        if cause:
            safe_cause = sanitize_for_vendor(cause)
            safe_description = f"{safe_description}\n\nCause: {safe_cause}"

        raw_priority = kwargs.get("priority", 2)
        priority = FRESHDESK_PRIORITY_MAP.get(raw_priority, 2)

        payload: dict[str, Any] = {
            "subject": safe_summary,
            "description": safe_description,
            "email": self.email,
            "priority": priority,
            "status": FRESHDESK_STATUS_OPEN,
        }

        ticket_type = kwargs.get("ticket_type")
        if ticket_type:
            payload["type"] = ticket_type

        if self.verbose:
            sys.stderr.write(f"→ Creating IREN ticket: {safe_summary[:60]}...\n")

        resp = self._freshdesk_request("post", "/api/v2/tickets", json_body=payload)
        if resp is None:
            return None

        if resp.status_code in (HTTP_OK, HTTP_CREATED):
            data = resp.json()
            ticket_id = str(data.get("id", ""))
            if self.verbose:
                sys.stderr.write(f"✓ Created IREN ticket #{ticket_id}\n")
            return {
                "id": ticket_id,
                "summary": safe_summary,
                "url": f"{self.BASE_URL}/support/tickets/{ticket_id}",
                "status": "Open",
            }

        logger.warning(
            "Freshdesk ticket creation failed: %s %s",
            resp.status_code,
            resp.text[:300],
        )
        if self.verbose:
            sys.stderr.write(f"  Ticket creation failed: {resp.status_code} {resp.text[:200]}\n")
        return None

    # ── Ticket status update (resolve / close) ────────────────────────

    @verbose_log("Updating IREN ticket status", "Status update completed")
    def update_ticket_status(self, ticket_id: str, status: int) -> dict[str, Any] | None:
        """Update the status of an IREN (Freshdesk) ticket.

        Tries Freshdesk API v2 first, then falls back to the portal AJAX
        endpoint.  Both strategies run inside the Playwright browser
        context so they inherit session cookies.

        Args:
            ticket_id: Numeric Freshdesk ticket ID.
            status: Freshdesk status code (2=Open, 3=Pending, 4=Resolved, 5=Closed).
        """
        if not self._ensure_authenticated():
            return None

        self._ensure_browser_context()
        assert self._page is not None

        result = self._try_api_v2_status_update(ticket_id, status)
        if result:
            return result

        result = self._try_portal_ajax_status_update(ticket_id, status)
        if result:
            return result

        if self.verbose:
            sys.stderr.write(f"  All status-update strategies failed for ticket {ticket_id}\n")
        return None

    def _try_api_v2_status_update(self, ticket_id: str, status: int) -> dict[str, Any] | None:
        """Update ticket status via Freshdesk API v2 (PUT /api/v2/tickets/{id})."""
        assert self._page is not None
        result = self._page.evaluate(
            """async ([url, status]) => {
                try {
                    const r = await fetch(url, {
                        method: 'PUT',
                        credentials: 'include',
                        headers: {
                            'Content-Type': 'application/json',
                            'Accept': 'application/json',
                            'X-Requested-With': 'XMLHttpRequest'
                        },
                        body: JSON.stringify({status: status})
                    });
                    if (!r.ok) return {error: r.status, body: await r.text()};
                    return await r.json();
                } catch(e) { return {error: e.message}; }
            }""",
            [f"{self.BASE_URL}/api/v2/tickets/{ticket_id}", status],
        )

        if isinstance(result, dict) and "error" in result:
            if self.verbose:
                sys.stderr.write(f"  API v2 status update → {result['error']}\n")
            return None

        if isinstance(result, dict):
            new_status = result.get("status", status)
            return {
                "ok": True,
                "ticket_id": ticket_id,
                "new_status": FRESHDESK_STATUS_NAMES.get(new_status, str(new_status)),
                "strategy": "api_v2",
            }

        if self.verbose and isinstance(result, dict):
            sys.stderr.write(f"  Unexpected API v2 response: {str(result)[:200]}\n")
        return None

    def _try_portal_ajax_status_update(self, ticket_id: str, status: int) -> dict[str, Any] | None:
        """Fallback: update ticket status via the Freshdesk portal AJAX endpoint."""
        assert self._page is not None
        result = self._page.evaluate(
            """async ([url, status]) => {
                try {
                    const fd = new FormData();
                    fd.append('helpdesk_ticket[status]', status);
                    const r = await fetch(url, {
                        method: 'PUT',
                        credentials: 'include',
                        headers: {
                            'Accept': 'application/json',
                            'X-Requested-With': 'XMLHttpRequest'
                        },
                        body: fd
                    });
                    if (!r.ok) return {error: r.status, body: await r.text()};
                    return await r.json();
                } catch(e) { return {error: e.message}; }
            }""",
            [f"{self.BASE_URL}/support/tickets/{ticket_id}", status],
        )

        if isinstance(result, dict) and "error" in result:
            if self.verbose:
                sys.stderr.write(f"  Portal AJAX status update → {result['error']}\n")
            return None

        if isinstance(result, dict):
            return {
                "ok": True,
                "ticket_id": ticket_id,
                "new_status": FRESHDESK_STATUS_NAMES.get(status, str(status)),
                "strategy": "portal_ajax",
            }

        if self.verbose and isinstance(result, dict):
            sys.stderr.write(f"  Unexpected portal response: {str(result)[:200]}\n")
        return None

    def resolve_ticket(self, ticket_id: str) -> dict[str, Any] | None:
        """Resolve an IREN ticket (set status to Resolved)."""
        return self.update_ticket_status(ticket_id, FRESHDESK_STATUS_RESOLVED)

    def close_ticket(self, ticket_id: str) -> dict[str, Any] | None:
        """Close an IREN ticket (set status to Closed)."""
        return self.update_ticket_status(ticket_id, FRESHDESK_STATUS_CLOSED)

    # ── Comments / notes ───────────────────────────────────────────────

    def add_comment(
        self, ticket_id: str, comment: str, public: bool = True
    ) -> dict[str, Any] | None:
        """Add a note (comment) to an IREN Freshdesk ticket.

        The comment body is sanitized to remove internal references
        before posting.

        Args:
            ticket_id: Numeric Freshdesk ticket ID.
            comment: Comment/note body text.
            public: If ``False`` the note is private (default for
                Freshdesk notes is private; we default to ``True``
                to match the base-class signature).
        """
        safe_body = sanitize_for_vendor(comment)
        private = not public

        payload: dict[str, Any] = {
            "body": safe_body,
            "private": private,
        }

        if self.verbose:
            sys.stderr.write(
                f"→ Adding {'private' if private else 'public'} note to IREN #{ticket_id}\n"
            )

        resp = self._freshdesk_request(
            "post",
            f"/api/v2/tickets/{ticket_id}/notes",
            json_body=payload,
        )
        if resp is None:
            return None

        if resp.status_code in (HTTP_OK, HTTP_CREATED):
            data = resp.json()
            if self.verbose:
                sys.stderr.write(f"✓ Note added to IREN #{ticket_id}\n")
            return dict(data)

        logger.warning(
            "Freshdesk add_comment failed for ticket %s: %s %s",
            ticket_id,
            resp.status_code,
            resp.text[:300],
        )
        if self.verbose:
            sys.stderr.write(f"  Failed to add note: {resp.status_code} {resp.text[:200]}\n")
        return None
