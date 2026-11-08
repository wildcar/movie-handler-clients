"""/torrents: visibility, ownership checks, delete and tracker-update flows."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from movie_handler_clients.core.state_db import StateDb
from movie_handler_clients.telegram.handlers import torrents as mod

ADMIN = 1
ALICE = 2
BOB = 3
H_ALICE = "A" * 40
H_BOB = "B" * 40
H_FOREIGN = "C" * 40
H_NEW = "D" * 40


def _rt_row(h: str, name: str, *, state: str = "active", done: int = 50) -> dict[str, Any]:
    return {
        "hash": h,
        "name": name,
        "size_bytes": 100,
        "completed_bytes": done,
        "down_rate": 0,
        "state": state,
        "comment": "",
    }


@pytest.fixture
def db(tmp_path: Path) -> StateDb:
    db = StateDb(path=tmp_path / "s.sqlite")
    a = db.upsert_telegram_user(tg_user_id=ALICE, display_name="Alice", chat_id=ALICE)
    b = db.upsert_telegram_user(tg_user_id=BOB, display_name="Bob", chat_id=BOB)
    db.add_download(
        user_id=a.id,
        info_hash=H_ALICE,
        kind="series",
        title="Show",
        media_id="rt-111",
        source="rutracker",
    )
    db.add_download(
        user_id=b.id,
        info_hash=H_BOB,
        kind="movie",
        title="Film",
        media_id="rt-222",
        source="rutracker",
    )
    return db


def _rtorrent(rows: list[dict[str, Any]]) -> AsyncMock:
    rt = AsyncMock()
    rt.list_downloads.return_value = {"downloads": rows, "error": None}
    return rt


ROWS = [
    _rt_row(H_ALICE, "Show [1-8]"),
    _rt_row(H_BOB, "Film", state="complete", done=100),
    {**_rt_row(H_FOREIGN, "Manual"), "comment": "https://rutracker.org/forum/viewtopic.php?t=333"},
]


async def test_user_sees_only_own_downloads(db: StateDb) -> None:
    items = await mod._load_items(_rtorrent(ROWS), db, ALICE, is_admin=False)
    assert [i.hash for i in items] == [H_ALICE]


async def test_admin_sees_everything_with_owner(db: StateDb) -> None:
    items = await mod._load_items(_rtorrent(ROWS), db, ADMIN, is_admin=True)
    assert {i.hash for i in items} == {H_ALICE, H_BOB, H_FOREIGN}
    by_hash = {i.hash: i for i in items}
    assert by_hash[H_BOB].owner is not None and by_hash[H_BOB].owner.display_name == "Bob"
    assert by_hash[H_FOREIGN].owner is None
    # Topic id: from the bot row, else from the ruTorrent comment.
    assert by_hash[H_ALICE].topic_id == 111
    assert by_hash[H_FOREIGN].topic_id == 333
    text, _kb = mod.render_list(items, 0, is_admin=True)
    assert "не через бота" in text and "Bob" in text and "100.0%" in text


async def test_unknown_user_sees_nothing(db: StateDb) -> None:
    assert await mod._load_items(_rtorrent(ROWS), db, 999, is_admin=False) == []


def test_list_paginates() -> None:
    items = [
        mod.TorrentItem(torrent=_rt_row(f"{i:040X}", f"T{i}"), download=None, owner=None)
        for i in range(mod.PAGE_SIZE + 3)
    ]
    text, kb = mod.render_list(items, 1, is_admin=True)
    assert "стр. 2/2" in text
    # 3 items + nav row (◀ and refresh, no ▶ on the last page).
    assert len(kb.inline_keyboard) == 4
    nav = [b.callback_data for b in kb.inline_keyboard[-1]]
    assert nav == ["trt:l:0", "trt:l:1"]
    assert all(
        len((b.callback_data or "").encode()) <= 64 for row in kb.inline_keyboard for b in row
    )


def _cq(data: str, user_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=user_id),
        message=SimpleNamespace(
            edit_text=AsyncMock(), answer=AsyncMock(), chat=SimpleNamespace(id=user_id)
        ),
        answer=AsyncMock(),
    )


async def _call(cq: SimpleNamespace, rt: AsyncMock, db: StateDb, **kw: Any) -> None:
    await mod.on_torrents_callback(
        cq,  # type: ignore[arg-type]
        rtorrent=rt,
        torrent=kw.get("torrent"),
        state_db=db,
        admin_user_ids={ADMIN},
        media_watch=kw.get("media_watch"),
    )


async def test_user_cannot_touch_foreign_download(db: StateDb) -> None:
    rt = _rtorrent(ROWS)
    cq = _cq(f"trt:Dy:{H_BOB}", ALICE)
    await _call(cq, rt, db)
    rt.remove.assert_not_awaited()
    cq.answer.assert_awaited_once()
    assert cq.answer.call_args.kwargs.get("show_alert") is True


async def test_remove_keeps_files_and_cancels_pending_row(db: StateDb) -> None:
    rt = _rtorrent(ROWS)
    rt.remove.return_value = {"ok": True, "error": None}
    media_watch = AsyncMock()
    await _call(_cq(f"trt:dy:{H_ALICE}", ALICE), rt, db, media_watch=media_watch)
    rt.remove.assert_awaited_once_with(H_ALICE, delete_data=False, tg_user_id=ALICE)
    media_watch.delete.assert_not_awaited()
    row = db.get_download_by_hash(H_ALICE)
    assert row is not None and row.state == "cancelled"


async def test_remove_with_data_purges_watch_records_and_forgets_row(db: StateDb) -> None:
    rt = _rtorrent(ROWS)
    rt.remove.return_value = {"ok": True, "deleted_files": 9, "error": None}
    media_watch = AsyncMock()
    cq = _cq(f"trt:Dy:{H_ALICE}", ALICE)
    await _call(cq, rt, db, media_watch=media_watch)
    rt.remove.assert_awaited_once_with(H_ALICE, delete_data=True, tg_user_id=ALICE)
    media_watch.delete.assert_awaited_once_with("rt-111", purge=True)
    assert db.get_download_by_hash(H_ALICE) is None
    (text,), _ = cq.message.edit_text.call_args
    assert "9" in text


async def test_remove_with_data_foreign_torrent_skips_purge(db: StateDb) -> None:
    rt = _rtorrent(ROWS)
    rt.remove.return_value = {"ok": True, "deleted_files": 1, "error": None}
    media_watch = AsyncMock()
    await _call(_cq(f"trt:Dy:{H_FOREIGN}", ADMIN), rt, db, media_watch=media_watch)
    rt.remove.assert_awaited_once()
    media_watch.delete.assert_not_awaited()


async def test_remove_error_is_shown_and_row_kept(db: StateDb) -> None:
    rt = _rtorrent(ROWS)
    rt.remove.return_value = {"ok": False, "error": {"code": "unsafe_path", "message": "nope"}}
    cq = _cq(f"trt:Dy:{H_ALICE}", ALICE)
    await _call(cq, rt, db, media_watch=AsyncMock())
    assert db.get_download_by_hash(H_ALICE) is not None
    (text,), _ = cq.message.edit_text.call_args
    assert "nope" in text


async def test_update_unchanged(db: StateDb) -> None:
    rt = _rtorrent(ROWS)
    rt.replace_torrent.return_value = {"changed": False, "error": None}
    torrent = AsyncMock()
    torrent.get_torrent_file.return_value = {"file": {"content_base64": "Zm9v"}, "error": None}
    cq = _cq(f"trt:u:{H_ALICE}", ALICE)
    await _call(cq, rt, db, torrent=torrent)
    torrent.get_torrent_file.assert_awaited_once_with(111, tg_user_id=ALICE)
    (text,), _ = cq.message.edit_text.call_args
    assert "не изменился" in text
    assert db.get_download_by_hash(H_ALICE) is not None


async def test_update_changed_moves_row_to_new_hash(db: StateDb) -> None:
    rt = _rtorrent(ROWS)
    rt.replace_torrent.return_value = {
        "changed": True,
        "old_hash": H_ALICE,
        "download": _rt_row(H_NEW, "Show [1-9]", done=0),
        "error": None,
    }
    torrent = AsyncMock()
    torrent.get_torrent_file.return_value = {"file": {"content_base64": "Zm9v"}, "error": None}
    cq = _cq(f"trt:u:{H_ALICE}", ALICE)
    await _call(cq, rt, db, torrent=torrent)
    rt.replace_torrent.assert_awaited_once_with(
        H_ALICE, torrent_file_base64="Zm9v", tg_user_id=ALICE
    )
    assert db.get_download_by_hash(H_ALICE) is None
    moved = db.get_download_by_hash(H_NEW)
    assert moved is not None and moved.state == "downloading" and moved.media_id == "rt-111"
    (text,), kwargs = cq.message.edit_text.call_args
    assert "обновился" in text and "Show [1-9]" in text
    assert kwargs["reply_markup"].inline_keyboard[0][0].callback_data == f"trt:u:{H_NEW}"


async def test_update_without_topic(db: StateDb) -> None:
    rows = [_rt_row(H_FOREIGN, "Manual")]  # no comment, no bot row
    rt = _rtorrent(rows)
    torrent = AsyncMock()
    cq = _cq(f"trt:u:{H_FOREIGN}", ADMIN)
    await _call(cq, rt, db, torrent=torrent)
    torrent.get_torrent_file.assert_not_awaited()
    (text,), _ = cq.message.edit_text.call_args
    assert "rutracker" in text


def test_state_db_torrent_helpers(db: StateDb) -> None:
    alice = db.get_telegram_identity(ALICE)
    assert alice is not None
    assert [d.info_hash for d, _ in db.list_torrent_downloads(alice.user_id)] == [H_ALICE]
    dl = db.get_download_by_hash(H_ALICE)
    assert dl is not None
    db.insert_watch_records(
        dl.id,
        [{"id": "rt-111-s01e01", "watch_url": "u", "stream_url": "s", "file_path": "/f"}],
    )
    db.delete_download(dl.id)
    assert db.get_download_by_hash(H_ALICE) is None
    assert db.list_watch_records(dl.id) == []


async def test_delete_with_files_shows_progress_before_removing(db: StateDb) -> None:
    rt = _rtorrent(ROWS)
    cq = _cq(f"trt:Dy:{H_ALICE}", ALICE)
    seen: list[Any] = []

    async def _remove(*_a: Any, **_kw: Any) -> dict[str, Any]:
        # By the time rtorrent is asked, the confirm buttons must be gone.
        seen.append(cq.message.edit_text.call_args)
        return {"ok": True, "deleted_files": 1, "error": None}

    rt.remove.side_effect = _remove
    await _call(cq, rt, db, media_watch=AsyncMock())
    (text,), kwargs = seen[0]
    assert "файлы" in text and kwargs["reply_markup"] is None
    # Final message replaces the progress line.
    (final,), _ = cq.message.edit_text.call_args
    assert "удалена вместе с файлами" in final
