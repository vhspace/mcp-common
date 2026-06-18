"""Tests for ``build_cli_from_mcp`` — the materializer side of dual_mode."""

import functools
import json
import re
from typing import Annotated, Literal

import anyio
import pydantic
import pytest
import typer
from fastmcp import Client, Context, FastMCP
from typer.testing import CliRunner

from mcp_common.dual_mode import build_cli_from_mcp, dual_mode_tool
from mcp_common.dual_mode._registry import _clear
from mcp_common.dual_mode._typer_params import PYDANTIC_FLATTEN_THRESHOLD
from mcp_common.testing.dual_mode import make_cli_runner


def _tool_input_schema(mcp: FastMCP, tool_name: str) -> dict:
    """Return the FastMCP input schema for ``tool_name`` via the in-memory client.

    Used to assert the MCP-side invariant that the CLI projection (positional
    args, etc.) never leaks into the tool's input schema.
    """

    async def _run() -> dict:
        async with Client(mcp) as client:
            for tool in await client.list_tools():
                if tool.name == tool_name:
                    return tool.inputSchema
        raise AssertionError(f"tool {tool_name!r} not registered on FastMCP")

    return anyio.run(_run)


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
    return make_cli_runner()


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

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/netbox-mcp")
        result = runner.invoke(app, ["lookup-device", "--hostname", "sw01", "--json"])

        assert result.exit_code == 0
        parsed = json.loads(result.stdout)
        assert parsed == {"hostname": "sw01", "interfaces": False}

    def test_sync_tool_human_mode_uses_str(
        self, mcp: FastMCP, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        @dual_mode_tool(mcp)
        def echo(message: str) -> str:
            """Echo a message."""
            return f"Echo: {message}"

        monkeypatch.setattr(
            "mcp_common.dual_mode.builder.should_emit_json",
            lambda explicit_json: explicit_json,
        )
        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/netbox-mcp")
        result = runner.invoke(app, ["echo", "--message", "hi"])

        assert result.exit_code == 0
        assert "Echo: hi" in result.stdout

    def test_sync_tool_piped_stdout_emits_json_without_flag(
        self, mcp: FastMCP, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        @dual_mode_tool(mcp)
        def lookup_device(hostname: str) -> dict:
            """Resolve a hostname/IP."""
            return {"hostname": hostname}

        monkeypatch.setattr(
            "mcp_common.dual_mode.builder.should_emit_json",
            lambda explicit_json: explicit_json or True,
        )
        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/netbox-mcp")
        result = runner.invoke(app, ["lookup-device", "--hostname", "sw01"])

        assert result.exit_code == 0
        assert json.loads(result.stdout) == {"hostname": "sw01"}

    def test_sync_tool_tty_still_honors_explicit_json_flag(
        self, mcp: FastMCP, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        @dual_mode_tool(mcp)
        def lookup_device(hostname: str) -> dict:
            """Resolve a hostname/IP."""
            return {"hostname": hostname}

        monkeypatch.setattr(
            "mcp_common.dual_mode.builder.should_emit_json",
            lambda explicit_json: explicit_json,
        )
        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/netbox-mcp")
        result = runner.invoke(app, ["lookup-device", "--hostname", "sw01", "--json"])

        assert result.exit_code == 0
        assert json.loads(result.stdout) == {"hostname": "sw01"}

    def test_sync_tool_bool_flag_activation(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        def lookup_device(hostname: str, include_interfaces: bool = False) -> dict:
            """Resolve a hostname/IP."""
            return {"hostname": hostname, "interfaces": include_interfaces}

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/netbox-mcp")
        result = runner.invoke(
            app,
            ["lookup-device", "--hostname", "sw01", "--include-interfaces", "--json"],
        )

        parsed = json.loads(result.stdout)
        assert parsed["interfaces"] is True


class TestLargeJsonOutput:
    """Issue #113: synthesized ``--json`` output must never be truncated.

    Returns route through ``echo_result``, which previously applied the
    default ``truncate=4096`` even in JSON mode — corrupting any ``--json``
    payload over 4 KB mid-structure so ``json.loads`` failed. The JSON path
    now emits complete, parseable output regardless of size.
    """

    def test_large_list_json_output_round_trips(self, mcp: FastMCP, runner: CliRunner) -> None:
        # ~6 KB of JSON once serialized — comfortably over the old 4096 cap.
        big = [{"i": i, "pad": "x" * 200} for i in range(30)]

        @dual_mode_tool(mcp)
        def big_list() -> list[dict]:
            """Return a large list."""
            return big

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/netbox-mcp")
        result = runner.invoke(app, ["big-list", "--json"])

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        assert len(result.stdout) > 4096
        assert "more chars" not in result.stdout
        assert json.loads(result.stdout) == big


class TestAsyncToolEndToEnd:
    def test_async_tool_is_driven_by_asyncio_run(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        async def async_lookup(name: str) -> dict:
            """Async lookup."""
            return {"name": name, "kind": "async"}

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/netbox-mcp")
        result = runner.invoke(app, ["async-lookup", "--name", "alice", "--json"])

        assert result.exit_code == 0
        assert json.loads(result.stdout) == {"name": "alice", "kind": "async"}

    def test_async_exception_propagates(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        async def boom(_x: int) -> dict:
            """Always raises."""
            raise RuntimeError("explode")

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/netbox-mcp")
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

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/netbox-mcp")
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

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/netbox-mcp")
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

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/netbox-mcp")
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

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/netbox-mcp")
        result = runner.invoke(app, ["select", "--mode", "fast", "--json"])

        assert result.exit_code == 0
        assert json.loads(result.stdout) == {"mode": "fast"}

    def test_literal_choice_rejects_invalid(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        def select(mode: Literal["fast", "slow"]) -> dict:
            """Pick a mode."""
            return {"mode": mode}

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/netbox-mcp")
        result = runner.invoke(app, ["select", "--mode", "bogus"])

        assert result.exit_code != 0
        assert "fast" in result.stderr or "slow" in result.stderr

    def test_literal_int_accepts_valid(self, mcp: FastMCP, runner: CliRunner) -> None:
        """``Literal[1, 2, 3]`` must accept ``--level 2`` (was rejecting all valid input)."""

        @dual_mode_tool(mcp)
        def pick(level: Literal[1, 2, 3]) -> dict:
            """Pick a level."""
            return {"level": level}

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/netbox-mcp")
        result = runner.invoke(app, ["pick", "--level", "2", "--json"])

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        assert json.loads(result.stdout) == {"level": 2}

    def test_literal_int_rejects_out_of_set(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        def pick(level: Literal[1, 2, 3]) -> dict:
            """Pick a level."""
            return {"level": level}

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/netbox-mcp")
        result = runner.invoke(app, ["pick", "--level", "5"])

        assert result.exit_code != 0
        assert "1" in result.stderr and "2" in result.stderr and "3" in result.stderr

    def test_literal_float_accepts_valid(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        def rate(rate: Literal[1.0, 2.0]) -> dict:
            """Pick a rate."""
            return {"rate": rate}

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/netbox-mcp")
        result = runner.invoke(app, ["rate", "--rate", "1.0", "--json"])

        assert result.exit_code == 0
        assert json.loads(result.stdout) == {"rate": 1.0}


class TestListParameter:
    def test_multi_value_list_collected(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        def tag_all(tags: list[str]) -> dict:
            """Tag many things."""
            return {"tags": tags}

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/netbox-mcp")
        result = runner.invoke(app, ["tag-all", "--tags", "alpha", "--tags", "beta", "--json"])

        assert result.exit_code == 0
        assert json.loads(result.stdout) == {"tags": ["alpha", "beta"]}


class TestOptionalParameter:
    def test_optional_with_default_is_optional(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp)
        def lookup(name: str | None = None) -> dict:
            """Optional name lookup."""
            return {"name": name}

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/netbox-mcp")
        result = runner.invoke(app, ["lookup", "--json"])

        assert result.exit_code == 0
        assert json.loads(result.stdout) == {"name": None}


class TestCliGroup:
    def test_cli_group_creates_subcommand(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp, cli_group="devices")
        def lookup_device(hostname: str) -> dict:
            """Look up under devices/"""
            return {"hostname": hostname}

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/netbox-mcp")

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

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/netbox-mcp")
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

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/netbox-mcp")
        result = runner.invoke(app, ["--help"])
        assert "public-tool" in result.stdout
        assert "server-only" not in result.stdout


class TestCustomFormatter:
    def test_human_formatter_used_in_human_mode(
        self, mcp: FastMCP, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fmt_dict(d: dict) -> str:
            return f"NAME={d['name']}"

        @dual_mode_tool(mcp, formatters={dict: fmt_dict})
        def lookup(name: str) -> dict:
            """Return a dict."""
            return {"name": name}

        monkeypatch.setattr(
            "mcp_common.dual_mode.builder.should_emit_json",
            lambda explicit_json: explicit_json,
        )
        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/netbox-mcp")
        result = runner.invoke(app, ["lookup", "--name", "sw01"])

        assert result.exit_code == 0
        assert "NAME=sw01" in result.stdout

    def test_human_formatter_ignored_in_json_mode(self, mcp: FastMCP, runner: CliRunner) -> None:
        def fmt_dict(d: dict) -> str:
            return "HUMAN ONLY"

        @dual_mode_tool(mcp, formatters={dict: fmt_dict})
        def lookup(name: str) -> dict:
            return {"name": name}

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/netbox-mcp")
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

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/netbox-mcp")
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

            app = build_cli_from_mcp(mcp, project_repo="togethercomputer/netbox-mcp")
            assert app.info.name == "netbox-cli"
        finally:
            _clear(mcp)

    def test_explicit_name_wins(self) -> None:
        mcp = FastMCP("netbox-mcp")
        try:

            @dual_mode_tool(mcp)
            def lookup(x: int) -> int:
                return x

            app = build_cli_from_mcp(
                mcp, project_repo="togethercomputer/netbox-mcp", name="my-custom-cli"
            )
            assert app.info.name == "my-custom-cli"
        finally:
            _clear(mcp)


class TestEmptyRegistry:
    def test_no_tools_yields_help_only_app(self, runner: CliRunner) -> None:
        mcp = FastMCP("empty")
        try:
            app = build_cli_from_mcp(mcp, project_repo="togethercomputer/empty-mcp")
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

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/test")
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

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/test")
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

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/test")
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

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/test")
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

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/test")
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

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/test")
        result = runner.invoke(app, ["create", "--help"])

        assert result.exit_code == 0
        clean = _strip_ansi(result.stdout)
        assert "The device hostname." in clean
        assert "Datacenter site." in clean
        # Generic placeholder no longer leaks for documented fields.
        assert "str option." not in clean


class TestPositionalArgument:
    """Issue #102: ``Annotated[T, typer.Argument(...)]`` → positional CLI arg.

    The CLI projection becomes ``cmd VALUE`` instead of ``cmd --value VALUE``,
    while the MCP tool's input schema stays exactly as if the param were a
    plain typed parameter — that's the critical invariant.
    """

    def test_positional_invocation(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp, cli_name="lookup-device")
        def lookup_device(
            hostname: Annotated[str, typer.Argument(help="Device hostname or IP.")],
            include_interfaces: bool = False,
        ) -> dict:
            """Resolve a hostname/IP to a NetBox device."""
            return {"hostname": hostname, "interfaces": include_interfaces}

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/netbox-mcp")
        result = runner.invoke(app, ["lookup-device", "sw01", "--json"])

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        assert json.loads(result.stdout) == {"hostname": "sw01", "interfaces": False}

    def test_positional_plus_option_mix(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp, cli_name="lookup-device")
        def lookup_device(
            hostname: Annotated[str, typer.Argument()],
            include_interfaces: bool = False,
        ) -> dict:
            """Resolve a hostname/IP to a NetBox device."""
            return {"hostname": hostname, "interfaces": include_interfaces}

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/netbox-mcp")
        result = runner.invoke(app, ["lookup-device", "sw01", "--include-interfaces", "--json"])

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        assert json.loads(result.stdout) == {"hostname": "sw01", "interfaces": True}

    def test_multiple_positionals(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp, cli_name="get")
        def get_obj(
            object_type: Annotated[str, typer.Argument()],
            object_id: Annotated[int, typer.Argument()],
            fields: str | None = None,
        ) -> dict:
            """Get an object by type and id."""
            return {"type": object_type, "id": object_id, "fields": fields}

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/netbox-mcp")
        result = runner.invoke(app, ["get", "dcim.device", "42", "--json"])

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        # int positional is coerced by Typer/Click, just like an int option.
        assert json.loads(result.stdout) == {
            "type": "dcim.device",
            "id": 42,
            "fields": None,
        }

    def test_optional_positional_via_python_default(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp, cli_name="types")
        def list_types(query: Annotated[str, typer.Argument(help="filter")] = "") -> dict:
            """List supported types, optionally filtered."""
            return {"query": query}

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/netbox-mcp")

        omitted = runner.invoke(app, ["types", "--json"])
        assert omitted.exit_code == 0, f"stderr: {omitted.stderr}"
        assert json.loads(omitted.stdout) == {"query": ""}

        given = runner.invoke(app, ["types", "dcim", "--json"])
        assert given.exit_code == 0, f"stderr: {given.stderr}"
        assert json.loads(given.stdout) == {"query": "dcim"}

    def test_help_shows_positional_in_usage(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp, cli_name="lookup-device")
        def lookup_device(
            hostname: Annotated[str, typer.Argument(help="Device hostname or IP.")],
        ) -> dict:
            """Resolve a hostname/IP to a NetBox device."""
            return {"hostname": hostname}

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/netbox-mcp")
        result = runner.invoke(app, ["lookup-device", "--help"])

        assert result.exit_code == 0
        clean = _strip_ansi(result.stdout)
        # Positional appears (uppercase) in the usage line / Arguments table,
        # NOT as a --hostname option.
        assert "HOSTNAME" in clean
        assert "--hostname" not in clean

    def test_mcp_schema_unchanged_by_argument_marker(self, mcp: FastMCP) -> None:
        """CRITICAL invariant: the Argument marker must NOT alter the MCP schema."""

        @dual_mode_tool(mcp, name="lookup_device", cli_name="lookup-device")
        def lookup_device(
            hostname: Annotated[str, typer.Argument(help="Device hostname or IP.")],
            include_interfaces: bool = False,
        ) -> dict:
            """Resolve a hostname/IP to a NetBox device."""
            return {"hostname": hostname, "interfaces": include_interfaces}

        schema = _tool_input_schema(mcp, "lookup_device")
        # hostname remains a normal required string — the Typer marker leaks nothing.
        assert schema["properties"]["hostname"] == {"type": "string"}
        assert "hostname" in schema.get("required", [])

    def test_mcp_schema_matches_plain_str_equivalent(self, mcp: FastMCP) -> None:
        """The Argument-marked schema must equal the plain ``hostname: str`` schema."""

        @dual_mode_tool(mcp, name="with_argument", cli_name="with-argument")
        def with_argument(
            hostname: Annotated[str, typer.Argument(help="Device hostname or IP.")],
            include_interfaces: bool = False,
        ) -> dict:
            """Resolve (positional CLI projection)."""
            return {"hostname": hostname}

        plain = FastMCP("netbox")
        try:

            @dual_mode_tool(plain, name="with_argument", cli_name="with-argument")
            def plain_str(
                hostname: str,
                include_interfaces: bool = False,
            ) -> dict:
                """Resolve (plain str)."""
                return {"hostname": hostname}

            argument_schema = _tool_input_schema(mcp, "with_argument")
            plain_schema = _tool_input_schema(plain, "with_argument")
            assert argument_schema["properties"] == plain_schema["properties"]
            assert argument_schema.get("required") == plain_schema.get("required")
        finally:
            _clear(plain)


class TestPositionalStrLiteralEndToEnd:
    """#110: a ``str``-``Literal`` positional still works end-to-end.

    The decoration-time guard rejects non-``str``-``Literal`` / model positionals
    (see ``test_dual_mode_decorator.py::TestPositionalTypeFailFast``); this
    confirms the supported case is unaffected.
    """

    def test_str_literal_positional_invocation(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp, cli_name="select")
        def select(mode: Annotated[Literal["fast", "slow"], typer.Argument()]) -> dict:
            """Pick a mode positionally."""
            return {"mode": mode}

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/netbox-mcp")

        ok = runner.invoke(app, ["select", "fast", "--json"])
        assert ok.exit_code == 0, f"stderr: {ok.stderr}"
        assert json.loads(ok.stdout) == {"mode": "fast"}

        bad = runner.invoke(app, ["select", "bogus"])
        assert bad.exit_code != 0


class TestSubgroupSuggestions:
    """#110: subgroups inherit ``SuggestingTyperGroup``.

    An unknown command *inside a subgroup* now gets the same typo suggestions
    and ``--json`` structured-error mode as the top-level app (previously
    subgroups fell back to Click's plain error, undercutting #100 for any MCP
    using ``cli_group``).
    """

    def _grouped_app(self, mcp: FastMCP) -> typer.Typer:
        @dual_mode_tool(mcp, cli_group="devices", cli_name="lookup-device")
        def lookup_device(hostname: str) -> dict:
            """Look up a device under the devices/ subgroup."""
            return {"hostname": hostname}

        return build_cli_from_mcp(mcp, project_repo="togethercomputer/netbox-mcp")

    def test_unknown_subcommand_json_error_mode(self, mcp: FastMCP, runner: CliRunner) -> None:
        app = self._grouped_app(mcp)
        result = runner.invoke(app, ["devices", "lookpu-device", "--json"])

        assert result.exit_code != 0
        payload = json.loads(result.stderr)
        assert payload["error"] == "No such command 'lookpu-device'."
        assert "lookup-device" in payload["suggestions"]
        assert "lookup-device" in payload["available_commands"]

    def test_unknown_subcommand_human_suggestions(self, mcp: FastMCP, runner: CliRunner) -> None:
        app = self._grouped_app(mcp)
        result = runner.invoke(app, ["devices", "lookpu-device"])

        assert result.exit_code != 0
        clean = _strip_ansi(result.stderr)
        assert "Did you mean" in clean
        assert "lookup-device" in clean


class TestTopLevelDictParameter:
    """#111: a top-level ``dict`` param is synthesizable via ``--<name>-json``.

    Mirrors awx-mcp's ``awx_list_resources(filters: dict)`` — Typer rejects a
    bare dict, so the CLI exposes a single ``--<name>-json`` blob parsed with
    ``json.loads`` while the MCP tool keeps the native dict parameter.
    """

    def test_required_dict_via_json_blob(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp, cli_name="list-resources")
        def list_resources(filters: dict) -> dict:
            """List with a dict filter."""
            return {"filters": filters}

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/awx-mcp")
        result = runner.invoke(
            app,
            ["list-resources", "--filters-json", '{"status": "active", "n": 2}', "--json"],
        )

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        assert json.loads(result.stdout) == {"filters": {"status": "active", "n": 2}}

    def test_parameterized_dict_via_json_blob(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp, cli_name="counts")
        def counts(values: dict[str, int]) -> dict:
            """Sum dict values."""
            return {"total": sum(values.values())}

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/awx-mcp")
        result = runner.invoke(app, ["counts", "--values-json", '{"a": 1, "b": 2}', "--json"])

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        assert json.loads(result.stdout) == {"total": 3}

    def test_optional_dict_omitted_uses_default(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp, cli_name="search")
        def search(filters: dict | None = None) -> dict:
            """Optional dict filter."""
            return {"filters": filters}

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/awx-mcp")
        result = runner.invoke(app, ["search", "--json"])

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        assert json.loads(result.stdout) == {"filters": None}

    def test_optional_dict_provided(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp, cli_name="search")
        def search(filters: dict | None = None) -> dict:
            return {"filters": filters}

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/awx-mcp")
        result = runner.invoke(app, ["search", "--filters-json", '{"q": "x"}', "--json"])

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        assert json.loads(result.stdout) == {"filters": {"q": "x"}}

    def test_invalid_json_errors(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp, cli_name="list-resources")
        def list_resources(filters: dict) -> dict:
            return {"filters": filters}

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/awx-mcp")
        result = runner.invoke(app, ["list-resources", "--filters-json", "{not json"])

        assert result.exit_code != 0

    def test_non_object_json_errors(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp, cli_name="list-resources")
        def list_resources(filters: dict) -> dict:
            return {"filters": filters}

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/awx-mcp")
        result = runner.invoke(app, ["list-resources", "--filters-json", "[1, 2]"])

        assert result.exit_code != 0

    def test_mcp_schema_keeps_dict_param(self, mcp: FastMCP) -> None:
        @dual_mode_tool(mcp, name="list_resources", cli_name="list-resources")
        def list_resources(filters: dict) -> dict:
            """List with a dict filter."""
            return {"filters": filters}

        schema = _tool_input_schema(mcp, "list_resources")
        # The CLI --json escape hatch must not leak into the MCP input schema.
        assert "filters" in schema["properties"]


class TestListLiteralParameter:
    """#111: ``list[Literal[...]]`` renders as a multi-value choice.

    Typer cannot render it natively (``AssertionError: List types with complex
    sub-types``); the framework coerces each repeated token to the literal's
    homogeneous scalar and validates membership. Mirrors awx-mcp's
    ``awx_parse_job_log(sections: list[Literal[...]])``.
    """

    def test_list_str_literal_collected(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp, cli_name="parse-log")
        def parse_log(sections: list[Literal["header", "body", "footer"]]) -> dict:
            """Parse selected sections."""
            return {"sections": sections}

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/awx-mcp")
        result = runner.invoke(
            app, ["parse-log", "--sections", "header", "--sections", "body", "--json"]
        )

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        assert json.loads(result.stdout) == {"sections": ["header", "body"]}

    def test_list_str_literal_rejects_invalid(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp, cli_name="parse-log")
        def parse_log(sections: list[Literal["header", "body"]]) -> dict:
            return {"sections": sections}

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/awx-mcp")
        result = runner.invoke(app, ["parse-log", "--sections", "bogus"])

        assert result.exit_code != 0

    def test_list_int_literal_collected(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp, cli_name="pick-levels")
        def pick_levels(levels: list[Literal[1, 2, 3]]) -> dict:
            """Pick levels."""
            return {"levels": levels}

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/awx-mcp")
        result = runner.invoke(app, ["pick-levels", "--levels", "1", "--levels", "3", "--json"])

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        assert json.loads(result.stdout) == {"levels": [1, 3]}

    def test_list_int_literal_rejects_out_of_set(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp, cli_name="pick-levels")
        def pick_levels(levels: list[Literal[1, 2, 3]]) -> dict:
            return {"levels": levels}

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/awx-mcp")
        result = runner.invoke(app, ["pick-levels", "--levels", "9"])

        assert result.exit_code != 0

    def test_list_literal_empty_default(self, mcp: FastMCP, runner: CliRunner) -> None:
        @dual_mode_tool(mcp, cli_name="parse-log")
        def parse_log(sections: list[Literal["a", "b"]]) -> dict:
            return {"sections": sections}

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/awx-mcp")
        result = runner.invoke(app, ["parse-log", "--json"])

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        assert json.loads(result.stdout) == {"sections": []}


class TestSyncGuardOverAsyncTool:
    """#112: a sync decorator wrapping an async tool gets a clear, actionable error.

    ``inspect.iscoroutinefunction`` doesn't follow ``__wrapped__``, so a naive
    sync guard over an async tool looks sync but returns an un-awaited coroutine.
    The dispatcher detects the wrapped coroutine function and points at the
    async-aware-decorator fix rather than the generic "declare async def".
    """

    def test_sync_guard_over_async_gives_clear_error(self, mcp: FastMCP, runner: CliRunner) -> None:
        def broken_sync_guard(fn):
            # BROKEN: a sync wrapper around an async fn (no coroutine branch). It
            # looks sync but returns an un-awaited coroutine — the #112 footgun.
            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                return fn(*args, **kwargs)

            return wrapper

        @dual_mode_tool(mcp, cli_name="lookup")
        @broken_sync_guard
        async def lookup(host: str) -> dict:
            return {"host": host}

        app = build_cli_from_mcp(mcp, project_repo="togethercomputer/test")
        result = runner.invoke(app, ["lookup", "--host", "x"])

        assert result.exit_code != 0
        assert isinstance(result.exception, RuntimeError)
        msg = str(result.exception)
        assert "wraps an async tool" in msg
        assert "async-aware" in msg
