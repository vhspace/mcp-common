"""Tests for ``mcp_common.dual_mode.dual_mode_tool`` decorator + registry."""

from __future__ import annotations

from unittest.mock import MagicMock

import pydantic
import pytest
from fastmcp import FastMCP

from mcp_common.dual_mode import dual_mode_tool, tool_cli_subcommands
from mcp_common.dual_mode._naming import (
    derive_cli_name,
    strip_mcp_namespace,
    to_kebab_case,
)
from mcp_common.dual_mode._registry import _clear, get_tools


@pytest.fixture
def mcp() -> FastMCP:
    """Fresh FastMCP instance per test so registry state never leaks."""
    instance = FastMCP("netbox")
    yield instance
    _clear(instance)


class _WithSetField(pydantic.BaseModel):
    """Module-level fixture so PEP 563 forward refs resolve cleanly."""

    tags: set[str]
    name: str


class TestDecoratorReturnsFunctionUnchanged:
    def test_decorator_returns_original_callable(self, mcp: FastMCP) -> None:
        @dual_mode_tool(mcp)
        def my_tool(x: int) -> int:
            return x + 1

        assert my_tool(3) == 4

    def test_decorator_preserves_metadata_attrs(self, mcp: FastMCP) -> None:
        @dual_mode_tool(mcp)
        def doc_tool(x: int) -> int:
            """Short summary line.

            Longer description.
            """
            return x

        assert doc_tool.__name__ == "doc_tool"
        assert "Short summary line." in (doc_tool.__doc__ or "")


class TestFastMcpRegistration:
    def test_decorator_calls_mcp_tool(self, mcp: FastMCP) -> None:
        spy = MagicMock(wraps=mcp.tool)
        mcp.tool = spy  # type: ignore[method-assign]

        @dual_mode_tool(mcp)
        def my_tool(x: int) -> int:
            return x

        assert spy.called
        kwargs = spy.call_args.kwargs
        assert kwargs.get("name") == "my_tool"

    def test_decorator_uses_explicit_name(self, mcp: FastMCP) -> None:
        spy = MagicMock(wraps=mcp.tool)
        mcp.tool = spy  # type: ignore[method-assign]

        @dual_mode_tool(mcp, name="custom_tool_name")
        def my_tool(x: int) -> int:
            return x

        kwargs = spy.call_args.kwargs
        assert kwargs.get("name") == "custom_tool_name"

    def test_decorator_passes_extra_kwargs_to_mcp_tool(self, mcp: FastMCP) -> None:
        spy = MagicMock(wraps=mcp.tool)
        mcp.tool = spy  # type: ignore[method-assign]

        @dual_mode_tool(mcp, annotations={"readOnlyHint": True}, tags={"netbox"})
        def my_tool(x: int) -> int:
            return x

        kwargs = spy.call_args.kwargs
        assert kwargs.get("annotations") == {"readOnlyHint": True}
        assert kwargs.get("tags") == {"netbox"}

    def test_first_docstring_line_becomes_description(self, mcp: FastMCP) -> None:
        spy = MagicMock(wraps=mcp.tool)
        mcp.tool = spy  # type: ignore[method-assign]

        @dual_mode_tool(mcp)
        def documented(x: int) -> int:
            """Look up a device by ID.

            Extended details that should not be in the description.
            """
            return x

        kwargs = spy.call_args.kwargs
        assert kwargs.get("description") == "Look up a device by ID."

    def test_explicit_summary_overrides_docstring(self, mcp: FastMCP) -> None:
        spy = MagicMock(wraps=mcp.tool)
        mcp.tool = spy  # type: ignore[method-assign]

        @dual_mode_tool(mcp, summary="Explicit summary.")
        def documented(x: int) -> int:
            """Docstring line that should be ignored."""
            return x

        kwargs = spy.call_args.kwargs
        assert kwargs.get("description") == "Explicit summary."


class TestCliOnlyFlag:
    def test_cli_only_skips_mcp_tool_registration(self, mcp: FastMCP) -> None:
        spy = MagicMock(wraps=mcp.tool)
        mcp.tool = spy  # type: ignore[method-assign]

        @dual_mode_tool(mcp, cli_only=True)
        def my_tool(x: int) -> int:
            return x

        assert not spy.called

    def test_cli_only_still_appears_in_registry(self, mcp: FastMCP) -> None:
        @dual_mode_tool(mcp, cli_only=True)
        def my_tool(x: int) -> int:
            return x

        registered = get_tools(mcp)
        assert [t.tool_name for t in registered] == ["my_tool"]
        assert registered[0].cli_only is True


