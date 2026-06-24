from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

JobFn = Callable[[], Awaitable[Any]]


class ConcurrencyLimiter:
    """Async concurrency limiter with per-key bulkheads."""

    def __init__(self, *, global_limit: int, per_key_limit: int) -> None:
        self._global = asyncio.Semaphore(global_limit)
        self._per_key_limit = per_key_limit
        self._per_key: dict[str, asyncio.Semaphore] = {}
        self._lock = asyncio.Lock()

    async def _get_key_sem(self, key: str) -> asyncio.Semaphore:
        async with self._lock:
            sem = self._per_key.get(key)
            if sem is None:
                sem = asyncio.Semaphore(self._per_key_limit)
                self._per_key[key] = sem
            return sem

    async def run(self, *, key: str | None, fn: JobFn) -> Any:
        async with self._global:
            if key:
                key_sem = await self._get_key_sem(key)
                async with key_sem:
                    return await fn()
            return await fn()
