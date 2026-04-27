"""MCP client for ``yt-dlp-mcp``.

Five tools mirrored as typed wrappers; the responses come back as raw
dicts (the bot's existing pattern for MCP envelopes — keeps imports
local and avoids dragging Pydantic models across repos).
"""

from __future__ import annotations

from typing import Any

from .mcp_client import BaseMCPClient


class YtDlpMCPClient(BaseMCPClient):
    """Calls the five yt-dlp tools."""

    async def probe(self, url: str, *, tg_user_id: int | None = None) -> dict[str, Any]:
        """Metadata + format list for ``url``. No download."""
        return await self.call_tool("probe", {"url": url}, tg_user_id=tg_user_id)

    async def start_download(
        self,
        url: str,
        *,
        format_selector: str | None = None,
        tg_user_id: int | None = None,
    ) -> dict[str, Any]:
        """Enqueue a background download. Returns ``task_id``."""
        args: dict[str, Any] = {"url": url}
        if format_selector is not None:
            args["format_selector"] = format_selector
        return await self.call_tool("start_download", args, tg_user_id=tg_user_id)

    async def get_download_status(
        self, task_id: str, *, tg_user_id: int | None = None
    ) -> dict[str, Any]:
        """Current state, progress, and (on completion) ``output_path``."""
        return await self.call_tool(
            "get_download_status", {"task_id": task_id}, tg_user_id=tg_user_id
        )

    async def list_playlist(
        self, url: str, *, limit: int | None = None, tg_user_id: int | None = None
    ) -> dict[str, Any]:
        """Flat-extract a playlist; preview at most ``limit`` entries."""
        args: dict[str, Any] = {"url": url}
        if limit is not None:
            args["limit"] = limit
        return await self.call_tool("list_playlist", args, tg_user_id=tg_user_id)

    async def health_check(self, *, tg_user_id: int | None = None) -> dict[str, Any]:
        """yt-dlp version + cookies state + canary probe."""
        return await self.call_tool("health_check", {}, tg_user_id=tg_user_id)
