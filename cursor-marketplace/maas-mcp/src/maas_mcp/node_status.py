"""MAAS node status codes for REST list filters.

The MAAS ``GET /api/2.0/machines/`` endpoint expects ``status`` as a
**lowercase string alias** (e.g. ``ready``, ``deployed``). Integer codes
are rejected with HTTP 400 on both MAAS 3.3 and 3.6.

.. _Canonical MAAS source: https://github.com/canonical/maas/blob/master/src/maascommon/enums/node.py
"""

from __future__ import annotations

from typing import Any

# Mirrors maascommon.enums.node.NodeStatus (stable API contract).
_NODE_STATUS_BY_NORMALIZED_NAME: dict[str, int] = {
    "default": 0,
    "new": 0,
    "commissioning": 1,
    "failed_commissioning": 2,
    "missing": 3,
    "ready": 4,
    "reserved": 5,
    "deployed": 6,
    "retired": 7,
    "broken": 8,
    "deploying": 9,
    "allocated": 10,
    "failed_deployment": 11,
    "releasing": 12,
    "failed_releasing": 13,
    "disk_erasing": 14,
    "failed_disk_erasing": 15,
    "rescue_mode": 16,
    "entering_rescue_mode": 17,
    "failed_entering_rescue_mode": 18,
    "exiting_rescue_mode": 19,
    "failed_exiting_rescue_mode": 20,
    "testing": 21,
    "failed_testing": 22,
}

# Human labels matching MAAS UI ``status_name`` (for docs / reference tool).
NODE_STATUS_REFERENCE: list[dict[str, Any]] = [
    {"value": 0, "status_name": "New", "keys": ["new", "default"]},
    {"value": 1, "status_name": "Commissioning", "keys": ["commissioning"]},
    {"value": 2, "status_name": "Failed commissioning", "keys": ["failed_commissioning"]},
    {"value": 3, "status_name": "Missing", "keys": ["missing"]},
    {"value": 4, "status_name": "Ready", "keys": ["ready"]},
    {"value": 5, "status_name": "Reserved", "keys": ["reserved"]},
    {"value": 6, "status_name": "Deployed", "keys": ["deployed"]},
    {"value": 7, "status_name": "Retired", "keys": ["retired"]},
    {"value": 8, "status_name": "Broken", "keys": ["broken"]},
    {"value": 9, "status_name": "Deploying", "keys": ["deploying"]},
    {"value": 10, "status_name": "Allocated", "keys": ["allocated"]},
    {"value": 11, "status_name": "Failed deployment", "keys": ["failed_deployment"]},
    {"value": 12, "status_name": "Releasing", "keys": ["releasing"]},
    {"value": 13, "status_name": "Releasing failed", "keys": ["failed_releasing"]},
    {"value": 14, "status_name": "Disk erasing", "keys": ["disk_erasing"]},
    {"value": 15, "status_name": "Failed disk erasing", "keys": ["failed_disk_erasing"]},
    {"value": 16, "status_name": "Rescue mode", "keys": ["rescue_mode"]},
    {"value": 17, "status_name": "Entering rescue mode", "keys": ["entering_rescue_mode"]},
    {
        "value": 18,
        "status_name": "Failed to enter rescue mode",
        "keys": ["failed_entering_rescue_mode"],
    },
    {"value": 19, "status_name": "Exiting rescue mode", "keys": ["exiting_rescue_mode"]},
    {
        "value": 20,
        "status_name": "Failed to exit rescue mode",
        "keys": ["failed_exiting_rescue_mode"],
    },
    {"value": 21, "status_name": "Testing", "keys": ["testing"]},
    {"value": 22, "status_name": "Failed testing", "keys": ["failed_testing"]},
]


def _normalize_status_key(name: str) -> str:
    s = name.strip().lower().replace(" ", "_").replace("-", "_")
    while "__" in s:
        s = s.replace("__", "_")
    return s


_NODE_STATUS_INT_TO_ALIAS: dict[int, str] = {}
for _alias, _code in _NODE_STATUS_BY_NORMALIZED_NAME.items():
    if _code not in _NODE_STATUS_INT_TO_ALIAS:
        _NODE_STATUS_INT_TO_ALIAS[_code] = _alias


def coerce_machines_list_status_value(status: Any) -> Any:
    """Return a lowercase string alias for ``machines/`` list queries when possible.

    MAAS API requires lowercase string aliases (``ready``, ``deployed``) for the
    ``status`` filter. Integer codes are rejected with HTTP 400.

    Accepts lowercase aliases, integers, numeric strings, or UI labels with
    spaces (``failed commissioning``). Unknown strings are returned unchanged
    so callers still see MAAS errors.
    """
    if status is None:
        return None
    if isinstance(status, bool):
        return status
    if isinstance(status, int):
        return _NODE_STATUS_INT_TO_ALIAS.get(status, status)
    s = str(status).strip()
    if not s:
        return s
    if s.isdigit():
        code = int(s)
        return _NODE_STATUS_INT_TO_ALIAS.get(code, s)
    key = _normalize_status_key(s)
    if key in _NODE_STATUS_BY_NORMALIZED_NAME:
        return key
    for entry in NODE_STATUS_REFERENCE:
        if _normalize_status_key(str(entry["status_name"])) == key:
            return _NODE_STATUS_INT_TO_ALIAS.get(int(entry["value"]), key)
    return status


def apply_status_coercion_to_machine_params(params: dict[str, Any]) -> dict[str, Any]:
    """Copy params and coerce ``status`` for ``GET machines/`` (single or list)."""
    if not params or "status" not in params:
        return dict(params or {})
    out = dict(params)
    st = out["status"]
    if isinstance(st, list):
        out["status"] = [coerce_machines_list_status_value(x) for x in st]
    else:
        out["status"] = coerce_machines_list_status_value(st)
    return out