class TestMcpOnlyFlag:
    def test_mcp_only_still_registers_with_mcp(self, mcp: FastMCP) -> None:
        spy = MagicMock(wraps=mcp.tool)
        mcp.tool = spy  # type: ignore[method-assign]

        @dual_mode_tool(mcp, mcp_only=True)
        def my_tool(x: int) -> int:
            return x

        assert spy.called

    def test_mcp_only_metadata_records_flag(self, mcp: FastMCP) -> None:
        @dual_mode_tool(mcp, mcp_only=True)
        def my_tool(x: int) -> int:
            return x

        registered = get_tools(mcp)
        assert registered[0].mcp_only is True


class TestCliOnlyAndMcpOnlyRejected:
    def test_both_flags_raise_value_error(self) -> None:
        mcp = FastMCP("test")
        with pytest.raises(ValueError, match="mutually exclusive"):

            @dual_mode_tool(mcp, cli_only=True, mcp_only=True)
            def my_tool(x: int) -> int:
                return x


class TestRegistry:
    def test_registry_records_tool_metadata(self, mcp: FastMCP) -> None:
        @dual_mode_tool(mcp, cli_name="custom-name", cli_group="devices")
        def my_tool(x: int) -> int:
            """First line."""
            return x

        registered = get_tools(mcp)
        assert len(registered) == 1
        meta = registered[0]
        assert meta.tool_name == "my_tool"
        assert meta.cli_name == "custom-name"
        assert meta.cli_group == "devices"
        assert meta.summary == "First line."
        assert meta.fn is my_tool

    def test_registry_preserves_decoration_order(self, mcp: FastMCP) -> None:
        @dual_mode_tool(mcp)
        def alpha(x: int) -> int:
            return x

        @dual_mode_tool(mcp)
        def beta(x: int) -> int:
            return x

        @dual_mode_tool(mcp)
        def gamma(x: int) -> int:
            return x

        names = [meta.tool_name for meta in get_tools(mcp)]
        assert names == ["alpha", "beta", "gamma"]

    def test_two_mcp_instances_have_independent_registries(self) -> None:
        mcp_a = FastMCP("a")
        mcp_b = FastMCP("b")

        @dual_mode_tool(mcp_a)
        def on_a(x: int) -> int:
            return x

        @dual_mode_tool(mcp_b)
        def on_b(x: int) -> int:
            return x

        assert [m.tool_name for m in get_tools(mcp_a)] == ["on_a"]
        assert [m.tool_name for m in get_tools(mcp_b)] == ["on_b"]

        _clear(mcp_a)
        _clear(mcp_b)


class TestCliNameDerivation:
    """Verify default ``cli_name`` strips the MCP namespace and kebab-cases."""

    def test_strip_namespace_with_underscore_prefix(self) -> None:
        assert strip_mcp_namespace("netbox_lookup_device", "netbox") == "lookup_device"

    def test_strip_namespace_with_kebab_mcp_name(self) -> None:
        assert strip_mcp_namespace("netbox_mcp_lookup_device", "netbox-mcp") == "lookup_device"

    def test_strip_namespace_no_prefix(self) -> None:
        assert strip_mcp_namespace("lookup_device", "netbox") == "lookup_device"

    def test_strip_namespace_case_insensitive(self) -> None:
        assert strip_mcp_namespace("NetBox_Lookup", "netbox") == "Lookup"

    def test_to_kebab_case_from_snake(self) -> None:
        assert to_kebab_case("lookup_device") == "lookup-device"

    def test_to_kebab_case_from_camel(self) -> None:
        assert to_kebab_case("lookupDevice") == "lookup-device"

    def test_to_kebab_case_passes_through(self) -> None:
        assert to_kebab_case("lookup-device") == "lookup-device"

    def test_derive_cli_name_strips_and_kebabs(self) -> None:
        assert derive_cli_name("netbox_lookup_device", "netbox") == "lookup-device"

    def test_decorator_default_cli_name(self, mcp: FastMCP) -> None:
        @dual_mode_tool(mcp)
        def netbox_lookup_device(hostname: str) -> str:
            return hostname

        meta = get_tools(mcp)[0]
        assert meta.cli_name == "lookup-device"

    def test_decorator_explicit_cli_name_wins(self, mcp: FastMCP) -> None:
        @dual_mode_tool(mcp, cli_name="custom-name")
        def some_tool(x: int) -> int:
            return x

        meta = get_tools(mcp)[0]
        assert meta.cli_name == "custom-name"


