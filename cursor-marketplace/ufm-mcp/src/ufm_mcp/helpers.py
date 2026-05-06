"""Shared helpers for ufm-mcp tools."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ufm_mcp.ufm_client import UfmRestClient


def ensure_json_serializable(obj: Any) -> Any:
    """Recursively coerce an object to be JSON-serializable."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): ensure_json_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [ensure_json_serializable(item) for item in obj]
    try:
        json.dumps(obj, default=str)
        return obj
    except (TypeError, ValueError):
        return str(obj)


def parse_ts_utc(ts: str) -> datetime | None:
    """Parse a UFM timestamp string as UTC datetime. Returns None on failure."""
    ts = (ts or "").strip()
    if not ts:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(ts, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def top_n(d: dict[str, int], n: int = 10) -> list[dict[str, Any]]:
    """Return the top N items from a counter dict, sorted by count descending."""
    return [
        {"name": k, "count": v}
        for k, v in sorted(d.items(), key=lambda kv: kv[1], reverse=True)[:n]
    ]


def normalize_list_payload(payload: Any) -> list[dict[str, Any]]:
    """Normalize UFM API responses (list or {\"data\": [...]})."""
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return [x for x in payload["data"] if isinstance(x, dict)]
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    return []


def count_severities(items: list[dict[str, Any]], key: str = "severity") -> dict[str, int]:
    """Count occurrences of each severity value in a list of dicts."""
    counts: Counter[str] = Counter()
    for item in items:
        if isinstance(item, dict):
            sev = str(item.get(key, "")).strip() or "Unknown"
            counts[sev] += 1
    return dict(counts)


def truncate_text(text: str, limit_chars: int) -> tuple[str, bool]:
    """Truncate text to limit_chars. Returns (text, was_truncated)."""
    if limit_chars <= 0:
        return "", True
    if len(text) <= limit_chars:
        return text, False
    return text[:limit_chars], True


def get_server_tzinfo(client: UfmRestClient, api_base: str) -> tuple[str, ZoneInfo | None]:
    """Best-effort fetch of UFM server timezone from /app/ufm_config."""
    tz_name = "UTC"
    try:
        cfg = client.get_json(f"{api_base}/app/ufm_config")
        if isinstance(cfg, dict):
            v = cfg.get("server_tz")
            if isinstance(v, str) and v.strip():
                tz_name = v.strip()
    except Exception:
        tz_name = "UTC"

    try:
        return tz_name, ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return tz_name, None


_UFM_LOG_TS_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:\.(?P<ms>\d{1,6}))?",
)
_SM_LOG_TS_RE = re.compile(
    r"^(?P<mon>[A-Z][a-z]{2})\s+(?P<day>\d{2})\s+(?P<hms>\d{2}:\d{2}:\d{2})",
)
_ERROR_RE = re.compile(r"\b(ERR|ERROR|WARN|WARNING|CRIT|CRITICAL)\b", re.IGNORECASE)


def parse_ufm_log_ts(line: str, tz: ZoneInfo | None) -> datetime | None:
    """Parse a UFM log line timestamp (e.g. '2026-02-06 16:26:02.688 ...')."""
    m = _UFM_LOG_TS_RE.match(line)
    if not m:
        return None
    dt = datetime.strptime(m.group("ts"), "%Y-%m-%d %H:%M:%S")
    if tz is not None:
        dt = dt.replace(tzinfo=tz)
    ms = m.group("ms")
    if ms:
        us = int((ms + "000000")[:6])
        dt = dt.replace(microsecond=us)
    return dt


def parse_sm_log_ts(line: str, tz: ZoneInfo | None, default_year: int) -> datetime | None:
    """Parse an SM log line timestamp (e.g. 'Feb 06 16:26:35 ...')."""
    m = _SM_LOG_TS_RE.match(line)
    if not m:
        return None
    dt = datetime.strptime(
        f"{default_year} {m.group('mon')} {m.group('day')} {m.group('hms')}",
        "%Y %b %d %H:%M:%S",
    )
    if tz is not None:
        dt = dt.replace(tzinfo=tz)
    # Handle year rollover: if parsed date is > 1 day in the future,
    # it's probably from the previous year
    now = datetime.now(tz) if tz else datetime.now()
    cutoff = now + timedelta(days=1)
    if dt > cutoff:
        dt = dt.replace(year=default_year - 1)
    return dt


def is_error_line(line: str) -> bool:
    """Check if a log line contains error/warning keywords."""
    return bool(_ERROR_RE.search(line))


def is_linkish(obj: dict[str, Any]) -> bool:
    """Heuristic: does this alarm/event dict look link-related?"""
    for k in ("type", "name", "category", "object_path", "description"):
        v = obj.get(k)
        if isinstance(v, str) and "link" in v.lower():
            return True
    return False


_ALARM_SUMMARY_KEYS = (
    "id",
    "name",
    "description",
    "severity",
    "timestamp",
    "type",
    "object_name",
    "resolved_name",
)
_EVENT_SUMMARY_KEYS = (
    "id",
    "severity",
    "name",
    "timestamp",
    "type",
    "object_name",
    "object_path",
    "description",
)


def summarize_alarm(a: dict[str, Any]) -> dict[str, Any]:
    """Project an alarm dict to the standard summary keys."""
    return {k: v for k in _ALARM_SUMMARY_KEYS if (v := a.get(k)) is not None}


def summarize_event(e: dict[str, Any]) -> dict[str, Any]:
    """Project an event dict to the standard summary keys."""
    return {k: e.get(k) for k in _EVENT_SUMMARY_KEYS}


