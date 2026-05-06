"""Loader and lookup functions for XID / SXid error catalogs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_XID_CACHE: dict[str, dict[int, dict[str, Any]]] = {}
_SXID_CACHE: dict[int, dict[str, Any]] | None = None


def _catalog_dir() -> Path:
    return Path(__file__).parent / "catalogs"


def load_xid_catalog(driver_version: str = "590") -> dict[int, dict[str, Any]]:
    """Load and cache the XID catalog for the given driver version.

    Returns a dict mapping XID code (int) to its metadata dict.
    """
    if driver_version in _XID_CACHE:
        return _XID_CACHE[driver_version]

    path = _catalog_dir() / f"xid_v{driver_version}.yaml"
    with path.open() as fh:
        data = yaml.safe_load(fh)

    catalog: dict[int, dict[str, Any]] = {}
    for code, entry in data.get("xid_codes", {}).items():
        catalog[int(code)] = entry

    _XID_CACHE[driver_version] = catalog
    return catalog


def load_sxid_catalog() -> dict[int, dict[str, Any]]:
    """Load and cache the SXid (NVSwitch) catalog.

    Returns a dict mapping SXid code (int) to its metadata dict.
    """
    global _SXID_CACHE
    if _SXID_CACHE is not None:
        return _SXID_CACHE

    path = _catalog_dir() / "sxid.yaml"
    with path.open() as fh:
        data = yaml.safe_load(fh)

    catalog: dict[int, dict[str, Any]] = {}
    for code, entry in data.get("sxid_codes", {}).items():
        catalog[int(code)] = entry

    _SXID_CACHE = catalog
    return catalog


def xid_lookup(code: int, driver_version: str = "590") -> dict[str, Any] | None:
    """Look up a single XID code.

    Returns the catalog entry with ``found: True`` and ``code`` injected,
    or ``{"found": False, "code": <code>}`` if unknown.
    """
    catalog = load_xid_catalog(driver_version)
    entry = catalog.get(code)
    if entry is None:
        return {"found": False, "code": code}
    return {"found": True, "code": code, **entry}


def sxid_lookup(code: int) -> dict[str, Any] | None:
    """Look up a single SXid (NVSwitch) code.

    Returns the catalog entry with ``found: True`` and ``code`` injected,
    or ``{"found": False, "code": <code>}`` if unknown.
    """
    catalog = load_sxid_catalog()
    entry = catalog.get(code)
    if entry is None:
        return {"found": False, "code": code}
    return {"found": True, "code": code, **entry}
