"""Tests for KVM CLI subcommand stubs."""

from __future__ import annotations

from typer.testing import CliRunner

from redfish_mcp.cli import app

runner = CliRunner(mix_stderr=False)


class TestKvmCliStubs:
    def test_kvm_help(self):
        r = runner.invoke(app, ["kvm", "--help"])
        assert r.exit_code == 0
        assert "screen" in r.stdout
        assert "send" in r.stdout
        assert "type-and-read" in r.stdout
        assert "close" in r.stdout
        assert "status" in r.stdout

    def test_kvm_screen_not_implemented(self):
        r = runner.invoke(app, ["kvm", "screen", "10.0.0.1"])
        assert r.exit_code != 0
        assert "not_implemented" in (r.stdout + r.stderr)

    def test_kvm_status_not_implemented(self):
        r = runner.invoke(app, ["kvm", "status"])
        assert r.exit_code != 0
        assert "not_implemented" in (r.stdout + r.stderr)
