"""Shared AWX credential plumbing and preflights for the eval harness."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from mcp_common.credential_chain import CredentialChain, EnvResolver
from mcp_common.testing.eval import assert_read_only_eval_mode

from awx_mcp.awx_client import AwxRestClient
from awx_mcp.config import DEFAULT_AWX_HOST, resolve_secret

logger = logging.getLogger(__name__)

AWX_TOKEN_ENV = "AWX_TOKEN"
AWX_HOST_ENV = "AWX_HOST"
ENFORCE_READONLY_ENV = "MCP_ENFORCE_READONLY"

_REPO_ROOT = Path(__file__).resolve().parent.parent
_LAST_RESOLVED_TOKEN: str | None = None


class AwxPreflightError(RuntimeError):
    """Raised when AWX credentials/connectivity cannot be established."""


def _awx_token_chain() -> CredentialChain:
    return CredentialChain([EnvResolver(AWX_TOKEN_ENV)], name="awx")


def resolve_awx_token(*, required: bool = True) -> str | None:
    """Resolve ``AWX_TOKEN`` in the parent to a plain token string."""
    global _LAST_RESOLVED_TOKEN
    raw = os.environ.get(AWX_TOKEN_ENV, "")
    try:
        if raw.startswith("op://"):
            token = resolve_secret(raw, key_name="mcp:awx-token")
        elif raw:
            token = _awx_token_chain().get()
        else:
            token = None
    except Exception:
        token = None

    if token:
        _LAST_RESOLVED_TOKEN = token
        return token

    if _LAST_RESOLVED_TOKEN:
        return _LAST_RESOLVED_TOKEN

    if required:
        hint = (
            "AWX_TOKEN is an op:// reference but it could not be resolved "
            "in the eval (parent) process — ensure the `op` CLI works here "
            "(op-forward session or OP_SERVICE_ACCOUNT_TOKEN)."
            if raw.startswith("op://")
            else "AWX_TOKEN is empty or unset in the eval (parent) process."
        )
        raise AwxPreflightError(hint)
    return None


def resolve_awx_host() -> str:
    raw = os.environ.get(AWX_HOST_ENV, "").strip() or os.environ.get("CONTROLLER_HOST", "").strip()
    return raw or DEFAULT_AWX_HOST


def apply_resolved_token_to_environ() -> str | None:
    """Resolve the token once and write the plain value into ``os.environ``."""
    token = resolve_awx_token(required=False)
    if token:
        os.environ[AWX_TOKEN_ENV] = token
    return token


def awx_mcp_env() -> dict[str, str]:
    """Build the ``env=`` dict for ``mcp_server_stdio`` with a plain token."""
    token = resolve_awx_token(required=False)
    if token is None:
        token = os.environ.get(AWX_TOKEN_ENV, "")
        if token.startswith("op://"):
            logger.warning(
                "awx_mcp_env: AWX_TOKEN is an unresolved op:// reference; "
                "forwarding it as-is. The spawned awx-mcp child will likely fail."
            )
    host = resolve_awx_host()
    os.environ.setdefault(ENFORCE_READONLY_ENV, "1")
    return {
        AWX_HOST_ENV: host,
        AWX_TOKEN_ENV: token,
        ENFORCE_READONLY_ENV: os.environ.get(ENFORCE_READONLY_ENV, "1"),
    }


def repo_bin_dir() -> Path:
    return Path(sys.executable).parent


def awx_cli_path() -> Path:
    return repo_bin_dir() / "awx-cli"


def awx_mcp_command() -> str:
    return str(repo_bin_dir() / "awx-mcp")


def prepend_repo_bin_to_path() -> str:
    bin_dir = str(repo_bin_dir())
    parts = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p and p != bin_dir]
    os.environ["PATH"] = os.pathsep.join([bin_dir, *parts])
    return bin_dir


def preflight_awx(*, verify_ssl: bool | None = None) -> dict[str, str]:
    """Fail-fast: resolve creds in parent + GET /api/v2/ping/."""
    was_opref = os.environ.get(AWX_TOKEN_ENV, "").startswith("op://")
    host = resolve_awx_host()
    token = resolve_awx_token(required=True)
    assert token is not None
    os.environ[AWX_HOST_ENV] = host
    os.environ[AWX_TOKEN_ENV] = token

    if verify_ssl is None:
        verify_ssl = os.environ.get("VERIFY_SSL", "true").lower() not in ("false", "0", "no")

    client = AwxRestClient(
        host=host,
        token=token,
        verify_ssl=verify_ssl,
    )
    try:
        client.get("ping")
    except Exception as exc:
        raise AwxPreflightError(
            f"authenticated AWX call (GET {host}/api/v2/ping/) failed: "
            f"{type(exc).__name__}: {exc}. Check AWX_HOST/AWX_TOKEN and connectivity."
        ) from exc
    finally:
        client.close()

    return {
        "awx_host": host,
        "token_source": "op:// (resolved in parent)" if was_opref else "plain",
        "status": "ok",
    }


def preflight_write_safety() -> dict[str, str | bool | list[str] | None]:
    """Fail-fast: assert enforced read-only eval mode before any model runs."""
    from awx_mcp.server import mcp as awx_mcp_server

    return assert_read_only_eval_mode(mcp=awx_mcp_server)
