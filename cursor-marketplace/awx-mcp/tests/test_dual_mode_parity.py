"""MCP<->CLI parity tests for the ``@dual_mode_tool`` migration (issue #36).

awx-mcp historically shipped its MCP tools (``server.py``) and its ``awx-cli``
commands (``cli.py``) as two hand-written implementations of the same AWX
operations, which is exactly the drift risk issue #36 unifies away by deriving
the CLI from the MCP tools via ``mcp_common.dual_mode``.

This module locks the contract for the first converted group — the system/health
tools — so the migration can be proven behavior-preserving and any future drift
fails here first. Each converted tool is exercised twice with the **same** mocked
AWX client backing both surfaces:

1. **MCP surface** — through an in-memory ``fastmcp.Client`` (input-schema
   validation + structured output), via
   :func:`mcp_common.testing.dual_mode.call_tool_via_mcp`.
2. **CLI surface** — through the Typer command synthesized by
   :func:`mcp_common.dual_mode.build_cli_from_mcp`, via
   :func:`mcp_common.testing.dual_mode.call_tool_via_cli`.

The two structured (``--json``) outputs must be equal (:func:`assert_parity`
normalizes key ordering — the synthesized CLI emits sorted keys through the
shared ``echo_result``; the data is identical). Human-mode output and the exact
CLI flag surface for the previously hand-written ``ping`` / ``me`` commands are
asserted separately so the conversion stays byte-faithful where it must.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from typing import Any

import click
import httpx
import pytest
import typer
from mcp_common.testing.dual_mode import (
    assert_parity,
    call_tool_via_cli,
    call_tool_via_mcp,
    make_cli_runner,
)

import awx_mcp.server as server
from awx_mcp.awx_client import AwxRestClient
from awx_mcp.cli import app

runner = make_cli_runner()


# ---------------------------------------------------------------------------
# Fixed mock data (one source of truth for both surfaces)
# ---------------------------------------------------------------------------

PING_RESPONSE: dict[str, Any] = {
    "ha": False,
    "version": "24.6.1",
    "active_node": "awx-task-0",
    "install_uuid": "uuid-abc-123",
    # 6 instances → exercises the ``len(list) > 5`` → "[N items]" human collapse.
    "instances": [{"node": f"n{i}"} for i in range(1, 7)],
    "instance_groups": [{"name": "controlplane"}, {"name": "default"}],
}
ME_USER: dict[str, Any] = {
    "id": 7,
    "username": "svc-awx",
    "email": "svc@together.ai",
    "is_superuser": True,
}
INSTANCES_LIST: dict[str, Any] = {"count": 6, "results": [{"hostname": "awx-1"}]}
INSTANCE_GROUPS_LIST: dict[str, Any] = {
    "count": 2,
    "results": [{"name": "controlplane"}, {"name": "default"}],
}
CONFIG_RESPONSE: dict[str, Any] = {"version": "24.6.1", "time_zone": "UTC"}
SETTINGS_RESPONSE: dict[str, Any] = {"debug": False}
UNIFIED_JOBS_COUNT = 1500
RUNNING_JOBS_COUNT = 5
FAILED_JOBS_COUNT = 23


def _handler(request: httpx.Request) -> httpx.Response:
    """Serve every endpoint the five system/health tools touch with fixed data."""
    path = request.url.path.rstrip("/")
    if path.endswith("/ping"):
        return httpx.Response(200, json=PING_RESPONSE)
    if path.endswith("/me"):
        # AWX commonly returns a list-wrapper for /me/; the tool unwraps results[0].
        return httpx.Response(200, json={"count": 1, "results": [ME_USER]})
    if path.endswith("/instances"):
        return httpx.Response(200, json=INSTANCES_LIST)
    if path.endswith("/instance_groups"):
        return httpx.Response(200, json=INSTANCE_GROUPS_LIST)
    if path.endswith("/config"):
        return httpx.Response(200, json=CONFIG_RESPONSE)
    if path.endswith("/settings"):
        return httpx.Response(200, json=SETTINGS_RESPONSE)
    if path.endswith("/unified_jobs"):
        return httpx.Response(200, json={"count": UNIFIED_JOBS_COUNT})
    if path.endswith("/jobs"):
        status = request.url.params.get("status")
        count = RUNNING_JOBS_COUNT if status == "running" else FAILED_JOBS_COUNT
        return httpx.Response(200, json={"count": count})
    return httpx.Response(404, text="not found")


@pytest.fixture
def patched_awx(monkeypatch: pytest.MonkeyPatch) -> Iterator[AwxRestClient]:
    """Back both surfaces with one mocked AWX client.

    The dual-mode framework runs the same Python function for the MCP tool and
    the synthesized CLI command, so a single mock on ``server.awx`` covers both
    paths: the CLI's ``before_command`` hook (``_init_dual_mode_awx_client``) is a
    no-op once ``server.awx`` is set, so the synthesized commands use this mock
    via ``server._get_awx()``.
    """
    client = AwxRestClient(
        host="https://awx.example.com",
        token="test-token",
        http_transport=httpx.MockTransport(_handler),
    )
    monkeypatch.setattr(server, "awx", client)
    try:
        yield client
    finally:
        client.close()


def _mcp(tool_name: str, **arguments: Any) -> Any:
    """Invoke an MCP tool via the in-memory client (sync wrapper)."""
    return asyncio.run(call_tool_via_mcp(server.mcp, tool_name, **arguments))


def _cli_group() -> click.Group:
    """Return the synthesized Typer app as its underlying Click group."""
    group = typer.main.get_command(app)
    assert isinstance(group, click.Group)
    return group


def _command_option_flags(command_name: str) -> set[str]:
    """Option flag strings (e.g. ``--json``, ``-j``) a synthesized command exposes.

    Read straight off the Click command's parameters, so the assertion is
    independent of terminal width and ANSI styling. Scraping the Rich-rendered
    ``--help`` text is unreliable in CI's no-TTY shell: it is emitted with color
    escape codes and wrapped to ~80 columns, which splits the literal ``--json`` /
    ``--fields`` substrings and breaks ``in help_text`` checks.
    """
    flags: set[str] = set()
    for param in _cli_group().commands[command_name].params:
        if isinstance(param, click.Option):
            flags.update(param.opts)
            flags.update(param.secondary_opts)
    return flags


def _registered_command_names() -> set[str]:
    """Names of all registered top-level CLI commands (Click-introspected)."""
    return set(_cli_group().commands)


# ---------------------------------------------------------------------------
# awx_ping  ->  ping   (no params; pre-existing CLI command)
# ---------------------------------------------------------------------------


class TestPingParity:
    def test_mcp_and_cli_match(self, patched_awx: AwxRestClient) -> None:
        mcp_result = _mcp("awx_ping")
        cli_result = call_tool_via_cli(app, "ping", runner=runner)
        assert_parity(mcp_result, cli_result)
        assert mcp_result["version"] == "24.6.1"
        assert mcp_result["active_node"] == "awx-task-0"

    def test_cli_human_output_matches_legacy_format(
        self, patched_awx: AwxRestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Human mode prints the same ``  key: value`` lines the hand-written
        ``ping`` produced via ``cli._output`` (list > 5 collapses to ``[N items]``)."""
        # Force human mode: ``should_emit_json`` auto-emits JSON when stdout is
        # not a TTY (#19/#21), which is always the case under CliRunner. Pin it
        # to honor only the explicit flag so the human formatting is exercised
        # (mirrors mcp-common's own dual_mode builder tests).
        monkeypatch.setattr(
            "mcp_common.dual_mode.builder.should_emit_json",
            lambda explicit_json: explicit_json,
        )
        result = runner.invoke(app, ["ping"])
        assert result.exit_code == 0, result.stderr
        out = result.stdout
        assert "  ha: False" in out
        assert "  version: 24.6.1" in out
        assert "  active_node: awx-task-0" in out
        assert "  instances: [6 items]" in out

    def test_cli_flag_surface_preserved(self) -> None:
        """``ping`` exposes only ``--json``/``-j`` (plus ``--help``) — unchanged.

        Introspects the synthesized Click command's options instead of scraping
        ``--help`` so the check is terminal-width-/ANSI-independent (see
        :func:`_command_option_flags`).
        """
        flags = _command_option_flags("ping")
        assert "--json" in flags
        assert "-j" in flags
        assert "--fields" not in flags


