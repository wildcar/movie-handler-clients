"""Movie-details + stub callbacks for trailer/download + back-to-list."""

from __future__ import annotations

import base64

import structlog
from aiogram import F, Router
from aiogram.types import BufferedInputFile, CallbackQuery

from ...core.formatters import (
    format_details,
    format_trailer_caption,
    plural_ru,
)
from ...core.i18n import t
from ...core.mcp_client import MCPClientError, MovieMetadataMCPClient
from ...core.rtorrent_client import RtorrentMCPClient
from ...core.torrent_client import RutrackerTorrentMCPClient
from ...core.trailer_client import MovieTrailerMCPClient
from ..download_tracker import DownloadTracker
from ..keyboards import (
    details_keyboard,
    search_results_keyboard,
    torrent_all_keyboard,
    torrent_list_keyboard,
    trailer_alternatives_keyboard,
)
from ..search_cache import SearchCache
from ..title_cache import Kind, TitleCache
from ..torrent_cache import TorrentCache
from ..trailer_cache import TrailerCache

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
    kind_hint: Kind = "series" if details.get("kind") == "series" else "movie"
    title_cache.put(
        imdb_id,
        str(details.get("title") or details.get("original_title") or ""),
        int(year_val) if isinstance(year_val, int) else None,
        kind_hint,
    )

    caption = format_details(payload)
    poster = details.get("poster_url")
    kb = details_keyboard(imdb_id, query_id or None, kind=kind_hint)

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
    trailer_cache: TrailerCache,
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

    # TrailersV2: main trailer as its own message (Telegram builds the
    # YouTube preview card from the URL), then a compact "Другие варианты:"
    # bubble with the rest as inline buttons — taps reveal each URL.
    trailer_cache.put(imdb_id, trailers)

    main = trailers[0]
    await cq.message.answer(
        format_trailer_caption(main), parse_mode="HTML", disable_web_page_preview=False
    )

    if len(trailers) > 1:
        await cq.message.answer(
            t("trailer.alternatives"),
            reply_markup=trailer_alternatives_keyboard(
                trailers[1:], imdb_id=imdb_id, start_index=1
            ),
        )
    await cq.answer()


@router.callback_query(F.data.startswith("tr:"))
async def on_trailer_pick(
    cq: CallbackQuery,
    trailer_cache: TrailerCache,
) -> None:
    parts = (cq.data or "").split(":", 2)
    if len(parts) < 3 or cq.message is None:
        await cq.answer()
        return
    imdb_id = parts[1]
    try:
        idx = int(parts[2])
    except ValueError:
        await cq.answer()
        return
    trailers = trailer_cache.get(imdb_id)
    if not trailers or idx >= len(trailers):
        await cq.answer(t("trailer.not_found"), show_alert=True)
        return
    await cq.message.answer(
        format_trailer_caption(trailers[idx]),
        parse_mode="HTML",
        disable_web_page_preview=False,
    )
    await cq.answer()


