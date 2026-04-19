"""SQLite-backed log of every MCP tool call.

Schema is intentionally simple:

    traffic(id INTEGER PK AUTOINCREMENT,
            ts          TEXT NOT NULL,  -- ISO-8601 UTC
            tg_user_id  INTEGER,        -- nullable (not every call ties to a user)
            tool        TEXT NOT NULL,
            request     TEXT NOT NULL,  -- JSON
            response    TEXT,           -- JSON (null if error)
            duration_ms INTEGER NOT NULL,
            error       TEXT)           -- null on success

Rows older than ``ttl_days`` are deleted lazily inside ``record``.
No background job; no rotation.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS traffic (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    tg_user_id INTEGER,
    tool TEXT NOT NULL,
    request TEXT NOT NULL,
    response TEXT,
    duration_ms INTEGER NOT NULL,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_traffic_ts ON traffic(ts);
"""


class TrafficLog:
    def __init__(self, db_path: Path, ttl_days: int = 30) -> None:
        self._path = db_path
        self._ttl = timedelta(days=ttl_days)
        self._conn: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def record(
        self,
        *,
        tool: str,
        request: dict[str, Any],
        response: dict[str, Any] | None,
        duration_ms: int,
        tg_user_id: int | None = None,
        error: str | None = None,
    ) -> None:
        if self._conn is None:
            raise RuntimeError("TrafficLog.open() must be called before record()")
        now = datetime.now(UTC)
        await self._conn.execute(
            "INSERT INTO traffic(ts, tg_user_id, tool, request, response, duration_ms, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                now.isoformat(),
                tg_user_id,
                tool,
                json.dumps(request, ensure_ascii=False, sort_keys=True),
                None if response is None else json.dumps(response, ensure_ascii=False),
                duration_ms,
                error,
            ),
        )
        cutoff = (now - self._ttl).isoformat()
        await self._conn.execute("DELETE FROM traffic WHERE ts < ?", (cutoff,))
        await self._conn.commit()
