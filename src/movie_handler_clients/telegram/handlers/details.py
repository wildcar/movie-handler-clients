"""Movie-details + stub callbacks for trailer/download + back-to-list."""

from __future__ import annotations

import base64
import re

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
from ...core.state_db import StateDb
from ...core.torrent_client import RutrackerTorrentMCPClient
from ...core.trailer_client import MovieTrailerMCPClient
from ..keyboards import (
    details_keyboard,
    search_results_keyboard,
    season_picker_keyboard,
    torrent_confirm_keyboard,
    torrent_list_keyboard,
    trailer_alternatives_keyboard,
)
from ..movie_meta_cache import MovieMetaCache
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
    search_cache: SearchCache,
    movie_meta_cache: MovieMetaCache,
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
    seasons_val = details.get("number_of_seasons")
    kind_hint = _kind_from_search_cache(search_cache, query_id, imdb_id)
    if kind_hint is None:
        kind_hint = "series" if details.get("kind") == "series" else "movie"
    title_cache.put(
        imdb_id,
        str(details.get("title") or details.get("original_title") or ""),
        int(year_val) if isinstance(year_val, int) else None,
        kind_hint,
        int(seasons_val) if isinstance(seasons_val, int) and seasons_val > 0 else None,
    )
    # Stash extra fields so the download tap can persist them into the
    # Download row without re-fetching get_movie_details.
    movie_meta_cache.put(
        imdb_id,
        description=str(details.get("plot") or details.get("overview") or ""),
        poster_url=str(details.get("poster_url") or ""),
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

    if torrent is None or cq.message is None:
        await cq.answer(t("stub.download"), show_alert=True)
        return

    cached = title_cache.get(imdb_id)
    if cached is None:
        # Should only happen if the bot restarted between details view and
        # download tap — tell the user to reopen the card.
        await cq.answer(t("download.reopen_card"), show_alert=True)
        return
    _title, _year, kind, seasons = cached

    # Series get an intermediate season picker before the rutracker search
    # so the user can narrow the query to a specific season. Movies (and
    # series with no known season count) skip straight to the search.
    if kind == "series" and seasons and seasons > 0:
        await cq.answer()
        await cq.message.answer(
            t("download.pick_season"),
            reply_markup=season_picker_keyboard(imdb_id, seasons),
        )
        return

    await _run_torrent_search(cq, torrent, torrent_cache, title_cache, imdb_id, season=None)


@router.callback_query(F.data.startswith("dls:"))
async def on_download_season(
    cq: CallbackQuery,
    torrent: RutrackerTorrentMCPClient | None,
    title_cache: TitleCache,
    torrent_cache: TorrentCache,
) -> None:
    # Callback shape: "dls:<imdb_id>:<season>".
    parts = (cq.data or "").split(":", 2)
    if len(parts) < 3:
        await cq.answer()
        return
    imdb_id = parts[1]
    try:
        season = int(parts[2])
    except ValueError:
        await cq.answer()
        return
    if torrent is None or cq.message is None:
        await cq.answer(t("stub.download"), show_alert=True)
        return
    if title_cache.get(imdb_id) is None:
        await cq.answer(t("download.reopen_card"), show_alert=True)
        return

    await _run_torrent_search(
        cq, torrent, torrent_cache, title_cache, imdb_id, season=season
    )


@router.callback_query(F.data.startswith("dla:"))
async def on_download_all_seasons(
    cq: CallbackQuery,
    torrent: RutrackerTorrentMCPClient | None,
    title_cache: TitleCache,
    torrent_cache: TorrentCache,
) -> None:
    imdb_id = (cq.data or "")[4:]
    if torrent is None or cq.message is None:
        await cq.answer(t("stub.download"), show_alert=True)
        return
    if title_cache.get(imdb_id) is None:
        await cq.answer(t("download.reopen_card"), show_alert=True)
        return
    await _run_torrent_search(cq, torrent, torrent_cache, title_cache, imdb_id, season=None)


async def _run_torrent_search(
    cq: CallbackQuery,
    torrent: RutrackerTorrentMCPClient,
    torrent_cache: TorrentCache,
    title_cache: TitleCache,
    imdb_id: str,
    *,
    season: int | None,
) -> None:
    """Execute a rutracker search and present the result list. Shared by
    the movie download path and both series-with-season paths.

    Movies use a year-qualified query with a no-year fallback (production
    year on rutracker often diverges from the metadata's theatrical year).

    Series ignore year entirely — Russian rutracker tags multi-season
    releases as ranges («2008-2013») and per-season releases inherit the
    year from the season's air date, which makes year-qualifying actively
    harmful. Instead we query just the title, fetch a wider page, and
    filter client-side by parsing the season number(s) out of each result
    title (`Сезон: 3`, `Сезон 1-5`, `S03`…).
    """
    assert cq.message is not None
    tg_user_id = cq.from_user.id if cq.from_user else None
    cached = title_cache.get(imdb_id)
    if cached is None:
        await cq.message.answer(t("download.reopen_card"))
        return
    title, year, kind, _seasons = cached

    is_series = kind == "series"

    if is_series:
        query = title
        fallback_query: str | None = None
        limit = 50
    else:
        query = f"{title} {year}" if year else title
        fallback_query = title if year else None
        limit = 10

    await cq.answer(t("download.searching"))

    try:
        payload = await torrent.search_torrents(query, limit=limit, tg_user_id=tg_user_id)
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

    # Movies only: year-mismatch fallback. Theatrical year (TMDB/Kinopoisk)
    # vs production year (rutracker) often differ by ±1, so retry without
    # the year qualifier when nothing came back.
    if not results and fallback_query:
        try:
            payload = await torrent.search_torrents(
                fallback_query, limit=limit, tg_user_id=tg_user_id
            )
        except MCPClientError as exc:
            log.warning("torrent.search_failed", error=str(exc))
        else:
            if not payload.get("error"):
                results = payload.get("results") or []
                if results:
                    query = fallback_query

    # Series with a chosen season: filter by what each release covers,
    # parsed out of the raw title. Bundles (Сезон 1-5) match every season
    # in their range. Releases without a recognizable season tag are
    # dropped — for an explicit «Сезон N» pick they're noise.
    display_label = query
    if is_series and season is not None:
        # Strict-single-season filter: the result list keyboard only
        # surfaces resolution / release type, so a multi-season bundle
        # would be impossible to tell apart from a per-season release at
        # pick time. Keep only releases that cover *exactly* the chosen
        # season (e.g. «Сезон: 3 / Серии 1-13»), drop bundles like
        # «Сезон: 1-5».
        results = [
            r for r in results
            if _parse_seasons(str(r.get("title") or "")) == {season}
        ]
        display_label = t("download.season_filter_label", title=title, season=season)
    elif is_series:
        display_label = title

    if not results:
        await cq.message.answer(t("download.no_results"))
        return

    # Cache results so the «Показать ещё» callback can show the full list.
    torrent_cache.put(imdb_id, results)

    from html import escape as _esc
    text = t("download.list_header", query=_esc(display_label), n=len(results))
    await cq.message.answer(
        text,
        parse_mode="HTML",
        reply_markup=torrent_list_keyboard(results, imdb_id=imdb_id),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data.startswith("tor:"))
async def on_torrent_pick(
    cq: CallbackQuery,
    torrent_cache: TorrentCache,
) -> None:
    """First step: show the user the full release title with a link to
    the rutracker topic, then offer a single «Скачать» button. We don't
    fetch the .torrent yet — that's the second tap (`tdl:` callback)."""
    parts = (cq.data or "").split(":", 2)
    if len(parts) < 2 or cq.message is None:
        await cq.answer()
        return
    try:
        topic_id = int(parts[1])
    except ValueError:
        await cq.answer()
        return
    imdb_id = parts[2] if len(parts) > 2 else ""

    full_title = ""
    cached_results = torrent_cache.get(imdb_id) if imdb_id else None
    if cached_results:
        for r in cached_results:
            if r.get("topic_id") == topic_id:
                full_title = str(r.get("title") or "")
                break

    rutracker_url = f"https://rutracker.org/forum/viewtopic.php?t={topic_id}"
    from html import escape as _esc
    if full_title:
        text = t(
            "download.confirm_message",
            title=_esc(full_title),
            url=_esc(rutracker_url),
        )
    else:
        text = t("download.confirm_message_no_title", url=_esc(rutracker_url))

    await cq.answer()
    await cq.message.answer(
        text,
        parse_mode="HTML",
        reply_markup=torrent_confirm_keyboard(topic_id, imdb_id),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data.startswith("tdl:"))
async def on_torrent_confirm(
    cq: CallbackQuery,
    torrent: RutrackerTorrentMCPClient | None,
    rtorrent: RtorrentMCPClient | None,
    title_cache: TitleCache,
    movie_meta_cache: MovieMetaCache,
    state_db: StateDb,
    admin_user_ids: set[int],
) -> None:
    """Second step: actually fetch the .torrent and push it to rtorrent.
    Callback shape is `tdl:<topic_id>:<imdb_id>`."""
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

    # Hide the «Скачать» button so the user can't double-tap while the
    # rutracker round-trip is in flight.
    try:
        await cq.message.edit_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001 — best-effort UI cleanup
        pass

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
            cq,
            rtorrent,
            state_db=state_db,
            admin_user_ids=admin_user_ids,
            b64=b64,
            kind=kind,
            media_id=f"rt-{topic_id}",
            imdb_id=imdb_id or None,
            title_cache=title_cache,
            movie_meta_cache=movie_meta_cache,
            comment=source_url,
            tg_user_id=tg_user_id,
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
    state_db: StateDb,
    admin_user_ids: set[int],
    b64: str,
    kind: Kind | None,
    media_id: str,
    imdb_id: str | None,
    title_cache: TitleCache,
    movie_meta_cache: MovieMetaCache,
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

    if hash_ and tg_user_id is not None and cq.from_user is not None:
        # Resolve / create the user, then persist the Download row so the
        # poller can finalize it on completion even after a bot restart.
        display_name = " ".join(
            p for p in (cq.from_user.first_name, cq.from_user.last_name) if p
        ).strip() or (cq.from_user.username or "")
        chat_id = cq.message.chat.id if cq.message.chat else tg_user_id
        user = state_db.upsert_telegram_user(
            tg_user_id=tg_user_id,
            display_name=display_name,
            chat_id=chat_id,
            is_admin=tg_user_id in admin_user_ids,
            meta={
                "username": cq.from_user.username,
                "first_name": cq.from_user.first_name,
                "last_name": cq.from_user.last_name,
            },
        )
        # Prefer the human title from the metadata card (e.g. «Короткометражка
        # Marvel: Агент Картер») over rtorrent's release name (which is just
        # the .torrent filename). Filename only kicks in if metadata is gone.
        cached_title = title_cache.get(imdb_id) if imdb_id else None
        human_title = ""
        if cached_title and cached_title[0]:
            human_title = cached_title[0]
            if cached_title[1]:
                human_title = f"{human_title} ({cached_title[1]})"
        title_for_db = human_title or name or "?"
        meta = movie_meta_cache.get(imdb_id) if imdb_id else None
        state_db.add_download(
            user_id=user.id,
            info_hash=hash_,
            kind=str(kind or "movie"),
            title=title_for_db,
            media_id=media_id,
            imdb_id=imdb_id or None,
            description=meta.description if meta else "",
            poster_url=meta.poster_url if meta else "",
            source="rutracker",
        )

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
    """Replace the «Показать ещё» button with rows for every remaining
    release. The list grows in place — pinned picks stay at the top
    with their icons, the rest are appended below."""
    imdb_id = (cq.data or "")[7:]
    results = torrent_cache.get(imdb_id) if imdb_id else None
    if not results or cq.message is None:
        await cq.answer()
        return
    await cq.answer()
    try:
        await cq.message.edit_reply_markup(
            reply_markup=torrent_list_keyboard(
                results, imdb_id=imdb_id, expand_all=True
            )
        )
    except Exception as exc:  # noqa: BLE001
        # If editing fails (e.g. message too old), fall back to a fresh
        # message so the user still gets the expanded list.
        log.info("torrent.expand_edit_failed", error=str(exc))
        await cq.message.answer(
            t("download.all_header"),
            reply_markup=torrent_list_keyboard(
                results, imdb_id=imdb_id, expand_all=True
            ),
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


_SEASON_PATTERNS: list[re.Pattern[str]] = [
    # Russian forms with optional colon and en-dash range:
    #   «Сезон: 1», «Сезон 4, Эпизод», «Сезон: 1-5», «Сезон 1–5», «1 сезон»
    re.compile(r"Сезон[:\s]*(\d{1,2})(?:\s*[-–—]\s*(\d{1,2}))?", re.IGNORECASE),
    re.compile(r"\b(\d{1,2})(?:\s*[-–—]\s*(\d{1,2}))?\s*сезон", re.IGNORECASE),
    # Scene-style: S03, S01-S05, S01E01.
    re.compile(r"\bS(\d{1,2})(?:\s*[-–—]\s*S?(\d{1,2}))?", re.IGNORECASE),
]


def _parse_seasons(title: str) -> set[int]:
    """Return every season number a release title declares — a single
    season, or every season in a range. Empty set means "no season tag
    we recognized"; the caller treats that as «doesn't match» when the
    user asked for a specific season."""
    out: set[int] = set()
    for pattern in _SEASON_PATTERNS:
        for m in pattern.finditer(title):
            try:
                start = int(m.group(1))
            except (TypeError, ValueError):
                continue
            end_raw = m.group(2)
            try:
                end = int(end_raw) if end_raw else start
            except ValueError:
                end = start
            if end < start or end - start > 30:
                # Stray double-digit token after the season number is more
                # likely an episode count or rip artifact than a true range.
                end = start
            for n in range(start, end + 1):
                out.add(n)
    return out


def _kind_from_search_cache(
    search_cache: SearchCache,
    query_id: str,
    imdb_id: str,
) -> Kind | None:
    entry = search_cache.get(query_id)
    if entry is None:
        return None
    _query, results = entry
    for item in results:
        if item.get("imdb_id") != imdb_id:
            continue
        kind = item.get("kind")
        if kind == "series":
            return "series"
        if kind == "movie":
            return "movie"
        return None
    return None


__all__ = ["router"]
