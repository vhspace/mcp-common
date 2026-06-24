from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("redfish_mcp.agent_state_store")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), sort_keys=True, default=str)


def default_state_dir() -> Path:
    # Prefer explicit override, otherwise follow XDG cache layout.
    override = os.getenv("REDFISH_STATE_DIR")
    if override and override.strip():
        return Path(override).expanduser()

    xdg = os.getenv("XDG_CACHE_HOME")
    if xdg and xdg.strip():
        return Path(xdg).expanduser() / "redfish-mcp"

    return Path.home() / ".cache" / "redfish-mcp"


@dataclass(frozen=True)
class HostStats:
    host: str
    window_minutes: int
    calls_total: int
    calls_error: int
    tools_top: list[dict[str, Any]]
    last_called_at_ms: int | None


class AgentStateStore:
    """SQLite-backed store for tool-call events, observations, and hint cooldowns."""

    def __init__(
        self,
        *,
        site: str = "default",
        db_path: Path | None = None,
    ) -> None:
        self.site = site
        state_dir = default_state_dir()
        state_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path or (state_dir / "agent_state.sqlite3")

        # Keep the connection open for the process lifetime.
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()

        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA busy_timeout=3000")
            self._ensure_schema()

    def close(self) -> None:
        with self._lock, suppress(Exception):
            self._conn.close()

    def _ensure_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tool_call_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts_ms INTEGER NOT NULL,
              site TEXT NOT NULL,
              client_id TEXT NULL,
              request_id TEXT NULL,
              tool_name TEXT NOT NULL,
              hosts_json TEXT NOT NULL,
              ok INTEGER NOT NULL,
              duration_ms INTEGER NULL,
              request_meta_json TEXT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_tool_call_events_site_ts
              ON tool_call_events(site, ts_ms);
            CREATE INDEX IF NOT EXISTS idx_tool_call_events_site_tool
              ON tool_call_events(site, tool_name);

            CREATE TABLE IF NOT EXISTS observations (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts_ms INTEGER NOT NULL,
              site TEXT NOT NULL,
              host_key TEXT NOT NULL,
              kind TEXT NOT NULL,
              summary TEXT NOT NULL,
              details_json TEXT NULL,
              tags_json TEXT NULL,
              confidence REAL NULL,
              reporter_id TEXT NULL,
              expires_at_ms INTEGER NULL
            );

            CREATE INDEX IF NOT EXISTS idx_observations_site_host_ts
              ON observations(site, host_key, ts_ms);

            CREATE TABLE IF NOT EXISTS hint_log (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts_ms INTEGER NOT NULL,
              site TEXT NOT NULL,
              client_id TEXT NULL,
              host_key TEXT NOT NULL,
              hint_type TEXT NOT NULL,
              cooldown_until_ms INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_hint_log_site_host_type
              ON hint_log(site, host_key, hint_type);
            """
        )
        self._conn.commit()

    def record_tool_call(
        self,
        *,
        tool_name: str,
        hosts: list[str],
        ok: bool,
        duration_ms: int | None,
        request_id: str | None,
        client_id: str | None,
        request_meta: dict[str, Any] | None,
    ) -> None:
        # Avoid accidentally persisting secrets:
        # only keep our namespaced meta subtree.
        meta_to_store: dict[str, Any] | None = None
        if request_meta:
            namespaced = request_meta.get("together.ai/redfish-mcp")
            if namespaced is not None:
                meta_to_store = {"together.ai/redfish-mcp": namespaced}

        row = (
            _now_ms(),
            self.site,
            client_id,
            request_id,
            tool_name,
            _json_dumps(hosts),
            1 if ok else 0,
            duration_ms,
            _json_dumps(meta_to_store) if meta_to_store is not None else None,
        )
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO tool_call_events
                (
                  ts_ms,
                  site,
                  client_id,
                  request_id,
                  tool_name,
                  hosts_json,
                  ok,
                  duration_ms,
                  request_meta_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                row,
            )
            self._conn.commit()

    def add_observation(
        self,
        *,
        host_key: str,
        kind: str,
        summary: str,
        details: dict[str, Any] | None,
        tags: list[str] | None,
        confidence: float | None,
        reporter_id: str | None,
        ttl_hours: int | None,
    ) -> int:
        expires_at_ms: int | None = None
        if ttl_hours is not None and ttl_hours > 0:
            expires_at_ms = _now_ms() + int(ttl_hours) * 60 * 60 * 1000

        row = (
            _now_ms(),
            self.site,
            host_key.lower(),
            kind,
            summary,
            _json_dumps(details) if details is not None else None,
            _json_dumps(tags) if tags is not None else None,
            confidence,
            reporter_id,
            expires_at_ms,
        )
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO observations
                (
                  ts_ms,
                  site,
                  host_key,
                  kind,
                  summary,
                  details_json,
                  tags_json,
                  confidence,
                  reporter_id,
                  expires_at_ms
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                row,
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def list_observations(
        self,
        *,
        host_key: str,
        limit: int = 20,
        include_expired: bool = False,
    ) -> list[dict[str, Any]]:
        host_key_n = host_key.lower()
        now = _now_ms()
        params: list[Any] = [self.site, host_key_n]

        where = "site = ? AND host_key = ?"
        if not include_expired:
            where += " AND (expires_at_ms IS NULL OR expires_at_ms > ?)"
            params.append(now)

        params.append(max(1, min(limit, 200)))

        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT
                  id,
                  ts_ms,
                  kind,
                  summary,
                  details_json,
                  tags_json,
                  confidence,
                  reporter_id,
                  expires_at_ms
                FROM observations
                WHERE {where}
                ORDER BY ts_ms DESC
                LIMIT ?
                """,
                params,
            ).fetchall()

        out: list[dict[str, Any]] = []
        for r in rows:
            details = json.loads(r["details_json"]) if r["details_json"] else None
            tags = json.loads(r["tags_json"]) if r["tags_json"] else None
            out.append(
                {
                    "id": r["id"],
                    "ts_ms": r["ts_ms"],
                    "kind": r["kind"],
                    "summary": r["summary"],
                    "details": details,
                    "tags": tags,
                    "confidence": r["confidence"],
                    "reporter_id": r["reporter_id"],
                    "expires_at_ms": r["expires_at_ms"],
                }
            )
        return out

    def get_host_stats(self, *, host_key: str, window_minutes: int = 60) -> HostStats:
        host_key_n = host_key.lower()
        window_minutes = max(1, min(window_minutes, 24 * 60))
        cutoff = _now_ms() - window_minutes * 60 * 1000

        with self._lock:
            total = self._conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM tool_call_events
                WHERE site = ? AND ts_ms >= ? AND hosts_json LIKE ?
                """,
                (self.site, cutoff, f'%"{host_key_n}"%'),
            ).fetchone()["c"]
            err = self._conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM tool_call_events
                WHERE site = ? AND ts_ms >= ? AND hosts_json LIKE ? AND ok = 0
                """,
                (self.site, cutoff, f'%"{host_key_n}"%'),
            ).fetchone()["c"]
            last = self._conn.execute(
                """
                SELECT MAX(ts_ms) AS m
                FROM tool_call_events
                WHERE site = ? AND hosts_json LIKE ?
                """,
                (self.site, f'%"{host_key_n}"%'),
            ).fetchone()["m"]
            top = self._conn.execute(
                """
                SELECT tool_name, COUNT(*) AS c
                FROM tool_call_events
                WHERE site = ? AND ts_ms >= ? AND hosts_json LIKE ?
                GROUP BY tool_name
                ORDER BY c DESC
                LIMIT 10
                """,
                (self.site, cutoff, f'%"{host_key_n}"%'),
            ).fetchall()

        tools_top = [{"tool": r["tool_name"], "count": r["c"]} for r in top]
        last_ms = int(last) if last is not None else None
        return HostStats(
            host=host_key,
            window_minutes=window_minutes,
            calls_total=int(total),
            calls_error=int(err),
            tools_top=tools_top,
            last_called_at_ms=last_ms,
        )

    def recent_hosts(self, *, limit: int = 10) -> list[str]:
        """Return distinct host keys seen in recent tool calls, most recent first."""
        limit = max(1, min(limit, 100))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT DISTINCT hosts_json
                FROM tool_call_events
                WHERE site = ?
                ORDER BY ts_ms DESC
                LIMIT ?
                """,
                (self.site, limit * 5),
            ).fetchall()

        seen: set[str] = set()
        out: list[str] = []
        for r in rows:
            try:
                hosts = json.loads(r["hosts_json"])
            except Exception:
                continue
            if isinstance(hosts, list):
                for h in hosts:
                    if isinstance(h, str) and h not in seen:
                        seen.add(h)
                        out.append(h)
                        if len(out) >= limit:
                            return out
        return out

    def hint_in_cooldown(
        self,
        *,
        host_key: str,
        hint_type: str,
        client_id: str | None,
    ) -> bool:
        host_key_n = host_key.lower()
        now = _now_ms()
        with self._lock:
            row = self._conn.execute(
                """
                SELECT MAX(cooldown_until_ms) AS c
                FROM hint_log
                WHERE site = ?
                  AND host_key = ?
                  AND hint_type = ?
                  AND (client_id IS ? OR client_id = ?)
                """,
                (self.site, host_key_n, hint_type, client_id, client_id),
            ).fetchone()
        until = row["c"] if row else None
        return bool(until is not None and int(until) > now)

    def set_hint_cooldown(
        self,
        *,
        host_key: str,
        hint_type: str,
        client_id: str | None,
        cooldown_seconds: int,
    ) -> None:
        cooldown_seconds = max(1, min(cooldown_seconds, 24 * 60 * 60))
        now = _now_ms()
        until = now + cooldown_seconds * 1000
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO hint_log (ts_ms, site, client_id, host_key, hint_type, cooldown_until_ms)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (now, self.site, client_id, host_key.lower(), hint_type, until),
            )
            self._conn.commit()