@router.callback_query(F.data.startswith("dl:"))
async def on_download(
    cq: CallbackQuery,
    torrent: RutrackerTorrentMCPClient | None,
    title_cache: TitleCache,
    torrent_cache: TorrentCache,
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
    title, year, _kind = cached
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

    # Cache results so the «Показать ещё» callback can show the full list.
    torrent_cache.put(imdb_id, results)

    from html import escape as _esc
    text = t("download.list_header", query=_esc(query), n=len(results))
    await cq.message.answer(
        text,
        parse_mode="HTML",
        reply_markup=torrent_list_keyboard(results, imdb_id=imdb_id),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data.startswith("tor:"))
async def on_torrent_pick(
    cq: CallbackQuery,
    torrent: RutrackerTorrentMCPClient | None,
    rtorrent: RtorrentMCPClient | None,
    title_cache: TitleCache,
    tracker: DownloadTracker,
) -> None:
    # Callback shape: "tor:<topic_id>:<imdb_id>". The IMDb id is carried
    # so we can pull the kind hint (movie vs series) from the title cache
    # without another round-trip — it decides which directory on the
    # media server the download lands in.
    parts = (cq.data or "").split(":", 2)
    if len(parts) < 2:
        await cq.answer()
        return
    try:
        topic_id = int(parts[1])
    except ValueError:
        await cq.answer()
        return
    imdb_id = parts[2] if len(parts) > 2 else ""
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

    # Prefer sending the torrent to the media server; fall back to
    # shipping the .torrent as a Telegram document only when rtorrent-mcp
    # is not configured or errors out. Cached kind (movie/series) routes
    # the payload to the matching /mnt/storage/Media/Video/{Movie,Series}
    # directory on the server side.
    kind: Kind | None = None
    cached = title_cache.get(imdb_id) if imdb_id else None
    if cached is not None:
        kind = cached[2]

    if rtorrent is not None:
        source_url = f"https://rutracker.org/forum/viewtopic.php?t={topic_id}"
        ok = await _try_send_to_rtorrent(
            cq, rtorrent, tracker=tracker, b64=b64, kind=kind,
            comment=source_url, tg_user_id=tg_user_id,
        )
        if ok:
            return  # done — no fallback needed

    blob = base64.b64decode(b64)
    await cq.message.answer_document(
        BufferedInputFile(blob, filename=filename),
        caption=t("download.sent_caption"),
    )


async def _try_send_to_rtorrent(
    cq: CallbackQuery,
    rtorrent: RtorrentMCPClient,
    *,
    tracker: DownloadTracker,
    b64: str,
    kind: Kind | None,
    comment: str | None = None,
    tg_user_id: int | None,
) -> bool:
    """Push the .torrent to rtorrent-mcp. Returns True on success so the
    outer handler can skip the Telegram-document fallback."""
    assert cq.message is not None
    try:
        payload = await rtorrent.add_torrent(
            torrent_file_base64=b64, kind=kind, comment=comment, tg_user_id=tg_user_id
        )
    except MCPClientError as exc:
        log.warning("rtorrent.add_failed", error=str(exc))
        return False
    if err := payload.get("error"):
        log.warning("rtorrent.add_tool_error", error=err)
        return False
    dl = payload.get("download") or {}
    name = str(dl.get("name") or "").strip()
    hash_ = str(dl.get("hash") or "").strip()

    if hash_ and tg_user_id is not None:
        tracker.track(hash_, tg_user_id, name)

    dest_key = "download.sent_to_server_series" if kind == "series" else "download.sent_to_server"
    from html import escape as _esc

    message = t(dest_key, name=_esc(name)) if name else t("download.sent_to_server_noname")
    await cq.message.answer(message, parse_mode="HTML")
    return True


@router.callback_query(F.data.startswith("torall:"))
async def on_torrent_show_all(
    cq: CallbackQuery,
    torrent_cache: TorrentCache,
) -> None:
    imdb_id = (cq.data or "")[7:]
    results = torrent_cache.get(imdb_id) if imdb_id else None
    if not results or cq.message is None:
        await cq.answer()
        return
    await cq.message.answer(
        t("download.all_header"),
        reply_markup=torrent_all_keyboard(results, imdb_id=imdb_id),
    )
    await cq.answer()


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
    _query, results = entry
    movies = [r for r in results if r.get("kind") != "series"]
    series = [r for r in results if r.get("kind") == "series"]
    summary_parts: list[str] = []
    if movies:
        summary_parts.append(
            f"🎦 {len(movies)} {plural_ru(len(movies), ('фильм', 'фильма', 'фильмов'))}"
        )
    if series:
        summary_parts.append(
            f"🧼 {len(series)} {plural_ru(len(series), ('сериал', 'сериала', 'сериалов'))}"
        )
    text = " · ".join(summary_parts) if summary_parts else t("details.not_found")
    await cq.message.answer(
        text,
        reply_markup=search_results_keyboard(results, query_id),
        disable_web_page_preview=True,
    )
    await cq.answer()


def _err_msg(err: object) -> str:
    if isinstance(err, dict):
        return str(err.get("message") or err.get("code") or err)
    return str(err)


__all__ = ["router"]
