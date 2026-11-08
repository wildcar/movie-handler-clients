"""MCP client for ``rtorrent-mcp`` — sends .torrent files to the remote
rtorrent instance that hosts the media library."""

from __future__ import annotations

from typing import Any, Literal

from .mcp_client import BaseMCPClient

MediaKind = Literal["movie", "series", "cartoon"]


class RtorrentMCPClient(BaseMCPClient):
    async def add_torrent(
        self,
        *,
        torrent_file_base64: str | None = None,
        magnet: str | None = None,
        kind: MediaKind | None = None,
        download_dir: str | None = None,
        start: bool = True,
        comment: str | None = None,
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
        if comment is not None:
            args["comment"] = comment
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

    async def remove(
        self, hash_: str, *, delete_data: bool = False, tg_user_id: int | None = None
    ) -> dict[str, Any]:
        """Erase from rtorrent; ``delete_data`` also unlinks the payload."""
        return await self.call_tool(
            "remove", {"hash": hash_, "delete_data": delete_data}, tg_user_id=tg_user_id
        )

    async def replace_torrent(
        self, hash_: str, *, torrent_file_base64: str, tg_user_id: int | None = None
    ) -> dict[str, Any]:
        """Swap the download for an updated .torrent; ``changed=False`` when
        the info-hash is the same."""
        return await self.call_tool(
            "replace_torrent",
            {"hash": hash_, "torrent_file_base64": torrent_file_base64},
            tg_user_id=tg_user_id,
        )
