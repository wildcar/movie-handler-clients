"""Text-search flow: /start greeting + free-text search."""

from __future__ import annotations

import re
from typing import Any

import structlog
from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from ...core.formatters import format_search_item
from ...core.i18n import t
from ...core.mcp_client import MCPClientError, MovieMetadataMCPClient
from ..keyboards import search_results_keyboard
from ..search_cache import SearchCache

router = Router(name="search")
log = structlog.get_logger(__name__)


@router.message(CommandStart())
async def on_start(message: Message) -> None:
    await message.answer(t("start.greeting"))


@router.message()
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

    title, year = _split_title_year(query)
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
    results: list[dict[str, Any]] = [
        r for r in (payload.get("results") or []) if r.get("imdb_id")
    ]
    if not results:
        await message.answer(t("search.no_results", query=query))
        return

    query_id = search_cache.put(query, results)
    header = t("search.results_header", query=query)
    body = "\n\n".join(
        f"{i}. {format_search_item(item)}" for i, item in enumerate(results, start=1)
    )
    await message.answer(
        f"{header}\n\n{body}",
        parse_mode="HTML",
        reply_markup=search_results_keyboard(results, query_id),
        disable_web_page_preview=True,
    )


_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")


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
