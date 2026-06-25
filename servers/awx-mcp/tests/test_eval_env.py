"""Unit tests for awx-mcp eval preflight helpers."""

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
