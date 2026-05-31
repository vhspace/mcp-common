"""Shared NetBox credential plumbing for the eval harness (netbox-mcp#117).

Why this module exists
----------------------
The eval tasks spawn the ``netbox-mcp`` server as a **child process** via
inspect-ai's ``mcp_server_stdio``. inspect (really the MCP python SDK) launches
that child with a *restricted* environment::

    env = {**get_default_environment(), **explicit_env}

where ``get_default_environment()`` only inherits a safelist
(``HOME``/``LOGNAME``/``PATH``/``SHELL``/``TERM``/``USER`` on POSIX) and
``explicit_env`` was just ``{"NETBOX_URL", "NETBOX_TOKEN"}``.

In the normal MCP/CLI runtime ``NETBOX_TOKEN`` is frequently an **``op://``
1Password reference** (resolved by mcp-common's ``EnvResolver`` via the ``op``
CLI / ``OP_SERVICE_ACCOUNT_TOKEN``). Because the child never receives
``OP_SERVICE_ACCOUNT_TOKEN`` (nor the op-forward session), the child's
``credential_chain[netbox]`` cannot resolve the ``op://`` reference and raises::

    credential_chain[netbox]: all resolvers exhausted, no credential available

NetBox tool calls then fail *inside* the eval, so **every model scores ~0 for an
infrastructure reason, not a skill reason** — silently depressing the whole
matrix (see netbox-mcp#117, #114, #116; prior env-forwarding #108/#109).

The fix
-------
Resolve the NetBox token **once, in the parent** (which *does* have ``op`` /
``OP_SERVICE_ACCOUNT_TOKEN``) using the SAME mechanism the runtime uses, then
hand the spawned child a **plain** token. The child then needs no 1Password
access at all. We also export the resolved plain token into ``os.environ`` so
the CLI/bash eval paths (which inherit the parent env) are covered too.

A second, subtle gotcha (also handled here): inspect-ai reloads the nearest
``.env`` with ``override=True`` *inside* ``inspect_ai.eval()`` when running under
VSCode/Cursor (``inspect_ai/_util/dotenv.py::init_dotenv`` ->
``is_running_in_vscode()``). If that ``.env`` sets ``NETBOX_TOKEN`` to an
``op://`` reference, it **clobbers** the plain token the preflight already
resolved — right before the task builds the child env — re-introducing the bug.
We defeat this by caching the first plain token resolved this process
(:data:`_LAST_RESOLVED_TOKEN`) and preferring it whenever the current env only
holds an unresolvable ``op://`` reference.

The CLI-aware mcp-common scorer (netbox-mcp#126 follow-up) is now adopted in
``cli_eval.py`` / ``combined_eval.py``. TODO(#112): schema work — still out of
scope here (no behavior change in this module).
"""

from __future__ import annotations

import logging
import os

from mcp_common.credential_chain import CredentialChain, EnvResolver

logger = logging.getLogger(__name__)

NETBOX_TOKEN_ENV = "NETBOX_TOKEN"
NETBOX_URL_ENV = "NETBOX_URL"

# Last token successfully resolved to a PLAIN value in THIS process. Cached so a
# good token survives inspect-ai reloading .env with override=True mid-eval.
#
# inspect_ai/_util/dotenv.py::init_dotenv() runs inside inspect_ai.eval() and,
# when running under VSCode/Cursor (is_running_in_vscode() -> True), reloads the
# nearest .env with override=True. That can clobber os.environ["NETBOX_TOKEN"]
# (which the preflight resolved to a usable plain token) with an op:// reference
# the spawned child cannot resolve — re-introducing the #117 failure. Preferring
# the cached plain token defeats that clobber.
_LAST_RESOLVED_TOKEN: str | None = None


class NetboxPreflightError(RuntimeError):
    """Raised when NetBox credentials/connectivity cannot be established.

    Carrying this as a dedicated type lets the matrix runner abort *loudly*
    (fail-fast) instead of silently scoring ~0 across every model.
    """


def _netbox_chain() -> CredentialChain:
    """The exact chain the netbox-mcp runtime uses to read ``NETBOX_TOKEN``.

    ``EnvResolver`` auto-detects ``op://`` references and resolves them via the
    ``op`` CLI, so calling ``.get()`` here (in the parent) turns an ``op://``
    reference into a plain token.
    """
    return CredentialChain([EnvResolver(NETBOX_TOKEN_ENV)], name="netbox")


