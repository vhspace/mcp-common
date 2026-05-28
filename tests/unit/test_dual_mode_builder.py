"""Tests for ``build_cli_from_mcp`` — the materializer side of dual_mode."""

import json
import re
from typing import Literal

import pydantic
import pytest
from fastmcp import Context, FastMCP
from typer.testing import CliRunner

from mcp_common.dual_mode import build_cli_from_mcp, dual_mode_tool
from mcp_common.dual_mode._registry import _clear
from mcp_common.dual_mode._typer_params import PYDANTIC_FLATTEN_THRESHOLD

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(s: str) -> str:
    """Remove ANSI SGR escape sequences from CLI output.

    Typer's Rich help renderer splits tokens like ``--payload-name`` into
    separately-styled runs under a color-capable ``TERM`` (CI uses
    ``xterm-256color``). Strip the codes before substring assertions so
    the tests don't depend on terminal capabilities.
    """
    return _ANSI_RE.sub("", s)


class _Inner(pydantic.BaseModel):
    """Inner model used by Bug3 nested-Pydantic flattening tests."""

    x: int
    label: str = "n/a"


class _Outer(pydantic.BaseModel):
    """Outer with both a primitive sibling and a nested Pydantic field."""

    name: str
    inner: _Inner


class _OuterWithList(pydantic.BaseModel):
    """Container with a ``list[_Inner]`` field for the JSON-blob fallback."""

    label: str
    items: list[_Inner]


class _DescribedFields(pydantic.BaseModel):
    """Bug8 fixture: per-field ``Field(description=...)`` must reach CLI help."""

    hostname: str = pydantic.Field(description="The device hostname.")
    site: str = pydantic.Field(default="dc1", description="Datacenter site.")


@pytest.fixture
def mcp() -> FastMCP:
    instance = FastMCP("netbox")
    yield instance
    _clear(instance)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner(mix_stderr=False)


# Module-level Pydantic models so PEP 563 forward refs resolve cleanly.
class _SmallPayload(pydantic.BaseModel):
    name: str
    count: int = 0


_BigPayload = pydantic.create_model(
    "_BigPayload",
    **{f"f{i}": (str, ...) for i in range(PYDANTIC_FLATTEN_THRESHOLD + 1)},  # type: ignore[arg-type]
)


