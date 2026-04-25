"""SQLite-backed persistent state for users and downloads.

Replaces the in-memory ``DownloadTracker`` so that in-flight downloads,
user identities, and the watch links produced by media-watch-web survive
bot restarts. Schema is platform-agnostic: identities live in a separate
table keyed on ``(platform, external_id)`` so future VK/web frontends
attach without changing the rest of the schema.

Uses stdlib ``sqlite3`` with ``check_same_thread=False`` plus a single
threading lock — fine for the bot's volume (a handful of writes per
minute) and avoids pulling extra async-sqlite ceremony into hot paths.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import structlog

log = structlog.get_logger(__name__)

Platform = Literal["telegram", "web", "vk"]
DownloadState = Literal[
    "downloading",
    "complete_pending_register",
    "registered",
    "register_failed",
    "cancelled",
]

# Once a download has been polled this many times after completion without
# a successful media-watch register, give up and let an admin retry.
MAX_REGISTER_ATTEMPTS = 5


@dataclass(frozen=True)
class User:
    id: int
    display_name: str
    is_admin: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class Identity:
    user_id: int
    platform: str
    external_id: str
    chat_id: str | None
    meta: dict[str, Any]


@dataclass(frozen=True)
class Download:
    id: int
    user_id: int
    info_hash: str
    kind: str
    imdb_id: str | None
    title: str
    description: str
    poster_url: str
    state: DownloadState
    state_message: str
    source: str
    register_attempts: int
    created_at: str
    updated_at: str
    completed_at: str | None


@dataclass(frozen=True)
class WatchRecord:
    id: int
    download_id: int
    media_watch_id: str
    watch_url: str
    stream_url: str
    file_path: str
    season: int | None
    episode: int | None


@dataclass(frozen=True)
class DownloadWithUser:
    download: Download
    identity: Identity


@dataclass
class StateDb:
    """Repository facade. Open lazily; close at process shutdown."""

    path: Path
    _conn: sqlite3.Connection = field(init=False)
    _lock: threading.RLock = field(init=False, default_factory=threading.RLock)

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._tx() as cur:
            cur.execute("PRAGMA journal_mode = WAL")
            cur.execute("PRAGMA synchronous = NORMAL")
            cur.execute("PRAGMA foreign_keys = ON")
        self._ensure_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            cur = self._conn.cursor()
            try:
                yield cur
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            finally:
                cur.close()

    def _ensure_schema(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY,
            display_name  TEXT NOT NULL DEFAULT '',
            is_admin      INTEGER NOT NULL DEFAULT 0,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS user_identities (
            user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            platform     TEXT NOT NULL,
            external_id  TEXT NOT NULL,
            chat_id      TEXT,
            meta         TEXT NOT NULL DEFAULT '{}',
            created_at   TEXT NOT NULL,
            PRIMARY KEY (platform, external_id)
        );
        CREATE INDEX IF NOT EXISTS idx_user_identities_user
            ON user_identities(user_id);

        CREATE TABLE IF NOT EXISTS downloads (
            id                 INTEGER PRIMARY KEY,
            user_id            INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            info_hash          TEXT NOT NULL UNIQUE,
            kind               TEXT NOT NULL,
            imdb_id            TEXT,
            title              TEXT NOT NULL,
            description        TEXT NOT NULL DEFAULT '',
            poster_url         TEXT NOT NULL DEFAULT '',
            state              TEXT NOT NULL,
            state_message      TEXT NOT NULL DEFAULT '',
            source             TEXT NOT NULL DEFAULT '',
            register_attempts  INTEGER NOT NULL DEFAULT 0,
            created_at         TEXT NOT NULL,
            updated_at         TEXT NOT NULL,
            completed_at       TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_downloads_state ON downloads(state);
        CREATE INDEX IF NOT EXISTS idx_downloads_user ON downloads(user_id);

        CREATE TABLE IF NOT EXISTS watch_records (
            id              INTEGER PRIMARY KEY,
            download_id     INTEGER NOT NULL REFERENCES downloads(id) ON DELETE CASCADE,
            media_watch_id  TEXT NOT NULL,
            watch_url       TEXT NOT NULL,
            stream_url      TEXT NOT NULL,
            file_path       TEXT NOT NULL,
            season          INTEGER,
            episode         INTEGER,
            created_at      TEXT NOT NULL,
            UNIQUE (download_id, media_watch_id)
        );
        CREATE INDEX IF NOT EXISTS idx_watch_records_download
            ON watch_records(download_id);

        CREATE TABLE IF NOT EXISTS notifications (
            id           INTEGER PRIMARY KEY,
            user_id      INTEGER NOT NULL,
            download_id  INTEGER NOT NULL,
            platform     TEXT NOT NULL,
            status       TEXT NOT NULL,
            sent_at      TEXT NOT NULL
        );
        """
        with self._tx() as cur:
            cur.executescript(ddl)

    # ------------------------------------------------------------------ users

    def upsert_telegram_user(
        self,
        *,
        tg_user_id: int,
        display_name: str = "",
        chat_id: int | None = None,
        is_admin: bool = False,
        meta: dict[str, Any] | None = None,
    ) -> User:
        """Resolve (or create) the user behind a Telegram message.

        ``is_admin`` is bootstrap-only: if a user is *currently* in the
        admin allowlist, the flag is set to True; if they're not, the
        existing flag is *not* cleared automatically — admin demotion is
        a separate decision and we don't want a typo in the env var to
        silently revoke access.
        """
        now = _now_iso()
        platform: Platform = "telegram"
        external_id = str(tg_user_id)
        with self._tx() as cur:
            cur.execute(
                "SELECT user_id FROM user_identities WHERE platform=? AND external_id=?",
                (platform, external_id),
            )
            row = cur.fetchone()
            if row is not None:
                user_id = int(row["user_id"])
                cur.execute(
                    """
                    UPDATE users
                       SET display_name = COALESCE(NULLIF(?, ''), display_name),
                           is_admin     = CASE WHEN ?=1 THEN 1 ELSE is_admin END,
                           updated_at   = ?
                     WHERE id = ?
                    """,
                    (display_name, 1 if is_admin else 0, now, user_id),
                )
                cur.execute(
                    """
                    UPDATE user_identities
                       SET chat_id = COALESCE(?, chat_id),
                           meta    = ?
                     WHERE platform=? AND external_id=?
                    """,
                    (
                        str(chat_id) if chat_id is not None else None,
                        json.dumps(meta or {}, ensure_ascii=False),
                        platform,
                        external_id,
                    ),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO users (display_name, is_admin, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (display_name, 1 if is_admin else 0, now, now),
                )
                user_id = int(cur.lastrowid or 0)
                cur.execute(
                    """
                    INSERT INTO user_identities
                        (user_id, platform, external_id, chat_id, meta, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        platform,
                        external_id,
                        str(chat_id) if chat_id is not None else None,
                        json.dumps(meta or {}, ensure_ascii=False),
                        now,
                    ),
                )
            cur.execute("SELECT * FROM users WHERE id=?", (user_id,))
            user_row = cur.fetchone()
        return _row_to_user(user_row)

    def get_user(self, user_id: int) -> User | None:
        with self._tx() as cur:
            cur.execute("SELECT * FROM users WHERE id=?", (user_id,))
            row = cur.fetchone()
        return _row_to_user(row) if row else None

    def get_telegram_identity(self, tg_user_id: int) -> Identity | None:
        with self._tx() as cur:
            cur.execute(
                "SELECT * FROM user_identities WHERE platform='telegram' AND external_id=?",
                (str(tg_user_id),),
            )
            row = cur.fetchone()
        return _row_to_identity(row) if row else None

    # -------------------------------------------------------------- downloads

    def add_download(
        self,
        *,
        user_id: int,
        info_hash: str,
        kind: str,
        title: str,
        imdb_id: str | None = None,
        description: str = "",
        poster_url: str = "",
        source: str = "",
    ) -> Download:
        """Insert (or, if the same hash exists, return-as-is) a download row."""
        now = _now_iso()
        info_hash_norm = info_hash.upper()
        with self._tx() as cur:
            cur.execute(
                """
                INSERT INTO downloads (
                    user_id, info_hash, kind, imdb_id, title, description,
                    poster_url, state, state_message, source,
                    register_attempts, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'downloading', '', ?, 0, ?, ?)
                ON CONFLICT(info_hash) DO UPDATE SET
                    user_id = excluded.user_id,
                    title = excluded.title,
                    imdb_id = COALESCE(excluded.imdb_id, downloads.imdb_id),
                    description = CASE
                        WHEN excluded.description != '' THEN excluded.description
                        ELSE downloads.description END,
                    poster_url = CASE
                        WHEN excluded.poster_url != '' THEN excluded.poster_url
                        ELSE downloads.poster_url END,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    info_hash_norm,
                    kind,
                    imdb_id,
                    title,
                    description,
                    poster_url,
                    source,
                    now,
                    now,
                ),
            )
            cur.execute("SELECT * FROM downloads WHERE info_hash=?", (info_hash_norm,))
            row = cur.fetchone()
        return _row_to_download(row)

    def get_download_by_hash(self, info_hash: str) -> Download | None:
        with self._tx() as cur:
            cur.execute("SELECT * FROM downloads WHERE info_hash=?", (info_hash.upper(),))
            row = cur.fetchone()
        return _row_to_download(row) if row else None

    def list_pending(self) -> list[DownloadWithUser]:
        """All downloads still being polled — i.e. not yet registered or terminal."""
        with self._tx() as cur:
            cur.execute(
                """
                SELECT d.*, i.user_id AS i_user_id, i.platform, i.external_id,
                       i.chat_id, i.meta
                  FROM downloads d
                  JOIN user_identities i ON i.user_id = d.user_id
                                          AND i.platform = 'telegram'
                 WHERE d.state IN ('downloading', 'complete_pending_register')
                 ORDER BY d.id ASC
                """
            )
            rows = cur.fetchall()
        return [
            DownloadWithUser(download=_row_to_download(r), identity=_row_to_identity(r))
            for r in rows
        ]

    def list_user_active(self, user_id: int) -> list[Download]:
        """Downloads worth showing in /status — incomplete or recently registered."""
        with self._tx() as cur:
            cur.execute(
                """
                SELECT * FROM downloads
                 WHERE user_id=?
                 ORDER BY id DESC
                 LIMIT 50
                """,
                (user_id,),
            )
            rows = cur.fetchall()
        return [_row_to_download(r) for r in rows]

    def list_user_registered(self, user_id: int) -> list[Download]:
        """Downloads that successfully landed on media-watch — the «library»
        view powering /list. Newest first."""
        with self._tx() as cur:
            cur.execute(
                """
                SELECT * FROM downloads
                 WHERE user_id=? AND state='registered'
                 ORDER BY COALESCE(completed_at, updated_at) DESC, id DESC
                """,
                (user_id,),
            )
            rows = cur.fetchall()
        return [_row_to_download(r) for r in rows]

    def list_user_hashes(self, user_id: int) -> list[str]:
        with self._tx() as cur:
            cur.execute(
                """
                SELECT info_hash FROM downloads
                 WHERE user_id=? AND state IN ('downloading', 'complete_pending_register')
                """,
                (user_id,),
            )
            rows = cur.fetchall()
        return [str(r["info_hash"]) for r in rows]

    def mark_pending_register(self, download_id: int, message: str = "") -> None:
        now = _now_iso()
        with self._tx() as cur:
            cur.execute(
                """
                UPDATE downloads
                   SET state='complete_pending_register',
                       state_message=?,
                       register_attempts=register_attempts+1,
                       completed_at=COALESCE(completed_at, ?),
                       updated_at=?
                 WHERE id=?
                """,
                (message, now, now, download_id),
            )

    def mark_registered(self, download_id: int) -> None:
        now = _now_iso()
        with self._tx() as cur:
            cur.execute(
                """
                UPDATE downloads
                   SET state='registered',
                       state_message='',
                       completed_at=COALESCE(completed_at, ?),
                       updated_at=?
                 WHERE id=?
                """,
                (now, now, download_id),
            )

    def mark_register_failed(self, download_id: int, message: str) -> None:
        now = _now_iso()
        with self._tx() as cur:
            cur.execute(
                """
                UPDATE downloads
                   SET state='register_failed',
                       state_message=?,
                       completed_at=COALESCE(completed_at, ?),
                       updated_at=?
                 WHERE id=?
                """,
                (message, now, now, download_id),
            )

    def mark_cancelled(self, info_hash: str, message: str = "") -> None:
        now = _now_iso()
        with self._tx() as cur:
            cur.execute(
                """
                UPDATE downloads
                   SET state='cancelled',
                       state_message=?,
                       updated_at=?
                 WHERE info_hash=?
                """,
                (message, now, info_hash.upper()),
            )

    # --------------------------------------------------------- watch_records

    def insert_watch_records(
        self,
        download_id: int,
        records: Iterable[dict[str, Any]],
    ) -> list[WatchRecord]:
        now = _now_iso()
        out: list[WatchRecord] = []
        with self._tx() as cur:
            for r in records:
                cur.execute(
                    """
                    INSERT INTO watch_records (
                        download_id, media_watch_id, watch_url, stream_url,
                        file_path, season, episode, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(download_id, media_watch_id) DO UPDATE SET
                        watch_url = excluded.watch_url,
                        stream_url = excluded.stream_url,
                        file_path = excluded.file_path,
                        season = excluded.season,
                        episode = excluded.episode
                    """,
                    (
                        download_id,
                        r["id"],
                        r["watch_url"],
                        r["stream_url"],
                        r["file_path"],
                        r.get("season"),
                        r.get("episode"),
                        now,
                    ),
                )
            cur.execute(
                "SELECT * FROM watch_records WHERE download_id=? ORDER BY season, episode, id",
                (download_id,),
            )
            rows = cur.fetchall()
        for row in rows:
            out.append(_row_to_watch(row))
        return out

    def list_watch_records(self, download_id: int) -> list[WatchRecord]:
        with self._tx() as cur:
            cur.execute(
                "SELECT * FROM watch_records WHERE download_id=? ORDER BY season, episode, id",
                (download_id,),
            )
            rows = cur.fetchall()
        return [_row_to_watch(r) for r in rows]

    # --------------------------------------------------------- notifications

    def record_notification(
        self,
        *,
        user_id: int,
        download_id: int,
        platform: Platform,
        status: str,
    ) -> None:
        with self._tx() as cur:
            cur.execute(
                """
                INSERT INTO notifications (user_id, download_id, platform, status, sent_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, download_id, platform, status, _now_iso()),
            )


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _row_to_user(row: sqlite3.Row | None) -> User:
    assert row is not None
    return User(
        id=int(row["id"]),
        display_name=str(row["display_name"] or ""),
        is_admin=bool(row["is_admin"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _row_to_identity(row: sqlite3.Row) -> Identity:
    meta_raw = row["meta"] if "meta" in row.keys() else "{}"
    try:
        meta = json.loads(meta_raw or "{}")
    except json.JSONDecodeError:
        meta = {}
    chat_id = row["chat_id"]
    user_id_key = "i_user_id" if "i_user_id" in row.keys() else "user_id"
    return Identity(
        user_id=int(row[user_id_key]),
        platform=str(row["platform"]),
        external_id=str(row["external_id"]),
        chat_id=str(chat_id) if chat_id is not None else None,
        meta=meta if isinstance(meta, dict) else {},
    )


def _row_to_download(row: sqlite3.Row) -> Download:
    return Download(
        id=int(row["id"]),
        user_id=int(row["user_id"]),
        info_hash=str(row["info_hash"]),
        kind=str(row["kind"]),
        imdb_id=str(row["imdb_id"]) if row["imdb_id"] else None,
        title=str(row["title"]),
        description=str(row["description"] or ""),
        poster_url=str(row["poster_url"] or ""),
        state=str(row["state"]),  # type: ignore[arg-type]
        state_message=str(row["state_message"] or ""),
        source=str(row["source"] or ""),
        register_attempts=int(row["register_attempts"] or 0),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        completed_at=str(row["completed_at"]) if row["completed_at"] else None,
    )


def _row_to_watch(row: sqlite3.Row) -> WatchRecord:
    return WatchRecord(
        id=int(row["id"]),
        download_id=int(row["download_id"]),
        media_watch_id=str(row["media_watch_id"]),
        watch_url=str(row["watch_url"]),
        stream_url=str(row["stream_url"]),
        file_path=str(row["file_path"]),
        season=int(row["season"]) if row["season"] is not None else None,
        episode=int(row["episode"]) if row["episode"] is not None else None,
    )


__all__ = [
    "MAX_REGISTER_ATTEMPTS",
    "Download",
    "DownloadState",
    "DownloadWithUser",
    "Identity",
    "Platform",
    "StateDb",
    "User",
    "WatchRecord",
]
