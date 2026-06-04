"""Post-install setup: installs Playwright browsers and validates config.

Usage:
    dc-support-setup            # install browsers + check config
    dc-support-setup --check    # only check config (no install)
"""

from __future__ import annotations

import subprocess
import sys

from .secrets import portal_configured, secret_configured


def _install_playwright_browsers() -> bool:
    print("Installing Playwright Chromium browser...")
    result = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium", "--with-deps"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print("  ✓ Chromium installed")
        return True
    print(f"  ✗ Failed: {result.stderr.strip()}")
    # Try without --with-deps (works on macOS where deps aren't needed)
    result = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print("  ✓ Chromium installed (without system deps)")
        return True
    print(f"  ✗ Failed: {result.stderr.strip()}")
    return False


def _check_config() -> list[str]:
    issues: list[str] = []

    # Check Playwright importable
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401

        print("  ✓ Playwright installed")
    except ImportError:
        issues.append("playwright not installed (pip install playwright)")

    # Check browser binary exists
    try:
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "--dry-run", "chromium"],
            capture_output=True,
            text=True,
        )
        if "is already installed" in result.stdout or result.returncode == 0:
            print("  ✓ Chromium browser available")
        else:
            issues.append("Chromium not installed (run: dc-support-setup)")
    except Exception:
        issues.append("Cannot check Playwright browsers")

    # Check vendor portal credentials (resolved via the credential chain, so a
    # literal value or an op:// 1Password reference both count as configured).
    # Only credential presence/source is inspected here — never the values.
    for vendor in ("ORI", "IREN"):
        user_var = f"{vendor}_PORTAL_USERNAME"
        pass_var = f"{vendor}_PORTAL_PASSWORD"
        if portal_configured(vendor):
            print(f"  ✓ {vendor} portal credentials configured")
        elif secret_configured(user_var) or secret_configured(pass_var):
            issues.append(f"{vendor}: only one of {user_var}/{pass_var} is set")
        else:
            print(f"  · {vendor} portal credentials not set (optional)")

    # Check optional API keys (enable REST API instead of browser scraping)
    if secret_configured("IREN_FRESHDESK_API_KEY"):
        print("  ✓ IREN Freshdesk API key configured (REST API enabled)")
    else:
        print("  · IREN_FRESHDESK_API_KEY not set (using browser fallback)")

    return issues


def main() -> None:
    check_only = "--check" in sys.argv

    print("dc-support-mcp setup\n")

    if not check_only:
        _install_playwright_browsers()
        print()

    print("Checking configuration...")
    issues = _check_config()

    if issues:
        print(f"\n⚠ {len(issues)} issue(s) found:")
        for issue in issues:
            print(f"  • {issue}")
        sys.exit(1)
    else:
        print("\n✓ Ready to use")


if __name__ == "__main__":
    main()
