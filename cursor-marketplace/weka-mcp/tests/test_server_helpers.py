"""Unit tests for CLI parsing and helper utilities."""

from __future__ import annotations

import sys

import pytest

from weka_mcp.server import (
    _ensure_json_serializable,
    _get_client,
    _safe_result,
    _select_fields,
    parse_cli_args,
)

# ── parse_cli_args ──────────────────────────────────────────────


def test_parse_cli_args_builds_overlay(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "weka-mcp",
            "--weka-host",
            "https://weka01:14000",
            "--weka-password",
            "secret",
            "--transport",
            "http",
            "--port",
            "9001",
            "--no-verify-ssl",
        ],
    )

    overlay = parse_cli_args()
    assert overlay["weka_host"] == "https://weka01:14000"
    assert overlay["weka_password"] == "secret"
    assert overlay["transport"] == "http"
    assert overlay["port"] == 9001
    assert overlay["verify_ssl"] is False


def test_parse_cli_args_omits_none_values(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["weka-mcp", "--weka-host", "https://h:14000"])
    overlay = parse_cli_args()
    assert "weka_password" not in overlay
    assert "transport" not in overlay


# ── _select_fields ──────────────────────────────────────────────


def test_select_fields_handles_dict_and_list() -> None:
    obj = {"a": 1, "b": 2, "c": 3}
    assert _select_fields(obj, ["a", "c"]) == {"a": 1, "c": 3}

    arr = [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
    assert _select_fields(arr, ["b"]) == [{"b": 2}, {"b": 4}]


def test_select_fields_returns_original_when_fields_is_none() -> None:
    obj = {"a": 1, "b": 2}
    assert _select_fields(obj, None) is obj


def test_select_fields_returns_non_dict_as_is() -> None:
    assert _select_fields("hello", ["a"]) == "hello"
    assert _select_fields(42, ["a"]) == 42


# ── _ensure_json_serializable ───────────────────────────────────


def test_ensure_json_serializable_handles_nested_types() -> None:
    payload = {"items": (1, 2, 3), "flag": True}
    out = _ensure_json_serializable(payload)
    assert out == {"items": [1, 2, 3], "flag": True}


def test_ensure_json_serializable_converts_unknown_types() -> None:
    from datetime import datetime

    dt = datetime(2025, 1, 15, 12, 0, 0)
    result = _ensure_json_serializable({"ts": dt, "count": 5})
    assert result["count"] == 5
    assert isinstance(result["ts"], str)


def test_ensure_json_serializable_primitives() -> None:
    assert _ensure_json_serializable(None) is None
    assert _ensure_json_serializable("hello") == "hello"
    assert _ensure_json_serializable(42) == 42
    assert _ensure_json_serializable(3.14) == 3.14
    assert _ensure_json_serializable(True) is True


# ── _safe_result ────────────────────────────────────────────────


def test_safe_result_applies_serialization_and_projection() -> None:
    resp = {"a": 1, "b": (2, 3), "c": "x"}
    out = _safe_result(resp, fields=["a", "b"])
    assert out == {"a": 1, "b": [2, 3]}


def test_safe_result_no_fields_returns_full() -> None:
    resp = {"a": 1, "b": 2}
    assert _safe_result(resp) == {"a": 1, "b": 2}


# ── _get_client ─────────────────────────────────────────────────


def test_get_client_raises_for_unknown_site() -> None:
    from fastmcp.exceptions import ToolError

    import weka_mcp.server as srv

    original_key = srv.sites._active_key
    srv.sites._active_key = None
    try:
        with pytest.raises(ToolError, match="No active"):
            _get_client()
    finally:
        srv.sites._active_key = original_key
