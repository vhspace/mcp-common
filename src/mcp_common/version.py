"""Runtime version introspection for MCP packages."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version


def get_version(package_name: str) -> str:
    """Get the installed version of a package.

    Args:
        package_name: The pip/uv package name.

    Returns:
        Version string, or "0.0.0-dev" if not installed as a package.
    """
    try:
        return version(package_name)
    except PackageNotFoundError:
        return "0.0.0-dev"
