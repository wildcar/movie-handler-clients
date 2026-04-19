"""Movie-details + stub callbacks for trailer/download + back-to-list."""

from __future__ import annotations

import structlog
from aiogram import F, Router
from aiogram.types import CallbackQuery

from ...core.formatters import format_details, format_search_item, format_trailer_caption
from ...core.i18n import t
from ...core.mcp_client import MCPClientError, MovieMetadataMCPClient
from ...core.trailer_client import MovieTrailerMCPClient
from ..keyboards import details_keyboard, search_results_keyboard
from ..search_cache import SearchCache

router = Router(name="details")
log = structlog.get_logger(__name__)


@router.callback_query(F.data.startswith("d:"))
async def on_details(
    cq: CallbackQuery,
    mcp: MovieMetadataMCPClient,
) -> None:
    _, imdb_id, query_id = (cq.data or "").split(":", 2)
    tg_user_id = cq.from_user.id if cq.from_user else None

    try:
        payload = await mcp.call_tool(
            "get_movie_details", {"imdb_id": imdb_id}, tg_user_id=tg_user_id
        )
    except MCPClientError as exc:
        await cq.answer(t("details.error", detail=str(exc)), show_alert=True)
        return

    if err := payload.get("error"):
        await cq.answer(t("details.error", detail=_err_msg(err)), show_alert=True)
        return

    details = payload.get("details")
    if not isinstance(details, dict):
        await cq.answer(t("details.not_found"), show_alert=True)
        return

    caption = format_details(payload)
    poster = details.get("poster_url")
    kb = details_keyboard(imdb_id, query_id or None)

    if cq.message is None:
        await cq.answer()
        return

    if poster:
        try:
            await cq.message.answer_photo(
                photo=str(poster), caption=caption, parse_mode="HTML", reply_markup=kb
            )
        except Exception:
            log.exception("details.photo_failed", imdb_id=imdb_id)
            await cq.message.answer(caption, parse_mode="HTML", reply_markup=kb)
    else:
        await cq.message.answer(caption, parse_mode="HTML", reply_markup=kb)

    await cq.answer()


@router.callback_query(F.data.startswith("t:"))
async def on_trailer(
    cq: CallbackQuery,
    trailer: MovieTrailerMCPClient | None,
) -> None:
    imdb_id = (cq.data or "")[2:]
    tg_user_id = cq.from_user.id if cq.from_user else None

    if trailer is None or cq.message is None:
        await cq.answer(t("stub.trailer"), show_alert=True)
        return

    try:
        payload = await trailer.find_trailer(imdb_id, tg_user_id=tg_user_id)
    except MCPClientError as exc:
        log.warning("trailer.mcp_failed", error=str(exc))
        await cq.answer(t("trailer.error", detail=str(exc)), show_alert=True)
        return

    if err := payload.get("error"):
        await cq.answer(t("trailer.not_found"), show_alert=True)
        log.info("trailer.tool_error", imdb_id=imdb_id, error=err)
        return

    trailers = payload.get("results") or []
    if not trailers:
        await cq.answer(t("trailer.not_found"), show_alert=True)
        return

    # Post each trailer as its own message so Telegram builds a preview
    # card with the video player for each one.
    for trl in trailers:
        caption = format_trailer_caption(trl)
        await cq.message.answer(caption, parse_mode="HTML", disable_web_page_preview=False)
    await cq.answer()


@router.callback_query(F.data.startswith("dl:"))
async def on_download_stub(cq: CallbackQuery) -> None:
    await cq.answer(t("stub.download"), show_alert=True)


@router.callback_query(F.data.startswith("b:"))
async def on_back(
    cq: CallbackQuery,
    search_cache: SearchCache,
) -> None:
    query_id = (cq.data or "")[2:]
    entry = search_cache.get(query_id)
    if entry is None or cq.message is None:
        await cq.answer()
        return
    query, results = entry
    # Cache stores results already ordered (movies then series, year desc);
    # splitting by kind here keeps the section headers consistent.
    movies = [r for r in results if r.get("kind") != "series"]
    series = [r for r in results if r.get("kind") == "series"]
    sections: list[str] = [t("search.results_header", query=query)]
    if movies:
        sections.append(t("search.section.movies"))
        sections.extend(format_search_item(i) for i in movies)
    if series:
        sections.append(t("search.section.series"))
        sections.extend(format_search_item(i) for i in series)
    await cq.message.answer(
        "\n".join(sections),
        parse_mode="HTML",
        reply_markup=search_results_keyboard(results, query_id),
        disable_web_page_preview=True,
    )
    await cq.answer()


def _err_msg(err: object) -> str:
    if isinstance(err, dict):
        return str(err.get("message") or err.get("code") or err)
    return str(err)


__all__ = ["router"]
