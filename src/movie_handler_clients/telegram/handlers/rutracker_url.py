"""Handle a pasted rutracker.org topic URL.

The user drops a link like ``https://rutracker.org/forum/viewtopic.php?t=6843582``
mid-chat. We fetch the topic title via ``rutracker-torrent-mcp.get_topic_info``,
clean it down to a ``Name (Year)`` form, run a metadata search, and surface:

- a single candidate → one preview message + ``tdl:`` confirm button (with imdb)
- several candidates → buttons «Это X» / «Это Y» / «Скачать без привязки»
- no candidates    → preview + ``tdl:`` confirm with empty imdb (rt-only id)

The actual .torrent fetch and rtorrent push happens in
``details.on_torrent_confirm`` (callback ``tdl:``); this module only wedges
the URL flow into the search → preview pipeline. The composite media id
(``rt-<topic_id>``) is computed downstream at insert time, so nothing about
imdb-vs-no-imdb leaks into ``state_db``.
"""

from __future__ import annotations

import re
from typing import Any

import structlog
from aiogram import F, Router
from aiogram.types import Message

from ...core.i18n import t
from ...core.mcp_client import MCPClientError, MovieMetadataMCPClient
from ...core.torrent_client import RutrackerTorrentMCPClient
from ..keyboards import rutracker_url_candidates_keyboard, torrent_confirm_keyboard
from ..movie_meta_cache import MovieMetaCache
from ..title_cache import TitleCache

router = Router(name="rutracker_url")
log = structlog.get_logger(__name__)


_RUTRACKER_URL_RE = re.compile(
    r"https?://rutracker\.org/forum/viewtopic\.php\?(?:[^\s]*&)?t=(\d+)",
    re.IGNORECASE,
)
# Matches the leading "Russian / English" pair that rutracker uploaders
# use almost universally. We keep only the part before the slash for the
# display title; both halves are kept for the metadata-search query.
_TITLE_SPLIT_RE = re.compile(r"\s+/\s+")
# Year sits inside the technical-tag bracket: «… [2024, США, фантастика, …]».
_YEAR_BRACKET_RE = re.compile(r"\[\s*(\d{4})\b")
_YEAR_BARE_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
# Trailing technical tags in parens or brackets — strip them off the
# display title.
_TRAIL_TECHNICAL_RE = re.compile(r"\s*[\[(].*$")

# Cap — Telegram inline keyboard rows + callback_data length both push
# back if we get greedy. 3 candidates + the unlink row = 4 buttons.
_MAX_CANDIDATES = 3


def detect_rutracker_topic_url(text: str) -> int | None:
    """Return the topic id if `text` *is* (or just contains) a rutracker
    topic URL; otherwise None."""
    m = _RUTRACKER_URL_RE.search(text or "")
    return int(m.group(1)) if m else None


