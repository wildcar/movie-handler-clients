"""/list — the user's media library.

Each row is a hyperlink whose visible text is the movie/series title (with
season/episode for series) and whose target is the watch URL on media-
watch-web. Telegram renders the link inline."""

from __future__ import annotations

from html import escape as _esc

import structlog
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

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

    lines: list[str] = [t("list.header"), ""]
    for dl in downloads:
        records = state_db.list_watch_records(dl.id)
        if not records:
            continue
        if dl.kind == "series" and len(records) > 1:
            lines.append(_format_series(dl, records))
        else:
            lines.append(_format_movie(dl, records[0]))
    if len(lines) <= 2:
        await message.answer(t("list.empty"))
        return

    await message.answer(
        "\n".join(lines),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


def _format_movie(dl: Download, rec: WatchRecord) -> str:
    if dl.kind == "cartoon":
        return t("list.cartoon_link", url=rec.watch_url, title=_esc(dl.title))
    return t("list.movie_link", url=rec.watch_url, title=_esc(dl.title))


def _format_series(dl: Download, records: list[WatchRecord]) -> str:
    head = t("list.series_header", title=_esc(dl.title))
    episode_lines: list[str] = []
    for r in records:
        if r.season is not None and r.episode is not None:
            episode_lines.append(
                t("list.series_episode", url=r.watch_url, season=r.season, episode=r.episode)
            )
        else:
            episode_lines.append(t("list.series_extra", url=r.watch_url))
    return head + "\n" + "\n".join(episode_lines)


__all__ = ["router"]
