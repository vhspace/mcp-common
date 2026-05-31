"""MCP↔CLI parity tests for the @dual_mode_tool migration.

Each migrated tool is invoked twice:

1. **MCP surface** — through an in-memory ``fastmcp.Client`` so the
   FastMCP-side registration (input-schema validation, JSON envelope,
   structured output) is exercised end-to-end.
2. **CLI surface** — through ``typer.testing.CliRunner`` against the
   Typer command synthesized by ``mcp_common.dual_mode.build_cli_from_mcp``.

The same NetBox client mock backs both invocations so we can assert
JSON-mode outputs are identical and human-mode outputs are sensible.
This is the contract the demo migration claims; if it ever drifts, this
file fails first.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastmcp import Client
from typer.testing import CliRunner

from netbox_mcp import cli, server
from netbox_mcp.cli import app
from netbox_mcp.server import mcp

runner = CliRunner(mix_stderr=False)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def patched_netbox(monkeypatch: pytest.MonkeyPatch) -> Iterator[MagicMock]:
    """Replace ``server.netbox`` with a MagicMock visible to both surfaces.

    The dual-mode framework calls the original Python function for both
    MCP and CLI invocations, so a single mock backs both paths. We also
    patch ``cli._client`` so any incidental CLI-only command doesn't try
    to build a real :class:`NetBoxRestClient`.
    """
    fake = MagicMock(name="netbox-client")
    monkeypatch.setattr(server, "netbox", fake)
    monkeypatch.setattr(cli, "_client", lambda: fake)
    yield fake


def _device_record(
    *,
    device_id: int = 42,
    name: str = "gpu-node-01",
    primary_ip4: str = "10.20.30.40/24",
    oob_ip: str = "192.168.196.12/24",
    site_name: str = "ORI-TX",
    status: str = "active",
    provider_id: str | None = "GPU-39",
) -> dict[str, Any]:
    """Build a minimal-but-realistic NetBox device record for tests."""
    return {
        "id": device_id,
        "name": name,
        "status": {"value": status, "label": status.capitalize()},
        "site": {"id": 1, "name": site_name, "slug": site_name.lower()},
        "rack": {"id": 10, "name": "R01"},
        "device_role": {"id": 2, "name": "GPU Node"},
        "device_type": {"id": 5, "name": "DGX H100"},
        "serial": "SN123456",
        "primary_ip4": {"id": 100, "address": primary_ip4, "family": 4},
        "primary_ip6": None,
        "oob_ip": {"id": 200, "address": oob_ip, "family": 4},
        "custom_fields": ({"Provider_Machine_ID": provider_id} if provider_id else {}),
    }


def _paginated(*results: dict[str, Any]) -> dict[str, Any]:
    return {
        "count": len(results),
        "next": None,
        "previous": None,
        "results": list(results),
    }


async def _mcp_call(tool_name: str, **kwargs: Any) -> Any:
    """Invoke ``tool_name`` via the in-memory MCP client.

    Returns ``result.structured_content`` (FastMCP's normalized JSON
    envelope) so JSON shape comparisons are direct. Falls back to the
    raw text content when no structured envelope is provided (FastMCP
    omits it for tools whose return value is a non-dict scalar).
    """
    async with Client(mcp) as client:
        result = await client.call_tool(tool_name, kwargs)
    if result.structured_content is not None:
        return result.structured_content
    if result.content:
        item = result.content[0]
        return getattr(item, "text", item)
    return None


def _cli_json(*args: str) -> dict[str, Any] | list[Any]:
    """Invoke the netbox-cli app with ``--json`` and parse stdout."""
    result = runner.invoke(app, [*args, "--json"])
    assert result.exit_code == 0, result.stderr or result.output
    return json.loads(result.output)


# ---------------------------------------------------------------------------
# netbox_lookup_device → lookup-device
# ---------------------------------------------------------------------------


class TestLookupDeviceParity:
    """Read-only ``lookup-device`` migration: MCP and CLI must agree.

    Covers the ``str`` (required) + ``Optional[str]`` + ``list[str] | None``
    parameter mix, and exercises the case-insensitive name search +
    Provider_Machine_ID fallback paths the framework now drives via the
    synthesized CLI command.
    """

    def test_mcp_and_cli_match_for_basic_lookup(
        self, anyio_backend: str, patched_netbox: MagicMock
    ) -> None:
        import asyncio

        device = _device_record()
        patched_netbox.get.return_value = _paginated(device)

        mcp_result = asyncio.run(_mcp_call("netbox_lookup_device", hostname="gpu-node-01"))
        patched_netbox.reset_mock()
        patched_netbox.get.return_value = _paginated(device)
        cli_result = _cli_json("lookup-device", "gpu-node-01")

        assert mcp_result == cli_result
        assert mcp_result["count"] == 1
        # Pydantic-serialized "results" shape — provider_machine_id and
        # _address fields are enriched by the function body.
        result_device = mcp_result["results"][0]
        assert result_device["primary_ip4_address"] == "10.20.30.40"
        assert result_device["oob_ip_address"] == "192.168.196.12"
        assert result_device["provider_machine_id"] == "GPU-39"

    def test_cli_passes_site_filter_to_netbox(self, patched_netbox: MagicMock) -> None:
        site_resp = _paginated({"id": 5, "name": "ORI-TX"})
        device_resp = _paginated(_device_record(site_name="ORI-TX"))
        patched_netbox.get.side_effect = [site_resp, device_resp]

        result = _cli_json("lookup-device", "gpu-node-01", "--site", "ORI-TX")

        assert result["count"] == 1
        device_call = patched_netbox.get.call_args_list[1]
        assert device_call.kwargs["params"]["site_id"] == 5

    def test_cli_passes_fields_as_repeatable_flag(self, patched_netbox: MagicMock) -> None:
        patched_netbox.get.return_value = _paginated(_device_record())

        result = _cli_json(
            "lookup-device",
            "gpu-node-01",
            "--fields",
            "id",
            "--fields",
            "name",
            "--fields",
            "oob_ip",
        )

        assert result["count"] == 1
        params = patched_netbox.get.call_args.kwargs["params"]
        assert params["fields"] == "id,name,oob_ip"

    def test_cli_human_mode_prints_count_and_query(self, patched_netbox: MagicMock) -> None:
        patched_netbox.get.return_value = _paginated(_device_record())

        result = runner.invoke(app, ["lookup-device", "gpu-node-01"])

        assert result.exit_code == 0, result.stderr
        # Default human formatter is ``str(result)`` which renders the
        # dict shape — adequate for parity verification.
        assert "gpu-node-01" in result.output
        assert "'count': 1" in result.output


# ---------------------------------------------------------------------------
# netbox_get_object_by_id → get-object-by-id
# ---------------------------------------------------------------------------


class TestGetObjectByIdParity:
    """Covers ``str`` + ``int`` (both required) + ``bool`` flag + ``list[str]``."""

    def test_mcp_and_cli_match(self, patched_netbox: MagicMock) -> None:
        import asyncio

        device = _device_record(device_id=42)
        patched_netbox.get.return_value = device

        mcp_result = asyncio.run(
            _mcp_call("netbox_get_object_by_id", object_type="dcim.device", object_id=42)
        )
        patched_netbox.reset_mock()
        patched_netbox.get.return_value = device
        cli_result = _cli_json(
            "get-object-by-id",
            "dcim.device",
            "42",
        )

        assert mcp_result == cli_result
        assert mcp_result["id"] == 42
        assert mcp_result["name"] == "gpu-node-01"

    def test_cli_brief_flag_propagates(self, patched_netbox: MagicMock) -> None:
        patched_netbox.get.return_value = _device_record(device_id=42)

        _cli_json(
            "get-object-by-id",
            "dcim.device",
            "42",
            "--brief",
        )

        assert patched_netbox.get.call_args.kwargs["params"]["brief"] == "1"

    def test_cli_rejects_invalid_object_type(self, patched_netbox: MagicMock) -> None:
        result = runner.invoke(
            app,
            [
                "get-object-by-id",
                "not.a.real.type",
                "1",
            ],
        )
        assert result.exit_code != 0
        # The tool body raises ``ValueError`` (wrapped by mcp_remediation_wrapper
        # into ``ToolError``); under ``CliRunner`` that bypasses the framework's
        # stderr exception handler, so we assert against ``result.exception``
        # rather than captured stderr.
        assert result.exception is not None
        assert "Invalid object_type" in str(result.exception)


# ---------------------------------------------------------------------------
# netbox_oob_summary → oob-summary (Pydantic return + Literal param)
# ---------------------------------------------------------------------------


class TestOobSummaryParity:
    """Covers Pydantic-model return + ``Literal[...]`` parameter mapping.

    Validates the framework's Pydantic-aware JSON encoding (``echo_result``
    routes the model through ``model_dump(mode="json")``) and the Click
    choice rendering for ``--status-filter``.
    """

    def test_mcp_and_cli_match_for_pydantic_return(self, patched_netbox: MagicMock) -> None:
        import asyncio

        device = _device_record(device_id=42, status="active")
        # ``netbox_oob_summary`` calls ``netbox_lookup_device`` internally,
        # so the mock just needs to satisfy the name-search query.
        patched_netbox.get.return_value = _paginated(device)

        mcp_result = asyncio.run(_mcp_call("netbox_oob_summary", hostname="gpu-node-01"))
        patched_netbox.reset_mock()
        patched_netbox.get.return_value = _paginated(device)
        cli_result = _cli_json("oob-summary", "gpu-node-01")

        # Pydantic serialization shape is identical on both sides.
        assert mcp_result == cli_result
        assert mcp_result["id"] == 42
        assert mcp_result["name"] == "gpu-node-01"
        assert mcp_result["status"] == "active"
        assert mcp_result["oob_ip"] == "192.168.196.12"
        assert mcp_result["primary_ip4"] == "10.20.30.40"
        assert mcp_result["provider_machine_id"] == "GPU-39"

    def test_cli_status_filter_literal_accepts_valid_choice(
        self, patched_netbox: MagicMock
    ) -> None:
        device = _device_record(status="active")
        patched_netbox.get.return_value = _paginated(device)

        result = _cli_json(
            "oob-summary",
            "gpu-node-01",
            "--status-filter",
            "active",
        )
        assert result["status"] == "active"

    def test_cli_status_filter_literal_rejects_invalid_choice(
        self, patched_netbox: MagicMock
    ) -> None:
        result = runner.invoke(
            app,
            [
                "oob-summary",
                "gpu-node-01",
                "--status-filter",
                "broken",
            ],
        )
        assert result.exit_code != 0
        # Click's choice-mismatch error mentions the bad value.
        assert "broken" in (result.stderr + result.output).lower()

    def test_status_filter_mismatch_surfaces_user_error(self, patched_netbox: MagicMock) -> None:
        # Device is "active" but caller asks for "planned".
        device = _device_record(status="active")
        patched_netbox.get.return_value = _paginated(device)

        result = runner.invoke(
            app,
            [
                "oob-summary",
                "gpu-node-01",
                "--status-filter",
                "planned",
                "--json",
            ],
        )
        assert result.exit_code != 0
        # ValueError raised in tool body is wrapped by mcp_remediation_wrapper
        # into a ToolError; under CliRunner it surfaces as ``result.exception``.
        assert result.exception is not None
        assert "expected 'planned'" in str(result.exception)


# ---------------------------------------------------------------------------
# Smoke checks on the dual-mode wiring itself
# ---------------------------------------------------------------------------


def test_synthesized_commands_registered_at_top_level() -> None:
    """All migrated tools materialize as top-level CLI commands."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for synthesized_command in (
        "lookup-device",
        "get-object-by-id",
        "oob-summary",
        "get-objects-by-ids",
    ):
        assert synthesized_command in result.output


