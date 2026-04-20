"""MCP client for ``rtorrent-mcp`` — sends .torrent files to the remote
rtorrent instance that hosts the media library."""

from __future__ import annotations

from typing import Any, Literal

from .mcp_client import BaseMCPClient

MediaKind = Literal["movie", "series"]


class RtorrentMCPClient(BaseMCPClient):
    async def add_torrent(
        self,
        *,
        torrent_file_base64: str | None = None,
        magnet: str | None = None,
        kind: MediaKind | None = None,
        download_dir: str | None = None,
        start: bool = True,
        tg_user_id: int | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {"start": start}
        if torrent_file_base64 is not None:
            args["torrent_file_base64"] = torrent_file_base64
        if magnet is not None:
            args["magnet"] = magnet
        if kind is not None:
            args["kind"] = kind
        if download_dir is not None:
            args["download_dir"] = download_dir
        return await self.call_tool("add_torrent", args, tg_user_id=tg_user_id)

    async def list_downloads(
        self, *, active_only: bool = False, tg_user_id: int | None = None
    ) -> dict[str, Any]:
        return await self.call_tool(
            "list_downloads", {"active_only": active_only}, tg_user_id=tg_user_id
        )

    async def get_download_status(
        self, hash_: str, *, tg_user_id: int | None = None
    ) -> dict[str, Any]:
        return await self.call_tool("get_download_status", {"hash": hash_}, tg_user_id=tg_user_id)
