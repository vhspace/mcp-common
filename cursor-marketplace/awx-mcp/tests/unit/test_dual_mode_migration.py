"""MCP↔CLI parity + polling tests for the @dual_mode_tool migration (#89).

Each migrated read-only tool is exercised on both surfaces:

1. **MCP** — via an in-memory ``fastmcp.Client`` so FastMCP registration
   (input-schema validation, structured output) is covered end-to-end.
2. **CLI** — via ``typer.testing.CliRunner`` against the Typer command
   synthesized by ``mcp_common.dual_mode.build_cli_from_mcp``.

A single MagicMock AWX client backs both surfaces (the framework calls the
same Python function), so ``--json`` outputs must be identical. The file also
covers the polling angle that motivated this pilot: the synchronous
``mcp_common.cli.poll_until`` (CLI ``--wait`` flows via ``_wait_for_terminal``)
and the async ``poll_with_progress`` path driven through ``CliContext`` for the
``wait-for-job`` command.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from typing import Any, ClassVar
from unittest.mock import MagicMock

import pytest
from fastmcp import Client
from mcp_common.cli import poll_until
from typer.testing import CliRunner

from awx_mcp import cli, server
from awx_mcp.cli import app
from awx_mcp.server import mcp


def _make_runner() -> CliRunner:
    """CliRunner with stdout/stderr separated across click versions.

    click<8.2 needs ``mix_stderr=False`` to split the streams; click>=8.2
    dropped the kwarg and always separates them.
    """
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:  # pragma: no cover - depends on installed click
        return CliRunner()


runner = _make_runner()


@pytest.fixture
def patched_awx(monkeypatch: pytest.MonkeyPatch) -> Iterator[MagicMock]:
    """Replace ``server.awx`` with a MagicMock visible to both surfaces.

    ``build_cli_from_mcp`` invokes the same Python functions the FastMCP tools
    do, so one mock backs both paths. The ``before_command`` hook
    (``cli._init_awx_client``) sees ``server.awx`` already set and skips real
    client construction; ``cli._client`` is patched too so any incidental
    hand-written command stays offline.
    """
    fake = MagicMock(name="awx-client")
    monkeypatch.setattr(server, "awx", fake)
    monkeypatch.setattr(cli, "_client", lambda: fake)
    yield fake


def _mcp_call(tool_name: str, **kwargs: Any) -> Any:
    """Invoke ``tool_name`` via the in-memory MCP client and return its data."""

    async def _run() -> Any:
        async with Client(mcp) as client:
            result = await client.call_tool(tool_name, kwargs)
        if result.data is not None:
            return result.data
        if result.structured_content is not None:
            return result.structured_content
        if result.content:
            item = result.content[0]
            return getattr(item, "text", item)
        return None

    return asyncio.run(_run())


def _cli_json(*args: str) -> Any:
    """Invoke ``awx-cli`` with ``--json`` and parse stdout."""
    result = runner.invoke(app, [*args, "--json"])
    assert result.exit_code == 0, result.stderr or result.stdout
    return json.loads(result.stdout)


# ---------------------------------------------------------------------------
# awx_ping → ping  (no-arg tool)
# ---------------------------------------------------------------------------


class TestPingParity:
    def test_mcp_and_cli_match(self, patched_awx: MagicMock) -> None:
        payload = {"version": "24.6.1", "active_node": "awx-1"}
        patched_awx.get.return_value = payload

        mcp_result = _mcp_call("awx_ping")
        patched_awx.reset_mock()
        patched_awx.get.return_value = payload
        cli_result = _cli_json("ping")

        assert mcp_result == cli_result == payload


# ---------------------------------------------------------------------------
# awx_get_me → me  (list[str] | None param + repeatable --fields flag)
# ---------------------------------------------------------------------------


class TestGetMeParity:
    def test_mcp_and_cli_match_with_field_selection(self, patched_awx: MagicMock) -> None:
        # /me/ can come back as a list-wrapper; the tool body unwraps results[0].
        wrapper = {"results": [{"id": 1, "username": "svc", "email": "svc@together.ai"}]}
        patched_awx.get.return_value = wrapper

        mcp_result = _mcp_call("awx_get_me", fields=["id", "username"])
        patched_awx.reset_mock()
        patched_awx.get.return_value = wrapper
        cli_result = _cli_json("me", "--fields", "id", "--fields", "username")

        assert mcp_result == cli_result == {"id": 1, "username": "svc"}

    def test_cli_repeatable_fields_flag(self, patched_awx: MagicMock) -> None:
        patched_awx.get.return_value = {"id": 1, "username": "svc", "email": "x"}
        result = _cli_json("me", "--fields", "id", "--fields", "email")
        assert result == {"id": 1, "email": "x"}


# ---------------------------------------------------------------------------
# awx_get_system_metrics → system-metrics  (no-arg, multiple GETs)
# ---------------------------------------------------------------------------


class TestSystemMetricsParity:
    @staticmethod
    def _metrics_get(endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if endpoint == "unified_jobs":
            return {"count": 100}
        if endpoint == "jobs":
            status = (params or {}).get("status")
            return {"count": 3 if status == "running" else 7}
        return {}

    def test_mcp_and_cli_match(self, patched_awx: MagicMock) -> None:
        patched_awx.get.side_effect = self._metrics_get

        mcp_result = _mcp_call("awx_get_system_metrics")
        cli_result = _cli_json("system-metrics")

        expected = {"total_jobs": 100, "active_jobs": 3, "failed_jobs": 7}
        assert mcp_result == cli_result == expected


# ---------------------------------------------------------------------------
# awx_get_workflow_visualization → workflow-visualization  (positional int)
# ---------------------------------------------------------------------------


class TestWorkflowVisualizationParity:
    NODES: ClassVar[dict[str, Any]] = {
        "results": [
            {
                "id": 1,
                "unified_job_type": "job",
                "identifier": "build",
                "unified_job_template": 10,
                "success_nodes": [2],
                "failure_nodes": [],
                "always_nodes": [],
            },
            {
                "id": 2,
                "unified_job_type": "job",
                "identifier": "deploy",
                "unified_job_template": 11,
                "success_nodes": [],
                "failure_nodes": [],
                "always_nodes": [],
            },
        ]
    }

    def test_mcp_and_cli_match_positional(self, patched_awx: MagicMock) -> None:
        patched_awx.get.return_value = self.NODES

        mcp_result = _mcp_call("awx_get_workflow_visualization", workflow_job_template_id=5)
        patched_awx.reset_mock()
        patched_awx.get.return_value = self.NODES
        # `5` is a positional CLI argument (Annotated[int, typer.Argument]).
        cli_result = _cli_json("workflow-visualization", "5")

        assert mcp_result == cli_result
        assert len(mcp_result["nodes"]) == 2
        assert {"source": 1, "target": 2, "type": "success"} in mcp_result["links"]


# ---------------------------------------------------------------------------
# awx_get_cluster_status → cluster-status  (async + Context via CliContext)
# ---------------------------------------------------------------------------


class TestClusterStatusCliContext:
    @staticmethod
    def _status_get(endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "instances": {"count": 2},
            "instance_groups": {"count": 1},
            "ping": {"version": "24.6.1"},
        }.get(endpoint, {})

    def test_async_context_tool_runs_on_both_surfaces(self, patched_awx: MagicMock) -> None:
        patched_awx.get.side_effect = self._status_get

        mcp_result = _mcp_call("awx_get_cluster_status")
        cli_result = _cli_json("cluster-status")

        # Identical return value despite the ctx.info() calls being shimmed to
        # the stdlib logger by CliContext on the CLI side.
        assert mcp_result == cli_result
        assert set(cli_result) == {"instances", "instance_groups", "ping"}


# ---------------------------------------------------------------------------
# MCP input schemas must be UNCHANGED by the CLI projection
# ---------------------------------------------------------------------------


class TestInputSchemaUnchanged:
    @staticmethod
    def _schemas() -> dict[str, dict[str, Any]]:
        async def _run() -> dict[str, dict[str, Any]]:
            async with Client(mcp) as client:
                return {t.name: t.inputSchema for t in await client.list_tools()}

        return asyncio.run(_run())

    def test_positional_argument_does_not_change_schema(self) -> None:
        schema = self._schemas()["awx_get_workflow_visualization"]
        props = schema["properties"]
        # Despite the Typer positional Argument marker, the field stays a
        # normal required integer in the MCP input schema.
        assert "workflow_job_template_id" in props
        assert props["workflow_job_template_id"]["type"] == "integer"
        assert "workflow_job_template_id" in schema.get("required", [])

    def test_context_param_excluded_but_others_intact(self) -> None:
        schema = self._schemas()["awx_wait_for_job"]
        props = schema["properties"]
        assert "ctx" not in props  # Context is never exposed
        assert props["job_id"]["type"] == "integer"
        assert "job_id" in schema.get("required", [])
        # Optional poll knobs survive with their defaults (not required).
        assert "timeout_seconds" in props
        assert "poll_interval_seconds" in props
        assert "timeout_seconds" not in schema.get("required", [])

    def test_debug_jt_credentials_positional_int(self) -> None:
        schema = self._schemas()["awx_debug_job_template_credentials"]
        props = schema["properties"]
        assert props["job_template_id"]["type"] == "integer"
        assert "job_template_id" in schema.get("required", [])


# ---------------------------------------------------------------------------
# Polling: mcp_common.cli.poll_until (sync) — the core of this pilot
# ---------------------------------------------------------------------------


class TestPollUntilDirect:
    def test_returns_terminal_value_after_pending_pending_successful(self) -> None:
        jobs = [
            {"id": 9, "status": "pending"},
            {"id": 9, "status": "pending"},
            {"id": 9, "status": "successful"},
        ]
        feed = iter(jobs)
        ticks: list[str] = []

        result = poll_until(
            fetch=lambda: next(feed),
            is_terminal=lambda j: j["status"] in {"successful", "failed", "error", "canceled"},
            timeout_s=5,
            interval_s=0,  # no real sleep in tests
            on_tick=lambda elapsed, snap: ticks.append(snap["status"]),
        )

        assert result == {"id": 9, "status": "successful"}
        # on_tick fires only for the two non-terminal snapshots.
        assert ticks == ["pending", "pending"]


# ---------------------------------------------------------------------------
# Polling: CLI inventory-sync --wait drives _wait_for_terminal -> poll_until
# ---------------------------------------------------------------------------


class TestWaitForTerminalViaInventorySync:
    def test_pending_pending_successful_reaches_terminal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = MagicMock(name="awx-client")
        client.post.return_value = {"id": 900, "status": "pending"}
        statuses = iter(["pending", "pending", "successful"])
        client.get.side_effect = lambda endpoint, params=None: {
            "id": 900,
            "status": next(statuses),
        }
        monkeypatch.setattr(cli, "_client", lambda: client)

        result = runner.invoke(
            app,
            [
                "inventory-sync",
                "42",
                "--wait",
                "--poll-interval",
                "0.01",
                "--timeout",
                "5",
                "--json",
            ],
        )

        assert result.exit_code == 0, result.stderr
        assert json.loads(result.stdout)["status"] == "successful"
        # poll_until's on_tick fired for each non-terminal poll, and the
        # wrapper emitted the terminal summary.
        assert result.stderr.count("Sync 900: pending") == 2
        assert "FINISHED: successful" in result.stderr


# ---------------------------------------------------------------------------
# Polling: CLI wait-for-job drives poll_with_progress through CliContext
# ---------------------------------------------------------------------------


class TestWaitForJobAsyncPolling:
    def test_cli_polls_to_terminal_via_clicontext(self, patched_awx: MagicMock) -> None:
        statuses = iter(["pending", "pending", "successful"])
        patched_awx.get.side_effect = lambda endpoint, params=None: {
            "id": 4348,
            "status": next(statuses),
        }

        result = runner.invoke(
            app,
            [
                "wait-for-job",
                "4348",
                "--poll-interval-seconds",
                "0.01",
                "--timeout-seconds",
                "5",
                "--json",
            ],
        )

        assert result.exit_code == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["status"] == "successful"
        assert data["id"] == 4348
        # CliContext.report_progress emitted progress lines to stderr.
        assert "%" in result.stderr


# ---------------------------------------------------------------------------
# Smoke checks on the dual-mode wiring itself
# ---------------------------------------------------------------------------


SYNTHESIZED_COMMANDS = (
    "ping",
    "me",
    "cluster-status",
    "system-info",
    "system-metrics",
    "wait-for-job",
    "supported-resources",
    "workflow-visualization",
    "debug-jt-credentials",
    "aws-credentials",
)


def test_all_synthesized_commands_registered() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in SYNTHESIZED_COMMANDS:
        assert command in result.stdout


def test_help_works_without_credentials() -> None:
    """before_command (AWX client init) must be skipped on --help paths."""
    top = runner.invoke(app, ["--help"])
    sub = runner.invoke(app, ["wait-for-job", "--help"])
    assert top.exit_code == 0
    assert sub.exit_code == 0


def test_bespoke_read_commands_kept_handwritten() -> None:
    """Generic get/list/stdout stay hand-written (richer behaviour); guard so a
    future over-eager migration that drops them fails loudly."""
    result = runner.invoke(app, ["--help"])
    for command in ("get", "list", "stdout", "log-summary"):
        assert command in result.stdout
