"""/list — the user's media library.

One row per title:
- movies / cartoons / single-file yt-dlp downloads → 🎬/🎨 + watch URL
- series → 📺 + link to the series index page on media-watch-web
  («Friends (1994) — 23 серии»). The index page lists every episode
  with its watch link, so the chat stays compact even for shows with
  many seasons.

Each entry is on its own line with a blank separator so the list is
scannable at a glance.
"""

from __future__ import annotations

from html import escape as _esc
from urllib.parse import urlparse

import structlog
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from ...core.formatters import plural_ru
from ...core.i18n import t
from ...core.state_db import Download, StateDb, WatchRecord

router = Router(name="list")
log = structlog.get_logger(__name__)


@router.message(Command("list"))
async def on_list(message: Message, state_db: StateDb) -> None:
    tg_user_id = message.from_user.id if message.from_user else None
    if tg_user_id is None:
        return

    identity = state_db.get_telegram_identity(tg_user_id)
    if identity is None:
        await message.answer(t("list.empty"))
        return

    downloads = state_db.list_user_registered(identity.user_id)
    if not downloads:
        await message.answer(t("list.empty"))
        return

    rows: list[str] = []
    for dl in downloads:
        records = state_db.list_watch_records(dl.id)
        if not records:
            continue
        if dl.kind == "series" and len(records) > 1:
            rows.append(_format_series(dl, records))
        else:
            rows.append(_format_movie(dl, records[0]))
    if not rows:
        await message.answer(t("list.empty"))
        return

    # Blank line between rows so adjacent titles don't blur together.
    await message.answer(
        t("list.header") + "\n\n" + "\n\n".join(rows),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


def _format_movie(dl: Download, rec: WatchRecord) -> str:
    if dl.kind == "cartoon":
        return t("list.cartoon_link", url=rec.watch_url, title=_esc(dl.title))
    return t("list.movie_link", url=rec.watch_url, title=_esc(dl.title))


def _format_series(dl: Download, records: list[WatchRecord]) -> str:
    n = len(records)
    series_url = _series_index_url(dl, records[0].watch_url)
    return t(
        "list.series_link",
        url=series_url,
        title=_esc(dl.title),
        n=n,
        episodes_word=plural_ru(n, ("серия", "серии", "серий")),
    )


def _series_index_url(dl: Download, sample_watch_url: str) -> str:
    """Build the URL of the series index page on media-watch-web.

    Reusing the scheme+host of any episode's watch URL avoids plumbing
    `media_watch_base_url` into this handler — every episode lives on
    the same host as the series page. ``dl.media_id`` is the parent
    composite id (e.g. ``rt-5273137``); episodes are stored under
    ``rt-5273137-s09e01``, the index aggregates them.
    """
    parsed = urlparse(sample_watch_url)
    base = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
    return f"{base}/series/{dl.media_id}"


__all__ = ["router"]
