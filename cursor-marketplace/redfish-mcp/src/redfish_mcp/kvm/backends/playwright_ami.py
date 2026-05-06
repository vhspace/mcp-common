"""PlaywrightAmiBackend — concrete KVMBackend for Gigabyte/AMI MegaRAC BMCs.

Uses headless Chromium via Playwright to:
  1. Navigate to the BMC web UI and fill the SPA login form.
  2. Wait for the dashboard to load (the SPA stores session state in JS memory).
  3. Open /viewer.html in a new tab (shares session cookies/state).
  4. Wait for the KVM <canvas> to appear and start rendering IVTP frames.
  5. Capture the canvas as a PNG screenshot.

No Java, Xvfb, or VNC required — a single Chromium process does everything.

Login selectors (validated on Gigabyte AMI MegaRAC 13.06.16):
  - Username: #userid
  - Password: #password
  - Login button: #btn-login
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field

from redfish_mcp.kvm.backend import ProgressCallback, ProgressEvent, SessionHandle
from redfish_mcp.kvm.exceptions import AuthFailedError, KVMError, StaleSessionError

logger = logging.getLogger("redfish_mcp.kvm.backends.playwright_ami")

_LOGIN_PAGE_TIMEOUT_MS = 15_000
_LOGIN_SUBMIT_TIMEOUT_MS = 15_000
_VIEWER_LOAD_TIMEOUT_MS = 15_000
_CANVAS_WAIT_TIMEOUT_MS = 20_000

_PLAYWRIGHT_KEY_MAP: dict[str, str] = {
    "enter": "Enter",
    "return": "Enter",
    "tab": "Tab",
    "escape": "Escape",
    "esc": "Escape",
    "backspace": "Backspace",
    "delete": "Delete",
    "up": "ArrowUp",
    "down": "ArrowDown",
    "left": "ArrowLeft",
    "right": "ArrowRight",
    "home": "Home",
    "end": "End",
    "pageup": "PageUp",
    "pagedown": "PageDown",
    "f1": "F1",
    "f2": "F2",
    "f3": "F3",
    "f4": "F4",
    "f5": "F5",
    "f6": "F6",
    "f7": "F7",
    "f8": "F8",
    "f9": "F9",
    "f10": "F10",
    "f11": "F11",
    "f12": "F12",
    "space": " ",
    "insert": "Insert",
}

_MODIFIER_MAP: dict[str, str] = {
    "ctrl": "Control",
    "control": "Control",
    "alt": "Alt",
    "shift": "Shift",
    "meta": "Meta",
    "win": "Meta",
    "super": "Meta",
}


@dataclass
class _LiveSession:
    session_id: str
    host: str
    user: str
    context: object  # playwright BrowserContext
    login_page: object  # playwright Page (main UI)
    viewer_page: object  # playwright Page (viewer.html)
    opened_at_ms: int
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class PlaywrightAmiBackend:
    """AMI MegaRAC KVM backend via Playwright headless Chromium."""

    def __init__(self, *, headless: bool = True) -> None:
        self._headless = headless
        self._live: dict[str, _LiveSession] = {}
        self._playwright: object | None = None
        self._browser: object | None = None
        self._browser_lock = asyncio.Lock()

    async def _ensure_browser(self) -> tuple:
        """Lazily start Playwright and launch a shared browser instance."""
        async with self._browser_lock:
            if self._browser is not None:
                return self._playwright, self._browser  # type: ignore[return-value]

            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:
                raise KVMError(
                    "playwright is not installed. Install with: "
                    "uv add playwright --optional kvm-playwright && "
                    "uv run playwright install chromium",
                    stage="launching_browser",
                ) from exc

            self._playwright = await async_playwright().start()
            pw = self._playwright
            self._browser = await pw.chromium.launch(  # type: ignore[union-attr]
                headless=self._headless,
                args=[
                    "--ignore-certificate-errors",
                    "--disable-gpu",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            return pw, self._browser

    async def open(
        self,
        host: str,
        user: str,
        password: str,
        progress: ProgressCallback,
    ) -> SessionHandle:
        await progress(ProgressEvent(stage="launching_browser"))
        _, browser = await self._ensure_browser()

        context = await browser.new_context(  # type: ignore[union-attr]
            ignore_https_errors=True,
            viewport={"width": 1920, "height": 1080},
        )

        login_page = None
        viewer_page = None
        try:
            login_page = await context.new_page()

            # --- Form-based SPA login ---
            await progress(ProgressEvent(stage="authenticating"))
            await self._form_login(login_page, host, user, password)

            # --- Open viewer in new tab (shares SPA session state) ---
            await progress(ProgressEvent(stage="loading_viewer"))
            viewer_page = await self._open_viewer(context, login_page, host)

            await progress(ProgressEvent(stage="waiting_for_canvas"))
            await self._wait_for_canvas(viewer_page)

            await progress(ProgressEvent(stage="ready"))
            session_id = f"pw-{uuid.uuid4().hex[:12]}"
            self._live[session_id] = _LiveSession(
                session_id=session_id,
                host=host,
                user=user,
                context=context,
                login_page=login_page,
                viewer_page=viewer_page,
                opened_at_ms=int(time.time() * 1000),
            )
            return SessionHandle(
                session_id=session_id,
                host=host,
                user=user,
                backend="playwright",
                opened_at_ms=int(time.time() * 1000),
            )
        except BaseException:
            if viewer_page and not viewer_page.is_closed():  # type: ignore[union-attr]
                try:
                    await viewer_page.close()  # type: ignore[union-attr]
                except Exception:
                    pass
            await context.close()
            raise

    async def _form_login(
        self, page: object, host: str, user: str, password: str
    ) -> None:
        """Navigate to the BMC web UI and authenticate via the SPA login form."""
        from playwright.async_api import Page

        assert isinstance(page, Page)

        resp = await page.goto(
            f"https://{host}/",
            wait_until="networkidle",
            timeout=_LOGIN_PAGE_TIMEOUT_MS,
        )
        if resp and resp.status >= 400:
            raise KVMError(
                f"BMC web UI returned HTTP {resp.status} on {host}",
                stage="authenticating",
            )

        # Wait for the SPA login form to render
        try:
            await page.wait_for_selector("#userid", timeout=_LOGIN_PAGE_TIMEOUT_MS)
        except Exception as exc:
            raise KVMError(
                f"Login form (#userid) did not appear on {host}",
                stage="authenticating",
            ) from exc

        await page.fill("#userid", user)
        await page.fill("#password", password)
        await asyncio.sleep(0.3)
        await page.click("#btn-login")

        # Wait for successful login: URL changes from #login to #dashboard
        try:
            await page.wait_for_url(
                f"**/#{'{'}dashboard,remote*{'}'}",
                timeout=_LOGIN_SUBMIT_TIMEOUT_MS,
            )
        except Exception as exc:
            # Check for error messages in the page
            err_text = await page.evaluate(
                "document.body?.innerText?.substring(0, 200) || ''"
            )
            if "login" in err_text.lower() and (
                "fail" in err_text.lower() or "invalid" in err_text.lower()
            ):
                raise AuthFailedError(
                    f"Login failed for {user}@{host}: {err_text[:100]}",
                    stage="authenticating",
                ) from exc
            raise KVMError(
                f"Post-login navigation did not complete on {host}: "
                f"URL stayed at {page.url}",
                stage="authenticating",
            ) from exc

        logger.info("SPA login succeeded for %s@%s", user, host)

    async def _open_viewer(
        self, context: object, login_page: object, host: str
    ) -> object:
        """Open viewer.html in a new tab within the authenticated context."""
        from playwright.async_api import BrowserContext, Page

        assert isinstance(context, BrowserContext)
        assert isinstance(login_page, Page)

        async with context.expect_page() as new_page_info:
            await login_page.evaluate('window.open("/viewer.html", "_blank")')
        viewer_page = await new_page_info.value

        # Wait for the viewer page to finish initial JS loading
        try:
            await viewer_page.wait_for_load_state("networkidle", timeout=_VIEWER_LOAD_TIMEOUT_MS)
        except Exception:
            logger.debug("viewer networkidle timeout (non-fatal, proceeding)")

        if viewer_page.url and "viewer.html" not in viewer_page.url:
            raise KVMError(
                f"Viewer page redirected away: {viewer_page.url}",
                stage="loading_viewer",
            )

        return viewer_page

    async def _wait_for_canvas(self, page: object) -> None:
        """Wait for the KVM canvas to appear and start rendering frames."""
        from playwright.async_api import Page

        assert isinstance(page, Page)

        try:
            await page.wait_for_selector("canvas", timeout=_CANVAS_WAIT_TIMEOUT_MS)
        except Exception as exc:
            raise KVMError(
                f"KVM canvas did not appear within {_CANVAS_WAIT_TIMEOUT_MS}ms",
                stage="waiting_for_canvas",
            ) from exc

        # Poll for non-black pixel data (IVTP stream needs time to deliver first frame)
        deadline = time.monotonic() + _CANVAS_WAIT_TIMEOUT_MS / 1000
        while time.monotonic() < deadline:
            has_content = await page.evaluate("""() => {
                const c = document.querySelector('canvas');
                if (!c) return false;
                const ctx = c.getContext('2d');
                if (!ctx) return false;
                const w = Math.min(c.width, 64);
                const h = Math.min(c.height, 64);
                if (w === 0 || h === 0) return false;
                const d = ctx.getImageData(0, 0, w, h).data;
                for (let i = 0; i < d.length; i += 4) {
                    if (d[i] !== 0 || d[i+1] !== 0 || d[i+2] !== 0) return true;
                }
                return false;
            }""")
            if has_content:
                return
            await asyncio.sleep(0.5)

        logger.warning("canvas appeared but no non-black pixels detected; proceeding anyway")

    async def screenshot(self, session: SessionHandle) -> bytes:
        live = self._live.get(session.session_id)
        if live is None:
            raise StaleSessionError(f"session {session.session_id} not found", stage="ready")

        from playwright.async_api import Page

        page: Page = live.viewer_page  # type: ignore[assignment]

        async with live._lock:
            try:
                canvases = await page.query_selector_all("canvas")
                if not canvases:
                    raise StaleSessionError(
                        "no canvas element found on viewer page", stage="ready"
                    )
                return await canvases[0].screenshot(type="png")
            except StaleSessionError:
                raise
            except Exception as exc:
                raise StaleSessionError(
                    f"screenshot failed: {exc}", stage="ready"
                ) from exc

    async def sendkeys(self, session: SessionHandle, text: str) -> None:
        live = self._live.get(session.session_id)
        if live is None:
            raise StaleSessionError(f"session {session.session_id} not found", stage="ready")

        from playwright.async_api import Page

        page: Page = live.viewer_page  # type: ignore[assignment]
        async with live._lock:
            await page.keyboard.type(text, delay=50)

    async def sendkey(
        self,
        session: SessionHandle,
        key: str,
        modifiers: list[str] | None = None,
    ) -> None:
        live = self._live.get(session.session_id)
        if live is None:
            raise StaleSessionError(f"session {session.session_id} not found", stage="ready")

        from playwright.async_api import Page

        page: Page = live.viewer_page  # type: ignore[assignment]
        pw_key = _PLAYWRIGHT_KEY_MAP.get(key.lower(), key)
        if modifiers:
            combo = "+".join(
                _MODIFIER_MAP.get(m.lower(), m) for m in modifiers
            )
            combo += f"+{pw_key}"
        else:
            combo = pw_key

        async with live._lock:
            await page.keyboard.press(combo)

    async def close(self, session: SessionHandle) -> None:
        live = self._live.pop(session.session_id, None)
        if live is None:
            return

        try:
            from playwright.async_api import BrowserContext

            ctx: BrowserContext = live.context  # type: ignore[assignment]
            await ctx.close()
        except Exception:
            logger.warning("browser context close failed for %s", session.session_id, exc_info=True)

    async def health(self, session: SessionHandle) -> str:
        live = self._live.get(session.session_id)
        if live is None:
            return "dead"

        from playwright.async_api import Page

        page: Page = live.viewer_page  # type: ignore[assignment]
        try:
            if page.is_closed():
                return "failed"
            has_canvas = await page.evaluate("!!document.querySelector('canvas')")
            return "ok" if has_canvas else "degraded"
        except Exception:
            return "failed"

    async def shutdown(self) -> None:
        """Shut down all live sessions and the shared browser."""
        for sid in list(self._live.keys()):
            handle = SessionHandle(
                session_id=sid,
                host=self._live[sid].host,
                user=self._live[sid].user,
                backend="playwright",
                opened_at_ms=self._live[sid].opened_at_ms,
            )
            await self.close(handle)
        if self._browser is not None:
            try:
                await self._browser.close()  # type: ignore[union-attr]
            except Exception:
                pass
            self._browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()  # type: ignore[union-attr]
            except Exception:
                pass
            self._playwright = None


async def capture_screen_ami(
    host: str,
    user: str,
    password: str,
    *,
    timeout_s: int = 60,
) -> tuple[bytes, str]:
    """One-shot AMI MegaRAC screen capture via Playwright.

    Launches a headless browser, logs in via the SPA form, navigates to
    the KVM viewer, takes a screenshot of the canvas, and cleans up.

    Returns (png_bytes, "image/png").
    """
    backend = PlaywrightAmiBackend(headless=True)
    progress_log: list[str] = []

    async def _log_progress(event: ProgressEvent) -> None:
        progress_log.append(event.stage)
        logger.info("capture_screen_ami: %s", event.stage)

    try:
        session = await asyncio.wait_for(
            backend.open(host, user, password, _log_progress),
            timeout=timeout_s,
        )
        png = await backend.screenshot(session)
        await backend.close(session)
        return png, "image/png"
    finally:
        await backend.shutdown()
