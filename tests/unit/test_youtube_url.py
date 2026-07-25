from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile

from movie_handler_clients.telegram.handlers import youtube_url as youtube_url_mod
from movie_handler_clients.telegram.ydl_cache import YtDlpCache, YtDlpEntry


async def test_youtube_antibot_error_does_not_call_valid_url_unsupported() -> None:
    url = "https://youtu.be/LHwUTx8kdjM?is=Ba9BQ4M2oa7fEGLv"
    pending = SimpleNamespace(edit_text=AsyncMock())
    message = SimpleNamespace(
        text=url,
        from_user=SimpleNamespace(id=42),
        answer=AsyncMock(return_value=pending),
    )
    yt_dlp = AsyncMock()
    yt_dlp.probe.return_value = {
        "error": {
            "code": "upstream_error",
            "message": "Sign in to confirm you’re not a bot. Use --cookies.",
        }
    }

    await youtube_url_mod.on_url(  # type: ignore[arg-type]
        message,
        yt_dlp=yt_dlp,
        ydl_cache=YtDlpCache(),
    )

    yt_dlp.probe.assert_awaited_once_with(url, tg_user_id=42)
    pending.edit_text.assert_awaited_once()
    (body,), _ = pending.edit_text.call_args
    assert "YouTube" in body
    assert "ссылка" in body.lower()
    assert "не распознана" not in body


def test_probe_error_key_keeps_unsupported_distinct() -> None:
    assert (
        youtube_url_mod._probe_error_key(
            {"code": "unsupported", "message": "No suitable extractor"}
        )
        == "ydl.unsupported"
    )
    assert (
        youtube_url_mod._probe_error_key(
            {"code": "upstream_error", "message": "Temporary upstream failure"}
        )
        == "ydl.probe_failed"
    )


async def test_confirm_reports_start_download_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = YtDlpCache()
    cache.put("token", YtDlpEntry(url="https://youtu.be/abc", title="Video"))
    message = SimpleNamespace(
        edit_reply_markup=AsyncMock(),
        answer=AsyncMock(),
        chat=SimpleNamespace(id=100),
    )
    cq = SimpleNamespace(
        data="ydl:token",
        message=message,
        from_user=SimpleNamespace(
            id=42,
            first_name="Test",
            last_name="User",
            username="test_user",
        ),
        answer=AsyncMock(),
    )
    yt_dlp = AsyncMock()

    async def hang(*args: object, **kwargs: object) -> None:
        await asyncio.Event().wait()

    yt_dlp.start_download.side_effect = hang
    monkeypatch.setattr(youtube_url_mod, "_START_TIMEOUT_SECONDS", 0.01)

    await youtube_url_mod.on_confirm(  # type: ignore[arg-type]
        cq,
        yt_dlp=yt_dlp,
        ydl_cache=cache,
        state_db=AsyncMock(),
        admin_user_ids=set(),
    )

    message.answer.assert_awaited_once()
    (body,), _ = message.answer.call_args
    assert "не ответил" in body


async def test_unexpected_failure_replaces_pending_bubble() -> None:
    """A crash mid-probe must not leave «Смотрю видео…» hanging forever."""
    url = "https://www.1tv.ru/-/skrlsx"
    pending = SimpleNamespace(edit_text=AsyncMock())
    message = SimpleNamespace(
        text=url,
        from_user=SimpleNamespace(id=42),
        answer=AsyncMock(return_value=pending),
    )
    yt_dlp = AsyncMock()
    yt_dlp.probe.side_effect = RuntimeError("transport blew up")

    await youtube_url_mod.on_url(  # type: ignore[arg-type]
        message,
        yt_dlp=yt_dlp,
        ydl_cache=YtDlpCache(),
    )

    pending.edit_text.assert_awaited_once()
    (body,), _ = pending.edit_text.call_args
    assert "внутренняя ошибка" in body.lower()


