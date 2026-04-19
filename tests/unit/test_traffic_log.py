from __future__ import annotations

import aiosqlite

from movie_handler_clients.core.traffic_log import TrafficLog


async def test_record_writes_row(traffic_log: TrafficLog) -> None:
    await traffic_log.record(
        tool="search_movie",
        request={"title": "Dune"},
        response={"results": []},
        duration_ms=42,
        tg_user_id=1001,
    )
    async with aiosqlite.connect(traffic_log._path) as conn:  # type: ignore[attr-defined]
        cur = await conn.execute("SELECT tool, tg_user_id, duration_ms, error FROM traffic")
        rows = await cur.fetchall()
    assert rows == [("search_movie", 1001, 42, None)]


async def test_record_purges_rows_older_than_ttl(tmp_path, monkeypatch) -> None:
    log = TrafficLog(tmp_path / "t.sqlite", ttl_days=1)
    await log.open()
    try:
        await log.record(tool="x", request={}, response={}, duration_ms=1, tg_user_id=None)
        # rewrite the row to look 2 days old, then trigger a purge via a fresh write
        async with aiosqlite.connect(log._path) as conn:  # type: ignore[attr-defined]
            await conn.execute("UPDATE traffic SET ts = '2000-01-01T00:00:00+00:00'")
            await conn.commit()
        await log.record(tool="y", request={}, response={}, duration_ms=1, tg_user_id=None)
        async with aiosqlite.connect(log._path) as conn:  # type: ignore[attr-defined]
            cur = await conn.execute("SELECT tool FROM traffic")
            rows = await cur.fetchall()
        assert rows == [("y",)]
    finally:
        await log.close()
