"""Integration tests for CachedResolver using real keyctl."""

from __future__ import annotations

import shutil
import time
import uuid

import pytest

from mcp_common.credential_chain import CachedResolver, StaticResolver

pytestmark = pytest.mark.skipif(
    not shutil.which("keyctl"), reason="keyctl not available"
)


class TestCachedResolverKeyctl:
    def test_cross_process_caching(self):
        """Value stored by one process is readable by another."""
        key_name = f"mcp:test:{uuid.uuid4().hex[:8]}"

        inner = StaticResolver("test-secret-123")
        resolver = CachedResolver(inner=inner, key_name=key_name, ttl_seconds=60)
        assert resolver.resolve() == "test-secret-123"

        # Read from a "different process" (same session) — inner returns empty
        resolver2 = CachedResolver(
            inner=StaticResolver(""), key_name=key_name, ttl_seconds=60
        )
        assert resolver2.resolve() == "test-secret-123"  # from cache, not inner

        resolver.invalidate()

    def test_ttl_expiry(self):
        """Key expires after TTL."""
        key_name = f"mcp:test:{uuid.uuid4().hex[:8]}"
        inner = StaticResolver("expires-soon")
        resolver = CachedResolver(inner=inner, key_name=key_name, ttl_seconds=2)
        resolver.resolve()

        time.sleep(3)

        # After expiry, cache miss — falls through to inner
        resolver2 = CachedResolver(
            inner=StaticResolver("new-value"), key_name=key_name, ttl_seconds=60
        )
        assert resolver2.resolve() == "new-value"

    def test_invalidate_removes_key(self):
        """Invalidate makes the key unreadable."""
        key_name = f"mcp:test:{uuid.uuid4().hex[:8]}"
        inner = StaticResolver("to-be-revoked")
        resolver = CachedResolver(inner=inner, key_name=key_name, ttl_seconds=60)
        resolver.resolve()

        resolver.invalidate()

        # After invalidation, cache miss
        resolver2 = CachedResolver(
            inner=StaticResolver("fresh"), key_name=key_name, ttl_seconds=60
        )
        assert resolver2.resolve() == "fresh"
