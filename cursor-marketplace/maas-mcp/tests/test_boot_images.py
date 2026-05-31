"""Tests for boot image management tools."""

from unittest.mock import AsyncMock

import pytest
from fastmcp.exceptions import ToolError

from maas_mcp import server

FAKE_RESOURCES = [
    {"id": 1, "name": "ubuntu/jammy", "architecture": "amd64/generic", "type": "synced"},
    {"id": 2, "name": "ubuntu/noble", "architecture": "amd64/generic", "type": "synced"},
]

FAKE_SOURCES = [{"id": 5, "url": "http://images.maas.io/ephemeral-v3/stable/"}]

FAKE_SELECTIONS = [
    {
        "id": 10,
        "os": "ubuntu",
        "release": "jammy",
        "arches": ["amd64"],
        "subarches": ["*"],
        "labels": ["*"],
    },
]


class FakeClient:
    """Minimal MAAS client that returns canned boot-image responses."""

    def __init__(
        self,
        resources=None,
        sources=None,
        selections=None,
        is_importing=False,
    ):
        self._resources = resources if resources is not None else FAKE_RESOURCES
        self._sources = sources if sources is not None else FAKE_SOURCES
        self._selections = selections if selections is not None else FAKE_SELECTIONS
        self._is_importing = is_importing
        self.post_calls: list[tuple[str, dict | None, dict | None]] = []
        self.delete_calls: list[str] = []

    def get(self, path, params=None):
        if path == "boot-resources":
            if params and params.get("op") == "is_importing":
                return self._is_importing
            return self._resources
        if path == "boot-sources":
            return self._sources
        if path.startswith("boot-sources/") and path.endswith("/selections"):
            return self._selections
        raise AssertionError(f"Unexpected GET: {path} params={params}")

    def post(self, path, data=None, *, params=None):
        self.post_calls.append((path, data, params))
        return {}

    def delete(self, path):
        self.delete_calls.append(path)


def _make_ctx():
    ctx = AsyncMock()
    ctx.info = AsyncMock()
    ctx.warning = AsyncMock()
    ctx.error = AsyncMock()
    ctx.debug = AsyncMock()
    return ctx


# ---------------------------------------------------------------------------
# maas_list_boot_images
# ---------------------------------------------------------------------------


class TestListBootImages:
    @pytest.mark.asyncio
    async def test_returns_resources_and_selections(self, monkeypatch):
        fake = FakeClient()
        monkeypatch.setattr(server, "get_client", lambda _: fake)

        result = await server.maas_list_boot_images(ctx=_make_ctx())

        assert len(result["boot_resources"]) == 2
        assert result["boot_resources"][0]["name"] == "ubuntu/jammy"
        assert len(result["boot_selections"]) == 1
        assert result["boot_selections"][0]["os"] == "ubuntu"
        assert result["is_importing"] is False
        assert result["boot_source_id"] == 5

    @pytest.mark.asyncio
    async def test_is_importing_true(self, monkeypatch):
        fake = FakeClient(is_importing=True)
        monkeypatch.setattr(server, "get_client", lambda _: fake)

        result = await server.maas_list_boot_images(ctx=_make_ctx())
        assert result["is_importing"] is True

    @pytest.mark.asyncio
    async def test_no_sources_returns_empty_selections(self, monkeypatch):
        fake = FakeClient(sources=[])
        monkeypatch.setattr(server, "get_client", lambda _: fake)

        result = await server.maas_list_boot_images(ctx=_make_ctx())
        assert result["boot_selections"] == []
        assert result["boot_source_id"] is None

    @pytest.mark.asyncio
    async def test_fields_filter_applies_to_resources(self, monkeypatch):
        fake = FakeClient()
        monkeypatch.setattr(server, "get_client", lambda _: fake)

        result = await server.maas_list_boot_images(ctx=_make_ctx(), fields=["name"])
        assert result["boot_resources"] == [{"name": "ubuntu/jammy"}, {"name": "ubuntu/noble"}]


# ---------------------------------------------------------------------------
# maas_manage_boot_images
# ---------------------------------------------------------------------------


