"""Tests for version introspection."""

from mcp_common.version import get_version


class TestGetVersion:
    def test_known_package(self) -> None:
        version = get_version("pytest")
        assert version
        assert version != "0.0.0-dev"

    def test_unknown_package_returns_dev(self) -> None:
        version = get_version("nonexistent-package-xyz-12345")
        assert version == "0.0.0-dev"
