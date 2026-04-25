"""Handler for /status command — shows the user's rtorrent downloads."""

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
from ...core.state_db import StateDb

router = Router(name="status")
log = structlog.get_logger(__name__)


@router.message(Command("status"))
async def on_status(
    message: Message,
    rtorrent: RtorrentMCPClient | None,
    state_db: StateDb,
) -> None:
    if rtorrent is None:
        await message.answer(t("status.not_configured"))
        return

    tg_user_id = message.from_user.id if message.from_user else None
    if tg_user_id is None:
        return

    identity = state_db.get_telegram_identity(tg_user_id)
    if identity is None:
        await message.answer(t("status.no_downloads"))
        return
    hashes = state_db.list_user_hashes(identity.user_id)
    if not hashes:
        await message.answer(t("status.no_downloads"))
        return

    rows: list[str] = []
    for h in hashes:
        try:
            payload = await rtorrent.get_download_status(h, tg_user_id=tg_user_id)
        except MCPClientError as exc:
            log.warning("status.fetch_failed", hash=h, error=str(exc))
            continue
        if err := payload.get("error"):
            code = (err or {}).get("code") if isinstance(err, dict) else None
            if code == "not_found":
                state_db.mark_cancelled(h, "rtorrent reports not_found")
            continue
        dl = payload.get("download")
        if dl:
            rows.append(_format_row(dl))

    if not rows:
        await message.answer(t("status.no_downloads"))
        return

    await message.answer(
        t("status.header") + "\n\n" + "\n\n".join(rows),
        parse_mode="HTML",
    )


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
        icon = "⬇️"
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
    seconds = (size - done) // rate
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