class TestManageBootImages:
    @pytest.mark.asyncio
    async def test_write_gate_refuses(self):
        from fastmcp.exceptions import ToolError

        with pytest.raises(ToolError, match="allow_write"):
            await server.maas_manage_boot_images(
                ctx=_make_ctx(), action="import", allow_write=False
            )

    @pytest.mark.asyncio
    async def test_invalid_action_raises(self):
        from fastmcp.exceptions import ToolError

        with pytest.raises(ToolError, match="action must be one of"):
            await server.maas_manage_boot_images(
                ctx=_make_ctx(), action="bad_action", allow_write=True
            )

    @pytest.mark.asyncio
    async def test_add_selection(self, monkeypatch):
        fake = FakeClient()
        monkeypatch.setattr(server, "get_client", lambda _: fake)
        monkeypatch.setattr(server, "_confirm_or_proceed", AsyncMock(return_value=None))

        result = await server.maas_manage_boot_images(
            ctx=_make_ctx(),
            action="add_selection",
            os="ubuntu",
            release="noble",
            arches="amd64",
            allow_write=True,
        )

        assert result.structured_content["ok"] is True
        assert "noble" in result.structured_content["detail"]
        assert len(fake.post_calls) == 1
        path, data, _ = fake.post_calls[0]
        assert path == "boot-sources/5/selections"
        assert data["os"] == "ubuntu"
        assert data["release"] == "noble"

    @pytest.mark.asyncio
    async def test_add_selection_requires_os_and_release(self, monkeypatch):
        fake = FakeClient()
        monkeypatch.setattr(server, "get_client", lambda _: fake)

        with pytest.raises(ToolError, match="os and release are required"):
            await server.maas_manage_boot_images(
                ctx=_make_ctx(), action="add_selection", os="ubuntu", allow_write=True
            )

    @pytest.mark.asyncio
    async def test_remove_selection(self, monkeypatch):
        fake = FakeClient()
        monkeypatch.setattr(server, "get_client", lambda _: fake)
        monkeypatch.setattr(server, "_confirm_or_proceed", AsyncMock(return_value=None))

        result = await server.maas_manage_boot_images(
            ctx=_make_ctx(),
            action="remove_selection",
            source_id=5,
            selection_id=10,
            allow_write=True,
        )

        assert result.structured_content["ok"] is True
        assert len(fake.delete_calls) == 1
        assert fake.delete_calls[0] == "boot-sources/5/selections/10"

    @pytest.mark.asyncio
    async def test_remove_selection_requires_id(self, monkeypatch):
        fake = FakeClient()
        monkeypatch.setattr(server, "get_client", lambda _: fake)

        with pytest.raises(ToolError, match="selection_id is required"):
            await server.maas_manage_boot_images(
                ctx=_make_ctx(), action="remove_selection", allow_write=True
            )

    @pytest.mark.asyncio
    async def test_import_triggers_sync(self, monkeypatch):
        fake = FakeClient()
        monkeypatch.setattr(server, "get_client", lambda _: fake)
        monkeypatch.setattr(server, "_confirm_or_proceed", AsyncMock(return_value=None))

        result = await server.maas_manage_boot_images(
            ctx=_make_ctx(), action="import", allow_write=True
        )

        assert result.structured_content["ok"] is True
        assert len(fake.post_calls) == 1
        path, _, params = fake.post_calls[0]
        assert path == "boot-resources"
        assert params == {"op": "import"}

    @pytest.mark.asyncio
    async def test_stop_import(self, monkeypatch):
        fake = FakeClient()
        monkeypatch.setattr(server, "get_client", lambda _: fake)
        monkeypatch.setattr(server, "_confirm_or_proceed", AsyncMock(return_value=None))

        result = await server.maas_manage_boot_images(
            ctx=_make_ctx(), action="stop_import", allow_write=True
        )

        assert result.structured_content["ok"] is True
        path, _, params = fake.post_calls[0]
        assert path == "boot-resources"
        assert params == {"op": "stop_import"}

    @pytest.mark.asyncio
    async def test_no_sources_returns_error(self, monkeypatch):
        fake = FakeClient(sources=[])
        monkeypatch.setattr(server, "get_client", lambda _: fake)

        result = await server.maas_manage_boot_images(
            ctx=_make_ctx(), action="add_selection", os="ubuntu", release="noble", allow_write=True
        )

        assert result.structured_content["ok"] is False
        assert "No boot sources" in result.structured_content["error"]