async def test_cancellation_propagates_without_swallowing() -> None:
    """Genuine cancellation (bot shutting down) must not be turned into copy."""
    pending = SimpleNamespace(edit_text=AsyncMock())
    message = SimpleNamespace(
        text="https://youtu.be/abc",
        from_user=SimpleNamespace(id=42),
        answer=AsyncMock(return_value=pending),
    )
    yt_dlp = AsyncMock()
    yt_dlp.probe.side_effect = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await youtube_url_mod.on_url(  # type: ignore[arg-type]
            message,
            yt_dlp=yt_dlp,
            ydl_cache=YtDlpCache(),
        )

    pending.edit_text.assert_not_awaited()


def _probe_payload() -> dict:
    return {
        "probe": {
            "video_id": "874850",
            "title": "Наперегонки со временем",
            "channel": "Первый канал",
            "duration_seconds": 2820,
            "thumbnails": [{"url": "https://static.1tv.ru/splash.jpg", "width": 1280,
                            "height": 720}],
        }
    }


def _bad_request(message: str) -> TelegramBadRequest:
    return TelegramBadRequest(method=SimpleNamespace(), message=message)  # type: ignore[arg-type]


async def test_thumbnail_refused_by_telegram_falls_back_to_a_text_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Telegram can't fetch static.1tv.ru; the card must still arrive."""
    pending = SimpleNamespace(edit_text=AsyncMock(), delete=AsyncMock(), answer=AsyncMock())
    message = SimpleNamespace(
        text="https://www.1tv.ru/-/skrlsx",
        from_user=SimpleNamespace(id=42),
        answer=AsyncMock(return_value=pending),
        answer_photo=AsyncMock(side_effect=_bad_request("wrong type of the web page content")),
    )
    yt_dlp = AsyncMock()
    yt_dlp.probe.return_value = _probe_payload()
    # Our own fetch attempt is out of scope here — skip straight to the text card.
    monkeypatch.setattr(youtube_url_mod, "_fetch_thumbnail", AsyncMock(return_value=None))

    await youtube_url_mod.on_url(  # type: ignore[arg-type]
        message,
        yt_dlp=yt_dlp,
        ydl_cache=YtDlpCache(),
    )

    # The bubble becomes the card instead of being deleted and left empty.
    pending.delete.assert_not_awaited()
    pending.edit_text.assert_awaited_once()
    (body,), kwargs = pending.edit_text.call_args
    assert "Наперегонки" in body
    assert kwargs["reply_markup"].inline_keyboard[0][0].callback_data.startswith("ydl:")


async def test_thumbnail_refused_by_url_is_uploaded_as_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending = SimpleNamespace(edit_text=AsyncMock(), delete=AsyncMock(), answer=AsyncMock())
    photo = BufferedInputFile(b"jpegbytes", filename="splash.jpg")
    message = SimpleNamespace(
        text="https://www.1tv.ru/-/skrlsx",
        from_user=SimpleNamespace(id=42),
        answer=AsyncMock(return_value=pending),
        answer_photo=AsyncMock(
            side_effect=[_bad_request("wrong type of the web page content"), None]
        ),
    )
    yt_dlp = AsyncMock()
    yt_dlp.probe.return_value = _probe_payload()
    monkeypatch.setattr(youtube_url_mod, "_fetch_thumbnail", AsyncMock(return_value=photo))

    await youtube_url_mod.on_url(  # type: ignore[arg-type]
        message,
        yt_dlp=yt_dlp,
        ydl_cache=YtDlpCache(),
    )

    assert message.answer_photo.await_count == 2
    assert message.answer_photo.await_args.kwargs["photo"] is photo
    pending.delete.assert_awaited_once()
    pending.edit_text.assert_not_awaited()


async def test_failure_after_the_bubble_is_gone_still_reports() -> None:
    """edit_text on a deleted bubble must not swallow the error report."""
    pending = SimpleNamespace(
        edit_text=AsyncMock(side_effect=_bad_request("message to edit not found")),
        answer=AsyncMock(),
    )

    await youtube_url_mod._safe_edit(pending, "boom")  # type: ignore[arg-type]

    pending.answer.assert_awaited_once_with("boom")
