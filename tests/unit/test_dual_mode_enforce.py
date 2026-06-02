"""Tests for enforced read-only ("eval") mode — mcp-common#148.

Covers, in one module (so the CI quick suite runs all of it — both gates use
an in-memory client/runner with no network):

* the pure classification/decision logic (:mod:`mcp_common.dual_mode._enforce`);
* the **CLI** dispatch gate baked into the synthesized Typer commands; and
* the **MCP** dispatch gate (the auto-installed FastMCP middleware), driven via
  the in-memory ``Client`` exactly as a calling agent would hit it — including
  the netbox-mcp case where a plain ``@mcp.tool(tags={"write"})`` is gated
  with no per-server change.
"""

from __future__ import annotations

import inspect
import json
import logging

import anyio
import pytest
import typer
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from typer.testing import CliRunner

from mcp_common.dual_mode import (
    build_cli_from_mcp,
    dual_mode_tool,
    enforce_read_only_cli,
    install_read_only_enforcement,
    verify_enforcement_installed,
)
from mcp_common.dual_mode._enforce import (
    ENFORCE_READONLY_ENV_VAR,
    READONLY_REFUSAL_MESSAGE,
    EnforceMode,
    MutationClass,
    ReadOnlyEnforcementMiddleware,
    _enforcement_installed,
    _warned_unrecognized_values,
    classify_mutation,
    current_enforce_mode,
    is_blocked,
)
from mcp_common.dual_mode._registry import _clear


