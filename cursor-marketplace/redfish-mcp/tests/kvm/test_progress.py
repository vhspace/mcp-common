"""Tests for the progress publisher."""

from __future__ import annotations

import asyncio

import pytest

from redfish_mcp.kvm.backend import ProgressEvent
from redfish_mcp.kvm.daemon.progress import ProgressPublisher


class TestProgressPublisher:
    @pytest.mark.anyio
    async def test_single_subscriber_sees_events(self):
        pub = ProgressPublisher()
        q = pub.subscribe("sess-1")
        await pub.publish("sess-1", ProgressEvent(stage="authenticating"))
        await pub.publish("sess-1", ProgressEvent(stage="ready"))
        ev1 = await asyncio.wait_for(q.get(), timeout=1)
        ev2 = await asyncio.wait_for(q.get(), timeout=1)
        assert ev1.stage == "authenticating"
        assert ev2.stage == "ready"

    @pytest.mark.anyio
    async def test_two_subscribers_both_see_events(self):
        pub = ProgressPublisher()
        a = pub.subscribe("sess-1")
        b = pub.subscribe("sess-1")
        await pub.publish("sess-1", ProgressEvent(stage="ready"))
        ea = await asyncio.wait_for(a.get(), timeout=1)
        eb = await asyncio.wait_for(b.get(), timeout=1)
        assert ea.stage == "ready"
        assert eb.stage == "ready"

    @pytest.mark.anyio
    async def test_unsubscribe_removes_queue(self):
        pub = ProgressPublisher()
        q = pub.subscribe("sess-1")
        pub.unsubscribe("sess-1", q)
        await pub.publish("sess-1", ProgressEvent(stage="ready"))
        assert q.empty()

    @pytest.mark.anyio
    async def test_complete_delivers_sentinel(self):
        pub = ProgressPublisher()
        q = pub.subscribe("sess-1")
        await pub.complete("sess-1")
        ev = await asyncio.wait_for(q.get(), timeout=1)
        assert ev is None

    @pytest.mark.anyio
    async def test_publish_to_unknown_session_is_silent(self):
        pub = ProgressPublisher()
        await pub.publish("missing", ProgressEvent(stage="x"))  # must not raise


@pytest.fixture
def anyio_backend():
    return "asyncio"
