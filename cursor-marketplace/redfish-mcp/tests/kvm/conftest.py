"""Shared fixtures for kvm tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def tmp_runtime_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A fresh per-test runtime dir exposed via XDG_RUNTIME_DIR."""
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    yield runtime


@pytest.fixture
def mock_runtime_deps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock check_runtime_deps() to pass in unit tests without real binaries."""
    monkeypatch.setattr(
        "redfish_mcp.kvm.daemon.preflight.check_runtime_deps",
        lambda backend="java": None,
    )
