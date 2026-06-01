"""Shared pytest fixtures for MCP server testing."""

from __future__ import annotations

import os
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Callable,
    Iterator,
    Mapping,
    Sequence,
)
from contextlib import ExitStack
from typing import Any, NamedTuple
from unittest import mock

import httpx
import pytest
from fastmcp import Client, FastMCP


async def mcp_client(server: FastMCP) -> AsyncGenerator[Client[Any], None]:
    """Create an MCP client connected to a FastMCP server instance.

    Usage in conftest.py::

        from mcp_common.testing import mcp_client
        from my_server import mcp as app

        @pytest.fixture
        async def client():
            async for c in mcp_client(app):
                yield c
    """
    async with Client(server) as client:
        yield client


# ---------------------------------------------------------------------------
# HTTP transport test fixtures (#94)
#
# Standardizes the `_make_app` / `_reset_init` scaffolding duplicated across
# each MCP's `test_http_transport.py` (netbox-mcp, weka-mcp, ...). A per-MCP
# `conftest.py` builds the fixtures once from the server's `create_app` and
# state-reset functions; tests just request `app` / `client` / `make_app`.
# ---------------------------------------------------------------------------


def reset_lru_caches(*funcs: Any) -> None:
    """Call ``.cache_clear()`` on each ``functools.lru_cache``-wrapped callable.

    Non-cache callables are skipped, so it's safe to pass a mixed list of
    module-level getters between tests::

        reset_lru_caches(get_settings, get_client)
    """
    for fn in funcs:
        clear = getattr(fn, "cache_clear", None)
        if callable(clear):
            clear()


def bearer_headers(token: str) -> dict[str, str]:
    """Return an ``Authorization: Bearer <token>`` header dict for auth tests."""
    return {"Authorization": f"Bearer {token}"}


class HttpTransportFixtures(NamedTuple):
    """Bundle of pytest fixtures returned by :func:`make_http_transport_fixtures`.

    Assign the fields to module-level names in a per-MCP ``conftest.py`` so
    pytest collects them::

        _fx = make_http_transport_fixtures(
            create_app=create_app,
            reset_fns=[_reset_settings, _reset_client],
            lru_caches=[get_settings],
            mocks=["netbox_mcp.server.NetBoxRestClient"],
        )
        make_app = _fx.make_app
        app = _fx.app
        client = _fx.client
        reset = _fx.reset
    """

    make_app: Any
    app: Any
    client: Any
    reset: Any


def make_http_transport_fixtures(
    *,
    create_app: Callable[[], Any],
    reset_fns: Sequence[Callable[[], None]] = (),
    lru_caches: Sequence[Any] = (),
    env: Mapping[str, str] | None = None,
    mocks: Sequence[str] = (),
    base_url: str = "http://test",
) -> HttpTransportFixtures:
    """Build reusable HTTP-transport test fixtures for an MCP server.

    Args:
        create_app: The server's ASGI app factory (e.g. ``create_app``); built
            under a patched environment with module state freshly reset.
        reset_fns: Callables that reset module-level server state (e.g. an
            ``_initialized`` flag, cached client) — run before each build and
            around each test via the ``reset`` fixture.
        lru_caches: ``functools.lru_cache``-decorated callables to clear between
            tests (settings/client getters).
        env: Base environment applied while building the app. ``TRANSPORT``
            defaults to ``"http"``.
        mocks: Dotted patch targets activated only while ``create_app()`` runs
            (e.g. a REST client class), so no real network client is built.
        base_url: Base URL for the test ``httpx.AsyncClient``.

    Returns:
        A :class:`HttpTransportFixtures` of pytest fixtures (``make_app``,
        ``app``, ``client``, ``reset``).
    """
    reset_fns = tuple(reset_fns)
    lru_caches = tuple(lru_caches)
    mocks = tuple(mocks)
    base_env = dict(env or {})

    def _do_reset() -> None:
        for fn in reset_fns:
            fn()
        reset_lru_caches(*lru_caches)

    def _make_app(access_token: str | None = None, **env_overrides: str) -> Any:
        _do_reset()
        full_env = dict(base_env)
        full_env.setdefault("TRANSPORT", "http")
        if access_token is not None:
            full_env["MCP_HTTP_ACCESS_TOKEN"] = access_token
        full_env.update(env_overrides)
        with ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, full_env, clear=False))
            for target in mocks:
                stack.enter_context(mock.patch(target))
            return create_app()

    @pytest.fixture
    def make_app() -> Callable[..., Any]:
        return _make_app

    @pytest.fixture
    def app() -> Any:
        return _make_app()

    @pytest.fixture
    async def client() -> AsyncIterator[httpx.AsyncClient]:
        transport = httpx.ASGITransport(app=_make_app())
        async with httpx.AsyncClient(transport=transport, base_url=base_url) as c:
            yield c

    @pytest.fixture
    def reset() -> Iterator[None]:
        _do_reset()
        yield
        _do_reset()

    return HttpTransportFixtures(make_app=make_app, app=app, client=client, reset=reset)