class TestUnsupportedAnnotationsRejected:
    """Bug4: ``set[T]`` and non-Optional ``Union[T, U]`` must fail at decoration."""

    def test_set_annotation_raises(self, mcp: FastMCP) -> None:
        with pytest.raises(TypeError, match="set/frozenset"):

            @dual_mode_tool(mcp)
            def fn(s: set[str]) -> dict:
                return {"s": list(s)}

    def test_frozenset_annotation_raises(self, mcp: FastMCP) -> None:
        with pytest.raises(TypeError, match="set/frozenset"):

            @dual_mode_tool(mcp)
            def fn(s: frozenset[str]) -> dict:
                return {"s": list(s)}

    def test_non_optional_union_raises(self, mcp: FastMCP) -> None:
        with pytest.raises(TypeError, match="non-Optional"):

            @dual_mode_tool(mcp)
            def fn(x: int | str) -> dict:
                return {"x": x}

    def test_optional_union_still_allowed(self, mcp: FastMCP) -> None:
        # Optional[T] ≡ T | None is allowed because the decorator unwraps it.
        @dual_mode_tool(mcp)
        def fn(x: int | None = None) -> dict:
            return {"x": x}

        assert fn.__name__ == "fn"

    def test_pydantic_with_set_field_via_complex_fallback(self, mcp: FastMCP) -> None:
        """Pydantic models with set fields are still allowed at the top level —
        the flattening path routes them through ``--<field>-json`` rather than
        attempting to surface them as primitive Typer options."""

        @dual_mode_tool(mcp)
        def fn(payload: _WithSetField) -> dict:
            return payload.model_dump(mode="json")

        # Decoration succeeds — the model is a Pydantic param, not a raw set[str] param.
        assert get_tools(mcp)[0].fn is fn


class TestReservedParameterName:
    """Bug5: a function parameter named ``json`` collides with the CLI flag."""

    def test_json_parameter_raises(self, mcp: FastMCP) -> None:
        with pytest.raises(ValueError, match="collides with the synthetic CLI"):

            @dual_mode_tool(mcp)
            def fn(json: bool = False) -> dict:
                return {"json": json}


class TestCliNameValidation:
    """Bug7: ``cli_name`` validation closes the unreachable-command gaps."""

    def test_duplicate_cli_name_raises(self, mcp: FastMCP) -> None:
        @dual_mode_tool(mcp, cli_name="lookup")
        def first() -> dict:
            return {}

        with pytest.raises(ValueError, match="already registered"):

            @dual_mode_tool(mcp, cli_name="lookup")
            def second() -> dict:
                return {}

    def test_whitespace_cli_name_raises(self, mcp: FastMCP) -> None:
        with pytest.raises(ValueError, match="invalid"):

            @dual_mode_tool(mcp, cli_name="lookup device")
            def fn() -> dict:
                return {}

    def test_uppercase_cli_name_raises(self, mcp: FastMCP) -> None:
        with pytest.raises(ValueError, match="invalid"):

            @dual_mode_tool(mcp, cli_name="LookupDevice")
            def fn() -> dict:
                return {}

    def test_underscore_in_cli_name_raises(self, mcp: FastMCP) -> None:
        with pytest.raises(ValueError, match="invalid"):

            @dual_mode_tool(mcp, cli_name="lookup_device")
            def fn() -> dict:
                return {}

    def test_leading_underscore_function_default_strips(self, mcp: FastMCP) -> None:
        """A tool named ``_private_thing`` defaults to ``private-thing`` —
        not ``-private-thing`` (which Click would parse as a flag)."""

        @dual_mode_tool(mcp)
        def _private_thing() -> dict:
            """Internal-flavored tool."""
            return {}

        meta = get_tools(mcp)[0]
        assert meta.cli_name == "private-thing"

    def test_mcp_only_does_not_collide(self, mcp: FastMCP) -> None:
        """``mcp_only=True`` tools never reach the CLI registry, so their
        ``cli_name`` is irrelevant — they must not block a later CLI tool
        from claiming the same name."""

        @dual_mode_tool(mcp, mcp_only=True, cli_name="lookup")
        def hidden() -> dict:
            return {}

        @dual_mode_tool(mcp, cli_name="lookup")
        def visible() -> dict:
            return {}

        names = [t.tool_name for t in get_tools(mcp)]
        assert "hidden" in names and "visible" in names