def resolve_netbox_token(*, required: bool = True) -> str | None:
    """Resolve ``NETBOX_TOKEN`` in the *parent* to a plain token string.

    Resolves ``op://`` references via the parent's ``op``/1Password access so
    the value returned is always a plain credential safe to forward to a child
    that has no 1Password access.

    Args:
        required: When ``True`` (default), raise :class:`NetboxPreflightError`
            if no credential can be resolved. When ``False``, return ``None``.
    """
    global _LAST_RESOLVED_TOKEN
    try:
        token = _netbox_chain().get()
    except Exception:
        token = None

    if token:
        _LAST_RESOLVED_TOKEN = token
        return token

    # Current env holds no resolvable token (empty, or an op:// ref this process
    # can't resolve). Prefer a plain token resolved earlier this run — this
    # defeats inspect-ai's override=True .env reload that can clobber a good
    # NETBOX_TOKEN mid-eval (see _LAST_RESOLVED_TOKEN note above).
    if _LAST_RESOLVED_TOKEN:
        return _LAST_RESOLVED_TOKEN

    if required:
        raw = os.environ.get(NETBOX_TOKEN_ENV, "")
        hint = (
            "NETBOX_TOKEN is an op:// reference but it could not be resolved "
            "in the eval (parent) process — ensure the `op` CLI works here "
            "(op-forward session or OP_SERVICE_ACCOUNT_TOKEN)."
            if raw.startswith("op://")
            else "NETBOX_TOKEN is empty or unset in the eval (parent) process."
        )
        raise NetboxPreflightError(hint)
    return None


def apply_resolved_token_to_environ() -> str | None:
    """Resolve the token once and write the **plain** value into ``os.environ``.

    This single action fixes every eval mode:

    * ``mcp`` / ``combined`` — the explicit ``mcp_server_stdio`` env dict reads
      ``os.environ["NETBOX_TOKEN"]`` (now plain), so the spawned child needs no
      1Password access.
    * ``cli`` / the bash half of ``combined`` — inspect's local bash sandbox
      inherits the parent ``os.environ`` (``{**os.environ, **env}``), so the
      ``netbox-cli`` it runs also sees a plain token.

    Idempotent: a plain token resolves to itself. Returns the token (or ``None``
    when nothing could be resolved).
    """
    token = resolve_netbox_token(required=False)
    if token:
        os.environ[NETBOX_TOKEN_ENV] = token
    return token


def netbox_mcp_env() -> dict[str, str]:
    """Build the ``env=`` dict for ``mcp_server_stdio`` with a **plain** token.

    Resolves the token in the parent so the spawned ``netbox-mcp`` child never
    needs ``op``/1Password. Falls back to the raw env value when resolution
    fails (best-effort) so that constructing a Task without creds — e.g. unit
    tests or ``--dry-run`` — does not raise; the matrix preflight is the loud
    fail-fast gate for real runs.
    """
    token = resolve_netbox_token(required=False)
    if token is None:
        token = os.environ.get(NETBOX_TOKEN_ENV, "")
        if token.startswith("op://"):
            logger.warning(
                "netbox_mcp_env: NETBOX_TOKEN is an unresolved op:// reference; "
                "forwarding it as-is. The spawned netbox-mcp child will likely "
                "fail credential_chain[netbox] (see netbox-mcp#117)."
            )
    return {
        NETBOX_URL_ENV: os.environ.get(NETBOX_URL_ENV, ""),
        NETBOX_TOKEN_ENV: token,
    }


def preflight_netbox(*, verify_ssl: bool | None = None) -> dict[str, str]:
    """Fail-fast preflight: resolve creds in parent + one cheap authenticated call.

    1. Resolves ``NETBOX_TOKEN`` (op:// -> plain) and exports the plain value to
       ``os.environ`` so all eval modes inherit a usable token.
    2. Asserts ``NETBOX_URL`` is set.
    3. Makes one cheap authenticated call (``GET /api/status/``) to prove the
       token and connectivity actually work.

    Raises :class:`NetboxPreflightError` (naming the missing/forbidden
    credential) on any failure, so the matrix aborts loudly instead of silently
    scoring ~0 for every model.

    Returns a small dict of preflight facts for surfacing in ``summary.json``.
    """
    # Capture the original token kind for human-readable provenance BEFORE we
    # overwrite os.environ with the resolved plain value.
    was_opref = os.environ.get(NETBOX_TOKEN_ENV, "").startswith("op://")

    url = os.environ.get(NETBOX_URL_ENV, "").strip()
    if not url:
        raise NetboxPreflightError(
            f"{NETBOX_URL_ENV} is not set in the eval (parent) process — cannot reach NetBox."
        )

    # Resolve in parent and export plain token for all child paths.
    token = resolve_netbox_token(required=True)
    assert token is not None  # resolve_netbox_token(required=True) raises otherwise
    os.environ[NETBOX_TOKEN_ENV] = token

    if verify_ssl is None:
        verify_ssl = os.environ.get("VERIFY_SSL", "true").lower() not in ("false", "0", "no")

    # Import here so the module is importable without the package installed
    # (e.g. tooling that only needs the resolver helpers).
    from netbox_mcp.netbox_client import NetBoxRestClient

    client = NetBoxRestClient(url=url, token=token, verify_ssl=verify_ssl)
    try:
        client.get("status")
    except Exception as exc:
        raise NetboxPreflightError(
            f"authenticated NetBox call (GET {url}/api/status/) failed: "
            f"{type(exc).__name__}: {exc}. Check NETBOX_URL/NETBOX_TOKEN and "
            "connectivity."
        ) from exc

    return {
        "netbox_url": url,
        "token_source": "op:// (resolved in parent)" if was_opref else "plain",
        "status": "ok",
    }
