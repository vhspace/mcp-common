"""Secret resolution helpers built on ``mcp_common`` credential primitives.

Every secret consumed by dc-support-mcp (vendor portal passwords, API tokens,
Grafana/NetBox credentials) is resolved through these helpers instead of a raw
``os.getenv``.  This gives the whole server one consistent behaviour:

- Any value may be a **literal** (``abc123``) or a **1Password reference**
  (``op://Vault/Item/field``).  ``op://`` refs are auto-detected and resolved at
  request time via the ``op`` CLI (works with op-forward in devcontainers or a
  signed-in ``op`` session).
- Resolved secrets are cached in the Linux kernel keyring (``keyctl``) so
  multiple CLI invocations in the same login session avoid repeated biometric
  prompts.  Caching degrades gracefully to a direct lookup when ``keyctl`` is
  unavailable.
- Resolution stays **optional / lazy**: a helper returns ``None`` when the
  backing environment variable is unset or empty, so handlers can keep their
  "configured?" checks unchanged.

Only **source metadata** (``env`` vs ``op://``) is ever logged — never the
resolved secret value.

Host URLs are non-secret configuration, but they may still flow through the
same resolver via :func:`host_url` so a deployment can point one at an
``op://`` reference if it wants (``maybe_secret(VAR) or DEFAULT``).  In the
common case the env var is unset and the built-in default is returned without
any 1Password / keyring access.  Truly static, never-overridable config can
keep using a plain ``os.getenv``.
"""

from __future__ import annotations

import logging

from mcp_common.credential_chain import CachedResolver, EnvResolver
from mcp_common.credentials import CredentialAuditEvent

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 1800  # 30 minutes, matches netbox-mcp

# Namespace for keyring entries so dc-support secrets do not collide with other
# mcp-common servers sharing the same login-session keyring.
_KEY_PREFIX = "mcp:dc-support"


def _raw_env(env_var: str) -> str:
    """Return the trimmed raw env value (the literal or the ``op://`` ref)."""
    import os

    return os.environ.get(env_var, "").strip()


def secret_source(env_var: str) -> str | None:
    """Return an audit-safe source label for *env_var* without resolving it.

    - ``None`` when unset/empty
    - ``"op://"`` when the value is a 1Password reference
    - ``"vault://"`` when the value is a (reserved) Vault reference
    - ``"env"`` for a literal value

    This only inspects the *shape* of the configured value, so it is safe to
    call in ``auth-status`` / ``vendors`` views without a live 1Password
    session.
    """
    raw = _raw_env(env_var)
    if not raw:
        return None
    if raw.startswith("op://"):
        return "op://"
    if raw.startswith("vault://"):
        return "vault://"
    return "env"


def secret_configured(env_var: str) -> bool:
    """Return True when *env_var* is set to a non-empty value."""
    return bool(_raw_env(env_var))


def maybe_secret(
    env_var: str,
    *,
    key_name: str | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> str | None:
    """Resolve a single secret, or ``None`` when it is not configured.

    Wraps ``CachedResolver(EnvResolver(env_var))`` so the value may be a literal
    or an ``op://`` reference, with keyctl caching.  Returns ``None`` when the
    env var is unset/empty (preserving the optional/lazy behaviour of the old
    ``os.getenv`` reads) or when resolution fails (e.g. ``op`` unavailable).

    Args:
        env_var: Name of the environment variable holding the secret (or ref).
        key_name: Keyring entry name; defaults to ``mcp:dc-support:<env_var>``.
        ttl_seconds: Keyring cache TTL.
    """
    if not _raw_env(env_var):
        return None
    resolver = CachedResolver(
        inner=EnvResolver(env_var),
        key_name=key_name or f"{_KEY_PREFIX}:{env_var}",
        ttl_seconds=ttl_seconds,
    )
    return resolver.resolve()


def host_url(
    env_var: str,
    default: str,
    *,
    key_name: str | None = None,
) -> str:
    """Resolve a host URL with a built-in *default* (``maybe_secret or default``).

    Host URLs are **non-secret** configuration, but they are resolved through
    the same credential chain as secrets so a deployment may, if it wants,
    point one at an ``op://`` reference (resolved via ``op``) instead of a
    literal.  In the common case the matching env var is unset, so *default* is
    returned without any 1Password / keyring access — no env required.

    Equivalent to ``maybe_secret(env_var) or default``: an unset/empty env var
    (or an ``op://`` ref that fails to resolve) falls back to *default*; a
    literal or a resolvable ``op://`` ref overrides it.
    """
    return maybe_secret(env_var, key_name=key_name) or default


def portal_credentials(vendor: str) -> tuple[str, str, str] | None:
    """Resolve ``{VENDOR}_PORTAL_USERNAME`` / ``{VENDOR}_PORTAL_PASSWORD``.

    Each half is resolved independently via :class:`EnvResolver`, so either the
    username or the password may be a literal value or an ``op://`` reference.

    Returns ``(username, password, source)`` where *source* is ``"op://"`` if
    either half was a 1Password reference, otherwise ``"env"``.  Returns
    ``None`` when either half is unset/empty or fails to resolve.

    Emits an audit-safe log line containing only the vendor and the source —
    never the resolved values.
    """
    prefix = vendor.upper()
    user_env = f"{prefix}_PORTAL_USERNAME"
    pass_env = f"{prefix}_PORTAL_PASSWORD"

    user_raw = _raw_env(user_env)
    pass_raw = _raw_env(pass_env)
    if not user_raw or not pass_raw:
        return None

    user = EnvResolver(user_env).resolve()
    password = EnvResolver(pass_env).resolve()
    if not user or not password:
        return None

    used_1password_refs = user_raw.startswith("op://") or pass_raw.startswith("op://")
    source = "op://" if used_1password_refs else "env"

    audit = CredentialAuditEvent(
        source=source,
        candidate=vendor.lower(),
        used_1password_refs=used_1password_refs,
    )
    logger.info("portal_credentials resolved: %s", audit.as_log_fields())

    return (user, password, source)


def portal_source(vendor: str) -> str | None:
    """Return an audit-safe source label for a vendor's portal pair.

    ``None`` when either half is unset; ``"op://"`` when either half is a
    1Password reference; otherwise ``"env"``.
    """
    prefix = vendor.upper()
    user_source = secret_source(f"{prefix}_PORTAL_USERNAME")
    pass_source = secret_source(f"{prefix}_PORTAL_PASSWORD")
    if user_source is None or pass_source is None:
        return None
    if "op://" in (user_source, pass_source):
        return "op://"
    return "env"


def portal_configured(vendor: str) -> bool:
    """Return True when both halves of a vendor's portal pair are set."""
    return portal_source(vendor) is not None
