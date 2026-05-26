"""Credential chain doctor — validates 1Password + keyctl + env setup.

Runs a series of checks against the local environment to diagnose
credential resolution issues. Designed to be safe to share with an
AI agent for triage — never prints credential values.

Usage:
    uv run python -m mcp_common.doctor
    # or after install:
    mcp-common-doctor
"""

from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Literal

CheckStatus = Literal["pass", "fail", "skip", "warn"]


@dataclass
class CheckResult:
    name: str
    status: CheckStatus
    detail: str = ""
    fix: str = ""


@dataclass
class DoctorReport:
    checks: list[CheckResult] = field(default_factory=list)

    def add(self, result: CheckResult) -> None:
        self.checks.append(result)

    @property
    def all_passed(self) -> bool:
        return all(c.status in ("pass", "skip", "warn") for c in self.checks)

    @property
    def failure_count(self) -> int:
        return sum(1 for c in self.checks if c.status == "fail")


def check_os(report: DoctorReport) -> None:
    """Detect OS and container environment."""
    system = platform.system()
    is_container = os.path.exists("/.dockerenv") or os.environ.get("DEVCONTAINER") == "true"
    detail = f"{system}"
    if is_container:
        detail += " (devcontainer)"
    report.add(CheckResult(name="OS", status="pass", detail=detail))