@router.message(F.text.regexp(_RUTRACKER_URL_RE))
async def on_rutracker_url(
    message: Message,
    torrent: RutrackerTorrentMCPClient | None,
    mcp: MovieMetadataMCPClient,
    title_cache: TitleCache,
    movie_meta_cache: MovieMetaCache,
) -> None:
    topic_id = detect_rutracker_topic_url(message.text or "")
    if topic_id is None:
        return  # filter said yes, regex says no — nothing to do
    tg_user_id = message.from_user.id if message.from_user else None

    if torrent is None:
        await message.answer(t("stub.download"))
        return

    pending = await message.answer(t("rt_url.fetching"))

    try:
        topic_payload = await torrent.get_topic_info(topic_id, tg_user_id=tg_user_id)
    except MCPClientError as exc:
        log.warning("rt_url.topic_failed", error=str(exc))
        await pending.edit_text(t("rt_url.topic_failed", detail=str(exc)))
        return

    if err := topic_payload.get("error"):
        await pending.edit_text(t("rt_url.topic_failed", detail=_err_msg(err)))
        return

    topic = topic_payload.get("topic") or {}
    raw_title = str(topic.get("title") or "")
    if not raw_title:
        await pending.edit_text(t("rt_url.topic_failed", detail="empty title"))
        return

    display_title, search_title, year = _clean_topic_title(raw_title)
    candidates = await _find_candidates(mcp, search_title, year, tg_user_id=tg_user_id)
    # Pre-populate caches so the downstream `tdl:` confirm picks up the
    # right kind/poster without re-fetching.
    for c in candidates:
        imdb = str(c.get("imdb_id") or "")
        if not imdb:
            continue
        title_cache.put(
            imdb,
            str(c.get("title") or ""),
            int(c["year"]) if isinstance(c.get("year"), int) else None,
            "series" if c.get("kind") == "series" else "movie",
        )
        movie_meta_cache.put(
            imdb,
            description=str(c.get("description") or ""),
            poster_url=str(c.get("poster_url") or ""),
        )

    display_year = f" ({year})" if year else ""
    pretty_title = f"{display_title}{display_year}"

    if len(candidates) == 1:
        only = candidates[0]
        imdb_id = str(only.get("imdb_id") or "")
        await pending.edit_text(
            t(
                "download.confirm_message",
                title=_esc(pretty_title),
                url=_topic_url(topic_id),
            ),
            parse_mode="HTML",
            reply_markup=torrent_confirm_keyboard(topic_id, imdb_id),
            disable_web_page_preview=True,
        )
        return

    if not candidates:
        await pending.edit_text(
            t("rt_url.no_match_header", title=_esc(pretty_title)),
            parse_mode="HTML",
            reply_markup=torrent_confirm_keyboard(topic_id, ""),
            disable_web_page_preview=True,
        )
        return

    await pending.edit_text(
        t("rt_url.candidates_header", title=_esc(pretty_title)),
        parse_mode="HTML",
        reply_markup=rutracker_url_candidates_keyboard(topic_id, candidates),
        disable_web_page_preview=True,
    )


def _clean_topic_title(raw: str) -> tuple[str, str, int | None]:
    """Decompose the rutracker title into ``(display, search_query, year)``.

    `display` is the trimmed Russian (or first) title with no technical
    tags — what the user actually wants to see in chat.
    `search_query` keeps both Russian and English title halves (split by
    `/`) to give movie-metadata-mcp the best shot at matching.
    `year` is parsed from the ``[YYYY, …]`` bracket when present; bare
    4-digit fallback otherwise.
    """
    year: int | None = None
    m = _YEAR_BRACKET_RE.search(raw)
    if m:
        year = int(m.group(1))
    else:
        m2 = _YEAR_BARE_RE.search(raw)
        if m2:
            year = int(m2.group(1))

    body = _TRAIL_TECHNICAL_RE.sub("", raw).strip()
    if not body:
        body = raw.strip()

    parts = _TITLE_SPLIT_RE.split(body)
    display = parts[0].strip() if parts else body
    # Search with both halves joined — TMDB matches surprisingly well on
    # the bilingual blob, and it costs nothing on the API side.
    search_query = " ".join(p.strip() for p in parts if p.strip()) or display
    return display, search_query, year


async def _find_candidates(
    mcp: MovieMetadataMCPClient,
    search_query: str,
    year: int | None,
    *,
    tg_user_id: int | None,
) -> list[dict[str, Any]]:
    args: dict[str, Any] = {"title": search_query}
    if year is not None:
        args["year"] = year

    try:
        payload = await mcp.call_tool("search_movie", args, tg_user_id=tg_user_id)
    except MCPClientError as exc:
        log.warning("rt_url.search_failed", error=str(exc))
        return []
    if payload.get("error"):
        return []

    results = [r for r in (payload.get("results") or []) if r.get("imdb_id")]
    # Cheap ranking: prefer year matches, then descending by year so
    # newer releases bubble up.
    results.sort(
        key=lambda r: (
            1 if year is not None and r.get("year") == year else 0,
            r.get("year") or 0,
        ),
        reverse=True,
    )
    return results[:_MAX_CANDIDATES]


def _topic_url(topic_id: int) -> str:
    return f"https://rutracker.org/forum/viewtopic.php?t={topic_id}"


def _esc(s: str) -> str:
    from html import escape

    return escape(s)


def _err_msg(err: object) -> str:
    if isinstance(err, dict):
        return str(err.get("message") or err.get("code") or err)
    return str(err)
