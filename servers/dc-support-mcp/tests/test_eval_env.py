"""Unit tests for dc-support-mcp eval preflight helpers."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_EVALS = Path(__file__).resolve().parent.parent / "evals"
if str(_EVALS) not in sys.path:
    sys.path.insert(0, str(_EVALS))

pytestmark = pytest.mark.unit


def test_preflight_write_safety_requires_enforce_toggle(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("inspect_ai")
    monkeypatch.delenv("MCP_ENFORCE_READONLY", raising=False)
    from _env import preflight_write_safety
    from mcp_common.testing.eval import WriteSafetyError

    with pytest.raises(WriteSafetyError):
        preflight_write_safety()


def test_preflight_write_safety_ok_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("inspect_ai")
    monkeypatch.setenv("MCP_ENFORCE_READONLY", "1")
    from _env import preflight_write_safety

    facts = preflight_write_safety()
    assert facts["ok"] is True
    assert facts["enforced_readonly"] is True


def test_preflight_credentials_aborts_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "ORI_PORTAL_USERNAME",
        "ORI_PORTAL_PASSWORD",
        "HYPERTEC_PORTAL_USERNAME",
        "HYPERTEC_PORTAL_PASSWORD",
        "IREN_FRESHDESK_API_KEY",
        "IREN_PORTAL_USERNAME",
        "IREN_PORTAL_PASSWORD",
    ):
        monkeypatch.delenv(var, raising=False)
    from _env import DcSupportPreflightError, preflight_credentials

    with pytest.raises(DcSupportPreflightError):
        preflight_credentials()


def test_preflight_credentials_ok_when_ori_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORI_PORTAL_USERNAME", "you@together.ai")
    monkeypatch.setenv("ORI_PORTAL_PASSWORD", "secret")
    from _env import preflight_credentials

    facts = preflight_credentials()
    assert facts["status"] == "ok"
    assert "ori" in facts["configured_vendors"]
