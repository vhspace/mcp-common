"""Tests for the write-safety preflight (assert_read_only_eval_mode, #156)."""

from __future__ import annotations

import os

import pytest

from mcp_common.dual_mode import ENFORCE_READONLY_ENV_VAR, READONLY_REFUSAL_MESSAGE
from mcp_common.testing.eval.write_safety import (
    WriteSafetyError,
    assert_read_only_eval_mode,
    write_safety_preflight_facts,
)


@pytest.mark.eval
class TestWriteSafetyPreflightToggle:
    def test_unset_toggle_is_a_violation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(ENFORCE_READONLY_ENV_VAR, raising=False)
        facts = write_safety_preflight_facts()
        assert facts["ok"] is False
        assert facts["enforced_readonly"] is False
        assert facts["enforce_mode"] == "off"
        assert facts["violations"]

    def test_unset_toggle_raises_and_aborts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # the whole point of the preflight: abort before any model runs
        monkeypatch.delenv(ENFORCE_READONLY_ENV_VAR, raising=False)
        with pytest.raises(WriteSafetyError, match=ENFORCE_READONLY_ENV_VAR):
            assert_read_only_eval_mode()

    def test_enabled_satisfies_contract(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENFORCE_READONLY_ENV_VAR, "1")
        facts = assert_read_only_eval_mode()
        assert facts["ok"] is True
        assert facts["enforced_readonly"] is True
        assert facts["enforce_mode"] == "enabled"
        assert facts["env_value"] == "1"
        assert facts["env_var"] == ENFORCE_READONLY_ENV_VAR
        # facts are the audit block destined for summary.json["write_safety_preflight"]
        assert facts["refusal_message"] == READONLY_REFUSAL_MESSAGE
        assert "checked_at" in facts

    def test_strict_satisfies_contract(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENFORCE_READONLY_ENV_VAR, "strict")
        facts = assert_read_only_eval_mode()
        assert facts["enforce_mode"] == "strict"
        assert facts["ok"] is True

    def test_require_strict_rejects_plain_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENFORCE_READONLY_ENV_VAR, "1")
        with pytest.raises(WriteSafetyError, match="strict"):
            assert_read_only_eval_mode(require_strict=True)
        facts = write_safety_preflight_facts(require_strict=True)
        assert facts["ok"] is False
        assert facts["require_strict"] is True

    def test_require_strict_accepts_strict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENFORCE_READONLY_ENV_VAR, "strict")
        facts = assert_read_only_eval_mode(require_strict=True)
        assert facts["ok"] is True

    def test_env_injection_overrides_and_leaves_os_environ_clean(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(ENFORCE_READONLY_ENV_VAR, raising=False)
        facts = assert_read_only_eval_mode(env={ENFORCE_READONLY_ENV_VAR: "1"})
        assert facts["enforced_readonly"] is True
        # the injected mapping must not leak into the real environment
        assert ENFORCE_READONLY_ENV_VAR not in os.environ


@pytest.mark.eval
class TestWriteSafetyMiddlewareCheck:
    def test_installed_middleware_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from fastmcp import FastMCP

        from mcp_common.dual_mode import install_read_only_enforcement

        monkeypatch.setenv(ENFORCE_READONLY_ENV_VAR, "1")
        mcp = FastMCP("write-safety-installed")
        install_read_only_enforcement(mcp)
        facts = assert_read_only_eval_mode(mcp=mcp)
        assert facts["middleware_installed"] is True
        assert facts["ok"] is True

    def test_missing_middleware_is_a_violation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from fastmcp import FastMCP

        monkeypatch.setenv(ENFORCE_READONLY_ENV_VAR, "1")
        mcp = FastMCP("write-safety-missing")
        facts = write_safety_preflight_facts(mcp=mcp)
        assert facts["middleware_installed"] is False
        assert facts["ok"] is False
        with pytest.raises(WriteSafetyError, match="ReadOnlyEnforcementMiddleware"):
            assert_read_only_eval_mode(mcp=mcp)

    def test_middleware_not_flagged_when_toggle_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from fastmcp import FastMCP

        monkeypatch.delenv(ENFORCE_READONLY_ENV_VAR, raising=False)
        mcp = FastMCP("write-safety-off")
        facts = write_safety_preflight_facts(mcp=mcp)
        # the only violation is the OFF toggle, not the (irrelevant) middleware gap
        assert facts["ok"] is False
        assert all("ReadOnlyEnforcementMiddleware" not in v for v in facts["violations"])
