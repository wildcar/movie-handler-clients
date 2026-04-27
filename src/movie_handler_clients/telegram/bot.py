"""Telegram bot entrypoint (aiogram 3.x, long-polling, single process)."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from html import escape as _esc

import structlog
from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

from ..core.config import Settings, get_settings
from ..core.i18n import t
from ..core.logging_conf import configure_logging
from ..core.mcp_client import MovieMetadataMCPClient
from ..core.media_watch_client import MediaWatchClient, MediaWatchError
from ..core.rtorrent_client import RtorrentMCPClient
from ..core.state_db import (
    MAX_REGISTER_ATTEMPTS,
    Download,
    DownloadWithUser,
    StateDb,
)
from ..core.torrent_client import RutrackerTorrentMCPClient
from ..core.traffic_log import TrafficLog
from ..core.trailer_client import MovieTrailerMCPClient
from ..core.yt_dlp_client import YtDlpMCPClient
from .handlers import details as details_handler
from .handlers import list as list_handler
from .handlers import rutracker_url as rutracker_url_handler
from .handlers import search as search_handler
from .handlers import status as status_handler
from .handlers import whoami as whoami_handler
from .handlers import youtube_url as youtube_url_handler
from .movie_meta_cache import MovieMetaCache
from .search_cache import SearchCache
from .title_cache import TitleCache
from .torrent_cache import TorrentCache
from .trailer_cache import TrailerCache
from .ydl_cache import YtDlpCache

log = structlog.get_logger(__name__)


_POLL_INTERVAL = 60  # seconds between completion checks
_PRUNE_INTERVAL_TICKS = 60  # one prune per ~hour, ridden along with the poller


async def _poll_completions(
    bot: Bot,
    rtorrent: RtorrentMCPClient | None,
    yt_dlp: YtDlpMCPClient | None,
    state_db: StateDb,
    media_watch: MediaWatchClient | None,
) -> None:
    """Background task: pick up completed downloads, register them with
    media-watch-web, and notify the user with the resulting watch link(s).
    Persistent across restarts via state_db. Dispatches per row by
    ``download.source`` — yt-dlp tasks go through ``yt-dlp-mcp``,
    everything else through rtorrent.

    Once an hour the same loop also runs a prune pass: pulls the list
    of live record ids from media-watch-web and drops any local
    ``watch_records`` (and their owning ``downloads``) that aren't on
    the server anymore, so files deleted from disk fall out of /list."""
    tick = 0
    while True:
        await asyncio.sleep(_POLL_INTERVAL)
        tick += 1
        try:
            pending = state_db.list_pending()
        except Exception as exc:
            log.warning("poll.list_pending_failed", error=str(exc))
            continue
        for entry in pending:
            try:
                await _process_one(bot, rtorrent, yt_dlp, state_db, media_watch, entry)
            except Exception as exc:
                log.exception("poll.process_failed", hash=entry.download.info_hash, error=str(exc))

        if media_watch is not None and tick % _PRUNE_INTERVAL_TICKS == 0:
            try:
                live_ids = await media_watch.list_record_ids()
                removed = state_db.prune_missing_watch_records(live_ids)
                if removed:
                    log.info("poll.prune", removed=removed, live=len(live_ids))
            except Exception as exc:
                log.warning("poll.prune_failed", error=str(exc))


async def _process_one(
    bot: Bot,
    rtorrent: RtorrentMCPClient | None,
    yt_dlp: YtDlpMCPClient | None,
    state_db: StateDb,
    media_watch: MediaWatchClient | None,
    entry: DownloadWithUser,
) -> None:
    dl = entry.download
    if dl.source == "yt-dlp":
        if yt_dlp is None:
            return  # poller can't talk to the MCP, leave the row pending
        await _process_ytdlp(bot, yt_dlp, state_db, media_watch, entry)
    else:
        if rtorrent is None:
            return
        await _process_rtorrent(bot, rtorrent, state_db, media_watch, entry)


async def _process_rtorrent(
    bot: Bot,
    rtorrent: RtorrentMCPClient,
    state_db: StateDb,
    media_watch: MediaWatchClient | None,
    entry: DownloadWithUser,
) -> None:
    dl = entry.download
    try:
        payload = await rtorrent.get_download_status(dl.info_hash)
    except Exception as exc:
        log.warning("poll.rtorrent_failed", hash=dl.info_hash, error=str(exc))
        return

    if err := payload.get("error"):
        code = (err or {}).get("code") if isinstance(err, dict) else None
        if code == "not_found":
            state_db.mark_cancelled(dl.info_hash, "rtorrent reports not_found")
        return

    rt_dl = payload.get("download") or {}
    if rt_dl.get("state") != "complete":
        return

    # Prefer ``base_path`` (the actual content path — file for single-file
    # torrents, folder for multi-file) over ``directory`` (the shared
    # download dir). Falling back to ``directory`` would point the
    # registration at /mnt/.../Movie/ for *every* single-file torrent
    # and our scanner would always pick the same largest file there.
    payload_path = str(rt_dl.get("base_path") or "").strip()
    if not payload_path:
        payload_path = str(rt_dl.get("directory") or "").strip()
    if not payload_path:
        log.warning("poll.no_payload_path", hash=dl.info_hash)
        return

    rt_name = str(rt_dl.get("name") or "").strip()
    await _register_and_notify(bot, state_db, media_watch, entry, payload_path, rt_name=rt_name)


async def _process_ytdlp(
    bot: Bot,
    yt_dlp: YtDlpMCPClient,
    state_db: StateDb,
    media_watch: MediaWatchClient | None,
    entry: DownloadWithUser,
) -> None:
    """yt-dlp path. ``info_hash`` carries the task_id we got from
    ``start_download``. Status states come from yt-dlp-mcp:
    queued/running → keep polling; complete → register; failed →
    mark_cancelled."""
    dl = entry.download
    try:
        payload = await yt_dlp.get_download_status(dl.info_hash)
    except Exception as exc:
        log.warning("poll.ytdlp_failed", task_id=dl.info_hash, error=str(exc))
        return

    if err := payload.get("error"):
        code = (err or {}).get("code") if isinstance(err, dict) else None
        if code == "not_found":
            # yt-dlp-mcp lost the task (e.g. the SQLite store was wiped).
            # Without status we can't finalise; treat as cancelled.
            state_db.mark_cancelled(dl.info_hash, "yt-dlp reports not_found")
        return

    task = payload.get("task") or {}
    state = str(task.get("state") or "")
    if state in ("queued", "running"):
        return
    if state in ("failed", "cancelled"):
        msg = str(task.get("error") or f"yt-dlp state={state}")
        state_db.mark_cancelled(dl.info_hash, msg)
        return
    if state != "complete":
        log.info("poll.ytdlp_unknown_state", task_id=dl.info_hash, state=state)
        return

    output_path = str(task.get("output_path") or "").strip()
    if not output_path:
        log.warning("poll.ytdlp_no_output_path", task_id=dl.info_hash)
        return

    await _register_and_notify(bot, state_db, media_watch, entry, output_path)


async def _register_and_notify(
    bot: Bot,
    state_db: StateDb,
    media_watch: MediaWatchClient | None,
    entry: DownloadWithUser,
    payload_path: str,
    *,
    rt_name: str = "",
) -> None:
    """Shared post-completion path: try to register the file on
    media-watch-web, save the resulting watch records, send the user
    the watch link, and mark the download as registered. On
    media-watch failure we retry up to ``MAX_REGISTER_ATTEMPTS`` times
    before giving up."""
    dl = entry.download
    chat_id_raw = entry.identity.chat_id or entry.identity.external_id
    try:
        chat_id = int(chat_id_raw) if chat_id_raw is not None else None
    except ValueError:
        chat_id = None

    if media_watch is None:
        # No media-watch — fall back to a plain «closed» notification.
        name = (rt_name or dl.title or "").strip()
        msg = t("download.complete", name=_esc(name)) if name else t("download.complete_noname")
        await _safe_send(bot, chat_id, msg)
        state_db.mark_registered(dl.id)
        return

    try:
        result = await media_watch.register(
            path=payload_path,
            title=dl.title,
            kind=dl.kind,
            media_id=dl.media_id,
            description=dl.description,
            poster_url=dl.poster_url,
        )
    except MediaWatchError as exc:
        if dl.register_attempts + 1 >= MAX_REGISTER_ATTEMPTS:
            state_db.mark_register_failed(dl.id, str(exc))
            failed_msg = t(
                "download.register_failed",
                name=_esc(dl.title),
                detail=_esc(str(exc)),
            )
            await _safe_send(bot, chat_id, failed_msg)
            state_db.record_notification(
                user_id=dl.user_id,
                download_id=dl.id,
                platform="telegram",
                status="sent",
            )
        else:
            state_db.mark_pending_register(dl.id, str(exc))
            log.info(
                "poll.register_retry_scheduled",
                hash=dl.info_hash,
                attempts=dl.register_attempts + 1,
            )
        return

    records = result.get("records") or []
    warnings = result.get("warnings") or []
    if warnings:
        log.info("media_watch.warnings", hash=dl.info_hash, warnings=warnings)

    saved = state_db.insert_watch_records(dl.id, records)
    state_db.mark_registered(dl.id)

    msg = _format_completion_message(dl, saved)
    await _safe_send(bot, chat_id, msg)
    state_db.record_notification(
        user_id=dl.user_id,
        download_id=dl.id,
        platform="telegram",
        status="sent",
    )


def _format_completion_message(dl: Download, watch_records: list) -> str:  # type: ignore[type-arg]
    if not watch_records:
        return t("download.complete", name=_esc(dl.title))
    if dl.kind == "series" and len(watch_records) > 1:
        lines = [t("download.complete_episodes_header", name=_esc(dl.title), n=len(watch_records))]
        for r in watch_records:
            if r.season is not None and r.episode is not None:
                lines.append(
                    t(
                        "download.complete_episode_line",
                        season=r.season,
                        episode=r.episode,
                        url=r.watch_url,
                    )
                )
            else:
                lines.append(t("download.complete_extra_line", url=r.watch_url))
        return "\n".join(lines)
    return t("download.complete_with_link", name=_esc(dl.title), url=watch_records[0].watch_url)


async def _safe_send(bot: Bot, chat_id: int | None, text: str) -> None:
    if chat_id is None:
        log.warning("notify.no_chat_id")
        return
    try:
        await bot.send_message(chat_id, text, parse_mode="HTML", disable_web_page_preview=False)
    except Exception as exc:
        log.warning("notify.failed", chat=chat_id, error=str(exc))


async def _run(settings: Settings) -> None:
    async with AsyncExitStack() as stack:
        traffic = TrafficLog(settings.log_db_path, ttl_days=settings.log_ttl_days)
        await traffic.open()
        stack.push_async_callback(traffic.close)

        state_db = StateDb(path=settings.state_db_path)
        stack.callback(state_db.close)

        mcp = await stack.enter_async_context(
            MovieMetadataMCPClient(
                url=settings.movie_metadata_mcp_url,
                auth_token=settings.mcp_auth_token,
                traffic_log=traffic,
            )
        )
        # Trailer MCP is optional — if it's down or not reachable, the bot
        # keeps running and the trailer button falls back to the stub.
        trailer: MovieTrailerMCPClient | None = None
        try:
            trailer = await stack.enter_async_context(
                MovieTrailerMCPClient(
                    url=settings.movie_trailer_mcp_url,
                    auth_token=settings.mcp_auth_token,
                    traffic_log=traffic,
                )
            )
        except (Exception, BaseExceptionGroup) as exc:
            log.warning(
                "trailer_mcp.unavailable", url=settings.movie_trailer_mcp_url, error=str(exc)
            )

        # Torrent MCP is also optional — same degradation story.
        torrent: RutrackerTorrentMCPClient | None = None
        try:
            torrent = await stack.enter_async_context(
                RutrackerTorrentMCPClient(
                    url=settings.rutracker_torrent_mcp_url,
                    auth_token=settings.mcp_auth_token,
                    traffic_log=traffic,
                )
            )
        except (Exception, BaseExceptionGroup) as exc:
            log.warning(
                "torrent_mcp.unavailable",
                url=settings.rutracker_torrent_mcp_url,
                error=str(exc),
            )

        # rtorrent MCP is opt-in: the URL defaults to None. When set, the
        # download flow pushes the .torrent straight to the media server;
        # when unset (or unreachable), we fall back to sending the file
        # to the user as a Telegram document.
        rtorrent: RtorrentMCPClient | None = None
        if settings.rtorrent_mcp_url:
            try:
                rtorrent = await stack.enter_async_context(
                    RtorrentMCPClient(
                        url=settings.rtorrent_mcp_url,
                        auth_token=settings.mcp_auth_token,
                        traffic_log=traffic,
                    )
                )
            except (Exception, BaseExceptionGroup) as exc:
                log.warning(
                    "rtorrent_mcp.unavailable",
                    url=settings.rtorrent_mcp_url,
                    error=str(exc),
                )

        # yt-dlp MCP — opt-in. When unset, pasted YouTube/Vimeo/Twitch
        # URLs land on «ссылка не распознана».
        yt_dlp: YtDlpMCPClient | None = None
        if settings.yt_dlp_mcp_url:
            try:
                yt_dlp = await stack.enter_async_context(
                    YtDlpMCPClient(
                        url=settings.yt_dlp_mcp_url,
                        auth_token=settings.mcp_auth_token,
                        traffic_log=traffic,
                    )
                )
            except (Exception, BaseExceptionGroup) as exc:
                log.warning(
                    "yt_dlp_mcp.unavailable",
                    url=settings.yt_dlp_mcp_url,
                    error=str(exc),
                )

        # media-watch-web is opt-in too: when both URL and token are set
        # we register completed downloads there and hand the user a
        # watch link; when not configured, we keep the old "download
        # finished" notification flow.
        media_watch: MediaWatchClient | None = None
        if settings.media_watch_base_url and settings.media_watch_api_token:
            try:
                media_watch = await stack.enter_async_context(
                    MediaWatchClient(
                        base_url=settings.media_watch_base_url,
                        api_token=settings.media_watch_api_token,
                    )
                )
            except (Exception, BaseExceptionGroup) as exc:
                log.warning(
                    "media_watch.unavailable",
                    url=settings.media_watch_base_url,
                    error=str(exc),
                )

        bot = Bot(token=settings.telegram_bot_token)
        stack.push_async_callback(bot.session.close)

        admin_user_ids = settings.admin_user_ids()

        # aiogram injects any kwargs we pass to the Dispatcher constructor
        # into handlers that declare matching parameter names.
        dp = Dispatcher(
            mcp=mcp,
            trailer=trailer,
            torrent=torrent,
            rtorrent=rtorrent,
            yt_dlp=yt_dlp,
            search_cache=SearchCache(),
            title_cache=TitleCache(),
            torrent_cache=TorrentCache(),
            trailer_cache=TrailerCache(),
            movie_meta_cache=MovieMetaCache(),
            ydl_cache=YtDlpCache(),
            state_db=state_db,
            admin_user_ids=admin_user_ids,
        )
        dp.include_router(status_handler.router)
        dp.include_router(list_handler.router)
        dp.include_router(whoami_handler.router)
        # URL routers come *before* the search router — search matches
        # anything not starting with `/`, so a pasted URL would land there
        # as free-text. rutracker first because its filter is the
        # narrowest; youtube_url claims everything http(s) it sees and
        # would otherwise swallow rutracker links.
        dp.include_router(rutracker_url_handler.router)
        dp.include_router(youtube_url_handler.router)
        dp.include_router(search_handler.router)
        dp.include_router(details_handler.router)

        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Что я умею"),
                BotCommand(command="status", description="Прогресс закачек"),
                BotCommand(command="list", description="Медиатека"),
            ]
        )

        log.info("bot.starting")
        if rtorrent is not None or yt_dlp is not None:
            poll_task = asyncio.create_task(
                _poll_completions(bot, rtorrent, yt_dlp, state_db, media_watch)
            )
            stack.callback(poll_task.cancel)
        await dp.start_polling(bot)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    try:
        asyncio.run(_run(settings))
    except (KeyboardInterrupt, SystemExit):
        log.info("bot.stopped")


if __name__ == "__main__":
    main()
