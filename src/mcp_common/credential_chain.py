"""Token credential chain with lazy resolution and TTL caching.

Provides a chain of resolvers that are tried in order to produce a bearer/token
credential.  Supports static values, environment variables, and 1Password
``op://`` references resolved at request time.

Usage::

    from mcp_common.credential_chain import CredentialChain, EnvResolver, StaticResolver

    # Static token (backward compat)
    chain = CredentialChain([StaticResolver("abc123")])

    # Env var with op:// support
    chain = CredentialChain([EnvResolver("NETBOX_TOKEN")], name="netbox")

    # Use with requests
    session.auth = ResolvedAuth(chain, header_format="Token {}")
"""

from __future__ import annotations

import abc
import logging
import os
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

DEFAULT_TTL = 300  # seconds


class Resolver(abc.ABC):
    """Base class for credential resolvers."""

    @abc.abstractmethod
    def resolve(self) -> str | None:
        """Attempt to resolve a credential value. Return None if unavailable."""


@dataclass(frozen=True)
class StaticResolver(Resolver):
    """Resolver that returns a fixed string value."""

    value: str

    def resolve(self) -> str | None:
        return self.value if self.value else None


@dataclass(frozen=True)
class EnvResolver(Resolver):
    """Resolver that reads from an environment variable.

    If the env var value starts with ``op://``, it is treated as a
    1Password secret reference and resolved via ``op read``.
    """

    env_var: str
    op_timeout_s: int = 5

    def resolve(self) -> str | None:
        raw = os.environ.get(self.env_var, "").strip()
        if not raw:
            return None
        if raw.startswith("op://"):
            return _read_op_reference(raw, timeout_s=self.op_timeout_s)
        return raw


@dataclass(frozen=True)
class OnePasswordResolver(Resolver):
    """Resolver that reads a 1Password ``op://`` reference directly.

    Unlike :class:`EnvResolver` which reads the reference from an environment
    variable, this resolver takes the ``op://`` reference value directly and
    resolves it via ``op read``.
    """

    reference: str
    op_timeout_s: int = 5

    def resolve(self) -> str | None:
        ref = self.reference.strip()
        if not ref:
            return None
        return _read_op_reference(ref, timeout_s=self.op_timeout_s)


@dataclass
class CredentialChain:
    """Ordered chain of resolvers with TTL caching.

    Tries each resolver in sequence, returning the first non-None value.
    Results are cached for ``ttl`` seconds to avoid repeated subprocess calls.
    """

    resolvers: Sequence[Resolver]
    name: str = "default"
    ttl: float = DEFAULT_TTL
    _cached_value: str | None = field(default=None, init=False, repr=False)
    _cached_at: float = field(default=0.0, init=False, repr=False)

    def get(self) -> str:
        """Resolve and return the current credential value.

        Raises:
            RuntimeError: If no resolver in the chain can produce a value.
        """
        now = time.monotonic()
        if self._cached_value is not None and (now - self._cached_at) < self.ttl:
            return self._cached_value

        for resolver in self.resolvers:
            value = resolver.resolve()
            if value:
                self._cached_value = value
                self._cached_at = now
                logger.debug(
                    "credential_chain[%s]: resolved via %s",
                    self.name,
                    type(resolver).__name__,
                )
                return value

        raise RuntimeError(
            f"credential_chain[{self.name}]: all resolvers exhausted, no credential available"
        )

    def invalidate(self) -> None:
        """Clear the cached value, forcing re-resolution on next access."""
        self._cached_value = None
        self._cached_at = 0.0


class ResolvedAuth:
    """Requests auth handler that resolves credentials per-request via a chain.

    Implements the ``requests.auth.AuthBase`` interface so it can be assigned
    to ``session.auth``.  The ``requests`` library is imported lazily so that
    consumers who only need the resolver classes are not forced to install it.

    Args:
        chain: The credential chain to resolve tokens from.
        header_format: Format string for the Authorization header value.
                      The resolved token replaces ``{}``.
                      E.g. ``"Token {}"`` or ``"Bearer {}"``.
    """

    def __init__(self, chain: CredentialChain, header_format: str = "Bearer {}") -> None:
        self._chain = chain
        self._header_format = header_format

    def __call__(self, r):  # type: ignore[override]
        token = self._chain.get()
        r.headers["Authorization"] = self._header_format.format(token)
        return r


def chain_from_value(
    value: str,
    *,
    op_fallback_env: str | None = None,
    name: str = "default",
    ttl: float = DEFAULT_TTL,
) -> CredentialChain:
    """Create a CredentialChain from a raw value.

    If the value starts with ``op://``, wraps it in an EnvResolver using
    *op_fallback_env* (so the reference is re-read from the env var on each
    resolution, allowing rotation). Otherwise wraps in a StaticResolver.

    Args:
        value: The token value or op:// reference.
        op_fallback_env: Env var name to use for EnvResolver when value is op://.
        name: Optional name for the chain (used in logs).
        ttl: Cache TTL in seconds.
    """
    if value.startswith("op://"):
        env_var = op_fallback_env or "CREDENTIAL_TOKEN"
        return CredentialChain([EnvResolver(env_var)], name=name, ttl=ttl)
    return CredentialChain([StaticResolver(value)], name=name, ttl=ttl)


def _read_op_reference(reference: str, *, timeout_s: int = 5) -> str | None:
    """Resolve an ``op://`` reference via the 1Password CLI."""
    try:
        proc = subprocess.run(
            ["op", "read", reference],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        logger.debug("op CLI unavailable or timed out for ref: %s", reference[:30])
        return None
    if proc.returncode != 0:
        logger.debug("op read failed (rc=%d) for ref: %s", proc.returncode, reference[:30])
        return None
    return proc.stdout.strip() or None