class TestSyncToolEndToEnd:
    def test_sync_tool_json_mode(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        def lookup_device(hostname: str, include_interfaces: bool = False) -> dict:
            """Resolve a hostname/IP."""
            return {"hostname": hostname, "interfaces": include_interfaces}

        app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")
        result = runner.invoke(app, ["lookup-device", "--hostname", "sw01", "--json"])

        assert result.exit_code == 0
        parsed = json.loads(result.stdout)
        assert parsed == {"hostname": "sw01", "interfaces": False}

    def test_sync_tool_human_mode_uses_str(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        def echo(message: str) -> str:
            """Echo a message."""
            return f"Echo: {message}"

        app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")
        result = runner.invoke(app, ["echo", "--message", "hi"])

        assert result.exit_code == 0
        assert "Echo: hi" in result.stdout

    def test_sync_tool_bool_flag_activation(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        def lookup_device(hostname: str, include_interfaces: bool = False) -> dict:
            """Resolve a hostname/IP."""
            return {"hostname": hostname, "interfaces": include_interfaces}

        app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")
        result = runner.invoke(
            app,
            ["lookup-device", "--hostname", "sw01", "--include-interfaces", "--json"],
        )

        parsed = json.loads(result.stdout)
        assert parsed["interfaces"] is True


class TestAsyncToolEndToEnd:
    def test_async_tool_is_driven_by_asyncio_run(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        async def async_lookup(name: str) -> dict:
            """Async lookup."""
            return {"name": name, "kind": "async"}

        app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")
        result = runner.invoke(app, ["async-lookup", "--name", "alice", "--json"])

        assert result.exit_code == 0
        assert json.loads(result.stdout) == {"name": "alice", "kind": "async"}

    def test_async_exception_propagates(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        async def boom(_x: int) -> dict:
            """Always raises."""
            raise RuntimeError("explode")

        app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")
        result = runner.invoke(app, ["boom", "--x", "1"])
        assert result.exit_code != 0


class TestContextShimming:
    def test_context_tool_runs_without_session(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        async def with_context(ctx: Context, name: str) -> dict:
            """Tool that uses the Context API."""
            await ctx.info(f"looking up {name}")
            await ctx.report_progress(progress=50, total=100, message="halfway")
            return {"name": name, "had_context": True}

        app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")
        result = runner.invoke(app, ["with-context", "--name", "bob", "--json"])

        assert result.exit_code == 0
        assert json.loads(result.stdout) == {"name": "bob", "had_context": True}
        # Progress should land on stderr (default CliContext stream).
        assert "halfway" in result.stderr


class TestPydanticParameter:
    def test_small_model_is_flattened_on_cli(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        def create_device(payload: _SmallPayload) -> dict:
            """Create a device."""
            return payload.model_dump()

        app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")
        result = runner.invoke(
            app,
            [
                "create-device",
                "--payload-name",
                "sw01",
                "--payload-count",
                "5",
                "--json",
            ],
        )

        assert result.exit_code == 0
        assert json.loads(result.stdout) == {"name": "sw01", "count": 5}

    def test_large_model_uses_params_blob(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        def create_big(payload: _BigPayload) -> dict:  # type: ignore[valid-type]
            """Create something with many fields."""
            return payload.model_dump()

        app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")
        blob = json.dumps({f"f{i}": str(i) for i in range(PYDANTIC_FLATTEN_THRESHOLD + 1)})
        result = runner.invoke(
            app,
            ["create-big", "--payload-params", blob, "--json"],
        )

        assert result.exit_code == 0
        parsed = json.loads(result.stdout)
        assert parsed[f"f{PYDANTIC_FLATTEN_THRESHOLD}"] == str(PYDANTIC_FLATTEN_THRESHOLD)


class TestLiteralChoice:
    def test_literal_choice_accepts_valid(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        def select(mode: Literal["fast", "slow"]) -> dict:
            """Pick a mode."""
            return {"mode": mode}

        app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")
        result = runner.invoke(app, ["select", "--mode", "fast", "--json"])

        assert result.exit_code == 0
        assert json.loads(result.stdout) == {"mode": "fast"}

    def test_literal_choice_rejects_invalid(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        def select(mode: Literal["fast", "slow"]) -> dict:
            """Pick a mode."""
            return {"mode": mode}

        app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")
        result = runner.invoke(app, ["select", "--mode", "bogus"])

        assert result.exit_code != 0
        assert "fast" in result.stderr or "slow" in result.stderr

    def test_literal_int_accepts_valid(self, mcp: FastMCP, runner: CliRunner) -> None:
        """``Literal[1, 2, 3]`` must accept ``--level 2`` (was rejecting all valid input)."""

        @dual_mode_tool(mcp)
        def pick(level: Literal[1, 2, 3]) -> dict:
            """Pick a level."""
            return {"level": level}

        app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")
        result = runner.invoke(app, ["pick", "--level", "2", "--json"])

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        assert json.loads(result.stdout) == {"level": 2}

    def test_literal_int_rejects_out_of_set(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        def pick(level: Literal[1, 2, 3]) -> dict:
            """Pick a level."""
            return {"level": level}

        app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")
        result = runner.invoke(app, ["pick", "--level", "5"])

        assert result.exit_code != 0
        assert "1" in result.stderr and "2" in result.stderr and "3" in result.stderr

    def test_literal_float_accepts_valid(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        def rate(rate: Literal[1.0, 2.0]) -> dict:
            """Pick a rate."""
            return {"rate": rate}

        app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")
        result = runner.invoke(app, ["rate", "--rate", "1.0", "--json"])

        assert result.exit_code == 0
        assert json.loads(result.stdout) == {"rate": 1.0}


class TestListParameter:
    def test_multi_value_list_collected(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        def tag_all(tags: list[str]) -> dict:
            """Tag many things."""
            return {"tags": tags}

        app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")
        result = runner.invoke(app, ["tag-all", "--tags", "alpha", "--tags", "beta", "--json"])

        assert result.exit_code == 0
        assert json.loads(result.stdout) == {"tags": ["alpha", "beta"]}


class TestOptionalParameter:
    def test_optional_with_default_is_optional(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        def lookup(name: str | None = None) -> dict:
            """Optional name lookup."""
            return {"name": name}

        app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")
        result = runner.invoke(app, ["lookup", "--json"])

        assert result.exit_code == 0
        assert json.loads(result.stdout) == {"name": None}


class TestCliGroup:
    def test_cli_group_creates_subcommand(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp, cli_group="devices")
        def lookup_device(hostname: str) -> dict:
            """Look up under devices/"""
            return {"hostname": hostname}

        app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")

        result = runner.invoke(app, ["devices", "--help"])
        assert result.exit_code == 0
        assert "lookup-device" in result.stdout

        result = runner.invoke(app, ["devices", "lookup-device", "--hostname", "sw01", "--json"])
        assert result.exit_code == 0
        assert json.loads(result.stdout) == {"hostname": "sw01"}

    def test_multiple_commands_share_one_subgroup(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp, cli_group="devices")
        def lookup_device(hostname: str) -> dict:
            return {"hostname": hostname}

        @dual_mode_tool(mcp, cli_group="devices")
        def list_devices() -> dict:
            return {"devices": []}

        app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")
        result = runner.invoke(app, ["devices", "--help"])
        assert "lookup-device" in result.stdout
        assert "list-devices" in result.stdout


class TestMcpOnly:
    def test_mcp_only_tools_omitted_from_cli(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        def public_tool(x: int) -> int:
            """Public tool."""
            return x

        @dual_mode_tool(mcp, mcp_only=True)
        def server_only(x: int) -> int:
            """MCP-only tool."""
            return x

        app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")
        result = runner.invoke(app, ["--help"])
        assert "public-tool" in result.stdout
        assert "server-only" not in result.stdout


class TestCustomFormatter:
    def test_human_formatter_used_in_human_mode(self, mcp: FastMCP, runner: CliRunner) -> None:
        def fmt_dict(d: dict) -> str:
            return f"NAME={d['name']}"

        @dual_mode_tool(mcp, formatters={dict: fmt_dict})
        def lookup(name: str) -> dict:
            """Return a dict."""
            return {"name": name}

        app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")
        result = runner.invoke(app, ["lookup", "--name", "sw01"])

        assert result.exit_code == 0
        assert "NAME=sw01" in result.stdout

    def test_human_formatter_ignored_in_json_mode(self, mcp: FastMCP, runner: CliRunner) -> None:
        def fmt_dict(d: dict) -> str:
            return "HUMAN ONLY"

        @dual_mode_tool(mcp, formatters={dict: fmt_dict})
        def lookup(name: str) -> dict:
            return {"name": name}

        app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")
        result = runner.invoke(app, ["lookup", "--name", "sw01", "--json"])

        assert result.exit_code == 0
        assert "HUMAN ONLY" not in result.stdout
        assert json.loads(result.stdout) == {"name": "sw01"}


class TestExceptionPropagation:
    def test_sync_exception_bubbles_to_exit_handler(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        def boom(x: int) -> int:
            """Always raises."""
            raise RuntimeError("kaboom")

        app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")
        # CliRunner catches exceptions by default; we just verify the command exited non-zero.
        result = runner.invoke(app, ["boom", "--x", "1"])
        assert result.exit_code != 0


class TestAppNaming:
    def test_default_cli_name_strips_mcp_suffix(self) -> None:
        mcp = FastMCP("netbox-mcp")
        try:

            @dual_mode_tool(mcp)
            def lookup(x: int) -> int:
                return x

            app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp")
            assert app.info.name == "netbox-cli"
        finally:
            _clear(mcp)

    def test_explicit_name_wins(self) -> None:
        mcp = FastMCP("netbox-mcp")
        try:

            @dual_mode_tool(mcp)
            def lookup(x: int) -> int:
                return x

            app = build_cli_from_mcp(mcp, project_repo="vhspace/netbox-mcp", name="my-custom-cli")
            assert app.info.name == "my-custom-cli"
        finally:
            _clear(mcp)


class TestEmptyRegistry:
    def test_no_tools_yields_help_only_app(self, runner: CliRunner) -> None:
        mcp = FastMCP("empty")
        try:
            app = build_cli_from_mcp(mcp, project_repo="vhspace/empty-mcp")
            result = runner.invoke(app, ["--help"])
            assert result.exit_code == 0
            # Empty registry → no top-level commands attached.
            assert app.registered_commands == []
        finally:
            _clear(mcp)


class TestNestedPydanticFallback:
    """Bug3: nested Pydantic models route to a per-field ``--<field>-json`` blob."""

    def test_nested_pydantic_via_json_blob(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        def create(payload: _Outer) -> dict:
            """Create a thing with a nested model."""
            return payload.model_dump()

        app = build_cli_from_mcp(mcp, project_repo="vhspace/test")
        result = runner.invoke(
            app,
            [
                "create",
                "--payload-name",
                "sw01",
                "--payload-inner-json",
                json.dumps({"x": 7, "label": "primary"}),
                "--json",
            ],
        )

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        assert json.loads(result.stdout) == {
            "name": "sw01",
            "inner": {"x": 7, "label": "primary"},
        }

    def test_nested_pydantic_help_shows_json_flag(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        def create(payload: _Outer) -> dict:
            """Create a thing with a nested model."""
            return payload.model_dump()

        app = build_cli_from_mcp(mcp, project_repo="vhspace/test")
        result = runner.invoke(app, ["create", "--help"])

        assert result.exit_code == 0
        clean = _strip_ansi(result.stdout)
        # Sibling primitive flattening preserved.
        assert "--payload-name" in clean
        # Nested model flattens to a per-field JSON option.
        assert "--payload-inner-json" in clean

    def test_list_of_pydantic_via_json_blob(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        def create(payload: _OuterWithList) -> dict:
            """Container with a list[Pydantic] field."""
            return payload.model_dump()

        app = build_cli_from_mcp(mcp, project_repo="vhspace/test")
        result = runner.invoke(
            app,
            [
                "create",
                "--payload-label",
                "group-a",
                "--payload-items-json",
                json.dumps([{"x": 1}, {"x": 2, "label": "tag"}]),
                "--json",
            ],
        )

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        assert json.loads(result.stdout) == {
            "label": "group-a",
            "items": [{"x": 1, "label": "n/a"}, {"x": 2, "label": "tag"}],
        }


class TestSyncCoroutineReturn:
    """Bug6: sync tool returning a coroutine/asyncgen must error, not str()."""

    def test_sync_returns_coroutine_raises(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        def bad(x: int) -> dict:
            """Pretends sync but returns a coroutine."""

            async def inner() -> dict:
                return {"x": x}

            return inner()

        app = build_cli_from_mcp(mcp, project_repo="vhspace/test")
        result = runner.invoke(app, ["bad", "--x", "1"])

        assert result.exit_code != 0
        assert isinstance(result.exception, RuntimeError)
        assert "decorated as sync" in str(result.exception)
        assert "coroutine" in str(result.exception)

    def test_sync_returns_asyncgen_raises(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        def bad(x: int) -> dict:
            """Pretends sync but returns an async generator."""

            async def gen():
                yield {"x": x}

            return gen()

        app = build_cli_from_mcp(mcp, project_repo="vhspace/test")
        result = runner.invoke(app, ["bad", "--x", "1"])

        assert result.exit_code != 0
        assert isinstance(result.exception, RuntimeError)
        assert "decorated as sync" in str(result.exception)
        assert "async_generator" in str(result.exception)


class TestPydanticFieldDescription:
    """Bug8: ``Field(description=...)`` must reach the synthesized CLI help."""

    def test_field_description_appears_in_help(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        def create(payload: _DescribedFields) -> dict:
            """Create a described thing."""
            return payload.model_dump()

        app = build_cli_from_mcp(mcp, project_repo="vhspace/test")
        result = runner.invoke(app, ["create", "--help"])

        assert result.exit_code == 0
        clean = _strip_ansi(result.stdout)
        assert "The device hostname." in clean
        assert "Datacenter site." in clean
        # Generic placeholder no longer leaks for documented fields.
        assert "str option." not in clean
