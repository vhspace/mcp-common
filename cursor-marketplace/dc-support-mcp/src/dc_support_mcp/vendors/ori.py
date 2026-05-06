"""
ORI vendor handler - Atlassian Service Desk integration.

Thin subclass of AtlassianServiceDeskHandler with ORI-specific configuration
and the ProForma-based ticket creation form.
"""

import pickle
import re
import sys
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from playwright.sync_api import Page

from ..constants import (
    BROWSER_NAVIGATION_TIMEOUT,
    BROWSER_WAIT_TIMEOUT,
    ORI_BASE_URL,
    ORI_PORTAL_ID,
)
from .atlassian_base import AtlassianServiceDeskHandler


class OriVendorHandler(AtlassianServiceDeskHandler):
    """ORI Industries vendor handler.

    Inherits all Atlassian Service Desk operations (get/list/comment)
    from the base class.  Adds ORI-specific ticket creation via the
    Infrastructure Support ProForma form.
    """

    VENDOR_NAME = "ori"
    BASE_URL = ORI_BASE_URL
    PORTAL_ID = ORI_PORTAL_ID
    TICKET_ID_PREFIX = "SUPP"
    COOKIE_FILE_NAME = ".ori_session_cookies.pkl"
    HELP_CENTER_ARI = (
        "ari:cloud:help::help-center/"
        "f3011a5f-3a2b-4f0c-8ce8-4a844ae642c2/"
        "30b91073-30af-40c2-95b8-9a7ba8bbec1e"
    )

    # ORI Infrastructure Support form uses Jira ProForma.
    # Request type 299; valid dropdown values discovered from the
    # /gateway/api/proforma/.../formchoices endpoint.
    INFRA_REQUEST_TYPE_ID = 299
    PRIORITY_OPTIONS: ClassVar[list[str]] = ["P1", "P2", "P3", "P4", "P5"]
    URGENCY_OPTIONS: ClassVar[list[str]] = [
        "Critical",
        "High",
        "Moderate",
        "Low",
        "Lowest",
    ]
    IMPACT_OPTIONS: ClassVar[list[str]] = [
        "Highest",
        "High",
        "Medium",
        "Low",
        "Lowest",
    ]

    def create_service_desk_request(
        self,
        summary: str,
        description: str,
        request_type_id: str | int = "299",
        extra_fields: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Route REST API creation to the Playwright form path.

        ORI's Infrastructure Support form uses Jira ProForma, which is
        incompatible with the standard /rest/servicedeskapi/request
        endpoint.  Delegate to create_ticket() which drives the form
        via browser automation.
        """
        priority = "P3"
        urgency = "Moderate"
        impact = "Medium"
        cause = ""
        if extra_fields:
            priority = extra_fields.get("priority", priority)
            urgency = extra_fields.get("urgency", urgency)
            impact = extra_fields.get("impact", impact)
            cause = extra_fields.get("cause", cause)

        result = self.create_ticket(
            summary=summary,
            description=description,
            cause=cause,
            priority=priority,
            urgency=urgency,
            impact=impact,
        )
        if result is None:
            return None
        return {
            "issueKey": result.get("id", ""),
            "issueId": result.get("id", ""),
            **result,
        }

    def create_ticket(
        self,
        summary: str,
        description: str,
        cause: str = "",
        priority: str = "P3",
        urgency: str = "Moderate",
        impact: str = "Medium",
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        """
        Create an Infrastructure Support ticket via Playwright
        form automation on the ORI customer portal.

        Returns dict with id, summary, url on success, None on failure.
        """
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise ImportError(
                "Playwright required for ticket creation.\n"
                "Install: uv pip install playwright && playwright install chromium"
            ) from e

        if self.verbose:
            sys.stderr.write(f"→ Creating ticket: {summary[:60]}...\n")

        create_url = (
            f"{self.BASE_URL}/servicedesk/customer"
            f"/portal/{self.PORTAL_ID}"
            f"/group/29/create/{self.INFRA_REQUEST_TYPE_ID}"
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()

            if self.cookie_file.exists():
                try:
                    with open(self.cookie_file, "rb") as f:
                        data = pickle.load(f)
                    pw_cookies = [
                        {
                            "name": c["name"],
                            "value": c["value"],
                            "domain": c.get("domain", ""),
                            "path": c.get("path", "/"),
                        }
                        for c in data.get("cookies", [])
                    ]
                    if pw_cookies:
                        context.add_cookies(pw_cookies)  # type: ignore[arg-type]
                except (OSError, pickle.PickleError, KeyError):
                    pass

            page = context.new_page()

            try:
                page.goto(
                    create_url,
                    wait_until="domcontentloaded",
                    timeout=BROWSER_NAVIGATION_TIMEOUT,
                )
                page.wait_for_load_state("networkidle")

                if "/user/login" in page.url:
                    if self.verbose:
                        sys.stderr.write("  Session expired, logging in...\n")
                    self._fill_login_form(page)
                    page.goto(
                        create_url,
                        wait_until="domcontentloaded",
                        timeout=BROWSER_NAVIGATION_TIMEOUT,
                    )
                    page.wait_for_load_state("networkidle")

                page.get_by_label("Summary").wait_for(timeout=BROWSER_WAIT_TIMEOUT)
                page.get_by_label("Summary").fill(summary)

                self._select_dropdown(page, "Priority", priority)
                self._select_dropdown(page, "Urgency", urgency)
                self._select_dropdown(page, "Impact", impact)

                desc_box = page.get_by_label("Main content area, start typing")
                desc_box.click()
                self._type_rich_text(page, description)

                cause_box = page.get_by_label("Explain what caused the issue")
                cause_box.fill(cause)

                page.get_by_role("button", name="Send").click()

                page.wait_for_url(
                    f"**/portal/{self.PORTAL_ID}/{self.TICKET_ID_PREFIX}-*",
                    timeout=BROWSER_NAVIGATION_TIMEOUT,
                )

                match = re.search(rf"({self.TICKET_ID_PREFIX}-\d+)", page.url)
                ticket_id = match.group(1) if match else ""

                self._save_cookies(context.cookies())  # type: ignore[arg-type]

                if self.verbose:
                    sys.stderr.write(f"✓ Created {ticket_id}\n")

                return {
                    "id": ticket_id,
                    "summary": summary,
                    "url": (
                        f"{self.BASE_URL}/servicedesk/customer/portal/{self.PORTAL_ID}/{ticket_id}"
                    ),
                    "status": "Open",
                }

            except (PlaywrightError, TimeoutError, ValueError) as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                sys.stderr.write(f"  ori: Ticket creation failed: {self.last_error}\n")
                return None
            finally:
                browser.close()

    @staticmethod
    def _select_dropdown(page: "Page", label: str, value: str) -> None:
        """Select a value from a portal combobox dropdown."""
        combo = page.get_by_role("combobox", name=label)
        combo.click()
        page.get_by_role("option", name=value, exact=True).click()

    @staticmethod
    def _type_rich_text(page: "Page", text: str) -> None:
        """Type into a ProseMirror rich-text editor with paragraph breaks."""
        lines = text.split("\n")
        for i, line in enumerate(lines):
            if line:
                page.keyboard.type(line)
            if i < len(lines) - 1:
                page.keyboard.press("Enter")
