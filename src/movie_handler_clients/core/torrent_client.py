"""MCP client for ``rutracker-torrent-mcp``."""

from __future__ import annotations

from typing import Any

from .mcp_client import BaseMCPClient


class RutrackerTorrentMCPClient(BaseMCPClient):
    """Calls the three rutracker tools."""

    async def search_torrents(
        self,
        query: str,
        *,
        category: int | None = None,
        min_seeders: int = 0,
        limit: int = 10,
        tg_user_id: int | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {"query": query, "min_seeders": min_seeders, "limit": limit}
        if category is not None:
            args["category"] = category
        return await self.call_tool("search_torrents", args, tg_user_id=tg_user_id)

    async def get_torrent_file(
        self, topic_id: int, *, tg_user_id: int | None = None
    ) -> dict[str, Any]:
        return await self.call_tool(
            "get_torrent_file", {"topic_id": topic_id}, tg_user_id=tg_user_id
        )
