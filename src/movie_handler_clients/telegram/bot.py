"""Telegram bot entrypoint (aiogram 3.x, long-polling, single process)."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from html import escape as _esc

import structlog
from aiogram import Bot, Dispatcher

from ..core.config import Settings, get_settings
from ..core.i18n import t
from ..core.logging_conf import configure_logging
from ..core.mcp_client import MovieMetadataMCPClient
from ..core.rtorrent_client import RtorrentMCPClient
from ..core.torrent_client import RutrackerTorrentMCPClient
from ..core.traffic_log import TrafficLog
from ..core.trailer_client import MovieTrailerMCPClient
from .download_tracker import DownloadTracker
from .handlers import details as details_handler
from .handlers import search as search_handler
from .handlers import status as status_handler
from .search_cache import SearchCache
from .title_cache import TitleCache
from .torrent_cache import TorrentCache

log = structlog.get_logger(__name__)


_POLL_INTERVAL = 60  # seconds between completion checks


async def _poll_completions(
    bot: Bot,
    rtorrent: RtorrentMCPClient,
    tracker: DownloadTracker,
) -> None:
    """Background task: notify users when their downloads finish."""
    while True:
        await asyncio.sleep(_POLL_INTERVAL)
        for hash_ in tracker.all_hashes():
            try:
                payload = await rtorrent.get_download_status(hash_)
            except Exception:
                continue
            if err := payload.get("error"):
                code = (err or {}).get("code") if isinstance(err, dict) else None
                if code == "not_found":
                    tracker.untrack(hash_)
                continue
            dl = payload.get("download") or {}
            if dl.get("state") != "complete":
                continue
            entry = tracker.get(hash_)
            if entry is None:
                continue
            name = str(dl.get("name") or entry.title or "").strip()
            msg = t("download.complete", name=_esc(name)) if name else t("download.complete_noname")
            try:
                await bot.send_message(entry.tg_user_id, msg, parse_mode="HTML")
            except Exception as exc:
                log.warning("poll.notify_failed", user=entry.tg_user_id, error=str(exc))
            tracker.untrack(hash_)


async def _run(settings: Settings) -> None:
    async with AsyncExitStack() as stack:
        traffic = TrafficLog(settings.log_db_path, ttl_days=settings.log_ttl_days)
        await traffic.open()
        stack.push_async_callback(traffic.close)

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

        bot = Bot(token=settings.telegram_bot_token)
        stack.push_async_callback(bot.session.close)

        tracker = DownloadTracker()

        # aiogram injects any kwargs we pass to the Dispatcher constructor
        # into handlers that declare matching parameter names.
        dp = Dispatcher(
            mcp=mcp,
            trailer=trailer,
            torrent=torrent,
            rtorrent=rtorrent,
            search_cache=SearchCache(),
            title_cache=TitleCache(),
            torrent_cache=TorrentCache(),
            tracker=tracker,
        )
        dp.include_router(status_handler.router)
        dp.include_router(search_handler.router)
        dp.include_router(details_handler.router)

        log.info("bot.starting")
        if rtorrent is not None:
            poll_task = asyncio.create_task(_poll_completions(bot, rtorrent, tracker))
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
