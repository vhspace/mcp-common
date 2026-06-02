"""Unit tests for the TTL-based lookup cache primitive."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from unittest.mock import patch

import pytest

from mcp_common.lookup_cache import LookupCache, NameIdResolver


@pytest.fixture()
def fake_clock() -> Iterator[list[float]]:
    """Patch ``time.monotonic`` in the module with a mutable, controllable clock.

    Yields a single-element list holding the current fake time; mutate
    ``clock[0]`` to advance it.
    """
    clock = [1000.0]
    with patch("mcp_common.lookup_cache.time.monotonic", lambda: clock[0]):
        yield clock


class TestBasicGet:
    def test_miss_returns_none(self) -> None:
        cache: LookupCache[str, int] = LookupCache()
        assert cache.get("absent") is None

    def test_get_or_load_caches(self) -> None:
        calls: list[str] = []

        def loader(key: str) -> int:
            calls.append(key)
            return len(key)

        cache: LookupCache[str, int] = LookupCache()
        assert cache.get_or_load("abc", loader) == 3
        assert cache.get_or_load("abc", loader) == 3
        assert cache.get("abc") == 3
        assert calls == ["abc"]

    def test_contains(self) -> None:
        cache: LookupCache[str, int] = LookupCache()
        cache.prime({"a": 1})
        assert "a" in cache
        assert "b" not in cache


class TestTTLExpiry:
    def test_get_expires_after_ttl(self, fake_clock: list[float]) -> None:
        cache: LookupCache[str, int] = LookupCache(ttl_seconds=300)
        cache.prime({"site": 7})
        assert cache.get("site") == 7

        fake_clock[0] += 299
        assert cache.get("site") == 7

        fake_clock[0] += 1  # now exactly at TTL -> expired
        assert cache.get("site") is None

    def test_get_or_load_reloads_after_expiry(self, fake_clock: list[float]) -> None:
        calls: list[str] = []

        def loader(key: str) -> int:
            calls.append(key)
            return len(calls)

        cache: LookupCache[str, int] = LookupCache(ttl_seconds=10)
        assert cache.get_or_load("k", loader) == 1
        fake_clock[0] += 5
        assert cache.get_or_load("k", loader) == 1  # still cached
        fake_clock[0] += 5  # at TTL -> expired
        assert cache.get_or_load("k", loader) == 2
        assert calls == ["k", "k"]

    def test_zero_ttl_never_expires(self, fake_clock: list[float]) -> None:
        cache: LookupCache[str, int] = LookupCache(ttl_seconds=0)
        cache.prime({"k": 1})
        fake_clock[0] += 1_000_000
        assert cache.get("k") == 1


class TestPrime:
    def test_bulk_load(self) -> None:
        cache: LookupCache[str, int] = LookupCache()
        cache.prime({"a": 1, "b": 2, "c": 3})
        assert cache.get("a") == 1
        assert cache.get("b") == 2
        assert cache.get("c") == 3
        assert len(cache) == 3

    def test_prime_overwrites(self) -> None:
        cache: LookupCache[str, int] = LookupCache()
        cache.prime({"a": 1})
        cache.prime({"a": 99})
        assert cache.get("a") == 99


class TestInvalidateClear:
    def test_invalidate_single_key(self) -> None:
        cache: LookupCache[str, int] = LookupCache()
        cache.prime({"a": 1, "b": 2})
        cache.invalidate("a")
        assert cache.get("a") is None
        assert cache.get("b") == 2

    def test_invalidate_missing_key_is_noop(self) -> None:
        cache: LookupCache[str, int] = LookupCache()
        cache.invalidate("nope")  # must not raise

    def test_clear(self) -> None:
        cache: LookupCache[str, int] = LookupCache()
        cache.prime({"a": 1, "b": 2})
        cache.clear()
        assert len(cache) == 0
        assert cache.get("a") is None


class TestEviction:
    def test_max_entries_evicts_lru(self) -> None:
        cache: LookupCache[str, int] = LookupCache(max_entries=2)
        cache.prime({"a": 1})
        cache.prime({"b": 2})
        # Touch "a" so "b" becomes least-recently-used.
        assert cache.get("a") == 1
        cache.prime({"c": 3})

        assert len(cache) == 2
        assert cache.get("a") == 1
        assert cache.get("c") == 3
        assert cache.get("b") is None  # evicted as LRU

    def test_get_or_load_respects_max_entries(self) -> None:
        cache: LookupCache[str, int] = LookupCache(max_entries=2)
        cache.get_or_load("a", len)
        cache.get_or_load("bb", len)
        cache.get_or_load("ccc", len)
        assert len(cache) == 2
        assert cache.get("a") is None  # first inserted, LRU, evicted

    def test_invalid_max_entries(self) -> None:
        with pytest.raises(ValueError):
            LookupCache(max_entries=0)


class TestThreadSafety:
    def test_concurrent_get_or_load_loads_once(self) -> None:
        load_count = 0
        count_lock = threading.Lock()
        start = threading.Barrier(16)

        def loader(key: str) -> int:
            nonlocal load_count
            with count_lock:
                load_count += 1
            time.sleep(0.02)  # widen the race window
            return 42

        cache: LookupCache[str, int] = LookupCache()
        results: list[int] = []
        results_lock = threading.Lock()

        def worker() -> None:
            start.wait()
            value = cache.get_or_load("shared", loader)
            with results_lock:
                results.append(value)

        threads = [threading.Thread(target=worker) for _ in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert results == [42] * 16
        assert load_count == 1

    def test_distinct_keys_load_concurrently(self) -> None:
        def loader(key: str) -> int:
            return len(key)

        cache: LookupCache[str, int] = LookupCache()
        errors: list[Exception] = []

        def worker(key: str) -> None:
            try:
                for _ in range(50):
                    cache.get_or_load(key, loader)
                    cache.invalidate(key)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(f"k{i}",)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []


class TestNameIdResolver:
    def test_resolve_uses_cache(self) -> None:
        calls: list[tuple[str, str]] = []

        def loader(object_type: str, name: str) -> int | None:
            calls.append((object_type, name))
            return {"site-a": 1, "site-b": 2}.get(name)

        resolver: NameIdResolver[int] = NameIdResolver(loader)
        assert resolver.resolve("dcim.site", "site-a") == 1
        assert resolver.resolve("dcim.site", "site-a") == 1
        assert calls == [("dcim.site", "site-a")]

    def test_resolve_distinguishes_object_type(self) -> None:
        def loader(object_type: str, name: str) -> int | None:
            return 100 if object_type == "cluster" else 200

        resolver: NameIdResolver[int] = NameIdResolver(loader)
        assert resolver.resolve("cluster", "x") == 100
        assert resolver.resolve("site", "x") == 200

    def test_none_results_not_cached(self) -> None:
        calls: list[str] = []

        def loader(object_type: str, name: str) -> int | None:
            calls.append(name)
            return None

        resolver: NameIdResolver[int] = NameIdResolver(loader)
        assert resolver.resolve("dcim.site", "ghost") is None
        assert resolver.resolve("dcim.site", "ghost") is None
        assert calls == ["ghost", "ghost"]  # re-queried, not pinned

    def test_prime_bulk(self) -> None:
        def loader(object_type: str, name: str) -> int | None:
            raise AssertionError("loader should not be called for primed entries")

        resolver: NameIdResolver[int] = NameIdResolver(loader)
        resolver.prime("dcim.site", {"site-a": 1, "site-b": 2})
        assert resolver.resolve("dcim.site", "site-a") == 1
        assert resolver.resolve("dcim.site", "site-b") == 2

    def test_invalidate_and_clear(self) -> None:
        def loader(object_type: str, name: str) -> int | None:
            return 7

        resolver: NameIdResolver[int] = NameIdResolver(loader)
        resolver.prime("t", {"a": 1})
        resolver.invalidate("t", "a")
        assert resolver.resolve("t", "a") == 7  # reloaded after invalidation

        resolver.prime("t", {"b": 5})
        resolver.clear()
        assert resolver.resolve("t", "b") == 7  # reloaded after clear

    def test_ttl_expiry(self, fake_clock: list[float]) -> None:
        calls: list[str] = []

        def loader(object_type: str, name: str) -> int | None:
            calls.append(name)
            return len(calls)

        resolver: NameIdResolver[int] = NameIdResolver(loader, ttl_seconds=60)
        assert resolver.resolve("t", "a") == 1
        fake_clock[0] += 60
        assert resolver.resolve("t", "a") == 2
