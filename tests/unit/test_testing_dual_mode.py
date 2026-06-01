"""Tests for ``mcp_common.testing.dual_mode`` — the shared MCP↔CLI parity helpers (#106)."""

import json
from collections.abc import Iterator

import anyio
import pydantic
import pytest
from fastmcp import FastMCP

from mcp_common.dual_mode import build_cli_from_mcp, dual_mode_tool
from mcp_common.dual_mode._registry import _clear
from mcp_common.testing.dual_mode import (
    assert_parity,
    call_tool_via_cli,
    call_tool_via_mcp,
    make_cli_runner,
)


class _Device(pydantic.BaseModel):
    """Module-level model so the model-returning tool resolves cleanly."""

    hostname: str
    active: bool = True


@pytest.fixture
def dual_mode_app() -> Iterator[tuple[FastMCP, object]]:
    """A small dual-mode server + synthesized CLI exercising dict/list/model/error tools."""
    mcp = FastMCP("netbox")

    @dual_mode_tool(mcp, cli_name="lookup-device")
    def lookup_device(hostname: str, active: bool = True) -> dict:
        """Return a device dict."""
        return {"hostname": hostname, "active": active}

    @dual_mode_tool(mcp, cli_name="list-tags")
    def list_tags() -> list[str]:
        """Return a list (FastMCP wraps non-dict returns in {'result': ...})."""
        return ["alpha", "beta"]

    @dual_mode_tool(mcp, cli_name="get-device")
    def get_device(hostname: str) -> _Device:
        """Return a Pydantic model (dumped to JSON on both surfaces)."""
        return _Device(hostname=hostname)

    @dual_mode_tool(mcp, cli_name="boom")
    def boom() -> dict:
        """Always raises."""
        raise RuntimeError("explode")

    app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")
    try:
        yield mcp, app
    finally:
        _clear(mcp)


def _mcp_value(mcp: FastMCP, tool: str, **kwargs: object) -> object:
    """Drive the async :func:`call_tool_via_mcp` from a sync test via anyio."""

    async def _run() -> object:
        return await call_tool_via_mcp(mcp, tool, **kwargs)

    return anyio.run(_run)


class TestCallToolViaMcp:
    def test_dict_tool_returns_structured(self, dual_mode_app: tuple[FastMCP, object]) -> None:
        mcp, _ = dual_mode_app
        assert _mcp_value(mcp, "lookup_device", hostname="sw01") == {
            "hostname": "sw01",
            "active": True,
        }

    def test_list_tool_unwraps_result_envelope(self, dual_mode_app: tuple[FastMCP, object]) -> None:
        mcp, _ = dual_mode_app
        # FastMCP wraps the list in {"result": [...]}; the helper unwraps it.
        assert _mcp_value(mcp, "list_tags") == ["alpha", "beta"]


class TestCallToolViaCli:
    def test_appends_json_and_parses(self, dual_mode_app: tuple[FastMCP, object]) -> None:
        _, app = dual_mode_app
        # No --json passed; the helper appends it before invoking.
        assert call_tool_via_cli(app, "lookup-device", ["--hostname", "sw01"]) == {
            "hostname": "sw01",
            "active": True,
        }

    def test_list_command(self, dual_mode_app: tuple[FastMCP, object]) -> None:
        _, app = dual_mode_app
        assert call_tool_via_cli(app, "list-tags") == ["alpha", "beta"]

    def test_explicit_json_not_duplicated(self, dual_mode_app: tuple[FastMCP, object]) -> None:
        _, app = dual_mode_app
        assert call_tool_via_cli(app, "lookup-device", ["--hostname", "x", "--json"]) == {
            "hostname": "x",
            "active": True,
        }

    def test_raises_on_nonzero_exit(self, dual_mode_app: tuple[FastMCP, object]) -> None:
        _, app = dual_mode_app
        with pytest.raises(AssertionError, match="exited"):
            call_tool_via_cli(app, "boom")

    def test_raises_on_unknown_command(self, dual_mode_app: tuple[FastMCP, object]) -> None:
        _, app = dual_mode_app
        with pytest.raises(AssertionError):
            call_tool_via_cli(app, "no-such-command")


class TestAssertParity:
    def test_passes_for_equal_dict_ignoring_key_order(self) -> None:
        assert_parity({"a": 1, "b": 2}, {"b": 2, "a": 1})

    def test_passes_for_equal_lists(self) -> None:
        assert_parity(["a", "b"], ["a", "b"])

    def test_raises_on_mismatch(self) -> None:
        with pytest.raises(AssertionError, match="parity mismatch"):
            assert_parity({"a": 1}, {"a": 2})

    def test_mismatch_message_carries_unified_diff(self) -> None:
        with pytest.raises(AssertionError) as exc:
            assert_parity({"x": 1}, {"x": 9})
        message = str(exc.value)
        assert "mcp" in message and "cli" in message
        assert "1" in message and "9" in message

    def test_custom_msg_is_prefixed(self) -> None:
        with pytest.raises(AssertionError, match="my custom context"):
            assert_parity(1, 2, msg="my custom context")


class TestEndToEndParity:
    """The headline use case: MCP and CLI surfaces agree for the same inputs."""

    def test_dict_tool_parity(self, dual_mode_app: tuple[FastMCP, object]) -> None:
        mcp, app = dual_mode_app
        mcp_result = _mcp_value(mcp, "lookup_device", hostname="sw01")
        cli_result = call_tool_via_cli(app, "lookup-device", ["--hostname", "sw01"])
        assert_parity(mcp_result, cli_result)

    def test_list_tool_parity(self, dual_mode_app: tuple[FastMCP, object]) -> None:
        mcp, app = dual_mode_app
        mcp_result = _mcp_value(mcp, "list_tags")
        cli_result = call_tool_via_cli(app, "list-tags")
        assert_parity(mcp_result, cli_result)

    def test_model_tool_parity(self, dual_mode_app: tuple[FastMCP, object]) -> None:
        mcp, app = dual_mode_app
        mcp_result = _mcp_value(mcp, "get_device", hostname="sw01")
        cli_result = call_tool_via_cli(app, "get-device", ["--hostname", "sw01"])
        assert_parity(mcp_result, cli_result)


class TestMakeCliRunner:
    def test_returns_usable_runner_with_separated_streams(
        self, dual_mode_app: tuple[FastMCP, object]
    ) -> None:
        _, app = dual_mode_app
        runner = make_cli_runner()
        result = runner.invoke(app, ["lookup-device", "--hostname", "x", "--json"])
        assert result.exit_code == 0, f"stderr: {result.stderr}"
        assert json.loads(result.stdout) == {"hostname": "x", "active": True}