class TestCurrentEnforceMode:
    """``MCP_ENFORCE_READONLY`` parsing — read at dispatch time, default OFF."""

    def test_unset_is_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(ENFORCE_READONLY_ENV_VAR, raising=False)
        assert current_enforce_mode() is EnforceMode.OFF

    @pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "none", "disabled"])
    def test_off_values(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv(ENFORCE_READONLY_ENV_VAR, value)
        assert current_enforce_mode() is EnforceMode.OFF

    @pytest.mark.parametrize("value", ["1", "true", "yes", "on", "enabled", "anything"])
    def test_enabled_values(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv(ENFORCE_READONLY_ENV_VAR, value)
        assert current_enforce_mode() is EnforceMode.ENABLED

    @pytest.mark.parametrize("value", ["strict", "STRICT", "  Strict  "])
    def test_strict_values(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv(ENFORCE_READONLY_ENV_VAR, value)
        assert current_enforce_mode() is EnforceMode.STRICT

    def test_case_and_whitespace_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENFORCE_READONLY_ENV_VAR, "  TRUE ")
        assert current_enforce_mode() is EnforceMode.ENABLED

    def test_unrecognized_value_fails_safe_to_enabled_and_warns_once(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        # A typo (e.g. ``stict`` for ``strict``) must NOT silently disable the
        # guard — it fails safe to ENABLED — but it IS warned about (once) so the
        # silent degradation from the intended ``strict`` is observable.
        _warned_unrecognized_values.discard("stict")
        monkeypatch.setenv(ENFORCE_READONLY_ENV_VAR, "stict")
        with caplog.at_level(logging.WARNING, logger="mcp_common.dual_mode._enforce"):
            assert current_enforce_mode() is EnforceMode.ENABLED
            assert sum("Unrecognized" in r.message for r in caplog.records) == 1
            # De-duplicated: a hot dispatch path must not spam the same warning.
            assert current_enforce_mode() is EnforceMode.ENABLED
            assert sum("Unrecognized" in r.message for r in caplog.records) == 1
        assert ENFORCE_READONLY_ENV_VAR in caplog.text

    @pytest.mark.parametrize("value", ["1", "enabled", "strict", "off", "0"])
    def test_recognized_values_do_not_warn(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, value: str
    ) -> None:
        monkeypatch.setenv(ENFORCE_READONLY_ENV_VAR, value)
        with caplog.at_level(logging.WARNING, logger="mcp_common.dual_mode._enforce"):
            current_enforce_mode()
        assert "Unrecognized" not in caplog.text


class TestClassifyMutation:
    """Classification from the explicit ``read_only`` flag + ``{"write"}`` tag."""

    def test_read_only_true_is_read_only(self) -> None:
        assert classify_mutation(True, None) is MutationClass.READ_ONLY

    def test_read_only_true_wins_over_write_tag(self) -> None:
        # An explicit read_only=True overrides a (contradictory) write tag.
        assert classify_mutation(True, {"write"}) is MutationClass.READ_ONLY

    def test_read_only_false_is_mutating(self) -> None:
        assert classify_mutation(False, None) is MutationClass.MUTATING

    def test_write_tag_is_mutating(self) -> None:
        assert classify_mutation(None, {"write", "dcim"}) is MutationClass.MUTATING

    def test_non_write_tags_are_unclassified(self) -> None:
        assert classify_mutation(None, {"query", "dcim"}) is MutationClass.UNCLASSIFIED

    def test_no_tags_is_unclassified(self) -> None:
        assert classify_mutation(None, None) is MutationClass.UNCLASSIFIED
        assert classify_mutation(None, set()) is MutationClass.UNCLASSIFIED


class TestIsBlocked:
    """Decision matrix across modes x classes."""

    @pytest.mark.parametrize("mutation", list(MutationClass))
    def test_off_never_blocks(self, mutation: MutationClass) -> None:
        assert is_blocked(EnforceMode.OFF, mutation) is False

    def test_enabled_blocks_only_mutating(self) -> None:
        assert is_blocked(EnforceMode.ENABLED, MutationClass.MUTATING) is True
        assert is_blocked(EnforceMode.ENABLED, MutationClass.UNCLASSIFIED) is False
        assert is_blocked(EnforceMode.ENABLED, MutationClass.READ_ONLY) is False

    def test_strict_blocks_everything_but_read_only(self) -> None:
        assert is_blocked(EnforceMode.STRICT, MutationClass.MUTATING) is True
        assert is_blocked(EnforceMode.STRICT, MutationClass.UNCLASSIFIED) is True
        assert is_blocked(EnforceMode.STRICT, MutationClass.READ_ONLY) is False


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner(mix_stderr=False)


@pytest.fixture
def calls() -> list[str]:
    return []


def _mcp_with_cli_tools(calls: list[str]) -> FastMCP:
    """Build a server whose tools record execution into ``calls``.

    Covers every classification: a ``{"write"}``-tagged tool, a
    ``read_only=False`` tool, an explicit ``read_only=True`` tool, and an
    unclassified tool (no tag, no flag).
    """
    instance = FastMCP("netbox")

    @dual_mode_tool(instance, tags={"write", "dcim"})
    def update_device(device: str) -> dict:
        """Mutating via the write tag."""
        calls.append("update_device")
        return {"device": device, "updated": True}

    @dual_mode_tool(instance, read_only=False)
    def delete_thing(name: str) -> dict:
        """Mutating via read_only=False."""
        calls.append("delete_thing")
        return {"deleted": name}

    @dual_mode_tool(instance, read_only=True, tags={"query"})
    def lookup_device(hostname: str) -> dict:
        """Explicitly read-only."""
        calls.append("lookup_device")
        return {"hostname": hostname}

    @dual_mode_tool(instance)
    def ping() -> dict:
        """Unclassified: no write tag, no read_only flag."""
        calls.append("ping")
        return {"ok": True}

    return instance


class TestCliEnforcementBlocks:
    """Mutating CLI commands are refused: stderr one-liner, non-zero exit, no run."""

    def test_write_tagged_command_refused(
        self, runner: CliRunner, calls: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENFORCE_READONLY_ENV_VAR, "1")
        mcp = _mcp_with_cli_tools(calls)
        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/netbox-mcp")
        try:
            result = runner.invoke(app, ["update-device", "--device", "sw01", "--json"])
        finally:
            _clear(mcp)

        assert result.exit_code != 0
        assert result.stderr == f"{READONLY_REFUSAL_MESSAGE}\n"
        assert result.stdout == ""
        assert calls == []  # fn never executed

    def test_read_only_false_command_refused(
        self, runner: CliRunner, calls: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENFORCE_READONLY_ENV_VAR, "1")
        mcp = _mcp_with_cli_tools(calls)
        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/netbox-mcp")
        try:
            result = runner.invoke(app, ["delete-thing", "--name", "x", "--json"])
        finally:
            _clear(mcp)

        assert result.exit_code != 0
        assert result.stderr.strip() == READONLY_REFUSAL_MESSAGE
        assert calls == []

    def test_unclassified_command_blocked_in_strict_only(
        self, runner: CliRunner, calls: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mcp = _mcp_with_cli_tools(calls)
        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/netbox-mcp")
        try:
            monkeypatch.setenv(ENFORCE_READONLY_ENV_VAR, "1")
            allowed = runner.invoke(app, ["ping", "--json"])
            assert allowed.exit_code == 0
            assert json.loads(allowed.stdout) == {"ok": True}
            assert calls == ["ping"]

            calls.clear()
            monkeypatch.setenv(ENFORCE_READONLY_ENV_VAR, "strict")
            blocked = runner.invoke(app, ["ping", "--json"])
            assert blocked.exit_code != 0
            assert blocked.stderr.strip() == READONLY_REFUSAL_MESSAGE
            assert calls == []
        finally:
            _clear(mcp)


class TestCliEnforcementAllows:
    """Read-only commands run normally; OFF mode is byte-identical to today."""

    def test_read_only_command_runs_when_enabled(
        self, runner: CliRunner, calls: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENFORCE_READONLY_ENV_VAR, "1")
        mcp = _mcp_with_cli_tools(calls)
        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/netbox-mcp")
        try:
            result = runner.invoke(app, ["lookup-device", "--hostname", "sw01", "--json"])
        finally:
            _clear(mcp)

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        assert json.loads(result.stdout) == {"hostname": "sw01"}
        assert calls == ["lookup_device"]

    def test_read_only_true_command_runs_in_strict(
        self, runner: CliRunner, calls: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENFORCE_READONLY_ENV_VAR, "strict")
        mcp = _mcp_with_cli_tools(calls)
        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/netbox-mcp")
        try:
            result = runner.invoke(app, ["lookup-device", "--hostname", "sw01", "--json"])
        finally:
            _clear(mcp)

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        assert json.loads(result.stdout) == {"hostname": "sw01"}
        assert calls == ["lookup_device"]

    def test_off_runs_write_command_byte_identical(
        self, runner: CliRunner, calls: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(ENFORCE_READONLY_ENV_VAR, raising=False)
        mcp = _mcp_with_cli_tools(calls)
        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/netbox-mcp")
        try:
            result = runner.invoke(app, ["update-device", "--device", "sw01", "--json"])
        finally:
            _clear(mcp)

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        assert json.loads(result.stdout) == {"device": "sw01", "updated": True}
        assert calls == ["update_device"]


# ---------------------------------------------------------------------------
# MCP dispatch gate (auto-installed FastMCP middleware), via in-memory Client.
# ---------------------------------------------------------------------------


def _call_raises(server: FastMCP, name: str, args: dict) -> str:
    """Call a tool expecting the default ``raise_on_error=True`` to raise.

    Returns the raised ``ToolError`` message so the caller can assert it is
    exactly the refusal string (this is what the calling agent receives).
    """

    async def _run() -> str:
        async with Client(server) as client:
            try:
                await client.call_tool(name, args)
            except ToolError as exc:
                return str(exc)
        raise AssertionError(f"{name} did not raise ToolError")

    return anyio.run(_run)


def _call_no_raise(server: FastMCP, name: str, args: dict) -> tuple[bool, str]:
    """Call a tool with ``raise_on_error=False``; return ``(is_error, text)``."""

    async def _run() -> tuple[bool, str]:
        async with Client(server) as client:
            result = await client.call_tool(name, args, raise_on_error=False)
            text = result.content[0].text if result.content else ""
            return result.is_error, text

    return anyio.run(_run)


def _call_ok(server: FastMCP, name: str, args: dict) -> dict:
    """Call a tool expected to succeed; return the structured payload."""

    async def _run() -> dict:
        async with Client(server) as client:
            result = await client.call_tool(name, args)
            payload = result.structured_content or {}
            if isinstance(payload, dict) and set(payload.keys()) == {"result"}:
                return payload["result"]
            return payload

    return anyio.run(_run)


class TestMcpEnforcementBlocks:
    """Mutating MCP tool calls are refused verbatim and never execute."""

    def test_write_tagged_tool_raises_exact_message(
        self, calls: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENFORCE_READONLY_ENV_VAR, "1")
        mcp = _mcp_with_cli_tools(calls)
        try:
            message = _call_raises(mcp, "update_device", {"device": "sw01"})
        finally:
            _clear(mcp)
        assert message == READONLY_REFUSAL_MESSAGE
        assert calls == []  # fn never executed

    def test_write_tagged_tool_surfaces_as_error_result(
        self, calls: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENFORCE_READONLY_ENV_VAR, "1")
        mcp = _mcp_with_cli_tools(calls)
        try:
            is_error, text = _call_no_raise(mcp, "update_device", {"device": "sw01"})
        finally:
            _clear(mcp)
        assert is_error is True
        assert text == READONLY_REFUSAL_MESSAGE
        assert calls == []

    def test_read_only_false_tool_refused(
        self, calls: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENFORCE_READONLY_ENV_VAR, "1")
        mcp = _mcp_with_cli_tools(calls)
        try:
            message = _call_raises(mcp, "delete_thing", {"name": "x"})
        finally:
            _clear(mcp)
        assert message == READONLY_REFUSAL_MESSAGE
        assert calls == []

    def test_unclassified_blocked_in_strict_only(
        self, calls: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mcp = _mcp_with_cli_tools(calls)
        try:
            monkeypatch.setenv(ENFORCE_READONLY_ENV_VAR, "1")
            assert _call_ok(mcp, "ping", {}) == {"ok": True}
            assert calls == ["ping"]

            calls.clear()
            monkeypatch.setenv(ENFORCE_READONLY_ENV_VAR, "strict")
            message = _call_raises(mcp, "ping", {})
            assert message == READONLY_REFUSAL_MESSAGE
            assert calls == []
        finally:
            _clear(mcp)


class TestMcpEnforcementAllows:
    """Read-only tools run normally; OFF mode is byte-identical to today."""

    def test_read_only_tool_runs_when_enabled(
        self, calls: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENFORCE_READONLY_ENV_VAR, "1")
        mcp = _mcp_with_cli_tools(calls)
        try:
            assert _call_ok(mcp, "lookup_device", {"hostname": "sw01"}) == {"hostname": "sw01"}
        finally:
            _clear(mcp)
        assert calls == ["lookup_device"]

    def test_read_only_true_runs_in_strict(
        self, calls: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENFORCE_READONLY_ENV_VAR, "strict")
        mcp = _mcp_with_cli_tools(calls)
        try:
            assert _call_ok(mcp, "lookup_device", {"hostname": "sw01"}) == {"hostname": "sw01"}
        finally:
            _clear(mcp)
        assert calls == ["lookup_device"]

    def test_off_runs_write_tool_byte_identical(
        self, calls: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(ENFORCE_READONLY_ENV_VAR, raising=False)
        mcp = _mcp_with_cli_tools(calls)
        try:
            result = _call_ok(mcp, "update_device", {"device": "sw01"})
        finally:
            _clear(mcp)
        assert result == {"device": "sw01", "updated": True}
        assert calls == ["update_device"]


class TestPlainMcpToolAutoBlocked:
    """A plain ``@mcp.tool`` (never ``@dual_mode_tool``) is still gated.

    This is the netbox-mcp case: ``netbox_update_device`` is a plain
    ``@mcp.tool(tags={"write", "dcim"})``. Because the server ALSO has
    ``@dual_mode_tool`` reads, the enforcement middleware is auto-installed and
    intercepts the plain write tool — auto-blocked with NO netbox change.
    """

    @staticmethod
    def _netbox_like(calls: list[str]) -> FastMCP:
        instance = FastMCP("netbox")

        # A dual-mode read tool — its mere presence installs the middleware.
        @dual_mode_tool(instance, read_only=True, tags={"query", "dcim"})
        def netbox_lookup_device(hostname: str) -> dict:
            """Read-only lookup."""
            calls.append("netbox_lookup_device")
            return {"hostname": hostname}

        # Plain @mcp.tool write — mirrors netbox_update_device exactly.
        @instance.tool(tags={"write", "dcim"})
        def netbox_update_device(device: str, status: str) -> dict:
            calls.append("netbox_update_device")
            return {"device": device, "status": status}

        return instance

    def test_plain_write_tool_blocked_when_enabled(
        self, calls: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENFORCE_READONLY_ENV_VAR, "1")
        mcp = self._netbox_like(calls)
        try:
            message = _call_raises(
                mcp, "netbox_update_device", {"device": "sw01", "status": "active"}
            )
        finally:
            _clear(mcp)
        assert message == READONLY_REFUSAL_MESSAGE
        assert calls == []  # the plain @mcp.tool never executed

    def test_plain_read_tool_still_runs_when_enabled(
        self, calls: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENFORCE_READONLY_ENV_VAR, "1")
        mcp = self._netbox_like(calls)
        try:
            assert _call_ok(mcp, "netbox_lookup_device", {"hostname": "sw01"}) == {
                "hostname": "sw01"
            }
        finally:
            _clear(mcp)
        assert calls == ["netbox_lookup_device"]

    def test_off_runs_plain_write_tool(
        self, calls: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(ENFORCE_READONLY_ENV_VAR, raising=False)
        mcp = self._netbox_like(calls)
        try:
            result = _call_ok(mcp, "netbox_update_device", {"device": "sw01", "status": "active"})
        finally:
            _clear(mcp)
        assert result == {"device": "sw01", "status": "active"}
        assert calls == ["netbox_update_device"]


# ---------------------------------------------------------------------------
# Finding 1: public install helper + observability for plain-@mcp.tool servers.
# ---------------------------------------------------------------------------


def _plain_tool_server(calls: list[str]) -> FastMCP:
    """A server whose tools are registered ONLY via plain ``@mcp.tool``.

    Mirrors awx-mcp / dc-support-mcp: there is no ``@dual_mode_tool`` anywhere,
    so the enforcement middleware is NOT auto-installed — the toggle is a silent
    no-op until :func:`install_read_only_enforcement` is called.
    """
    instance = FastMCP("awx")

    @instance.tool(tags={"write"})
    def reboot_host(host: str) -> dict:
        calls.append("reboot_host")
        return {"rebooted": host}

    @instance.tool(tags={"query"})
    def host_status(host: str) -> dict:
        calls.append("host_status")
        return {"host": host}

    return instance


def _middleware_count(mcp: FastMCP) -> int:
    return sum(isinstance(m, ReadOnlyEnforcementMiddleware) for m in mcp.middleware)


class TestInstallReadOnlyEnforcement:
    """``install_read_only_enforcement`` is the one-call opt-in for plain servers."""

    def test_not_installed_until_called(self) -> None:
        mcp = _plain_tool_server([])
        # No @dual_mode_tool ⇒ nothing auto-installed the middleware.
        assert _enforcement_installed(mcp) is False

    def test_install_is_idempotent(self) -> None:
        mcp = _plain_tool_server([])
        install_read_only_enforcement(mcp)
        install_read_only_enforcement(mcp)
        assert _middleware_count(mcp) == 1
        assert _enforcement_installed(mcp) is True

    def test_plain_write_refused_only_after_install(
        self, calls: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENFORCE_READONLY_ENV_VAR, "1")
        mcp = _plain_tool_server(calls)

        # Before install: the toggle is a no-op for this server — write runs.
        assert _call_ok(mcp, "reboot_host", {"host": "h1"}) == {"rebooted": "h1"}
        assert calls == ["reboot_host"]

        # After install: the SAME server now refuses the write verbatim.
        calls.clear()
        install_read_only_enforcement(mcp)
        assert _call_raises(mcp, "reboot_host", {"host": "h1"}) == READONLY_REFUSAL_MESSAGE
        assert calls == []
        # Read-only tool still runs.
        assert _call_ok(mcp, "host_status", {"host": "h1"}) == {"host": "h1"}
        assert calls == ["host_status"]


class TestVerifyEnforcementInstalled:
    """``verify_enforcement_installed`` reports status and warns on the no-op gap."""

    def test_returns_true_and_silent_when_installed(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv(ENFORCE_READONLY_ENV_VAR, "1")
        mcp = _plain_tool_server([])
        install_read_only_enforcement(mcp)
        with caplog.at_level(logging.WARNING, logger="mcp_common.dual_mode._enforce"):
            assert verify_enforcement_installed(mcp) is True
        assert caplog.text == ""

    def test_warns_when_enabled_but_missing_on_server_with_tools(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv(ENFORCE_READONLY_ENV_VAR, "1")
        mcp = _plain_tool_server([])
        with caplog.at_level(logging.WARNING, logger="mcp_common.dual_mode._enforce"):
            assert verify_enforcement_installed(mcp) is False
        assert "NOT installed" in caplog.text
        assert "install_read_only_enforcement" in caplog.text
        assert ENFORCE_READONLY_ENV_VAR in caplog.text

    def test_no_warning_when_toggle_off(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.delenv(ENFORCE_READONLY_ENV_VAR, raising=False)
        mcp = _plain_tool_server([])
        with caplog.at_level(logging.WARNING, logger="mcp_common.dual_mode._enforce"):
            assert verify_enforcement_installed(mcp) is False
        assert caplog.text == ""

    def test_no_warning_on_empty_server(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv(ENFORCE_READONLY_ENV_VAR, "1")
        empty = FastMCP("empty")
        with caplog.at_level(logging.WARNING, logger="mcp_common.dual_mode._enforce"):
            assert verify_enforcement_installed(empty) is False
        assert caplog.text == ""


# ---------------------------------------------------------------------------
# Finding 2: app-level CLI gate for hand-written @app.command() write commands.
# ---------------------------------------------------------------------------


def _cli_app_with_handwritten_commands(ran: list[tuple]) -> typer.Typer:
    """A multi-command Typer app with hand-written (NOT synthesized) commands.

    Mirrors netbox-cli: hand-written ``@app.command()`` functions that never go
    through ``build_cli_from_mcp``. The write command opts into the gate with
    ``@enforce_read_only_cli``. (≥2 commands so Typer keeps the app a group
    rather than collapsing into a single flat command.)
    """
    app = typer.Typer()

    @app.command(name="lookup")
    def lookup(host: str = typer.Argument(...)) -> None:
        ran.append(("lookup", host))
        typer.echo(f"looked up {host}")

    @app.command(name="update-device")
    @enforce_read_only_cli(read_only=False)
    def update_device(
        device: str = typer.Argument(...),
        status: str = typer.Option(None, "--status"),
        confirm: bool = typer.Option(False, "--confirm"),
    ) -> None:
        ran.append(("update", device))
        typer.echo(f"updated {device}")

    @app.command(name="read-only-cmd")
    @enforce_read_only_cli(read_only=True)
    def read_only_cmd(host: str = typer.Argument(...)) -> None:
        ran.append(("read_only_cmd", host))
        typer.echo(f"ro {host}")

    @app.command(name="unclassified-cmd")
    @enforce_read_only_cli()
    def unclassified_cmd(host: str = typer.Argument(...)) -> None:
        ran.append(("unclassified_cmd", host))
        typer.echo(f"unc {host}")

    return app


class TestEnforceReadOnlyCliDecorator:
    """The decorator gates hand-written write commands identically to synthesized ones."""

    def test_write_command_refused_when_enabled(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENFORCE_READONLY_ENV_VAR, "1")
        ran: list[tuple] = []
        app = _cli_app_with_handwritten_commands(ran)
        result = runner.invoke(app, ["update-device", "dev1", "--status", "active", "--confirm"])
        assert result.exit_code != 0
        assert result.stderr == f"{READONLY_REFUSAL_MESSAGE}\n"
        assert result.stdout == ""
        assert ran == []  # guard fired BEFORE the body — no write attempted

    def test_write_command_runs_when_off_byte_identical(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(ENFORCE_READONLY_ENV_VAR, raising=False)
        ran: list[tuple] = []
        app = _cli_app_with_handwritten_commands(ran)
        result = runner.invoke(app, ["update-device", "dev1", "--status", "active", "--confirm"])
        assert result.exit_code == 0, f"stderr: {result.stderr}"
        assert result.stdout == "updated dev1\n"
        assert ran == [("update", "dev1")]

    def test_decorator_preserves_signature_for_typer(self) -> None:
        # The wrapped command must still expose the wrapped function's params to
        # Typer (not a bare ``*args, **kwargs``). Introspect the Click command
        # directly rather than parsing ``--help`` (whose Rich rendering wraps /
        # ANSI-styles text differently across terminal widths).
        from typer.main import get_command

        ran: list[tuple] = []
        app = _cli_app_with_handwritten_commands(ran)
        cmd = get_command(app).commands["update-device"]  # type: ignore[attr-defined]
        param_names = {p.name for p in cmd.params}
        assert {"device", "status", "confirm"} <= param_names
        opt_flags = {opt for p in cmd.params for opt in getattr(p, "opts", [])}
        assert "--status" in opt_flags
        assert "--confirm" in opt_flags

    def test_read_only_command_runs_in_strict(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENFORCE_READONLY_ENV_VAR, "strict")
        ran: list[tuple] = []
        app = _cli_app_with_handwritten_commands(ran)
        result = runner.invoke(app, ["read-only-cmd", "h1"])
        assert result.exit_code == 0, f"stderr: {result.stderr}"
        assert ran == [("read_only_cmd", "h1")]

    def test_unclassified_command_blocked_in_strict_only(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ran: list[tuple] = []
        app = _cli_app_with_handwritten_commands(ran)

        monkeypatch.setenv(ENFORCE_READONLY_ENV_VAR, "1")
        allowed = runner.invoke(app, ["unclassified-cmd", "h1"])
        assert allowed.exit_code == 0
        assert ran == [("unclassified_cmd", "h1")]

        ran.clear()
        monkeypatch.setenv(ENFORCE_READONLY_ENV_VAR, "strict")
        blocked = runner.invoke(app, ["unclassified-cmd", "h1"])
        assert blocked.exit_code != 0
        assert blocked.stderr.strip() == READONLY_REFUSAL_MESSAGE
        assert ran == []


class TestEnforceReadOnlyCliAsyncAware:
    """#112: ``enforce_read_only_cli`` preserves a wrapped tool's coroutine-ness.

    A naive sync wrapper around an async tool would look sync to
    :func:`inspect.iscoroutinefunction` (which does not follow ``__wrapped__``)
    yet return an un-awaited coroutine, tripping ``build_cli_from_mcp``'s
    sync/coroutine guard. The decorator branches on async-ness and returns an
    ``async def`` wrapper for coroutine tools — the reference recipe for any
    decorator stacked under ``@dual_mode_tool``.
    """

    def test_preserves_coroutine_function(self) -> None:
        @enforce_read_only_cli(read_only=True)
        async def tool(x: int) -> dict:
            return {"x": x}

        assert inspect.iscoroutinefunction(tool)

    def test_sync_tool_stays_sync(self) -> None:
        @enforce_read_only_cli(read_only=True)
        def tool(x: int) -> dict:
            return {"x": x}

        assert not inspect.iscoroutinefunction(tool)

    def test_async_runs_when_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(ENFORCE_READONLY_ENV_VAR, raising=False)

        @enforce_read_only_cli(read_only=False)
        async def tool(x: int) -> dict:
            return {"x": x}

        assert anyio.run(tool, 5) == {"x": 5}

    def test_async_refused_when_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENFORCE_READONLY_ENV_VAR, "1")
        ran: list[int] = []

        @enforce_read_only_cli(read_only=False)
        async def tool(x: int) -> dict:
            ran.append(x)
            return {"x": x}

        with pytest.raises(typer.Exit):
            anyio.run(tool, 5)
        assert ran == []  # guard fired before the body

    def test_async_guard_over_async_dual_mode_tool_runs_via_cli(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # End-to-end proof: stack the (async-aware) guard UNDER @dual_mode_tool on
        # an async tool and drive it through the synthesized CLI. If async-ness
        # were stripped, build_cli_from_mcp's guard would fire and exit non-zero
        # instead of returning the result.
        monkeypatch.delenv(ENFORCE_READONLY_ENV_VAR, raising=False)
        mcp = FastMCP("netbox")
        try:

            @dual_mode_tool(mcp, cli_name="lookup", read_only=True)
            @enforce_read_only_cli(read_only=True)
            async def lookup(host: str) -> dict:
                """Async read-only lookup behind a guard."""
                return {"host": host}

            app = build_cli_from_mcp(mcp, project_repo="togethercomputer/netbox-mcp")
            result = runner.invoke(app, ["lookup", "--host", "sw01", "--json"])
        finally:
            _clear(mcp)

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        assert json.loads(result.stdout) == {"host": "sw01"}
