#!/usr/bin/env python3
"""Validate 1Password CLI auth and required env vars for MCP secret bridging.

Run from the repository root (uses the same environment as the project)::

    uv run python scripts/check_1password_bridge.py REDFISH_USER_REF REDFISH_PASSWORD_REF

Authentication matches ``mcp-plugin-gen doctor``: if ``OP_SERVICE_ACCOUNT_TOKEN`` is
set, ``op whoami`` is not required; otherwise an interactive ``op`` session must work.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from mcp_common.onepassword_cli import op_authenticated, op_cli_version_line


def main(argv: Sequence[str] | None = None) -> int:
    if argv is None and len(sys.argv) <= 1:
        prog = Path(sys.argv[0]).name
        print(
            f"Usage: {prog} ENV_VAR [ENV_VAR ...]",
            file=sys.stderr,
        )
        return 1

    parser = argparse.ArgumentParser(
        description=(
            "Check 1Password CLI availability, authentication, and that "
            "listed environment variables are set."
        )
    )
    parser.add_argument(
        "env_vars",
        nargs="+",
        metavar="ENV_VAR",
        help="Environment variable names that must be non-empty",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    print("Checking 1Password CLI availability...")
    cli_ok, ver_line = op_cli_version_line()
    if not cli_ok:
        print("  FAIL: 'op' CLI not found on PATH or `op --version` failed")
        return 1
    print(f"  OK: op CLI found ({ver_line})")

    print("Checking 1Password authentication...")
    auth_ok, auth_lines = op_authenticated()
    for line in auth_lines:
        print(f"  {line}")
    if not auth_ok:
        return 1

    missing = False
    print("Checking required environment variables...")
    for var in args.env_vars:
        value = os.getenv(var, "").strip()
        if not value:
            print(f"  FAIL: {var} is missing or empty")
            missing = True
        else:
            print(f"  OK: {var} is set")

    if missing:
        return 1

    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
