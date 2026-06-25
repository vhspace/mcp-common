"""``redfish-cli --version`` flag.

redfish-cli already owns a root ``@app.callback()`` for the global
``--user``/``--password`` options, so the framework ``build_cli_from_mcp(
package_name=...)`` flag would be clobbered (Typer allows one root callback).
The eager ``--version`` flag is therefore merged into that callback and prints
the installed redfish-mcp package version (``mcp_common.get_version``) before any
subcommand runs — no BMC credentials required.
"""

from __future__ import annotations

from typer.testing import CliRunner

from redfish_mcp.cli import app

runner = CliRunner()


def test_version_flag_prints_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip()


def test_version_flag_matches_package_version() -> None:
    from mcp_common import get_version

    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == get_version("redfish-mcp")


def test_version_flag_needs_no_credentials(monkeypatch) -> None:
    """``--version`` short-circuits before the credential-resolving command body."""
    monkeypatch.delenv("REDFISH_USER", raising=False)
    monkeypatch.delenv("REDFISH_PASSWORD", raising=False)
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0, result.output
