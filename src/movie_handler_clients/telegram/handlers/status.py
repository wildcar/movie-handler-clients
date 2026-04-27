"""Handler for /status — shows the user's in-flight downloads.

Walks the active rows in ``state.sqlite`` and queries the right MCP per
row by ``source`` — rutracker / unknown → ``rtorrent-mcp``, yt-dlp →
``yt-dlp-mcp``. Either MCP being down just hides those rows, the rest
still render.
"""

from __future__ import annotations

from html import escape as _esc
from typing import Any

import structlog
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from ...core.i18n import t
from ...core.mcp_client import MCPClientError
from ...core.rtorrent_client import RtorrentMCPClient
from ...core.state_db import Download, StateDb
from ...core.yt_dlp_client import YtDlpMCPClient

router = Router(name="status")
log = structlog.get_logger(__name__)


@router.message(Command("status"))
async def on_status(
    message: Message,
    rtorrent: RtorrentMCPClient | None,
    yt_dlp: YtDlpMCPClient | None,
    state_db: StateDb,
) -> None:
    if rtorrent is None and yt_dlp is None:
        await message.answer(t("status.not_configured"))
        return

    tg_user_id = message.from_user.id if message.from_user else None
    if tg_user_id is None:
        return

    identity = state_db.get_telegram_identity(tg_user_id)
    if identity is None:
        await message.answer(t("status.no_downloads"))
        return
    pending = [
        dl
        for dl in state_db.list_user_active(identity.user_id)
        if dl.state in ("downloading", "complete_pending_register")
    ]
    if not pending:
        await message.answer(t("status.no_downloads"))
        return

    rows: list[str] = []
    for dl in pending:
        if dl.source == "yt-dlp":
            row = await _row_for_ytdlp(dl, yt_dlp, tg_user_id, state_db)
        else:
            row = await _row_for_rtorrent(dl, rtorrent, tg_user_id, state_db)
        if row:
            rows.append(row)

    if not rows:
        await message.answer(t("status.no_downloads"))
        return

    await message.answer(
        t("status.header") + "\n\n" + "\n\n".join(rows),
        parse_mode="HTML",
    )


async def _row_for_rtorrent(
    dl: Download,
    rtorrent: RtorrentMCPClient | None,
    tg_user_id: int,
    state_db: StateDb,
) -> str | None:
    if rtorrent is None:
        return None
    try:
        payload = await rtorrent.get_download_status(dl.info_hash, tg_user_id=tg_user_id)
    except MCPClientError as exc:
        log.warning("status.rtorrent_failed", hash=dl.info_hash, error=str(exc))
        return None
    if err := payload.get("error"):
        code = (err or {}).get("code") if isinstance(err, dict) else None
        if code == "not_found":
            state_db.mark_cancelled(dl.info_hash, "rtorrent reports not_found")
        return None
    rt_dl = payload.get("download")
    if not rt_dl:
        return None
    return _format_row(rt_dl)


async def _row_for_ytdlp(
    dl: Download,
    yt_dlp: YtDlpMCPClient | None,
    tg_user_id: int,
    state_db: StateDb,
) -> str | None:
    if yt_dlp is None:
        return None
    try:
        payload = await yt_dlp.get_download_status(dl.info_hash, tg_user_id=tg_user_id)
    except MCPClientError as exc:
        log.warning("status.ytdlp_failed", task_id=dl.info_hash, error=str(exc))
        return None
    if err := payload.get("error"):
        code = (err or {}).get("code") if isinstance(err, dict) else None
        if code == "not_found":
            state_db.mark_cancelled(dl.info_hash, "yt-dlp reports not_found")
        return None
    task = payload.get("task")
    if not task:
        return None
    return _format_ytdlp_row(dl, task)


def _format_row(dl: dict[str, Any]) -> str:
    name = str(dl.get("name") or "?")
    size = int(dl.get("size_bytes") or 0)
    done = int(dl.get("completed_bytes") or 0)
    rate = int(dl.get("down_rate") or 0)
    state = str(dl.get("state") or "")

    pct = round(done / size * 100, 1) if size > 0 else 0.0
    bar = _progress_bar(pct)

    if state == "complete":
        icon = "✅"
        eta = ""
    elif state == "active":
        icon = "↓"
        eta = "  " + _format_eta(size, done, rate) if size > 0 else ""
    elif state == "paused":
        icon = "⏸"
        eta = ""
    else:
        icon = "⏹"
        eta = ""

    size_str = _human_gb(size)
    return f"{icon} <b>{_esc(name)}</b>\n   {bar} {pct}%  {size_str}{eta}"


def _format_ytdlp_row(dl: Download, task: dict[str, Any]) -> str:
    """Render a yt-dlp task in the same shape as the rtorrent row."""
    name = str(task.get("title") or dl.title or "?")
    state = str(task.get("state") or "")
    pct = float(task.get("progress_pct") or 0.0)
    total = int(task.get("total_bytes") or 0)
    speed = float(task.get("speed_bps") or 0.0)
    eta_seconds = task.get("eta_seconds")

    bar = _progress_bar(pct)
    if state == "complete":
        icon = "✅"
        eta = ""
    elif state == "running":
        icon = "↓"
        if isinstance(eta_seconds, int) and eta_seconds > 0:
            eta = "  " + _format_eta_seconds(eta_seconds)
        elif total and speed > 0:
            eta = "  " + _format_eta(total, int(task.get("downloaded_bytes") or 0), int(speed))
        else:
            eta = ""
    elif state == "queued":
        icon = "⏳"
        eta = ""
    else:
        icon = "⏹"
        eta = ""

    size_str = _human_gb(total)
    return f"{icon} <b>{_esc(name)}</b>\n   {bar} {round(pct, 1)}%  {size_str}{eta}"


def _format_row(dl: dict[str, Any]) -> str:
    name = str(dl.get("name") or "?")
    size = int(dl.get("size_bytes") or 0)
    done = int(dl.get("completed_bytes") or 0)
    rate = int(dl.get("down_rate") or 0)
    state = str(dl.get("state") or "")

    pct = round(done / size * 100, 1) if size > 0 else 0.0
    bar = _progress_bar(pct)

    if state == "complete":
        icon = "✅"
        eta = ""
    elif state == "active":
        icon = "↓"
        eta = "  " + _format_eta(size, done, rate) if size > 0 else ""
    elif state == "paused":
        icon = "⏸"
        eta = ""
    else:
        icon = "⏹"
        eta = ""

    size_str = _human_gb(size)
    return f"{icon} <b>{_esc(name)}</b>\n   {bar} {pct}%  {size_str}{eta}"


def _progress_bar(pct: float, width: int = 10) -> str:
    filled = round(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)


def _format_eta(size: int, done: int, rate: int) -> str:
    if rate <= 0:
        return "⏱ —"
    return _format_eta_seconds((size - done) // rate)


def _format_eta_seconds(seconds: int) -> str:
    if seconds <= 0:
        return "⏱ —"
    if seconds < 60:
        # parse_mode=HTML: a literal "<1" reads as a start tag — escape it.
        return "⏱ &lt;1 мин"
    if seconds < 3600:
        return f"⏱ ~{seconds // 60} мин"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"⏱ ~{h} ч {m} мин" if m else f"⏱ ~{h} ч"


def _human_gb(n: int) -> str:
    if n == 0:
        return ""
    gb = n / 1_073_741_824
    return f"{gb:.1f} GB"


__all__ = ["router"]
