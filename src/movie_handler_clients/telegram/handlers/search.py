"""Text-search flow: /start greeting + free-text search."""

from __future__ import annotations

import re
from typing import Any

import structlog
from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from ...core.formatters import plural_ru
from ...core.i18n import t
from ...core.mcp_client import MCPClientError, MovieMetadataMCPClient
from ..keyboards import search_results_keyboard
from ..search_cache import SearchCache

router = Router(name="search")
log = structlog.get_logger(__name__)


@router.message(CommandStart())
async def on_start(message: Message) -> None:
    await message.answer(t("start.greeting"))


@router.message(F.text.regexp(r"^[^/]"))
async def on_text(
    message: Message,
    mcp: MovieMetadataMCPClient,
    search_cache: SearchCache,
) -> None:
    query = (message.text or "").strip()
    if not query:
        await message.answer(t("search.empty_query"))
        return

    tg_user_id = message.from_user.id if message.from_user else None

    cleaned, kind_hint = _clean_query(query)
    title, year = _split_title_year(cleaned)
    if not title:
        await message.answer(t("search.empty_query"))
        return
    args: dict[str, Any] = {"title": title}
    if year is not None:
        args["year"] = year

    try:
        payload = await mcp.call_tool("search_movie", args, tg_user_id=tg_user_id)
    except MCPClientError as exc:
        log.warning("search.mcp_failed", error=str(exc))
        await message.answer(t("search.error", detail=str(exc)))
        return

    if err := payload.get("error"):
        await message.answer(t("search.error", detail=_err_msg(err)))
        return

    # Drop items without IMDb IDs: get_movie_details and all downstream MCPs
    # key off IMDb ID, so we can't act on them anyway.
    results: list[dict[str, Any]] = [r for r in (payload.get("results") or []) if r.get("imdb_id")]
    if kind_hint is not None:
        # User disambiguated ("сериал X" / "фильм X"); drop the other kind.
        results = [r for r in results if r.get("kind") == kind_hint]
    if not results:
        await message.answer(t("search.no_results", query=query))
        return

    movies = sorted(
        (r for r in results if r.get("kind") != "series"),
        key=_sort_key,
        reverse=True,
    )
    series = sorted(
        (r for r in results if r.get("kind") == "series"),
        key=_sort_key,
        reverse=True,
    )
    # Movies first, then series — both descending by year. The keyboard
    # follows the same order so button positions match the text sections.
    ordered = movies + series
    query_id = search_cache.put(query, ordered)

    # SearchV2 (final): message text carries only the pluralized count
    # summary — all hits live in the keyboard buttons in
    # «Title, YYYY, Country 🟢 7.9» form.
    summary_parts: list[str] = []
    if movies:
        summary_parts.append(
            f"🎦 {len(movies)} {plural_ru(len(movies), ('фильм', 'фильма', 'фильмов'))}"
        )
    if series:
        summary_parts.append(
            f"🧼 {len(series)} {plural_ru(len(series), ('сериал', 'сериала', 'сериалов'))}"
        )
    text = " · ".join(summary_parts) if summary_parts else t("search.no_results", query=query)

    await message.answer(
        text,
        reply_markup=search_results_keyboard(ordered, query_id),
        disable_web_page_preview=True,
    )


def _sort_key(item: dict[str, Any]) -> int:
    year = item.get("year")
    return year if isinstance(year, int) else -1


_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")

# Characters people wrap titles in: ASCII + CJK + Russian typographic quotes,
# plus parens/brackets. Stripped from both ends of the query.
_QUOTE_CHARS = "\"'«»„“”‘’‹›()[]{}<>"

# Lead/trail tokens that signal "I want a film" vs "I want a series".
# Regex word-boundaries don't cooperate with Cyrillic; we match as plain
# tokens surrounded by whitespace after punctuation stripping.
_KIND_HINT_MOVIE = {"фильм", "фильма", "кино", "film", "movie"}
_KIND_HINT_SERIES = {"сериал", "сериала", "шоу", "series", "show", "tv"}
_KIND_HINTS = _KIND_HINT_MOVIE | _KIND_HINT_SERIES


def _clean_query(text: str) -> tuple[str, str | None]:
    """Strip noise from a user's search query.

    - Removes typographic quotes and brackets wherever they appear.
    - Detects a leading or trailing "фильм"/"сериал"/etc. keyword and
      returns it as a ``kind`` hint (``"movie"`` | ``"series"``) so the
      caller can filter results accordingly.

    Returns ``(cleaned_title, kind_hint)``; the hint is ``None`` when no
    keyword is present.
    """

    s = text.strip()
    # Drop every quote/bracket character; users sometimes sprinkle them
    # inside the title ("Сериал «Отыграть назад»"), not just at the edges.
    s = s.translate({ord(c): " " for c in _QUOTE_CHARS})
    s = re.sub(r"\s+", " ", s).strip()

    kind_hint: str | None = None
    tokens = s.split(" ")
    while tokens and tokens[0].lower().rstrip(".,:-") in _KIND_HINTS:
        kind_hint = _hint_to_kind(tokens.pop(0))
    while tokens and tokens[-1].lower().rstrip(".,:-") in _KIND_HINTS:
        kind_hint = _hint_to_kind(tokens.pop()) or kind_hint

    return " ".join(tokens), kind_hint


def _hint_to_kind(token: str) -> str | None:
    t = token.lower().rstrip(".,:-")
    if t in _KIND_HINT_SERIES:
        return "series"
    if t in _KIND_HINT_MOVIE:
        return "movie"
    return None


def _split_title_year(query: str) -> tuple[str, int | None]:
    """Pull a 4-digit release year out of the free-text query, if present.

    Matches the first 19xx / 20xx occurrence; the remainder (minus the year)
    becomes the title. Returns ``(query, None)`` when no year is found.
    """
    m = _YEAR_RE.search(query)
    if not m:
        return query, None
    year = int(m.group(1))
    title = (query[: m.start()] + query[m.end() :]).strip(" -,.")
    title = re.sub(r"\s+", " ", title)
    return (title or query), year


def _err_msg(err: object) -> str:
    if isinstance(err, dict):
        return str(err.get("message") or err.get("code") or err)
    return str(err)