class TestCliAliases:
    """#133: declarable canonical CLI subcommand + aliases for eval scoring."""

    def test_default_no_aliases(self, mcp: FastMCP) -> None:
        @dual_mode_tool(mcp)
        def netbox_lookup_device(hostname: str) -> str:
            return hostname

        assert get_tools(mcp)[0].cli_aliases == ()

    def test_aliases_recorded_in_metadata(self, mcp: FastMCP) -> None:
        @dual_mode_tool(mcp, cli_name="list", cli_aliases=("search", "devices"))
        def netbox_get_objects(q: str) -> dict:
            return {}

        assert get_tools(mcp)[0].cli_aliases == ("search", "devices")

    def test_aliases_allowed_on_mcp_only(self, mcp: FastMCP) -> None:
        # the MCP tool's CLI form lives in separate commands; declare them anyway
        @dual_mode_tool(mcp, mcp_only=True, cli_aliases=("list", "search"))
        def netbox_get_objects(q: str) -> dict:
            return {}

        assert get_tools(mcp)[0].cli_aliases == ("list", "search")

    def test_alias_equal_to_cli_name_is_dropped(self, mcp: FastMCP) -> None:
        @dual_mode_tool(mcp, cli_name="list", cli_aliases=("list", "search"))
        def netbox_get_objects(q: str) -> dict:
            return {}

        assert get_tools(mcp)[0].cli_aliases == ("search",)

    def test_duplicate_aliases_collapsed_in_order(self, mcp: FastMCP) -> None:
        @dual_mode_tool(mcp, cli_aliases=("search", "devices", "search"))
        def netbox_get_objects(q: str) -> dict:
            return {}

        assert get_tools(mcp)[0].cli_aliases == ("search", "devices")

    def test_invalid_alias_shape_raises(self, mcp: FastMCP) -> None:
        with pytest.raises(ValueError, match="cli_alias"):

            @dual_mode_tool(mcp, cli_aliases=("Not Valid",))
            def fn() -> dict:
                return {}

    def test_non_string_alias_raises(self, mcp: FastMCP) -> None:
        with pytest.raises(ValueError, match="must be strings"):

            @dual_mode_tool(mcp, cli_aliases=("ok", 123))  # type: ignore[arg-type]
            def fn() -> dict:
                return {}


class TestToolCliSubcommands:
    """#133: ``tool_cli_subcommands`` bridges declared aliases to the scorer."""

    def test_maps_tool_to_cli_name(self, mcp: FastMCP) -> None:
        @dual_mode_tool(mcp)
        def netbox_lookup_device(hostname: str) -> str:
            return hostname

        assert tool_cli_subcommands(mcp) == {"netbox_lookup_device": ["lookup-device"]}

    def test_includes_aliases_after_cli_name(self, mcp: FastMCP) -> None:
        @dual_mode_tool(mcp, cli_name="list", cli_aliases=("search", "devices"))
        def netbox_get_objects(q: str) -> dict:
            return {}

        assert tool_cli_subcommands(mcp) == {"netbox_get_objects": ["list", "search", "devices"]}

    def test_includes_mcp_only_tools(self, mcp: FastMCP) -> None:
        @dual_mode_tool(mcp, mcp_only=True, cli_aliases=("list", "search"))
        def netbox_get_objects(q: str) -> dict:
            return {}

        mapping = tool_cli_subcommands(mcp)
        assert mapping["netbox_get_objects"] == ["get-objects", "list", "search"]

    def test_empty_registry(self, mcp: FastMCP) -> None:
        assert tool_cli_subcommands(mcp) == {}

    def test_multiple_tools(self, mcp: FastMCP) -> None:
        @dual_mode_tool(mcp, cli_name="list", cli_aliases=("search",))
        def netbox_get_objects(q: str) -> dict:
            return {}

        @dual_mode_tool(mcp)
        def netbox_lookup_device(hostname: str) -> str:
            return hostname

        assert tool_cli_subcommands(mcp) == {
            "netbox_get_objects": ["list", "search"],
            "netbox_lookup_device": ["lookup-device"],
        }


