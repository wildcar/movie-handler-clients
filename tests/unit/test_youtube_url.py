from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from movie_handler_clients.telegram.handlers import youtube_url as youtube_url_mod
from movie_handler_clients.telegram.ydl_cache import YtDlpCache


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