def test_legacy_commands_removed() -> None:
    """``lookup`` and ``get`` no longer exist on the CLI surface.

    Guard test so a future revert that re-introduces the hand-written
    commands fails loudly.
    """
    result = runner.invoke(app, ["lookup", "anything"])
    assert result.exit_code != 0
    assert "Unknown command 'lookup'" in (result.stderr + result.output) or (
        "No such command 'lookup'" in (result.stderr + result.output)
    )

    result = runner.invoke(app, ["get", "dcim.device", "1"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# netbox_get_objects_by_ids → get-objects-by-ids (newly synthesized)
# ---------------------------------------------------------------------------


class TestGetObjectsByIdsParity:
    """Read-only ``get-objects-by-ids`` migration: MCP and CLI must agree.

    Covers the ``Annotated[str, Argument]`` positional + ``Annotated[list[int],
    Argument]`` variadic positional shape (``get-objects-by-ids TYPE ID ID ...``)
    plus the ``list[str] | None`` / ``bool`` options. The MCP tool keeps an
    identical input schema; the variadic ``ids`` reads naturally on the CLI.
    """

    def test_mcp_and_cli_match(self, patched_netbox: MagicMock) -> None:
        import asyncio

        batch = _paginated(
            _device_record(device_id=1, name="gpu-node-01"),
            _device_record(device_id=2, name="gpu-node-02"),
        )
        patched_netbox.get.return_value = batch

        mcp_result = asyncio.run(
            _mcp_call("netbox_get_objects_by_ids", object_type="dcim.device", ids=[1, 2])
        )
        patched_netbox.reset_mock()
        patched_netbox.get.return_value = batch
        cli_result = _cli_json("get-objects-by-ids", "dcim.device", "1", "2")

        assert mcp_result == cli_result
        assert mcp_result["count"] == 2
        assert [d["id"] for d in mcp_result["results"]] == [1, 2]

    def test_cli_passes_ids_as_repeated_id_filter(self, patched_netbox: MagicMock) -> None:
        patched_netbox.get.return_value = _paginated(
            _device_record(device_id=7), _device_record(device_id=9)
        )

        _cli_json("get-objects-by-ids", "dcim.device", "7", "9")

        # ``id__in`` is normalized to a repeated-key ``id`` list by the body.
        params = patched_netbox.get.call_args.kwargs["params"]
        assert params["id"] == [7, 9]

    def test_cli_brief_flag_propagates(self, patched_netbox: MagicMock) -> None:
        patched_netbox.get.return_value = _paginated(_device_record(device_id=1))

        _cli_json("get-objects-by-ids", "dcim.device", "1", "--brief")

        assert patched_netbox.get.call_args.kwargs["params"]["brief"] == "1"

    def test_cli_human_mode_renders(self, patched_netbox: MagicMock) -> None:
        patched_netbox.get.return_value = _paginated(
            _device_record(device_id=1, name="gpu-node-01")
        )

        result = runner.invoke(app, ["get-objects-by-ids", "dcim.device", "1"])

        assert result.exit_code == 0, result.stderr
        assert "gpu-node-01" in result.output


# ---------------------------------------------------------------------------
# MCP input-schema invariance for Annotated[..., typer.Argument()] params
# ---------------------------------------------------------------------------


def _tool_input_schema(tool_name: str) -> dict[str, Any]:
    """Return the live MCP input schema for ``tool_name`` via an in-memory client."""
    import asyncio

    async def _go() -> dict[str, Any]:
        async with Client(mcp) as client:
            tools = await client.list_tools()
        return {t.name: t.inputSchema for t in tools}[tool_name]

    return asyncio.run(_go())


@pytest.mark.parametrize(
    ("tool_name", "param", "expected_property"),
    [
        ("netbox_lookup_device", "hostname", {"type": "string"}),
        # object_type carries a derived ``enum`` (netbox-mcp#126). The enum is an
        # intentional part of the schema, NOT a leaked Typer marker, so the
        # invariants below (no title/description, still required, closed object)
        # must continue to hold alongside it.
        (
            "netbox_get_object_by_id",
            "object_type",
            {"type": "string", "enum": server._OBJECT_TYPE_ENUM},
        ),
        ("netbox_get_object_by_id", "object_id", {"type": "integer"}),
        (
            "netbox_get_objects_by_ids",
            "object_type",
            {"type": "string", "enum": server._OBJECT_TYPE_ENUM},
        ),
        (
            "netbox_get_objects_by_ids",
            "ids",
            {"type": "array", "items": {"type": "integer"}},
        ),
        ("netbox_oob_summary", "hostname", {"type": "string"}),
    ],
)
def test_argument_marker_does_not_leak_into_mcp_schema(
    tool_name: str, param: str, expected_property: dict[str, Any]
) -> None:
    """``typer.Argument(help=...)`` must not alter the MCP tool input schema.

    The framework projects these params to *positional* CLI arguments, but
    FastMCP ignores the Typer marker when building the schema. So each param
    stays a plain JSON-schema field with no injected ``title``/``description``
    (aside from intentional constraints like the ``object_type`` ``enum`` added
    in netbox-mcp#126) and remains ``required`` — the invariant the migration
    depends on.
    """
    schema = _tool_input_schema(tool_name)
    assert schema["properties"][param] == expected_property
    assert param in schema["required"]
    # Positional-arg projection must not relax the closed-object contract.
    assert schema["additionalProperties"] is False


# ---------------------------------------------------------------------------
# Dict-filter tools: MCP-reachable, bespoke CLI, NOT synthesized (#111)
# ---------------------------------------------------------------------------


class TestDictFilterToolsKeptBespoke:
    """``netbox_get_objects`` / ``netbox_get_changelogs`` carry a top-level
    ``dict`` filter param that the framework cannot synthesize to a CLI option
    (vhspace/mcp-common#111). They must stay reachable over MCP while their CLI
    surface is provided by the hand-written ``list`` / ``changelogs`` commands —
    and the CLI must still build (no Typer "Type not yet supported" crash).
    """

    def test_get_objects_reachable_via_mcp(self, patched_netbox: MagicMock) -> None:
        import asyncio

        patched_netbox.get.return_value = _paginated(_device_record())
        result = asyncio.run(
            _mcp_call("netbox_get_objects", object_type="dcim.device", filters={"status": "active"})
        )
        assert result["count"] == 1

    def test_get_changelogs_reachable_via_mcp(self, patched_netbox: MagicMock) -> None:
        import asyncio

        patched_netbox.get.return_value = _paginated({"id": 1, "action": "update"})
        result = asyncio.run(_mcp_call("netbox_get_changelogs", filters={"action": "update"}))
        assert result["count"] == 1
        assert patched_netbox.get.call_args.kwargs["params"]["action"] == "update"

    def test_bespoke_list_and_changelogs_cli_work(self, patched_netbox: MagicMock) -> None:
        patched_netbox.get.return_value = _paginated(_device_record())
        assert runner.invoke(app, ["list", "dcim.device", "--json"]).exit_code == 0

        patched_netbox.get.return_value = _paginated({"id": 1, "action": "update"})
        assert runner.invoke(app, ["changelogs", "--json"]).exit_code == 0

    def test_dict_filter_tools_not_synthesized_to_cli(self) -> None:
        """No framework-synthesized ``get-objects`` / ``get-changelogs`` command.

        If either dict-filter tool were forced through ``@dual_mode_tool``
        without ``mcp_only=True``, the CLI would crash at build time. The mere
        fact that ``--help`` renders proves the build succeeded; we also assert
        the dict tools resolve only to their bespoke aliases.
        """
        top = runner.invoke(app, ["--help"])
        assert top.exit_code == 0
        # Bespoke aliases exist; framework-synthesized variants do not.
        assert runner.invoke(app, ["list", "--help"]).exit_code == 0
        assert runner.invoke(app, ["changelogs", "--help"]).exit_code == 0
        assert runner.invoke(app, ["get-changelogs", "--help"]).exit_code != 0
        assert runner.invoke(app, ["get-objects", "--help"]).exit_code != 0


# ---------------------------------------------------------------------------
# Pagination + large-result integrity (the reflection-cluster stress shape)
# ---------------------------------------------------------------------------

REFLECTION_DEVICE_COUNT = 476


def _make_devices(n: int, *, site: str = "ORI-TX") -> list[dict[str, Any]]:
    """Build ``n`` realistic device records (sized so 476 >> 4096 chars JSON)."""
    return [
        {
            "id": i,
            "name": f"reflection-gpu-{i:04d}",
            "status": {"value": "active", "label": "Active"},
            "site": {"id": 1, "name": site, "slug": site.lower()},
            "device_type": {"id": 5, "name": "DGX H100"},
            "role": {"id": 2, "name": "GPU Node"},
            "primary_ip4_address": f"10.20.{i // 256}.{i % 256}",
            "oob_ip_address": f"192.168.{i // 256}.{i % 256}",
            "cluster": {"id": 99, "name": "reflection"},
        }
        for i in range(1, n + 1)
    ]


def _reflection_side_effect(devices: list[dict[str, Any]]):
    """``client.get`` side effect honoring ``limit``/``offset`` like NetBox.

    Resolves the ``reflection`` cluster and paginates ``dcim/devices`` by the
    requested window so ``--limit 476`` returns the full set in one call while
    the default limit returns a capped page (with the real ``count``).
    """

    def _side(endpoint: str, id: int | None = None, params: dict[str, Any] | None = None):
        params = params or {}
        if endpoint == "virtualization/clusters":
            return {"count": 1, "results": [{"id": 99, "name": "reflection"}]}
        if endpoint == "dcim/devices":
            limit = int(params.get("limit", 100))
            offset = int(params.get("offset", 0))
            window = devices[offset : offset + limit]
            return {"count": len(devices), "next": None, "previous": None, "results": window}
        return {"count": 0, "results": []}

    return _side


class TestPaginationAndLargeResults:
    """The large-cluster read path must return COMPLETE results and the
    ``--json`` output must stay parseable (never truncated like the framework's
    ``echo_result`` would do at 4096 chars in human mode)."""

    def test_list_returns_all_476_with_explicit_limit_json(self, patched_netbox: MagicMock) -> None:
        devices = _make_devices(REFLECTION_DEVICE_COUNT)
        patched_netbox.get.side_effect = _reflection_side_effect(devices)

        result = _cli_json(
            "list",
            "dcim.device",
            "--filter",
            "cluster=reflection",
            "--limit",
            str(REFLECTION_DEVICE_COUNT),
        )

        assert result["count"] == REFLECTION_DEVICE_COUNT
        assert len(result["results"]) == REFLECTION_DEVICE_COUNT
        assert {d["id"] for d in result["results"]} == set(range(1, REFLECTION_DEVICE_COUNT + 1))

    def test_list_default_limit_caps_page_but_reports_full_count(
        self, patched_netbox: MagicMock
    ) -> None:
        devices = _make_devices(REFLECTION_DEVICE_COUNT)
        patched_netbox.get.side_effect = _reflection_side_effect(devices)

        # Default --limit for ``list`` is 100: one capped page, full count.
        result = _cli_json("list", "dcim.device", "--filter", "cluster=reflection")
        assert result["count"] == REFLECTION_DEVICE_COUNT
        assert len(result["results"]) == 100

    def test_list_human_mode_hints_full_count(self, patched_netbox: MagicMock) -> None:
        devices = _make_devices(REFLECTION_DEVICE_COUNT)
        patched_netbox.get.side_effect = _reflection_side_effect(devices)

        result = runner.invoke(app, ["list", "dcim.device", "--filter", "cluster=reflection"])
        assert result.exit_code == 0, result.stderr
        # Shows the true total and tells the agent how to fetch everything.
        assert "476 result(s)" in result.output
        assert f"--limit {REFLECTION_DEVICE_COUNT}" in result.output

    def test_large_json_is_complete_and_parseable_not_truncated(
        self, patched_netbox: MagicMock
    ) -> None:
        """476-device ``--json`` exceeds the 4096-char echo_result truncation
        threshold but the bespoke ``_output`` path emits complete, parseable
        JSON (no ``"… (N more chars)"`` corruption)."""
        devices = _make_devices(REFLECTION_DEVICE_COUNT)
        patched_netbox.get.side_effect = _reflection_side_effect(devices)

        raw = runner.invoke(
            app,
            [
                "list",
                "dcim.device",
                "--filter",
                "cluster=reflection",
                "--limit",
                str(REFLECTION_DEVICE_COUNT),
                "--json",
            ],
        )
        assert raw.exit_code == 0, raw.stderr
        assert len(raw.output) > 4096  # would be truncated by echo_result's default
        assert "more chars)" not in raw.output  # no truncation marker
        parsed = json.loads(raw.output)  # complete + parseable
        assert len(parsed["results"]) == REFLECTION_DEVICE_COUNT

    def test_search_cluster_autoexpand_reports_full_count(self, patched_netbox: MagicMock) -> None:
        """``search`` auto-expands a matched cluster and reports the full member
        count (not capped at the page size), with a hint to the full listing."""
        devices = _make_devices(REFLECTION_DEVICE_COUNT)
        patched_netbox.get.side_effect = _reflection_side_effect(devices)

        result = _cli_json("search", "reflection")
        expanded = result["cluster_devices"]["reflection"]
        assert expanded["count"] == REFLECTION_DEVICE_COUNT

        human = runner.invoke(app, ["search", "reflection"])
        assert human.exit_code == 0, human.stderr
        assert "476 devices" in human.output
        assert "netbox-cli devices --cluster reflection" in human.output