def check_keyctl(report: DoctorReport) -> None:
    """Linux kernel keyring availability."""
    if platform.system() != "Linux":
        report.add(
            CheckResult(
                name="keyctl",
                status="skip",
                detail="not Linux (kernel keyring caching unavailable)",
            )
        )
        return

    path = shutil.which("keyctl")
    if not path:
        report.add(
            CheckResult(
                name="keyctl",
                status="fail",
                detail="keyctl not installed",
                fix="apt-get install keyutils  # or equivalent",
            )
        )
        return

    try:
        add = subprocess.run(
            ["keyctl", "add", "user", "mcp-common-doctor-test", "test", "@s"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if add.returncode != 0:
            report.add(
                CheckResult(
                    name="keyctl",
                    status="fail",
                    detail=f"session keyring not writable: {add.stderr.strip()}",
                    fix="ensure process is in a login session with a writable @s keyring",
                )
            )
            return
        key_id = add.stdout.strip()
        subprocess.run(["keyctl", "revoke", key_id], capture_output=True, timeout=5)
    except (subprocess.TimeoutExpired, OSError) as e:
        report.add(
            CheckResult(
                name="keyctl",
                status="fail",
                detail=f"keyctl error: {e}",
                fix="check keyctl permissions or kernel support",
            )
        )
        return

    report.add(
        CheckResult(
            name="keyctl",
            status="pass",
            detail=f"available at {path}, session keyring writable",
        )
    )


def check_op_cli(report: DoctorReport) -> None:
    """1Password CLI availability."""
    path = shutil.which("op")
    if not path:
        report.add(
            CheckResult(
                name="op CLI",
                status="warn",
                detail="op not installed (only static tokens will work)",
                fix="install 1Password CLI: see docs/credential-chain-setup.md",
            )
        )
        return

    try:
        proc = subprocess.run(["op", "--version"], capture_output=True, text=True, timeout=5)
        version = proc.stdout.strip() if proc.returncode == 0 else "unknown"
    except (subprocess.TimeoutExpired, OSError):
        version = "unknown"

    report.add(
        CheckResult(
            name="op CLI",
            status="pass",
            detail=f"v{version} at {path}",
        )
    )


def check_op_auth(report: DoctorReport) -> None:
    """1Password authentication (session, service account, or op-forward)."""
    if not shutil.which("op"):
        report.add(
            CheckResult(
                name="op auth",
                status="skip",
                detail="op CLI not installed",
            )
        )
        return

    if os.environ.get("OP_SERVICE_ACCOUNT_TOKEN"):
        try:
            proc = subprocess.run(
                ["op", "vault", "list"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if proc.returncode == 0:
                report.add(
                    CheckResult(
                        name="op auth",
                        status="pass",
                        detail="service account token (OP_SERVICE_ACCOUNT_TOKEN)",
                    )
                )
                return
            report.add(
                CheckResult(
                    name="op auth",
                    status="fail",
                    detail="OP_SERVICE_ACCOUNT_TOKEN set but rejected",
                    fix=f"verify token validity: {proc.stderr.strip()[:200]}",
                )
            )
            return
        except (subprocess.TimeoutExpired, OSError):
            report.add(CheckResult(name="op auth", status="fail", detail="op timed out"))
            return

    try:
        proc = subprocess.run(
            ["op", "account", "list"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            lines = [line for line in proc.stdout.strip().split("\n") if line.strip()]
            account_count = max(0, len(lines) - 1)
            report.add(
                CheckResult(
                    name="op auth",
                    status="pass",
                    detail=f"{account_count} account(s) signed in",
                )
            )
            return
    except (subprocess.TimeoutExpired, OSError):
        pass

    report.add(
        CheckResult(
            name="op auth",
            status="fail",
            detail="no active op session",
            fix=(
                "run `op signin` (native macOS), set up op-forward (Linux container), "
                "or set OP_SERVICE_ACCOUNT_TOKEN (headless). See docs/credential-chain-setup.md"
            ),
        )
    )


def check_op_forward_relay(report: DoctorReport) -> None:
    """op-forward socat relay (Linux containers)."""
    if platform.system() != "Linux":
        report.add(CheckResult(name="op-forward relay", status="skip", detail="not Linux"))
        return

    is_container = os.path.exists("/.dockerenv") or os.environ.get("DEVCONTAINER") == "true"
    if not is_container:
        report.add(CheckResult(name="op-forward relay", status="skip", detail="not a container"))
        return

    try:
        with socket.create_connection(("127.0.0.1", 18340), timeout=2):
            report.add(
                CheckResult(
                    name="op-forward relay",
                    status="pass",
                    detail="127.0.0.1:18340 reachable",
                )
            )
    except (TimeoutError, OSError):
        report.add(
            CheckResult(
                name="op-forward relay",
                status="warn",
                detail="127.0.0.1:18340 not reachable (op resolution will fail in container)",
                fix=(
                    "restart relay: socat TCP4-LISTEN:18340,bind=127.0.0.1,fork,reuseaddr "
                    "TCP4:host.internal:18340 &"
                ),
            )
        )


def check_env_credentials(report: DoctorReport) -> None:
    """Scan environment for credential-shaped variables and classify them."""
    candidates = [
        k
        for k in os.environ
        if any(k.endswith(suffix) for suffix in ("_TOKEN", "_PASSWORD", "_API_KEY", "_SECRET"))
    ]

    if not candidates:
        report.add(
            CheckResult(
                name="env credentials",
                status="warn",
                detail=(
                    "no credential-shaped env vars detected "
                    "(no *_TOKEN, *_PASSWORD, *_API_KEY, *_SECRET)"
                ),
            )
        )
        return

    classifications = {"static": 0, "op://": 0, "vault://": 0, "empty": 0}
    op_vars: list[str] = []
    for key in candidates:
        value = os.environ.get(key, "").strip()
        if not value:
            classifications["empty"] += 1
        elif value.startswith("op://"):
            classifications["op://"] += 1
            op_vars.append(key)
        elif value.startswith("vault://"):
            classifications["vault://"] += 1
        else:
            classifications["static"] += 1

    parts = []
    for cls, count in classifications.items():
        if count > 0:
            parts.append(f"{count} {cls}")
    detail = f"{len(candidates)} found: " + ", ".join(parts)

    report.add(
        CheckResult(
            name="env credentials",
            status="pass",
            detail=detail,
        )
    )

    if op_vars and shutil.which("op"):
        sample = op_vars[0]
        ref = os.environ[sample]
        try:
            proc = subprocess.run(
                ["op", "read", ref],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                report.add(
                    CheckResult(
                        name=f"op:// resolution ({sample})",
                        status="pass",
                        detail="resolved successfully (value redacted)",
                    )
                )
            else:
                report.add(
                    CheckResult(
                        name=f"op:// resolution ({sample})",
                        status="fail",
                        detail=f"op read failed: {proc.stderr.strip()[:200]}",
                        fix=f'verify reference: op read "{ref}" (run manually to see error)',
                    )
                )
        except subprocess.TimeoutExpired:
            report.add(
                CheckResult(
                    name=f"op:// resolution ({sample})",
                    status="fail",
                    detail="op read timed out after 15s",
                    fix="check op-forward relay; approve Touch ID prompt on host",
                )
            )


def render_report(report: DoctorReport, *, use_color: bool = True) -> str:
    """Render the report as plain text."""
    if use_color and sys.stdout.isatty():
        colors = {
            "pass": "\033[32m",
            "fail": "\033[31m",
            "skip": "\033[90m",
            "warn": "\033[33m",
            "reset": "\033[0m",
        }
    else:
        colors = {"pass": "", "fail": "", "skip": "", "warn": "", "reset": ""}

    icons = {"pass": "[OK]", "fail": "[FAIL]", "skip": "[SKIP]", "warn": "[WARN]"}

    lines = ["mcp-common credential chain doctor", "=" * 40, ""]
    max_name = max(len(c.name) for c in report.checks) if report.checks else 0
    for check in report.checks:
        c = colors[check.status]
        r = colors["reset"]
        icon = icons[check.status]
        lines.append(f"  {c}{icon}{r} {check.name.ljust(max_name)}  {check.detail}")
        if check.fix and check.status == "fail":
            lines.append(f"       fix: {check.fix}")

    lines.append("")
    if report.all_passed:
        lines.append(f"  {colors['pass']}Status: all checks passed{colors['reset']}")
    else:
        lines.append(
            f"  {colors['fail']}Status: {report.failure_count} check(s) failed{colors['reset']}"
        )
    return "\n".join(lines)


def run() -> int:
    """Run all checks and print report. Returns exit code (0 ok, 1 fail)."""
    report = DoctorReport()
    check_os(report)
    check_keyctl(report)
    check_op_cli(report)
    check_op_auth(report)
    check_op_forward_relay(report)
    check_env_credentials(report)

    print(render_report(report))
    return 0 if report.all_passed else 1


def main() -> None:
    """CLI entry point."""
    sys.exit(run())


if __name__ == "__main__":
    main()
