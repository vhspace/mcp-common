"""Shared 1Password CLI readiness checks for doctor and helper scripts."""

from __future__ import annotations

import os
import subprocess

SERVICE_ACCOUNT_TOKEN_ENV = "OP_SERVICE_ACCOUNT_TOKEN"


def op_cli_version_line(*, timeout_s: float = 5.0) -> tuple[bool, str]:
    """Return whether ``op`` is usable and a short version string for display."""
    try:
        proc = subprocess.run(
            ["op", "--version"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False, "missing/unavailable"
    if proc.returncode != 0:
        return False, "missing/unavailable"
    return True, (proc.stdout.strip() or "ok")


def op_authenticated(*, timeout_s: float = 5.0) -> tuple[bool, list[str]]:
    """Return whether 1Password auth is sufficient for ``op read`` / similar.

    If ``OP_SERVICE_ACCOUNT_TOKEN`` is set (non-empty), returns success without
    calling ``op whoami`` (non-interactive / CI path).

    Otherwise requires ``op whoami`` to succeed (interactive session).
    """
    token = os.getenv(SERVICE_ACCOUNT_TOKEN_ENV, "").strip()
    if token:
        return True, [
            f"auth: service account ({SERVICE_ACCOUNT_TOKEN_ENV} is set; whoami not required)",
        ]

    try:
        whoami = subprocess.run(
            ["op", "whoami"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False, [
            "auth: FAIL — `op` not runnable (install CLI, check PATH, or set "
            f"{SERVICE_ACCOUNT_TOKEN_ENV})",
        ]

    if whoami.returncode == 0:
        return True, ["auth: interactive session (`op whoami` succeeded)"]

    detail = (whoami.stderr or whoami.stdout or "").strip()
    lines = [
        "auth: FAIL — not authenticated. Set "
        f"{SERVICE_ACCOUNT_TOKEN_ENV} for non-interactive use, or run "
        "`op signin` / `op account add` for an interactive session.",
    ]
    if detail:
        lines.append(f"op whoami: {detail}")
    return False, lines
