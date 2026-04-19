"""MCP client for ``movie-trailer-mcp``.

A second long-lived ClientSession lives next to :class:`MovieMetadataMCPClient`
and shares the same traffic log. The surface is intentionally thin — just
``find_trailer`` is needed from the bot side.
"""

from __future__ import annotations

from typing import Any

from .mcp_client import BaseMCPClient


class MovieTrailerMCPClient(BaseMCPClient):
    """Calls ``find_trailer`` on the trailer MCP server."""

    async def find_trailer(
        self, imdb_id: str, *, language: str = "ru", tg_user_id: int | None = None
    ) -> dict[str, Any]:
        return await self.call_tool(
            "find_trailer",
            {"imdb_id": imdb_id, "language": language},
            tg_user_id=tg_user_id,
        )
