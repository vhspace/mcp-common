"""In-memory screenshot cache with SHA-256 change detection.

Avoids sending identical VGA framebuffer images back to agents,
saving significant token costs when screens haven't changed.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass

logger = logging.getLogger("redfish_mcp.screenshot_cache")


@dataclass
class CachedScreenshot:
    host: str
    sha256: str
    captured_at: float
    image_bytes: bytes
    mime_type: str
    method_used: str
    ocr_text: str | None = None
    analyses: dict[str, dict] | None = None


class ScreenshotCache:
    """Per-host screenshot cache keyed by SHA-256 of image bytes.

    Keeps at most ``max_entries`` screenshots.  Eviction is LRU by capture time.

    Set ``enabled=False`` to disable caching entirely (all captures
    treated as changed, no entries stored).
    """

    def __init__(self, *, max_entries: int = 128, enabled: bool = True) -> None:
        self._entries: dict[str, CachedScreenshot] = {}
        self._max = max_entries
        self.enabled = enabled

    @staticmethod
    def _key(host: str) -> str:
        return host.strip().lower()

    @staticmethod
    def _hash(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def get(self, host: str) -> CachedScreenshot | None:
        return self._entries.get(self._key(host))

    def has_changed(self, host: str, new_bytes: bytes) -> bool:
        """Return True if the screenshot differs from the cached version (or is new)."""
        if not self.enabled:
            return True
        entry = self._entries.get(self._key(host))
        if entry is None:
            return True
        return entry.sha256 != self._hash(new_bytes)

    def store(
        self,
        host: str,
        image_bytes: bytes,
        mime_type: str,
        method_used: str,
    ) -> CachedScreenshot:
        """Store a screenshot, evicting the oldest entry if at capacity.

        Preserves cached analyses and OCR text when the image hash is unchanged.
        """
        new_hash = self._hash(image_bytes)
        key = self._key(host)
        prev = self._entries.get(key)

        entry = CachedScreenshot(
            host=host,
            sha256=new_hash,
            captured_at=time.monotonic(),
            image_bytes=image_bytes,
            mime_type=mime_type,
            method_used=method_used,
        )

        if prev and prev.sha256 == new_hash:
            entry.ocr_text = prev.ocr_text
            entry.analyses = prev.analyses

        if not self.enabled:
            return entry

        if len(self._entries) >= self._max and key not in self._entries:
            oldest_key = min(self._entries, key=lambda k: self._entries[k].captured_at)
            del self._entries[oldest_key]

        self._entries[key] = entry
        logger.debug("Cached screenshot for %s (sha256=%s…)", host, entry.sha256[:12])
        return entry

    def set_ocr_text(self, host: str, text: str) -> None:
        """Attach OCR text to an existing cache entry."""
        entry = self._entries.get(self._key(host))
        if entry is not None:
            entry.ocr_text = text

    def set_analysis(self, host: str, mode: str, result: dict) -> None:
        """Attach an LLM analysis result to an existing cache entry."""
        entry = self._entries.get(self._key(host))
        if entry is not None:
            if entry.analyses is None:
                entry.analyses = {}
            entry.analyses[mode] = result

    def get_analysis(self, host: str, mode: str) -> dict | None:
        """Return cached analysis for a mode, or None."""
        entry = self._entries.get(self._key(host))
        if entry is not None and entry.analyses:
            return entry.analyses.get(mode)
        return None

    def invalidate(self, host: str) -> None:
        self._entries.pop(self._key(host), None)

    def clear(self) -> None:
        self._entries.clear()
