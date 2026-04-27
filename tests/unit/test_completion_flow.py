"""Tests for the completion-poll handler in bot._process_one — wires
state_db, rtorrent (mocked), and media-watch-web (mocked) together.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from movie_handler_clients.core.media_watch_client import MediaWatchError
from movie_handler_clients.core.state_db import MAX_REGISTER_ATTEMPTS, StateDb
from movie_handler_clients.telegram.bot import _process_one


def _make_pending(db: StateDb, *, kind: str = "movie") -> tuple[StateDb, int]:
    user = db.upsert_telegram_user(tg_user_id=42, chat_id=42)
    dl = db.add_download(
        user_id=user.id,
        info_hash="cc" * 20,
        kind=kind,
        title="Movie X",
        media_id="rt-12345",
        imdb_id="tt1",
        description="d",
        poster_url="p",
    )
    return db, dl.id


async def test_completion_registers_and_notifies(tmp_path) -> None:  # type: ignore[no-untyped-def]
    db, dl_id = _make_pending(StateDb(path=tmp_path / "s.sqlite"))
    try:
        rtorrent = AsyncMock()
        rtorrent.get_download_status = AsyncMock(
            return_value={
                "download": {
                    "state": "complete",
                    "name": "Movie X",
                    # `directory` is the shared download dir; `base_path`
                    # is the actual file. Bot must prefer base_path so
                    # single-file torrents don't all land on the largest
                    # video file in the shared dir (real prod bug).
                    "directory": "/mnt/storage/Media/Video/Movie",
                    "base_path": "/mnt/storage/Media/Video/Movie/Movie X.mkv",
                },
                "error": None,
            }
        )
        media_watch = AsyncMock()
        media_watch.register = AsyncMock(
            return_value={
                "records": [
                    {
                        "id": "tt1",
                        "watch_url": "https://v.example/watch/tt1",
                        "stream_url": "https://v.example/stream/tt1",
                        "file_path": "/mnt/storage/Media/Video/Movie/Movie X/file.mkv",
                        "season": None,
                        "episode": None,
                    }
                ],
                "warnings": [],
            }
        )
        bot = SimpleNamespace(send_message=AsyncMock())

        entry = db.list_pending()[0]
        await _process_one(bot, rtorrent, None, db, media_watch, entry)  # type: ignore[arg-type]

        media_watch.register.assert_awaited_once()
        kwargs = media_watch.register.call_args.kwargs
        assert kwargs["path"] == "/mnt/storage/Media/Video/Movie/Movie X.mkv"
        assert kwargs["kind"] == "movie"
        assert kwargs["media_id"] == "rt-12345"

        bot.send_message.assert_awaited_once()
        sent_text = bot.send_message.call_args.args[1]
        assert "https://v.example/watch/tt1" in sent_text

        download = db.get_download_by_hash("cc" * 20)
        assert download is not None and download.state == "registered"
        assert len(db.list_watch_records(dl_id)) == 1
    finally:
        db.close()


async def test_completion_register_failure_retries_then_gives_up(tmp_path) -> None:  # type: ignore[no-untyped-def]
    db, _ = _make_pending(StateDb(path=tmp_path / "s.sqlite"))
    try:
        rtorrent = AsyncMock()
        rtorrent.get_download_status = AsyncMock(
            return_value={
                "download": {
                    "state": "complete",
                    "name": "X",
                    "directory": "/mnt/x",
                },
                "error": None,
            }
        )
        media_watch = AsyncMock()
        media_watch.register = AsyncMock(
            side_effect=MediaWatchError("server angry", status=500, body={})
        )
        bot = SimpleNamespace(send_message=AsyncMock())

        for _ in range(MAX_REGISTER_ATTEMPTS):
            entry = db.list_pending()[0]
            await _process_one(bot, rtorrent, None, db, media_watch, entry)  # type: ignore[arg-type]

        download = db.get_download_by_hash("cc" * 20)
        assert download is not None and download.state == "register_failed"
        bot.send_message.assert_awaited_once()
        assert "не удалась" in bot.send_message.call_args.args[1]
    finally:
        db.close()


async def test_no_media_watch_falls_back_to_legacy_message(tmp_path) -> None:  # type: ignore[no-untyped-def]
    db, _ = _make_pending(StateDb(path=tmp_path / "s.sqlite"))
    try:
        rtorrent = AsyncMock()
        rtorrent.get_download_status = AsyncMock(
            return_value={
                "download": {
                    "state": "complete",
                    "name": "Y",
                    "directory": "/mnt/y",
                },
                "error": None,
            }
        )
        bot = SimpleNamespace(send_message=AsyncMock())
        entry = db.list_pending()[0]
        await _process_one(bot, rtorrent, None, db, None, entry)  # type: ignore[arg-type]

        bot.send_message.assert_awaited_once()
        download = db.get_download_by_hash("cc" * 20)
        assert download is not None and download.state == "registered"
    finally:
        db.close()


async def test_ytdlp_completion_registers_via_output_path(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """yt-dlp branch: poller resolves output_path via get_download_status,
    then runs the same register-and-notify path as rtorrent."""
    db = StateDb(path=tmp_path / "s.sqlite")
    try:
        user = db.upsert_telegram_user(tg_user_id=42, chat_id=42)
        db.add_download(
            user_id=user.id,
            info_hash="task1234abcd",  # task_id, not BT info hash
            kind="movie",
            title="Veritasium video",
            media_id="yt-aBcDeFg1234",
            source="yt-dlp",
            description="d",
            poster_url="p",
        )

        yt_dlp = AsyncMock()
        yt_dlp.get_download_status = AsyncMock(
            return_value={
                "task": {
                    "task_id": "task1234abcd",
                    "state": "complete",
                    "output_path": "/mnt/storage/Media/Video/Clip/veritasium/foo.mp4",
                },
                "error": None,
            }
        )
        media_watch = AsyncMock()
        media_watch.register = AsyncMock(
            return_value={
                "records": [
                    {
                        "id": "yt-aBcDeFg1234",
                        "watch_url": "https://v.example/watch/yt-aBcDeFg1234",
                        "stream_url": "https://v.example/stream/yt-aBcDeFg1234",
                        "file_path": "/mnt/storage/Media/Video/Clip/veritasium/foo.mp4",
                        "season": None,
                        "episode": None,
                    }
                ],
                "warnings": [],
            }
        )
        bot = SimpleNamespace(send_message=AsyncMock())
        entry = db.list_pending()[0]
        await _process_one(bot, None, yt_dlp, db, media_watch, entry)  # type: ignore[arg-type]

        kwargs = media_watch.register.call_args.kwargs
        assert kwargs["path"] == "/mnt/storage/Media/Video/Clip/veritasium/foo.mp4"
        assert kwargs["media_id"] == "yt-aBcDeFg1234"

        download = db.get_download_by_hash("task1234abcd")
        assert download is not None and download.state == "registered"
    finally:
        db.close()


async def test_series_completion_lists_all_episodes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    db, _ = _make_pending(StateDb(path=tmp_path / "s.sqlite"), kind="series")
    try:
        rtorrent = AsyncMock()
        rtorrent.get_download_status = AsyncMock(
            return_value={
                "download": {
                    "state": "complete",
                    "name": "Show",
                    "directory": "/mnt/series/Show",
                },
                "error": None,
            }
        )
        media_watch = AsyncMock()
        media_watch.register = AsyncMock(
            return_value={
                "records": [
                    {
                        "id": "tt1-s01e01",
                        "watch_url": "https://v.example/watch/tt1-s01e01",
                        "stream_url": "https://v.example/stream/tt1-s01e01",
                        "file_path": "/mnt/series/Show/E01.mkv",
                        "season": 1,
                        "episode": 1,
                    },
                    {
                        "id": "tt1-s01e02",
                        "watch_url": "https://v.example/watch/tt1-s01e02",
                        "stream_url": "https://v.example/stream/tt1-s01e02",
                        "file_path": "/mnt/series/Show/E02.mkv",
                        "season": 1,
                        "episode": 2,
                    },
                ],
                "warnings": [],
            }
        )
        bot = SimpleNamespace(send_message=AsyncMock())
        entry = db.list_pending()[0]
        await _process_one(bot, rtorrent, None, db, media_watch, entry)  # type: ignore[arg-type]

        sent_text = bot.send_message.call_args.args[1]
        assert "S01E01" in sent_text and "S01E02" in sent_text
        assert sent_text.count("https://v.example/watch/") == 2
    finally:
        db.close()