class TestMcpOnlySkipsTyperParamValidation:
    """#138: ``mcp_only=True`` tools skip the Typer-parameter validation.

    Such tools are never rendered as Typer/CLI commands, so a ``dict`` param or
    a non-Optional union (e.g. ``netbox_get_objects``'s ``filters`` / ``ordering``)
    must not block decoration — they can then carry ``cli_aliases`` for the
    scorer mapping instead of needing the netbox-mcp#125 workaround. Full
    validation still applies to real dual-mode (CLI-rendered) tools.
    """

    def test_mcp_only_dict_param_decorates(self, mcp: FastMCP) -> None:
        @dual_mode_tool(mcp, mcp_only=True)
        def fn(filters: dict) -> dict:
            return filters

        assert get_tools(mcp)[0].fn is fn

    def test_mcp_only_non_optional_union_param_decorates(self, mcp: FastMCP) -> None:
        @dual_mode_tool(mcp, mcp_only=True)
        def fn(ordering: str | list[str]) -> dict:
            return {"ordering": ordering}

        assert get_tools(mcp)[0].fn is fn

    def test_mcp_only_set_param_decorates(self, mcp: FastMCP) -> None:
        @dual_mode_tool(mcp, mcp_only=True)
        def fn(tags: set[str]) -> dict:
            return {"tags": sorted(tags)}

        assert get_tools(mcp)[0].fn is fn

    def test_mcp_only_reserved_json_param_decorates(self, mcp: FastMCP) -> None:
        # the synthetic ``--json`` flag only exists for CLI-rendered tools, so
        # an mcp_only tool may take a parameter literally named ``json``.
        @dual_mode_tool(mcp, mcp_only=True)
        def fn(json: bool = False) -> dict:
            return {"json": json}

        assert get_tools(mcp)[0].fn is fn

    def test_mcp_only_typer_incompatible_tool_carries_cli_aliases(self, mcp: FastMCP) -> None:
        # mirrors netbox_get_objects: dict ``filters`` + non-Optional union
        # ``ordering`` (the exact shapes that raised at decoration), now decorating
        # with mcp_only=True and exposing its aliases via tool_cli_subcommands.
        @dual_mode_tool(mcp, mcp_only=True, cli_aliases=("list", "search", "devices"))
        def netbox_get_objects(
            object_type: str,
            filters: dict | None = None,
            ordering: str | list[str] | None = None,
        ) -> dict:
            """List/search NetBox objects."""
            return {}

        meta = get_tools(mcp)[0]
        assert meta.fn is netbox_get_objects
        assert meta.mcp_only is True
        assert meta.cli_aliases == ("list", "search", "devices")
        assert tool_cli_subcommands(mcp) == {
            "netbox_get_objects": ["get-objects", "list", "search", "devices"],
        }

    def test_non_mcp_only_non_optional_union_still_raises(self, mcp: FastMCP) -> None:
        # the same union param on a real dual-mode tool still fails fast.
        with pytest.raises(TypeError, match="non-Optional"):

            @dual_mode_tool(mcp, cli_aliases=("list",))
            def netbox_get_objects(
                object_type: str,
                ordering: str | list[str] | None = None,
            ) -> dict:
                return {}

    def test_non_mcp_only_set_param_still_raises(self, mcp: FastMCP) -> None:
        with pytest.raises(TypeError, match="set/frozenset"):

            @dual_mode_tool(mcp)
            def fn(tags: set[str]) -> dict:
                return {"tags": sorted(tags)}

    def test_non_mcp_only_reserved_json_param_still_raises(self, mcp: FastMCP) -> None:
        with pytest.raises(ValueError, match="collides with the synthetic CLI"):

            @dual_mode_tool(mcp)
            def fn(json: bool = False) -> dict:
                return {"json": json}
