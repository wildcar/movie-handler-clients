"""Handler for /torrents — browse and manage rtorrent downloads.

Admins see every download in rtorrent's session (including ones added by
hand through ruTorrent); everyone else sees only the downloads they
started through the bot (``state.sqlite`` rows, yt-dlp excluded). The list
shows name, state and percent done; tapping a row opens a card with three
actions:

- **Удалить раздачу** — ``remove`` without data: rtorrent forgets the
  download, files and watch links stay.
- **Удалить с файлами** — ``remove(delete_data=True)`` plus a
  media-watch ``DELETE ?purge=1`` (records, episodes, remuxed share mp4s),
  then the bot forgets the download. Both deletions ask for confirmation.
- **Обновить** — re-fetch the .torrent from rutracker; if its info-hash
  changed, ``replace_torrent`` swaps it in rtorrent (data on disk reused)
  and the bot row follows the new hash so the poller re-registers it.

Callback data: ``trt:<op>:<arg>`` — ``l`` list page, ``s`` card,
``d``/``D`` ask to delete (keep / with files), ``dy``/``Dy`` confirmed,
``u`` update. ``<arg>`` is the 40-char info-hash (or the page number),
which keeps every payload under Telegram's 64-byte limit. Every action
re-checks ownership: a forwarded or stale button grants nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape as _esc
from typing import Any

import structlog
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InaccessibleMessage,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from ...core.i18n import t
from ...core.mcp_client import MCPClientError
from ...core.media_watch_client import MediaWatchClient, MediaWatchError
from ...core.rtorrent_client import RtorrentMCPClient
from ...core.state_db import Download, StateDb, User
from ...core.torrent_client import RutrackerTorrentMCPClient
from ..challenge import maybe_handle_challenge

router = Router(name="torrents")
log = structlog.get_logger(__name__)

PAGE_SIZE = 8
_BUTTON_NAME_LIMIT = 40
_TOPIC_RE = re.compile(r"rutracker\.[a-z]+/forum/viewtopic\.php\?(?:.*&)?t=(\d+)")
_STATE_ORDER = {"active": 0, "paused": 1, "stopped": 2, "complete": 3}


@dataclass(frozen=True)
class TorrentItem:
    """One rtorrent download as /torrents shows it, joined to its bot row."""

    torrent: dict[str, Any]
    download: Download | None
    owner: User | None

    @property
    def hash(self) -> str:
        return str(self.torrent.get("hash") or "").upper()

    @property
    def name(self) -> str:
        name = str(self.torrent.get("name") or "").strip()
        if name:
            return name
        return self.download.title if self.download else self.hash[:12]

    @property
    def percent(self) -> float:
        size = int(self.torrent.get("size_bytes") or 0)
        done = int(self.torrent.get("completed_bytes") or 0)
        if self.torrent.get("state") == "complete":
            return 100.0
        return round(done / size * 100, 1) if size > 0 else 0.0

    @property
    def topic_id(self) -> int | None:
        if self.download and self.download.media_id.startswith("rt-"):
            tail = self.download.media_id[3:]
            if tail.isdigit():
                return int(tail)
        m = _TOPIC_RE.search(str(self.torrent.get("comment") or ""))
        return int(m.group(1)) if m else None


class _Unavailable(Exception):
    """rtorrent-mcp missing or failing — the message is user-facing."""


# ------------------------------------------------------------------ data


async def _load_items(
    rtorrent: RtorrentMCPClient,
    state_db: StateDb,
    tg_user_id: int,
    is_admin: bool,
) -> list[TorrentItem]:
    """Downloads visible to this user, sorted active-first then by name."""
    try:
        payload = await rtorrent.list_downloads(tg_user_id=tg_user_id)
    except MCPClientError as exc:
        log.warning("torrents.list_failed", error=str(exc))
        raise _Unavailable(t("torrents.unavailable")) from exc
    if err := payload.get("error"):
        log.warning("torrents.list_tool_error", error=err)
        raise _Unavailable(t("torrents.unavailable"))

    known: dict[str, tuple[Download, User]] = {}
    if is_admin:
        rows = state_db.list_torrent_downloads()
    else:
        identity = state_db.get_telegram_identity(tg_user_id)
        rows = state_db.list_torrent_downloads(identity.user_id) if identity else []
    for dl, user in rows:
        known.setdefault(dl.info_hash.upper(), (dl, user))

    items: list[TorrentItem] = []
    for raw in payload.get("downloads") or []:
        if not isinstance(raw, dict):
            continue
        h = str(raw.get("hash") or "").upper()
        pair = known.get(h)
        if pair is None and not is_admin:
            continue
        items.append(
            TorrentItem(
                torrent=raw,
                download=pair[0] if pair else None,
                owner=pair[1] if pair else None,
            )
        )
    items.sort(key=lambda i: (_STATE_ORDER.get(str(i.torrent.get("state")), 9), i.name.lower()))
    return items


async def _load_item(
    rtorrent: RtorrentMCPClient,
    state_db: StateDb,
    tg_user_id: int,
    is_admin: bool,
    hash_: str,
) -> TorrentItem | None:
    """The single download ``hash_`` if this user may see/manage it."""
    for item in await _load_items(rtorrent, state_db, tg_user_id, is_admin):
        if item.hash == hash_.upper():
            return item
    return None


# ------------------------------------------------------------- rendering


def _state_label(item: TorrentItem) -> tuple[str, str]:
    state = str(item.torrent.get("state") or "")
    if state == "complete":
        return "✅", t("torrents.state.seeding")
    if state == "active":
        return "↓", t("torrents.state.downloading")
    if state == "paused":
        return "⏸", t("torrents.state.paused")
    return "⏹", t("torrents.state.stopped")


def _human_size(n: int) -> str:
    if n <= 0:
        return "—"
    gb = n / 1_073_741_824
    if gb >= 1:
        return f"{gb:.1f} ГБ"
    return f"{n / 1_048_576:.0f} МБ"


def _owner_label(item: TorrentItem) -> str:
    if item.owner is None:
        return t("torrents.owner_unknown")
    return item.owner.display_name or f"id={item.owner.id}"


def render_list(
    items: list[TorrentItem], page: int, *, is_admin: bool
) -> tuple[str, InlineKeyboardMarkup]:
    pages = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(max(page, 0), pages - 1)
    chunk = items[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    header = t("torrents.header_admin" if is_admin else "torrents.header", n=len(items))
    if pages > 1:
        header += " " + t("torrents.page", page=page + 1, pages=pages)
    lines = [header, ""]
    buttons: list[list[InlineKeyboardButton]] = []
    for idx, item in enumerate(chunk, start=page * PAGE_SIZE + 1):
        icon, label = _state_label(item)
        owner = f" · 👤 {_esc(_owner_label(item))}" if is_admin else ""
        lines.append(
            f"{idx}. {icon} <b>{_esc(item.name)}</b>\n    {item.percent}% · {label}{owner}"
        )
        short = item.name if len(item.name) <= _BUTTON_NAME_LIMIT else item.name[:39] + "…"
        buttons.append(
            [InlineKeyboardButton(text=f"{idx}. {short}", callback_data=f"trt:s:{item.hash}")]
        )
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀", callback_data=f"trt:l:{page - 1}"))
    nav.append(InlineKeyboardButton(text=t("torrents.refresh"), callback_data=f"trt:l:{page}"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text="▶", callback_data=f"trt:l:{page + 1}"))
    buttons.append(nav)
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)


def render_card(item: TorrentItem, *, is_admin: bool) -> tuple[str, InlineKeyboardMarkup]:
    icon, label = _state_label(item)
    size = int(item.torrent.get("size_bytes") or 0)
    lines = [
        f"{icon} <b>{_esc(item.name)}</b>",
        "",
        t("torrents.card.state", state=label, pct=item.percent),
        t("torrents.card.size", size=_human_size(size)),
    ]
    rate = int(item.torrent.get("down_rate") or 0)
    if rate > 0 and item.torrent.get("state") == "active":
        lines.append(t("torrents.card.rate", rate=_human_size(rate)))
    if is_admin:
        lines.append(t("torrents.card.owner", owner=_esc(_owner_label(item))))
    if item.topic_id is not None:
        lines.append(
            t(
                "torrents.card.topic",
                url=f"https://rutracker.org/forum/viewtopic.php?t={item.topic_id}",
            )
        )
    h = item.hash
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("torrents.btn.update"), callback_data=f"trt:u:{h}")],
            [InlineKeyboardButton(text=t("torrents.btn.remove"), callback_data=f"trt:d:{h}")],
            [InlineKeyboardButton(text=t("torrents.btn.remove_data"), callback_data=f"trt:D:{h}")],
            [InlineKeyboardButton(text=t("torrents.btn.back"), callback_data="trt:l:0")],
        ]
    )
    return "\n".join(lines), kb


def _confirm_kb(op: str, hash_: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("torrents.btn.confirm_yes"), callback_data=f"trt:{op}y:{hash_}"
                ),
                InlineKeyboardButton(text=t("torrents.btn.cancel"), callback_data=f"trt:s:{hash_}"),
            ]
        ]
    )


def _back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("torrents.btn.back"), callback_data="trt:l:0")]
        ]
    )


# -------------------------------------------------------------- handlers


@router.message(Command("torrents"))
async def on_torrents(
    message: Message,
    rtorrent: RtorrentMCPClient | None,
    state_db: StateDb,
    admin_user_ids: set[int],
) -> None:
    tg_user_id = message.from_user.id if message.from_user else None
    if tg_user_id is None:
        return
    if rtorrent is None:
        await message.answer(t("status.not_configured"))
        return
    is_admin = tg_user_id in admin_user_ids
    try:
        items = await _load_items(rtorrent, state_db, tg_user_id, is_admin)
    except _Unavailable as exc:
        await message.answer(str(exc))
        return
    if not items:
        await message.answer(t("torrents.empty"))
        return
    text, kb = render_list(items, 0, is_admin=is_admin)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("trt:"))
async def on_torrents_callback(
    cq: CallbackQuery,
    rtorrent: RtorrentMCPClient | None,
    torrent: RutrackerTorrentMCPClient | None,
    state_db: StateDb,
    admin_user_ids: set[int],
    media_watch: MediaWatchClient | None = None,
) -> None:
    parts = (cq.data or "").split(":", 2)
    if len(parts) != 3 or not _usable(cq.message) or cq.from_user is None:
        await cq.answer()
        return
    _, op, arg = parts
    if rtorrent is None:
        await cq.answer(t("status.not_configured"), show_alert=True)
        return
    tg_user_id = cq.from_user.id
    is_admin = tg_user_id in admin_user_ids
    message = cq.message
    assert message is not None and not isinstance(message, InaccessibleMessage)

    try:
        if op == "l":
            items = await _load_items(rtorrent, state_db, tg_user_id, is_admin)
            if not items:
                await _edit(message, t("torrents.empty"), None)
            else:
                page = int(arg) if arg.isdigit() else 0
                await _edit(message, *render_list(items, page, is_admin=is_admin))
            await cq.answer()
            return

        item = await _load_item(rtorrent, state_db, tg_user_id, is_admin, arg)
        if item is None:
            await cq.answer(t("torrents.gone"), show_alert=True)
            return

        if op == "s":
            await _edit(message, *render_card(item, is_admin=is_admin))
            await cq.answer()
        elif op in ("d", "D"):
            key = "torrents.confirm_remove" if op == "d" else "torrents.confirm_remove_data"
            await _edit(message, t(key, name=_esc(item.name)), _confirm_kb(op, item.hash))
            await cq.answer()
        elif op in ("dy", "Dy"):
            await cq.answer(t("torrents.removing"))
            # Deleting a large payload takes a while: swap the confirm
            # buttons for a progress line at once, so nobody stares at
            # «Да, удалить» wondering whether the tap registered (or taps
            # again).
            progress = "torrents.removing_data" if op == "Dy" else "torrents.removing_progress"
            await _edit(message, t(progress, name=_esc(item.name)), None)
            await _remove(message, rtorrent, state_db, media_watch, item, op == "Dy", tg_user_id)
        elif op == "u":
            await cq.answer(t("torrents.updating"))
            await _edit(message, t("torrents.updating_progress", name=_esc(item.name)), None)
            await _update(
                cq, message, rtorrent, torrent, state_db, admin_user_ids, item, tg_user_id
            )
        else:
            await cq.answer()
    except _Unavailable as exc:
        await cq.answer(str(exc), show_alert=True)


def _usable(message: object) -> bool:
    """A callback's message can be edited unless it's gone/inaccessible."""
    return message is not None and not isinstance(message, InaccessibleMessage)


async def _edit(message: Message, text: str, kb: InlineKeyboardMarkup | None) -> None:
    try:
        await message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception as exc:  # "message is not modified" on a no-op refresh
        log.debug("torrents.edit_skipped", error=str(exc))


async def _remove(
    message: Message,
    rtorrent: RtorrentMCPClient,
    state_db: StateDb,
    media_watch: MediaWatchClient | None,
    item: TorrentItem,
    delete_data: bool,
    tg_user_id: int,
) -> None:
    try:
        payload = await rtorrent.remove(item.hash, delete_data=delete_data, tg_user_id=tg_user_id)
    except MCPClientError as exc:
        log.warning("torrents.remove_failed", hash=item.hash, error=str(exc))
        await _edit(message, t("torrents.remove_error", detail=_esc(str(exc))), _back_kb())
        return
    if err := payload.get("error"):
        detail = err.get("message") if isinstance(err, dict) else str(err)
        log.warning("torrents.remove_tool_error", hash=item.hash, error=err)
        await _edit(message, t("torrents.remove_error", detail=_esc(str(detail))), _back_kb())
        return

    dl = item.download
    if delete_data:
        # Only purge media-watch records the bot itself registered for this
        # download: a foreign torrent's guessed ``rt-<topic>`` might belong
        # to another copy on disk. The hourly sweep catches the rest.
        if dl is not None and media_watch is not None:
            try:
                await media_watch.delete(dl.media_id, purge=True)
            except (MediaWatchError, MCPClientError, OSError, RuntimeError) as exc:
                log.warning("torrents.purge_failed", media_id=dl.media_id, error=str(exc))
        if dl is not None:
            state_db.delete_download(dl.id)
        text = t(
            "torrents.removed_data",
            name=_esc(item.name),
            n=int(payload.get("deleted_files") or 0),
        )
    else:
        if dl is not None and dl.state in ("downloading", "complete_pending_register"):
            state_db.mark_cancelled(dl.info_hash, "removed via /torrents")
        text = t("torrents.removed", name=_esc(item.name))
    log.info("torrents.removed", hash=item.hash, delete_data=delete_data, by=tg_user_id)
    await _edit(message, text, _back_kb())


async def _update(
    cq: CallbackQuery,
    message: Message,
    rtorrent: RtorrentMCPClient,
    torrent: RutrackerTorrentMCPClient | None,
    state_db: StateDb,
    admin_user_ids: set[int],
    item: TorrentItem,
    tg_user_id: int,
) -> None:
    is_admin = tg_user_id in admin_user_ids
    topic_id = item.topic_id
    if topic_id is None:
        await _edit(message, t("torrents.update_no_topic", name=_esc(item.name)), _back_kb())
        return
    if torrent is None:
        await _edit(message, t("stub.download"), _back_kb())
        return

    try:
        payload = await torrent.get_torrent_file(topic_id, tg_user_id=tg_user_id)
    except MCPClientError as exc:
        await _edit(message, t("torrents.update_error", detail=_esc(str(exc))), _back_kb())
        return
    if err := payload.get("error"):

        async def _retry(new_cq: CallbackQuery) -> None:
            retry_message = new_cq.message
            if retry_message is None or isinstance(retry_message, InaccessibleMessage):
                return
            await _update(
                new_cq,
                retry_message,
                rtorrent,
                torrent,
                state_db,
                admin_user_ids,
                item,
                tg_user_id,
            )

        if not await maybe_handle_challenge(
            message, err, tg_user_id, admin_user_ids, requester=cq.from_user, retry=_retry
        ):
            detail = err.get("message") if isinstance(err, dict) else str(err)
            await _edit(message, t("torrents.update_error", detail=_esc(str(detail))), _back_kb())
        return
    b64 = (payload.get("file") or {}).get("content_base64")
    if not isinstance(b64, str):
        await _edit(message, t("torrents.update_error", detail="empty .torrent"), _back_kb())
        return

    try:
        result = await rtorrent.replace_torrent(
            item.hash, torrent_file_base64=b64, tg_user_id=tg_user_id
        )
    except MCPClientError as exc:
        await _edit(message, t("torrents.update_error", detail=_esc(str(exc))), _back_kb())
        return
    if err := result.get("error"):
        detail = err.get("message") if isinstance(err, dict) else str(err)
        log.warning("torrents.replace_tool_error", hash=item.hash, error=err)
        await _edit(message, t("torrents.update_error", detail=_esc(str(detail))), _back_kb())
        return

    if not result.get("changed"):
        text, kb = render_card(item, is_admin=is_admin)
        await _edit(message, t("torrents.update_unchanged") + "\n\n" + text, kb)
        return

    new = result.get("download") or {}
    new_hash = str(new.get("hash") or "").upper()
    if item.download is not None and new_hash:
        state_db.replace_download_hash(item.download.id, new_hash)
    log.info("torrents.updated", old=item.hash, new=new_hash, topic=topic_id, by=tg_user_id)
    new_item = TorrentItem(
        torrent=new, download=state_db.get_download_by_hash(new_hash), owner=item.owner
    )
    text, kb = render_card(new_item, is_admin=is_admin)
    await _edit(message, t("torrents.update_done") + "\n\n" + text, kb)


__all__ = ["render_card", "render_list", "router"]
