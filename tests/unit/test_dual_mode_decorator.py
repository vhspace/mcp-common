"""Tests for ``mcp_common.dual_mode.dual_mode_tool`` decorator + registry."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastmcp import FastMCP

from mcp_common.dual_mode import dual_mode_tool
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
