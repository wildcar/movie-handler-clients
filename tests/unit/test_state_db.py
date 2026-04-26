"""Unit tests for state_db.StateDb."""

from __future__ import annotations

from movie_handler_clients.core.state_db import StateDb


def test_user_upsert_and_admin_flag(tmp_path) -> None:  # type: ignore[no-untyped-def]
    db = StateDb(path=tmp_path / "s.sqlite")
    try:
        u1 = db.upsert_telegram_user(tg_user_id=42, display_name="A", chat_id=42)
        assert u1.is_admin is False

        # Re-upsert with admin=True promotes.
        u2 = db.upsert_telegram_user(tg_user_id=42, display_name="A", is_admin=True)
        assert u2.id == u1.id
        assert u2.is_admin is True

        # Subsequent upsert without admin must NOT demote (sticky).
        u3 = db.upsert_telegram_user(tg_user_id=42, is_admin=False)
        assert u3.is_admin is True
    finally:
        db.close()


def test_download_lifecycle(tmp_path) -> None:  # type: ignore[no-untyped-def]
    db = StateDb(path=tmp_path / "s.sqlite")
    try:
        u = db.upsert_telegram_user(tg_user_id=7, chat_id=7)
        d = db.add_download(
            user_id=u.id,
            info_hash="aabb" * 10,
            kind="movie",
            title="T",
            media_id="rt-12345",
            imdb_id="tt1",
            description="desc",
            poster_url="p",
        )
        assert d.state == "downloading"
        assert d.info_hash == ("aabb" * 10).upper()
        assert d.description == "desc"
        assert d.media_id == "rt-12345"

        # idempotent re-add — keeps id, refreshes title.
        d2 = db.add_download(
            user_id=u.id, info_hash="aabb" * 10, kind="movie", title="T2", media_id="rt-12345"
        )
        assert d2.id == d.id
        assert d2.title == "T2"
        # description preserved when not provided again.
        assert d2.description == "desc"

        # pending — increments attempts, keeps row in pending list.
        db.mark_pending_register(d.id, "boom")
        pending = db.list_pending()
        assert any(p.download.id == d.id for p in pending)
        assert pending[0].download.register_attempts == 1
        assert pending[0].download.state == "complete_pending_register"

        # mark registered → drops from pending.
        records = db.insert_watch_records(
            d.id,
            [
                {
                    "id": "tt1",
                    "watch_url": "https://w/watch/tt1",
                    "stream_url": "https://w/stream/tt1",
                    "file_path": "/x/a.mkv",
                }
            ],
        )
        assert len(records) == 1
        db.mark_registered(d.id)
        assert all(p.download.id != d.id for p in db.list_pending())

        stored = db.list_watch_records(d.id)
        assert stored[0].watch_url == "https://w/watch/tt1"
    finally:
        db.close()


def test_pending_includes_telegram_identity(tmp_path) -> None:  # type: ignore[no-untyped-def]
    db = StateDb(path=tmp_path / "s.sqlite")
    try:
        u = db.upsert_telegram_user(tg_user_id=99, chat_id=12345)
        db.add_download(
            user_id=u.id, info_hash="ff" * 20, kind="series", title="Show", media_id="rt-77"
        )
        pending = db.list_pending()
        assert len(pending) == 1
        assert pending[0].identity.platform == "telegram"
        assert pending[0].identity.external_id == "99"
        assert pending[0].identity.chat_id == "12345"
    finally:
        db.close()
