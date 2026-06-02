"""Shared write-safety preflight for **write-capable** MCP eval suites.

The shared eval harness (:mod:`mcp_common.testing.eval`) grew up around the
**read-only** ``netbox-mcp`` suite, so it never needed write-safety. Two
**write / side-effecting** servers now stand up eval suites on top of it —
``togethercomputer/awx-mcp`` (launches jobs, creates/updates/deletes resources) and
``togethercomputer/dc-support-mcp`` (files real vendor tickets, flips NetBox status,
creates Alertmanager silences, drives Playwright vendor auth with an
account-lockout risk) — and both need the **same** building blocks
(togethercomputer/mcp-common#156).

This module is the **harness-side glue** around the server-side enforced
read-only guarantee shipped in togethercomputer/mcp-common#148
(:mod:`mcp_common.dual_mode._enforce`, toggled by ``MCP_ENFORCE_READONLY``). It
does **not** re-implement enforcement; it asserts, *before any model runs*, that
the enforced-mode env contract is satisfied so an eval can never accidentally
issue a real side effect, and it captures the facts to drop under
``summary.json["write_safety_preflight"]`` for auditability.

Why a preflight (not just the server guard)
--------------------------------------------
``read_only_tools`` (togethercomputer/mcp-common#131) trims the *exposed* surface and the
``MCP_ENFORCE_READONLY`` middleware refuses writes *server-side*, but neither
**aborts the run** when the toggle was simply never set. A write-capable matrix
that starts with the toggle off would happily let a ``bash`` tool run
``awx-cli launch`` / ``dc-support-cli triage`` for real. The preflight is the
fail-fast that converts that silent misconfiguration into a loud, pre-run error
— mirroring the credential (togethercomputer/netbox-mcp#117) and version
(togethercomputer/netbox-mcp#137/#140) preflights the matrices already run.

Usage::

    from mcp_common.testing.eval import assert_read_only_eval_mode

    # In run_matrix.py, before launching any model:
    summary["write_safety_preflight"] = assert_read_only_eval_mode(mcp=server)
    # raises WriteSafetyError if MCP_ENFORCE_READONLY is unset/off.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fastmcp import FastMCP

__all__ = [
    "WriteSafetyError",
    "assert_read_only_eval_mode",
    "write_safety_preflight_facts",
]


class WriteSafetyError(RuntimeError):
    """Raised when the enforced read-only eval-mode contract is **not** satisfied.

    A write-capable eval suite raises this from :func:`assert_read_only_eval_mode`
    to abort the matrix *before any model runs*, so a misconfiguration (the
    ``MCP_ENFORCE_READONLY`` toggle left unset) can never let an eval issue a
    real side effect.
    """


def _enforce_mode_name(env: Mapping[str, str]) -> tuple[str, str | None, bool, bool]:
    """Resolve the enforce mode from ``env`` without importing fastmcp eagerly.

    Returns ``(mode_name, raw_value, is_off, is_strict)``. The classification is
    delegated to :func:`mcp_common.dual_mode.current_enforce_mode` (the single
    source of truth for the toggle's semantics) by temporarily reflecting ``env``
    into ``os.environ`` only when a custom mapping is supplied — the common case
    (``env is os.environ``) calls straight through.
    """
    from mcp_common.dual_mode import ENFORCE_READONLY_ENV_VAR, EnforceMode, current_enforce_mode

    raw = env.get(ENFORCE_READONLY_ENV_VAR)

    if env is os.environ:
        mode = current_enforce_mode()
    else:
        # Resolve a caller-supplied mapping through the same logic by briefly
        # setting (and restoring) the real env var.
        sentinel = object()
        previous: str | object = os.environ.get(ENFORCE_READONLY_ENV_VAR, sentinel)
        try:
            if raw is None:
                os.environ.pop(ENFORCE_READONLY_ENV_VAR, None)
            else:
                os.environ[ENFORCE_READONLY_ENV_VAR] = raw
            mode = current_enforce_mode()
        finally:
            if previous is sentinel:
                os.environ.pop(ENFORCE_READONLY_ENV_VAR, None)
            else:
                os.environ[ENFORCE_READONLY_ENV_VAR] = previous  # type: ignore[assignment]

    return mode.value, raw, mode is EnforceMode.OFF, mode is EnforceMode.STRICT


def _middleware_installed(mcp: FastMCP) -> bool:
    """Whether the server-side read-only backstop is installed on ``mcp``.

    Uses the public :func:`mcp_common.dual_mode.verify_enforcement_installed`,
    which returns ``True`` only when :class:`ReadOnlyEnforcementMiddleware` is
    attached (and warns when the toggle is on but the middleware is missing).
    """
    from mcp_common.dual_mode import verify_enforcement_installed

    return verify_enforcement_installed(mcp)


def write_safety_preflight_facts(
    *,
    require_strict: bool = False,
    mcp: FastMCP | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build the write-safety preflight facts **without raising**.

    Returns the audit block intended for ``summary.json["write_safety_preflight"]``
    (see :func:`assert_read_only_eval_mode` for the raising variant). The block
    records the resolved enforce mode, the toggle's env var/value, the terse
    refusal message a blocked write would receive, whether the server-side
    middleware is installed (when ``mcp`` is supplied), and an ``ok`` flag plus a
    human-readable ``violations`` list so a caller can log/inspect the result
    even when not aborting.

    Args:
        require_strict: When ``True``, only :attr:`EnforceMode.STRICT` satisfies
            the contract (unclassified tools are also refused). When ``False``
            (default) either ``enabled`` or ``strict`` satisfies it.
        mcp: Optional FastMCP server to additionally verify the
            :class:`ReadOnlyEnforcementMiddleware` backstop is installed on.
        env: Environment mapping to read the toggle from (defaults to
            :data:`os.environ`); injectable for tests.
    """
    from mcp_common.dual_mode import ENFORCE_READONLY_ENV_VAR, READONLY_REFUSAL_MESSAGE

    resolved_env: Mapping[str, str] = os.environ if env is None else env
    mode_name, raw_value, is_off, is_strict = _enforce_mode_name(resolved_env)

    violations: list[str] = []
    if is_off:
        violations.append(
            f"{ENFORCE_READONLY_ENV_VAR} is unset/off (resolved mode={mode_name!r}); "
            f"write-capable evals must run with it enabled. Set "
            f"{ENFORCE_READONLY_ENV_VAR}=1 (or 'strict') before launching the matrix."
        )
    elif require_strict and not is_strict:
        violations.append(
            f"{ENFORCE_READONLY_ENV_VAR}={raw_value!r} resolves to {mode_name!r}, but "
            f"require_strict=True needs the 'strict' mode (also refuse unclassified tools)."
        )

    middleware_installed: bool | None = None
    if mcp is not None:
        middleware_installed = _middleware_installed(mcp)
        if not is_off and not middleware_installed:
            violations.append(
                "ReadOnlyEnforcementMiddleware is NOT installed on the FastMCP server, so "
                f"{ENFORCE_READONLY_ENV_VAR} is a no-op for it. Call "
                "mcp_common.dual_mode.install_read_only_enforcement(mcp) at startup."
            )

    return {
        "ok": not violations,
        "enforced_readonly": not is_off,
        "enforce_mode": mode_name,
        "require_strict": require_strict,
        "env_var": ENFORCE_READONLY_ENV_VAR,
        "env_value": raw_value,
        "refusal_message": READONLY_REFUSAL_MESSAGE,
        "middleware_installed": middleware_installed,
        "violations": violations,
        "checked_at": datetime.now(UTC).isoformat(),
    }


def assert_read_only_eval_mode(
    *,
    require_strict: bool = False,
    mcp: FastMCP | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Fail-fast that the enforced read-only eval-mode contract is satisfied.

    Call this **once, before any model runs** in a write-capable eval matrix.
    It resolves ``MCP_ENFORCE_READONLY`` through
    :func:`mcp_common.dual_mode.current_enforce_mode` and raises
    :class:`WriteSafetyError` (aborting the matrix) when the toggle is unset/off
    — or, with ``require_strict=True``, when it is not the ``strict`` variant.
    When ``mcp`` is given it additionally requires the server-side
    :class:`ReadOnlyEnforcementMiddleware` backstop to be installed.

    On success it returns the same facts dict as
    :func:`write_safety_preflight_facts`, intended to be stored under
    ``summary.json["write_safety_preflight"]`` for auditability::

        summary["write_safety_preflight"] = assert_read_only_eval_mode(mcp=server)

    Args:
        require_strict: Require the ``strict`` mode (block unclassified tools
            too), not just ``enabled``.
        mcp: Optional FastMCP server whose enforcement middleware must be
            installed.
        env: Environment mapping (defaults to :data:`os.environ`); injectable
            for tests.

    Returns:
        The write-safety preflight facts block.

    Raises:
        WriteSafetyError: If the enforced read-only contract is not satisfied.
    """
    facts = write_safety_preflight_facts(require_strict=require_strict, mcp=mcp, env=env)
    if not facts["ok"]:
        raise WriteSafetyError(
            "Write-safety preflight failed for a write-capable eval:\n  - "
            + "\n  - ".join(facts["violations"])
        )
    return facts
