"""Movie-details + stub callbacks for trailer/download + back-to-list."""

from __future__ import annotations

import base64

import structlog
from aiogram import F, Router
from aiogram.types import BufferedInputFile, CallbackQuery

from ...core.formatters import (
    format_details,
    format_search_item,
    format_torrent_list,
    format_trailer_caption,
)
from ...core.i18n import t
from ...core.mcp_client import MCPClientError, MovieMetadataMCPClient
from ...core.torrent_client import RutrackerTorrentMCPClient
from ...core.trailer_client import MovieTrailerMCPClient
from ..keyboards import details_keyboard, search_results_keyboard, torrent_list_keyboard
from ..search_cache import SearchCache
from ..title_cache import TitleCache

router = Router(name="details")
log = structlog.get_logger(__name__)


@router.callback_query(F.data.startswith("d:"))
async def on_details(
    cq: CallbackQuery,
    mcp: MovieMetadataMCPClient,
    title_cache: TitleCache,
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

    # Remember title+year for the later "⬇️ Скачать" callback, which only
    # carries the IMDb id — we don't want to re-fetch details just to
    # build the rutracker query.
    year_val = details.get("year")
    title_cache.put(
        imdb_id,
        str(details.get("title") or details.get("original_title") or ""),
        int(year_val) if isinstance(year_val, int) else None,
    )

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
async def on_download(
    cq: CallbackQuery,
    torrent: RutrackerTorrentMCPClient | None,
    title_cache: TitleCache,
) -> None:
    imdb_id = (cq.data or "")[3:]
    tg_user_id = cq.from_user.id if cq.from_user else None

    if torrent is None or cq.message is None:
        await cq.answer(t("stub.download"), show_alert=True)
        return

    cached = title_cache.get(imdb_id)
    if cached is None:
        # Should only happen if the bot restarted between details view and
        # download tap — tell the user to reopen the card.
        await cq.answer(t("download.reopen_card"), show_alert=True)
        return
    title, year = cached
    query = f"{title} {year}" if year else title

    # Stop the button spinner immediately — the rutracker search can take
    # longer than Telegram's ~15s callback_query TTL, after which cq.answer()
    # raises "query is too old" and the user sees nothing at all.
    await cq.answer(t("download.searching"))

    try:
        payload = await torrent.search_torrents(query, limit=10, tg_user_id=tg_user_id)
    except MCPClientError as exc:
        log.warning("torrent.search_failed", error=str(exc))
        await cq.message.answer(t("download.error", detail=str(exc)))
        return

    if err := payload.get("error"):
        code = (err or {}).get("code") if isinstance(err, dict) else None
        if code == "captcha_required":
            await cq.message.answer(t("download.captcha"))
        elif code == "not_configured":
            await cq.message.answer(t("download.not_configured"))
        else:
            await cq.message.answer(t("download.error", detail=_err_msg(err)))
        return

    results = payload.get("results") or []
    if not results:
        await cq.message.answer(t("download.no_results"))
        return

    header = t("download.list_header", query=query)
    body = format_torrent_list(results)
    await cq.message.answer(
        f"{header}\n{body}",
        parse_mode="HTML",
        reply_markup=torrent_list_keyboard(results),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data.startswith("tor:"))
async def on_torrent_pick(
    cq: CallbackQuery,
    torrent: RutrackerTorrentMCPClient | None,
) -> None:
    topic_id_str = (cq.data or "")[4:]
    try:
        topic_id = int(topic_id_str)
    except ValueError:
        await cq.answer()
        return
    tg_user_id = cq.from_user.id if cq.from_user else None

    if torrent is None or cq.message is None:
        await cq.answer(t("stub.download"), show_alert=True)
        return

    # Ack immediately; dl.php can take a while and we don't want the
    # callback_query TTL to expire before we reply.
    await cq.answer(t("download.fetching"))

    try:
        payload = await torrent.get_torrent_file(topic_id, tg_user_id=tg_user_id)
    except MCPClientError as exc:
        log.warning("torrent.download_failed", error=str(exc))
        await cq.message.answer(t("download.error", detail=str(exc)))
        return

    if err := payload.get("error"):
        code = (err or {}).get("code") if isinstance(err, dict) else None
        if code == "captcha_required":
            await cq.message.answer(t("download.captcha"))
        else:
            await cq.message.answer(t("download.error", detail=_err_msg(err)))
        return

    f = payload.get("file") or {}
    b64 = f.get("content_base64")
    filename = f.get("filename") or f"[rutracker.org].t{topic_id}.torrent"
    if not isinstance(b64, str):
        await cq.message.answer(t("download.error", detail="empty payload"))
        return

    blob = base64.b64decode(b64)
    await cq.message.answer_document(
        BufferedInputFile(blob, filename=filename),
        caption=t("download.sent_caption"),
    )


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
