"""Tests for preset filter resolution."""

from __future__ import annotations

import pytest

from mcp_network.presets import PRESETS, resolve_preset


def test_resolve_preset_routing() -> None:
    result = resolve_preset("routing")
    assert result == {"unit": "frr.service"}


def test_resolve_preset_all_errors() -> None:
    result = resolve_preset("all-errors")
    assert result == {"priority": "err"}


def test_resolve_preset_kernel() -> None:
    result = resolve_preset("kernel")
    assert result == {"kernel": True}


def test_resolve_preset_platform() -> None:
    result = resolve_preset("platform")
    assert result == {"identifier": "smond"}


def test_resolve_preset_none_returns_empty() -> None:
    result = resolve_preset(None)
    assert result == {}


def test_resolve_preset_unknown_raises() -> None:
    with pytest.raises(ValueError, match="unknown preset"):
        resolve_preset("nonexistent")


def test_explicit_overrides_preset() -> None:
    result = resolve_preset("routing", priority="err")
    assert result == {"unit": "frr.service", "priority": "err"}


def test_explicit_unit_overrides_preset_unit() -> None:
    result = resolve_preset("routing", unit="switchd.service")
    assert result["unit"] == "switchd.service"


def test_explicit_grep_adds_to_preset() -> None:
    result = resolve_preset("all-errors", grep="link.*down")
    assert result == {"priority": "err", "grep": "link.*down"}


def test_explicit_flags_without_preset() -> None:
    result = resolve_preset(None, boot=True, kernel=True, priority="warning")
    assert result == {"boot": True, "kernel": True, "priority": "warning"}


def test_all_presets_have_description() -> None:
    for name, preset in PRESETS.items():
        assert "description" in preset, f"preset {name!r} missing description"


def test_all_presets_resolve_cleanly() -> None:
    for name in PRESETS:
        result = resolve_preset(name)
        assert isinstance(result, dict)
        assert "description" not in result
