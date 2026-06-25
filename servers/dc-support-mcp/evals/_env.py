"""Shared dc-support credential plumbing and preflights for the eval harness."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from mcp_common.testing.eval import assert_read_only_eval_mode

from dc_support_mcp.secrets import maybe_secret, portal_source, secret_configured

logger = logging.getLogger(__name__)

ENFORCE_READONLY_ENV = "MCP_ENFORCE_READONLY"

# Portal creds forwarded to the spawned MCP child (op:// resolved in parent).
_PORTAL_ENV_VARS = (
    "ORI_PORTAL_USERNAME",
    "ORI_PORTAL_PASSWORD",
    "HYPERTEC_PORTAL_USERNAME",
    "HYPERTEC_PORTAL_PASSWORD",
    "IREN_PORTAL_USERNAME",
    "IREN_PORTAL_PASSWORD",
    "IREN_FRESHDESK_API_KEY",
)


class DcSupportPreflightError(RuntimeError):
    """Raised when dc-support credentials/connectivity cannot be established."""


def configured_vendors() -> list[str]:
    """Return vendors with at least one credential env var set."""
    vendors: list[str] = []
    for vendor in ("ori", "hypertec"):
        if portal_source(vendor):
            vendors.append(vendor)
    if secret_configured("IREN_FRESHDESK_API_KEY") or portal_source("iren"):
        vendors.append("iren")
    return vendors


def apply_resolved_secrets_to_environ() -> list[str]:
    """Resolve op:// portal secrets in the parent and export plain values."""
    resolved: list[str] = []
    for env_var in _PORTAL_ENV_VARS:
        raw = os.environ.get(env_var, "").strip()
        if not raw:
            continue
        if raw.startswith("op://"):
            plain = maybe_secret(env_var)
            if plain:
                os.environ[env_var] = plain
                resolved.append(env_var)
        else:
            resolved.append(env_var)
    return resolved


def dc_support_mcp_env() -> dict[str, str]:
    """Build the ``env=`` dict for ``mcp_server_stdio`` with resolved secrets."""
    apply_resolved_secrets_to_environ()
    os.environ.setdefault(ENFORCE_READONLY_ENV, "1")
    env = {ENFORCE_READONLY_ENV: os.environ.get(ENFORCE_READONLY_ENV, "1")}
    for env_var in _PORTAL_ENV_VARS:
        value = os.environ.get(env_var, "")
        if value:
            env[env_var] = value
    return env


def repo_bin_dir() -> Path:
    return Path(sys.executable).parent


def dc_support_cli_path() -> Path:
    return repo_bin_dir() / "dc-support-cli"


def dc_support_mcp_command() -> str:
    return str(repo_bin_dir() / "dc-support-mcp")


def prepend_repo_bin_to_path() -> str:
    bin_dir = str(repo_bin_dir())
    parts = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p and p != bin_dir]
    os.environ["PATH"] = os.pathsep.join([bin_dir, *parts])
    return bin_dir


def preflight_credentials() -> dict[str, str]:
    """Fail-fast: at least one vendor credential set must be configured."""
    vendors = configured_vendors()
    if not vendors:
        raise DcSupportPreflightError(
            "No dc-support vendor credentials configured in the eval (parent) process. "
            "Set ORI_PORTAL_USERNAME/PASSWORD, HYPERTEC_PORTAL_USERNAME/PASSWORD, "
            "or IREN_FRESHDESK_API_KEY (see servers/dc-support-mcp/docs/CREDENTIALS.md)."
        )
    apply_resolved_secrets_to_environ()
    return {
        "configured_vendors": ",".join(vendors),
        "status": "ok",
    }


def preflight_write_safety() -> dict[str, str | bool | list[str] | None]:
    """Fail-fast: assert enforced read-only eval mode before any model runs."""
    from dc_support_mcp.mcp_server import mcp as dc_support_mcp_server

    return assert_read_only_eval_mode(mcp=dc_support_mcp_server)
