"""Admin-only commands.

Two slash commands, both gated on ``cq.from_user.id in admin_user_ids``:

- ``/notify_toggle`` — flip the per-admin «DM me when any user's
  download finishes» flag. Stored in ``users.notify_downloads``;
  consumed by ``bot._register_and_notify`` after the user-side
  watch-link message goes out.
- ``/global_list`` — admin-side counterpart of ``/list``: every
  registered download grouped by owning user. Compact format, splits
  across multiple messages when over Telegram's ~4 KB limit.

`/whoami` lives in `whoami.py` and stays accessible to everyone (just
hidden from the user menu); admins see /notify_toggle and /global_list
in their own scoped menu via ``BotCommandScopeChat`` set up in
``bot.py``.
"""

from __future__ import annotations

from html import escape as _esc
from urllib.parse import urlparse

import structlog
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from ...core.i18n import t
from ...core.state_db import Download, StateDb, User

router = Router(name="admin")
log = structlog.get_logger(__name__)


# Telegram caps a single message at 4096 chars. /global_list can blow
# past that on busy installs, so we chunk the output and send several
# messages back-to-back. Buffer < the API limit by a margin to leave
# room for HTML entities expanding past visible character count.
_MESSAGE_CHUNK_LIMIT = 3500


@router.message(Command("notify_toggle"))
async def on_notify_toggle(
    message: Message,
    state_db: StateDb,
    admin_user_ids: set[int],
) -> None:
    tg_user_id = message.from_user.id if message.from_user else None
    if tg_user_id is None or tg_user_id not in admin_user_ids:
        await message.answer(t("admin.not_admin"))
        return
    identity = state_db.get_telegram_identity(tg_user_id)
    if identity is None:
        # Admin hasn't said anything to the bot yet → no users row.
        # Bootstrap by upserting them so we have somewhere to store
        # the flag.
        user = state_db.upsert_telegram_user(
            tg_user_id=tg_user_id,
            chat_id=message.chat.id if message.chat else tg_user_id,
            is_admin=True,
        )
    else:
        existing = state_db.get_user(identity.user_id)
        if existing is None:
            return
        user = existing

    new_value = not user.notify_downloads
    state_db.set_notify_downloads(user.id, new_value)
    await message.answer(t("admin.notify_on" if new_value else "admin.notify_off"))


@router.message(Command("global_list"))
async def on_global_list(
    message: Message,
    state_db: StateDb,
    admin_user_ids: set[int],
) -> None:
    tg_user_id = message.from_user.id if message.from_user else None
    if tg_user_id is None or tg_user_id not in admin_user_ids:
        await message.answer(t("admin.not_admin"))
        return

    rows = state_db.list_all_registered_with_user()
    if not rows:
        await message.answer(t("admin.global_list_empty"))
        return

    # Group by user so each block reads as «owner → titles».
    by_user: dict[int, tuple[User, list[Download]]] = {}
    for download, user in rows:
        bucket = by_user.setdefault(user.id, (user, []))
        bucket[1].append(download)

    lines: list[str] = [t("admin.global_list_header"), ""]
    for user, downloads in by_user.values():
        lines.append(t("admin.global_list_user_header", name=_esc(_user_label(user))))
        for dl in downloads:
            line = _format_download_line(state_db, dl)
            if line:
                lines.append(line)
        lines.append("")  # blank separator between users

    for chunk in _chunk_message(lines):
        await message.answer(chunk, parse_mode="HTML", disable_web_page_preview=True)


def _user_label(user: User) -> str:
    """Display name fallback chain: name → numeric id."""
    return user.display_name or f"id={user.id}"


def _format_download_line(state_db: StateDb, dl: Download) -> str | None:
    records = state_db.list_watch_records(dl.id)
    if not records:
        return None
    first = records[0]
    if dl.kind == "series" and len(records) > 1:
        # Link to the series index page on media-watch-web. Reuse host
        # from the first episode's watch URL — same shape as /list.
        parsed = urlparse(first.watch_url)
        base = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
        return t(
            "admin.global_list_series_line",
            url=f"{base}/series/{dl.media_id}",
            title=_esc(dl.title),
            n=len(records),
        )
    return t(
        "admin.global_list_movie_line",
        url=first.watch_url,
        title=_esc(dl.title or "—"),
    )


def _chunk_message(lines: list[str]) -> list[str]:
    """Group lines into ≤``_MESSAGE_CHUNK_LIMIT``-byte chunks, breaking
    only on line boundaries. Ensures we never split mid-link."""
    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for line in lines:
        next_len = buf_len + len(line) + 1
        if buf and next_len > _MESSAGE_CHUNK_LIMIT:
            chunks.append("\n".join(buf))
            buf = [line]
            buf_len = len(line) + 1
        else:
            buf.append(line)
            buf_len = next_len
    if buf:
        chunks.append("\n".join(buf))
    return chunks


__all__ = ["router"]
