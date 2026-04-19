"""Telegram bot entrypoint (aiogram 3.x, long-polling, single process)."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack

import structlog
from aiogram import Bot, Dispatcher

from ..core.config import Settings, get_settings
from ..core.logging_conf import configure_logging
from ..core.mcp_client import MovieMetadataMCPClient
from ..core.traffic_log import TrafficLog
from .handlers import details as details_handler
from .handlers import search as search_handler
from .search_cache import SearchCache

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

        bot = Bot(token=settings.telegram_bot_token)
        stack.push_async_callback(bot.session.close)

        # aiogram injects any kwargs we pass to the Dispatcher constructor
        # into handlers that declare matching parameter names.
        dp = Dispatcher(mcp=mcp, search_cache=SearchCache())
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
