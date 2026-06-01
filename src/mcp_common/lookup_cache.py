"""Shared TTL-based name→id lookup cache primitive for cross-MCP reference tables.

Every MCP server in this workspace performs the same small-table ``name → id``
lookup: netbox-mcp resolves cluster/site/role names to IDs, MAAS resolves
machine names, AWX resolves inventory/template names, IPA resolves
host/usergroup names. These reference tables (``dcim.site``,
``virtualization.cluster``, ``dcim.device-role``, ...) change rarely but are hit
on every agent turn. A short-TTL cache makes that first hop essentially free.

This module provides two building blocks:

- :class:`LookupCache` — a generic, thread-safe, TTL-keyed cache.
- :class:`NameIdResolver` — an opinionated wrapper for the
  ``(object_type, name|slug) -> id`` case so callers don't reimplement the same
  key shape every time.

This is intentionally a separate primitive from
:class:`mcp_common.credential_chain.CachedResolver` (credential caching, kernel
keyring) — different lifecycle, different keys, different consumers.

Eviction policy
---------------

:class:`LookupCache` evicts on a **least-recently-used (LRU)** basis. Both
:meth:`LookupCache.get` and :meth:`LookupCache.get_or_load` count as a "use" and
move the entry to the most-recently-used end. When the cache exceeds
``max_entries``, the least-recently-used entries are dropped first. TTL expiry is
independent of eviction: an entry is considered absent once it is older than
``ttl_seconds`` regardless of its LRU position.

Background-refresh pattern (callers own the schedule)
-----------------------------------------------------

This primitive deliberately does **not** spawn any threads. The cache only ever
does work on the calling thread inside ``get`` / ``get_or_load`` / ``prime``. If
you want to keep a hot table warm so that no agent turn ever pays the load
latency, run your own background refresh loop and call :meth:`LookupCache.prime`
(or :meth:`NameIdResolver.prime`) on a cadence shorter than ``ttl_seconds``.

The canonical precedent for this in the workspace is ``VPNMonitor`` in
``netbox-mcp/src/netbox_mcp/server.py`` (the ``threading.Thread`` +
``threading.Event`` poll loop), which already runs a daemon thread that wakes on
an interval and stops cleanly on a shutdown event. Mirror that shape::

    import threading

    cache: LookupCache[str, int] = LookupCache(ttl_seconds=300)
    _stop = threading.Event()

    def _refresh_loop() -> None:
        while not _stop.is_set():
            try:
                cache.prime(fetch_all_sites())  # {name: id}
            except Exception:  # noqa: BLE001 - keep the loop alive
                logger.exception("site cache refresh failed")
            _stop.wait(240)  # refresh inside the 300s TTL

    threading.Thread(target=_refresh_loop, name="site-cache", daemon=True).start()
    # ... on shutdown: _stop.set()

Keeping the loop in the caller means each MCP owns its own cadence, shutdown
signal, and error handling, and the primitive stays a pure, easily-tested data
structure.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass

__all__ = ["LookupCache", "NameIdResolver"]

DEFAULT_TTL_SECONDS = 300
DEFAULT_MAX_ENTRIES = 4096


@dataclass(slots=True)
class _Entry[V]:
    """A cached value together with the monotonic time it was stored."""

    value: V
    stored_at: float


class LookupCache[K, V]:
    """Generic, thread-safe, TTL-keyed cache with LRU eviction.

    Args:
        ttl_seconds: Entries older than this are treated as absent. Defaults to
            ``300``. A non-positive value means entries never expire by time.
        max_entries: Maximum number of live entries. When exceeded, the
            least-recently-used entries are evicted. Defaults to ``4096``.

    The cache is safe to share across threads. ``get``, ``get_or_load``,
    ``prime``, ``invalidate`` and ``clear`` all hold an internal lock while
    mutating shared state. For ``get_or_load`` the user-supplied ``loader`` is
    called **without** the main lock held (so a slow loader does not block
    readers of other keys), but a per-key lock guarantees the loader runs at
    most once for concurrent callers racing on the same missing key.
    """

    def __init__(
        self,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._store: OrderedDict[K, _Entry[V]] = OrderedDict()
        self._lock = threading.Lock()
        self._key_locks: dict[K, threading.Lock] = {}

    def get(self, key: K) -> V | None:
        """Return the cached value for *key*, or ``None`` if absent/expired.

        A hit refreshes the entry's recency (LRU). An expired entry is evicted
        as a side effect and reported as a miss.
        """
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if self._is_expired(entry, now):
                del self._store[key]
                return None
            self._store.move_to_end(key)
            return entry.value

    def get_or_load(self, key: K, loader: Callable[[K], V]) -> V:
        """Return the cached value for *key*, else load, cache, and return it.

        ``loader(key)`` is invoked at most once across threads racing on the
        same missing key. The loader runs without the main cache lock held.
        """
        cached = self.get(key)
        if cached is not None:
            return cached

        key_lock = self._get_key_lock(key)
        with key_lock:
            # Double-check: another thread may have populated the key while we
            # were waiting on the per-key lock.
            cached = self.get(key)
            if cached is not None:
                return cached
            value = loader(key)
            self._set(key, value)
            return value

    def prime(self, entries: Mapping[K, V]) -> None:
        """Bulk-load *entries* into the cache (e.g. pre-warm a reference table).

        Each entry's TTL starts now. Newest insertions are most-recently-used.
        """
        now = time.monotonic()
        with self._lock:
            for key, value in entries.items():
                self._store[key] = _Entry(value=value, stored_at=now)
                self._store.move_to_end(key)
            self._evict_locked()

    def invalidate(self, key: K) -> None:
        """Remove *key* from the cache. No-op if it is not present."""
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        """Remove all entries from the cache."""
        with self._lock:
            self._store.clear()

    def __len__(self) -> int:
        """Number of stored entries, including any not-yet-evicted expired ones."""
        with self._lock:
            return len(self._store)

    def __contains__(self, key: object) -> bool:
        """Whether *key* has a live (non-expired) entry."""
        return self.get(key) is not None  # type: ignore[arg-type]

    def _set(self, key: K, value: V) -> None:
        now = time.monotonic()
        with self._lock:
            self._store[key] = _Entry(value=value, stored_at=now)
            self._store.move_to_end(key)
            self._evict_locked()

    def _is_expired(self, entry: _Entry[V], now: float) -> bool:
        if self.ttl_seconds <= 0:
            return False
        return (now - entry.stored_at) >= self.ttl_seconds

    def _evict_locked(self) -> None:
        """Drop least-recently-used entries until within ``max_entries``.

        Caller must hold ``self._lock``.
        """
        while len(self._store) > self.max_entries:
            self._store.popitem(last=False)

    def _get_key_lock(self, key: K) -> threading.Lock:
        with self._lock:
            key_lock = self._key_locks.get(key)
            if key_lock is None:
                key_lock = threading.Lock()
                self._key_locks[key] = key_lock
            return key_lock


class NameIdResolver[IdT]:
    """Opinionated ``(object_type, name|slug) -> id`` cache over :class:`LookupCache`.

    Wraps a loader function of the shape ``loader_fn(object_type, name) -> id``
    (returning ``None`` when the name does not resolve) so each MCP doesn't
    reimplement the same ``(object_type, name)`` key shape.

    Args:
        loader_fn: Called on a cache miss as ``loader_fn(object_type, name)``.
            Return the id, or ``None`` if the name does not resolve.
        ttl_seconds: TTL for resolved ids. Defaults to ``300``.
        max_entries: Maximum cached ``(object_type, name)`` pairs. Defaults to
            ``4096``.

    Negative results (``None`` from ``loader_fn``) are **not** cached, so a name
    that does not yet exist is re-queried on the next call rather than being
    pinned as missing for the whole TTL window.
    """

    def __init__(
        self,
        loader_fn: Callable[[str, str], IdT | None],
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ) -> None:
        self._loader_fn = loader_fn
        self._cache: LookupCache[tuple[str, str], IdT] = LookupCache(
            ttl_seconds=ttl_seconds,
            max_entries=max_entries,
        )

    def resolve(self, object_type: str, name: str) -> IdT | None:
        """Resolve ``(object_type, name)`` to an id, using the cache when warm.

        Returns the cached id, or calls ``loader_fn`` on a miss. ``None`` results
        are returned but not cached.
        """
        key = (object_type, name)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        value = self._loader_fn(object_type, name)
        if value is not None:
            self._cache.prime({key: value})
        return value

    def prime(self, object_type: str, entries: Mapping[str, IdT]) -> None:
        """Bulk-load ``{name: id}`` *entries* for a single *object_type*."""
        self._cache.prime({(object_type, name): id_ for name, id_ in entries.items()})

    def invalidate(self, object_type: str, name: str) -> None:
        """Drop a single cached ``(object_type, name)`` entry."""
        self._cache.invalidate((object_type, name))

    def clear(self) -> None:
        """Drop all cached ids."""
        self._cache.clear()
