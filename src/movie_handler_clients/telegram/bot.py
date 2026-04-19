"""Telegram bot entrypoint (aiogram 3.x, long-polling, single process)."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack

import structlog
from aiogram import Bot, Dispatcher

from ..core.config import Settings, get_settings
from ..core.logging_conf import configure_logging
from ..core.mcp_client import MovieMetadataMCPClient
from ..core.torrent_client import RutrackerTorrentMCPClient
from ..core.traffic_log import TrafficLog
from ..core.trailer_client import MovieTrailerMCPClient
from .handlers import details as details_handler
from .handlers import search as search_handler
from .search_cache import SearchCache
from .title_cache import TitleCache

log = structlog.get_logger(__name__)


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
        except Exception as exc:
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
        except Exception as exc:
            log.warning(
                "torrent_mcp.unavailable",
                url=settings.rutracker_torrent_mcp_url,
                error=str(exc),
            )

        bot = Bot(token=settings.telegram_bot_token)
        stack.push_async_callback(bot.session.close)

        # aiogram injects any kwargs we pass to the Dispatcher constructor
        # into handlers that declare matching parameter names.
        dp = Dispatcher(
            mcp=mcp,
            trailer=trailer,
            torrent=torrent,
            search_cache=SearchCache(),
            title_cache=TitleCache(),
        )
        dp.include_router(search_handler.router)
        dp.include_router(details_handler.router)

        log.info("bot.starting")
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
