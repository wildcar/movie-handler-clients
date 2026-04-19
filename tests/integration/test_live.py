"""Live integration smoke tests — skipped unless credentials are present.

Two scenarios:

1. ``getMe`` against the real Telegram Bot API using ``TELEGRAM_BOT_TOKEN``.
2. ``tools/list`` against a locally running ``movie-metadata-mcp``
   (``MOVIE_METADATA_MCP_URL`` + ``MCP_AUTH_TOKEN``).

Both are opt-in via ``-m integration``.
"""

from __future__ import annotations

import os

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    not os.getenv("TELEGRAM_BOT_TOKEN"),
    reason="TELEGRAM_BOT_TOKEN not set",
)
async def test_telegram_get_me() -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"https://api.telegram.org/bot{token}/getMe")
    r.raise_for_status()
    body = r.json()
    assert body.get("ok") is True
    assert body["result"].get("is_bot") is True


@pytest.mark.skipif(
    not (os.getenv("MOVIE_METADATA_MCP_URL") and os.getenv("MCP_AUTH_TOKEN")),
    reason="MOVIE_METADATA_MCP_URL or MCP_AUTH_TOKEN not set",
)
async def test_metadata_mcp_list_tools() -> None:
    url = os.environ["MOVIE_METADATA_MCP_URL"]
    token = os.environ["MCP_AUTH_TOKEN"]
    headers = {"Authorization": f"Bearer {token}"}
    async with streamablehttp_client(url, headers=headers) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            tools = await session.list_tools()
    names = {t.name for t in tools.tools}
    assert "search_movie" in names
    assert "get_movie_details" in names