# ---------------------------------------------------------------------------
# awx_get_me  ->  me   (list[str]|None fields; pre-existing CLI command)
# ---------------------------------------------------------------------------


class TestGetMeParity:
    def test_mcp_and_cli_match(self, patched_awx: AwxRestClient) -> None:
        mcp_result = _mcp("awx_get_me")
        cli_result = call_tool_via_cli(app, "me", runner=runner)
        assert_parity(mcp_result, cli_result)
        # Both surfaces unwrap the AWX list-wrapper to the user dict.
        assert mcp_result["username"] == "svc-awx"
        assert "results" not in mcp_result

    def test_cli_human_output_matches_legacy_format(
        self, patched_awx: AwxRestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Force human mode (see TestPingParity for rationale): under CliRunner
        # stdout is never a TTY, so ``should_emit_json`` would otherwise auto-
        # emit JSON (#19/#21). Pin it to honor only the explicit ``--json`` flag.
        monkeypatch.setattr(
            "mcp_common.dual_mode.builder.should_emit_json",
            lambda explicit_json: explicit_json,
        )
        result = runner.invoke(app, ["me"])
        assert result.exit_code == 0, result.stderr
        out = result.stdout
        assert "  id: 7" in out
        assert "  username: svc-awx" in out
        assert "  email: svc@together.ai" in out

    def test_cli_fields_projection_matches_mcp(self, patched_awx: AwxRestClient) -> None:
        """``me`` gains a repeatable ``--fields`` flag derived from the MCP tool's
        ``fields`` parameter; it must project identically on both surfaces."""
        mcp_result = _mcp("awx_get_me", fields=["id", "username"])
        cli_result = call_tool_via_cli(
            app, "me", ["--fields", "id", "--fields", "username"], runner=runner
        )
        assert_parity(mcp_result, cli_result)
        assert cli_result == {"id": 7, "username": "svc-awx"}

    def test_cli_fields_flag_present(self) -> None:
        """The synthesized ``me`` advertises ``--fields`` (proves it is derived
        from the MCP tool, not the old hand-written command which lacked it).

        Introspects the Click command's options (width-/ANSI-independent).
        """
        flags = _command_option_flags("me")
        assert "--fields" in flags
        assert "--json" in flags

    def test_me_plain_dict_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When AWX returns a bare user dict (no list-wrapper) both surfaces agree."""
        bare = {"id": 9, "username": "plain-user"}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.rstrip("/").endswith("/me"):
                return httpx.Response(200, json=bare)
            return httpx.Response(404, text="not found")

        client = AwxRestClient(
            host="https://awx.example.com",
            token="test-token",
            http_transport=httpx.MockTransport(handler),
        )
        monkeypatch.setattr(server, "awx", client)
        try:
            mcp_result = _mcp("awx_get_me")
            cli_result = call_tool_via_cli(app, "me", runner=runner)
            assert_parity(mcp_result, cli_result)
            assert cli_result == bare
        finally:
            client.close()


# ---------------------------------------------------------------------------
# awx_get_system_info  ->  get-system-info   (async + Context, newly synthesized)
# ---------------------------------------------------------------------------


class TestGetSystemInfoParity:
    def test_mcp_and_cli_match(self, patched_awx: AwxRestClient) -> None:
        mcp_result = _mcp("awx_get_system_info")
        cli_result = call_tool_via_cli(app, "get-system-info", runner=runner)
        assert_parity(mcp_result, cli_result)
        assert mcp_result["ping"]["version"] == "24.6.1"
        assert mcp_result["config"]["time_zone"] == "UTC"
        assert mcp_result["settings"]["debug"] is False


# ---------------------------------------------------------------------------
# awx_get_cluster_status  ->  get-cluster-status   (async + Context, newly synthesized)
# ---------------------------------------------------------------------------


class TestGetClusterStatusParity:
    def test_mcp_and_cli_match(self, patched_awx: AwxRestClient) -> None:
        mcp_result = _mcp("awx_get_cluster_status")
        cli_result = call_tool_via_cli(app, "get-cluster-status", runner=runner)
        assert_parity(mcp_result, cli_result)
        assert mcp_result["instances"]["count"] == 6
        assert mcp_result["instance_groups"]["count"] == 2
        assert mcp_result["ping"]["version"] == "24.6.1"


# ---------------------------------------------------------------------------
# awx_get_system_metrics  ->  get-system-metrics   (newly synthesized)
# ---------------------------------------------------------------------------


class TestGetSystemMetricsParity:
    def test_mcp_and_cli_match(self, patched_awx: AwxRestClient) -> None:
        mcp_result = _mcp("awx_get_system_metrics")
        cli_result = call_tool_via_cli(app, "get-system-metrics", runner=runner)
        assert_parity(mcp_result, cli_result)
        assert mcp_result == {
            "total_jobs": UNIFIED_JOBS_COUNT,
            "active_jobs": RUNNING_JOBS_COUNT,
            "failed_jobs": FAILED_JOBS_COUNT,
        }


# ---------------------------------------------------------------------------
# Smoke checks on the dual-mode wiring itself
# ---------------------------------------------------------------------------

SYNTHESIZED_COMMANDS = (
    "ping",
    "me",
    "get-system-info",
    "get-cluster-status",
    "get-system-metrics",
)


def test_synthesized_commands_registered_at_top_level() -> None:
    """Every converted tool materializes as a top-level CLI command.

    Introspects the registered Click command names (width-/ANSI-independent)
    rather than scraping the Rich-rendered top-level ``--help`` table.
    """
    names = _registered_command_names()
    for command in SYNTHESIZED_COMMANDS:
        assert command in names


def test_unconverted_commands_still_present() -> None:
    """The other hand-written commands are untouched by this increment."""
    names = _registered_command_names()
    for command in ("templates", "jobs", "launch", "list", "get", "check-access"):
        assert command in names


# ---------------------------------------------------------------------------
# MCP surface invariance: the converted tools keep their exact descriptions
# + input schemas (the agent-facing contract must not drift in this refactor).
# ---------------------------------------------------------------------------

EXPECTED_DESCRIPTIONS: dict[str, str] = {
    "awx_ping": (
        "Check basic connectivity to AWX/Controller.\n\n"
        "Returns:\n"
        "    Dict from GET /api/v2/ping/ (version, active_node, etc)."
    ),
    "awx_get_me": (
        "Get the current user for the configured AWX token.\n\n"
        "Notes:\n"
        "- Some AWX versions return a list wrapper with `results[0]` for /me/."
    ),
    "awx_get_system_info": "Get system information and health status.",
    "awx_get_cluster_status": (
        "Get overall AWX cluster health: instances, instance groups, and ping in parallel."
    ),
    "awx_get_system_metrics": "Get system performance metrics and statistics.",
}


def _tool_table() -> dict[str, Any]:
    async def _go() -> dict[str, Any]:
        from fastmcp import Client

        async with Client(server.mcp) as client:
            tools = await client.list_tools()
        return {t.name: t for t in tools}

    return asyncio.run(_go())


@pytest.mark.parametrize("tool_name", list(EXPECTED_DESCRIPTIONS))
def test_mcp_description_preserved(tool_name: str) -> None:
    """dual_mode defaults the description to the first docstring line; the
    converted tools pin the original (full) description so the MCP surface is
    byte-identical to pre-migration."""
    tool = _tool_table()[tool_name]
    assert tool.description == EXPECTED_DESCRIPTIONS[tool_name]


def test_mcp_input_schemas_preserved() -> None:
    """Input schemas are unchanged: the no-arg tools stay no-arg and
    ``awx_get_me`` keeps its optional ``fields: list[str] | None`` param."""
    tools = _tool_table()
    no_arg = {
        "awx_ping",
        "awx_get_system_info",
        "awx_get_cluster_status",
        "awx_get_system_metrics",
    }
    for name in no_arg:
        assert tools[name].inputSchema["properties"] == {}

    me_props = tools["awx_get_me"].inputSchema["properties"]
    assert set(me_props) == {"fields"}
    assert me_props["fields"]["anyOf"] == [
        {"items": {"type": "string"}, "type": "array"},
        {"type": "null"},
    ]


def test_get_me_has_no_output_schema() -> None:
    """``awx_get_me`` stays annotated ``-> Any`` (no output schema), preserving
    its exact MCP output surface; the dict-returning tools keep their object schema."""
    tools = _tool_table()
    assert tools["awx_get_me"].outputSchema is None
    for name in ("awx_ping", "awx_get_system_info", "awx_get_system_metrics"):
        assert tools[name].outputSchema == {"additionalProperties": True, "type": "object"}


def test_json_output_is_parseable_and_structurally_equal(patched_awx: AwxRestClient) -> None:
    """The synthesized ``--json`` payload is complete + parseable and matches the
    MCP structured output exactly (modulo key ordering)."""
    raw = runner.invoke(app, ["get-system-metrics", "--json"])
    assert raw.exit_code == 0, raw.stderr
    parsed = json.loads(raw.stdout)
    assert parsed == _mcp("awx_get_system_metrics")
