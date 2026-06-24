from __future__ import annotations

import json
import os
from typing import Any


def _json_text(obj: Any) -> str:
    try:
        return json.dumps(obj, indent=2, sort_keys=True, default=str)
    except Exception:
        return str(obj)


def env(name: str) -> str | None:
    """Return env var value if set to a non-empty string; otherwise None."""
    v = os.getenv(name)
    return v if v and v.strip() else None


def require(name: str, v: str | None) -> str:
    """Require a non-empty string, otherwise terminate with a helpful message."""
    if not v:
        raise SystemExit(f"Missing required value: {name}")
    return v


def norm(s: str) -> str:
    """Normalize user input for comparisons (lowercase alnum only)."""
    return "".join(ch.lower() for ch in s.strip() if ch.isalnum())
