"""Credential resolution helpers for MCP servers.

This module provides a small, reusable provider for resolving username/password
pairs from explicit input, environment variables, and 1Password references.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class UsernamePassword:
    user: str
    password: str


@dataclass(frozen=True)
class CredentialAuditEvent:
    """Audit-safe metadata for a credential resolution decision."""

    source: str
    candidate: str | None = None
    host: str | None = None
    site_hint: str | None = None
    used_1password_refs: bool = False

    def as_log_fields(self) -> dict[str, str | bool]:
        fields: dict[str, str | bool] = {"source": self.source}
        if self.candidate:
            fields["candidate"] = self.candidate
        if self.host:
            fields["host"] = self.host
        if self.site_hint:
            fields["site_hint"] = self.site_hint
        fields["used_1password_refs"] = self.used_1password_refs
        return fields


@dataclass(frozen=True)
class CredentialResult:
    credentials: UsernamePassword
    audit: CredentialAuditEvent


@dataclass(frozen=True)
class CredentialCandidate:
    """How to resolve one named credential pair.

    If *_ref_env keys are set and populated, the value is resolved via
    `op read <reference>`. Otherwise the plain env var values are used.
    """

    name: str
    user_env: str
    password_env: str
    user_ref_env: str | None = None
    password_ref_env: str | None = None


def _read_1password_reference(
    reference: str,
    *,
    timeout_s: int = 5,
) -> str | None:
    ref = reference.strip()
    if not ref:
        return None
    try:
        proc = subprocess.run(
            ["op", "read", ref],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


class UsernamePasswordCredentialProvider:
    """Resolve username/password pairs from multiple sources.

    Resolution order:
    1) Explicit user/password arguments (if both present)
    2) Candidate selected by `site_hint_env` (exact match by candidate name)
    3) Candidate selected by `site_selector(host)` callback
    4) Single resolvable non-generic candidate (when unambiguous)
    5) Generic candidate fallback
    """

    def __init__(
        self,
        *,
        candidates: Sequence[CredentialCandidate],
        generic_candidate: CredentialCandidate | None = None,
        site_hint_env: str | None = None,
        site_selector: Callable[[str], str | None] | None = None,
    ) -> None:
        self._candidates = list(candidates)
        self._generic = generic_candidate
        self._site_hint_env = site_hint_env
        self._site_selector = site_selector

        seen: dict[str, str] = {}
        for c in self._candidates:
            key = c.name.strip().upper()
            if not key:
                msg = "CredentialCandidate name must be non-empty"
                raise ValueError(msg)
            if key in seen:
                msg = (
                    f"Duplicate credential candidate name {c.name!r} (conflicts with {seen[key]!r})"
                )
                raise ValueError(msg)
            seen[key] = c.name
        if self._generic is not None:
            gkey = self._generic.name.strip().upper()
            if not gkey:
                msg = "generic_candidate name must be non-empty"
                raise ValueError(msg)
            if gkey in seen:
                msg = (
                    f"generic_candidate name {self._generic.name!r} duplicates "
                    f"candidate {seen[gkey]!r}"
                )
                raise ValueError(msg)

    @staticmethod
    def _value(key: str | None) -> str:
        if not key:
            return ""
        return os.getenv(key, "").strip()

    def _resolve_candidate(
        self,
        candidate: CredentialCandidate,
    ) -> CredentialResult | None:
        user_ref = self._value(candidate.user_ref_env)
        password_ref = self._value(candidate.password_ref_env)

        used_refs = False
        user = ""
        password = ""

        if user_ref and password_ref:
            user_val = _read_1password_reference(user_ref)
            pass_val = _read_1password_reference(password_ref)
            if user_val and pass_val:
                user = user_val
                password = pass_val
                used_refs = True

        if not user or not password:
            user = self._value(candidate.user_env)
            password = self._value(candidate.password_env)

        if not user or not password:
            return None

        source = "1password_ref" if used_refs else "env"
        return CredentialResult(
            credentials=UsernamePassword(user=user, password=password),
            audit=CredentialAuditEvent(
                source=source,
                candidate=candidate.name,
                used_1password_refs=used_refs,
            ),
        )

    def resolve(
        self,
        *,
        host: str = "",
        explicit_user: str | None = None,
        explicit_password: str | None = None,
    ) -> CredentialResult | None:
        host = host.strip()
        user = (explicit_user or "").strip()
        password = (explicit_password or "").strip()
        if user and password:
            return CredentialResult(
                credentials=UsernamePassword(user=user, password=password),
                audit=CredentialAuditEvent(
                    source="explicit",
                    host=host or None,
                ),
            )

        by_name = {c.name.upper(): c for c in self._candidates}
        site_hint = self._value(self._site_hint_env).upper() if self._site_hint_env else ""
        if site_hint and site_hint in by_name:
            result = self._resolve_candidate(by_name[site_hint])
            if result is not None:
                return CredentialResult(
                    credentials=result.credentials,
                    audit=CredentialAuditEvent(
                        source=result.audit.source,
                        candidate=result.audit.candidate,
                        host=host or None,
                        site_hint=site_hint,
                        used_1password_refs=result.audit.used_1password_refs,
                    ),
                )

        if host and self._site_selector is not None:
            selected = (self._site_selector(host) or "").strip().upper()
            if selected and selected in by_name:
                result = self._resolve_candidate(by_name[selected])
                if result is not None:
                    return CredentialResult(
                        credentials=result.credentials,
                        audit=CredentialAuditEvent(
                            source=result.audit.source,
                            candidate=result.audit.candidate,
                            host=host,
                            site_hint=selected,
                            used_1password_refs=(result.audit.used_1password_refs),
                        ),
                    )

        resolved: list[CredentialResult] = []
        for candidate in self._candidates:
            result = self._resolve_candidate(candidate)
            if result is not None:
                resolved.append(result)
        if len(resolved) == 1:
            only = resolved[0]
            return CredentialResult(
                credentials=only.credentials,
                audit=CredentialAuditEvent(
                    source=only.audit.source,
                    candidate=only.audit.candidate,
                    host=host or None,
                    used_1password_refs=only.audit.used_1password_refs,
                ),
            )

        if self._generic is not None:
            generic = self._resolve_candidate(self._generic)
            if generic is not None:
                return CredentialResult(
                    credentials=generic.credentials,
                    audit=CredentialAuditEvent(
                        source=generic.audit.source,
                        candidate=generic.audit.candidate,
                        host=host or None,
                        used_1password_refs=generic.audit.used_1password_refs,
                    ),
                )
        return None