# ----------------------------------------------------------------
#  GUID → hostname resolution
# ----------------------------------------------------------------


def build_guid_to_hostname_map(systems: list[Any]) -> dict[str, str]:
    """Build a GUID→hostname map from UFM systems data.

    Extracts system GUIDs, module GUIDs, and port GUIDs from the systems
    response and maps each to the system's hostname (system_name).
    All GUIDs are stored lowercased for case-insensitive lookup.
    """
    guid_map: dict[str, str] = {}
    for s in systems:
        if not isinstance(s, dict):
            continue
        hostname = str(s.get("system_name") or s.get("description") or "").strip()
        if not hostname:
            continue

        for key in ("system_guid", "guid", "name"):
            guid = str(s.get(key) or "").strip().lower()
            if guid and len(guid) > 4:
                guid_map[guid] = hostname

        for mod in s.get("modules") or []:
            if isinstance(mod, dict):
                mguid = str(mod.get("guid") or "").strip().lower()
                if mguid:
                    guid_map[mguid] = hostname

        for port in s.get("ports") or []:
            if isinstance(port, dict):
                pguid = str(port.get("guid") or "").strip().lower()
                if pguid:
                    guid_map[pguid] = hostname

    return guid_map


def resolve_pkey_guids_to_hosts(
    pkey_data: Any, guid_map: dict[str, str]
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Resolve pkey membership GUIDs to hostnames.

    Returns (host_summary, unresolved) where host_summary is a sorted list
    of per-host dicts and unresolved is a list of GUIDs that couldn't be mapped.
    """
    guids_list: list[Any] = []
    if isinstance(pkey_data, dict):
        guids_list = pkey_data.get("guids", [])
        if not guids_list and isinstance(pkey_data.get("data"), (dict, list)):
            inner = pkey_data["data"]
            if isinstance(inner, dict):
                guids_list = inner.get("guids", [])
            elif isinstance(inner, list):
                guids_list = inner
    elif isinstance(pkey_data, list):
        guids_list = pkey_data

    hosts: dict[str, dict[str, Any]] = {}
    unresolved: list[dict[str, str]] = []

    for entry in guids_list:
        if isinstance(entry, str):
            guid = entry.strip().lower()
            membership = "unknown"
        elif isinstance(entry, dict):
            guid = str(entry.get("guid") or "").strip().lower()
            membership = str(entry.get("membership") or "unknown")
        else:
            continue
        if not guid:
            continue

        hostname = guid_map.get(guid)
        if hostname:
            if hostname not in hosts:
                hosts[hostname] = {"guids": [], "membership_types": set()}
            hosts[hostname]["guids"].append(guid)
            hosts[hostname]["membership_types"].add(membership)
        else:
            unresolved.append({"guid": guid, "membership": membership})

    host_summary = []
    for hostname in sorted(hosts):
        info = hosts[hostname]
        host_summary.append(
            {
                "hostname": hostname,
                "guid_count": len(info["guids"]),
                "membership_types": sorted(info["membership_types"]),
                "guids": info["guids"],
            }
        )

    return host_summary, unresolved


def deduplicate_log_lines(lines: list[str]) -> list[str]:
    """Collapse consecutive identical log messages into summary lines.

    When consecutive lines share the same message body (after stripping the
    timestamp prefix), collapse them into a single line with a repeat count
    and the timespan they cover.
    """
    if not lines:
        return []

    def _strip_ts(line: str) -> str:
        m = _UFM_LOG_TS_RE.match(line)
        if m:
            return line[m.end() :].strip()
        m = _SM_LOG_TS_RE.match(line)
        if m:
            return line[m.end() :].strip()
        return line.strip()

    def _extract_ts(line: str) -> str | None:
        m = _UFM_LOG_TS_RE.match(line)
        if m:
            return m.group("ts")
        m = _SM_LOG_TS_RE.match(line)
        if m:
            return f"{m.group('mon')} {m.group('day')} {m.group('hms')}"
        return None

    result: list[str] = []
    prev_msg = _strip_ts(lines[0])
    count = 1
    first_ts = _extract_ts(lines[0])
    last_ts = first_ts
    representative = lines[0]

    for line in lines[1:]:
        msg = _strip_ts(line)
        if msg == prev_msg:
            count += 1
            last_ts = _extract_ts(line) or last_ts
        else:
            if count > 1:
                span = (
                    f"{first_ts} — {last_ts}"
                    if first_ts and last_ts and first_ts != last_ts
                    else ""
                )
                suffix = f" (x{count} in {span})" if span else f" (x{count})"
                result.append(f"{representative}{suffix}")
            else:
                result.append(representative)
            prev_msg = msg
            count = 1
            first_ts = _extract_ts(line)
            last_ts = first_ts
            representative = line

    if count > 1:
        span = f"{first_ts} — {last_ts}" if first_ts and last_ts and first_ts != last_ts else ""
        suffix = f" (x{count} in {span})" if span else f" (x{count})"
        result.append(f"{representative}{suffix}")
    else:
        result.append(representative)

    return result


def pkey_diff(current_hosts: list[str], expected_hosts: list[str]) -> dict[str, list[str]]:
    """Compute the diff between current and expected pkey host membership.

    Returns a dict with to_add, to_remove, and unchanged host lists.
    """
    current_set = set(current_hosts)
    expected_set = set(expected_hosts)
    return {
        "to_add": sorted(expected_set - current_set),
        "to_remove": sorted(current_set - expected_set),
        "unchanged": sorted(current_set & expected_set),
    }
