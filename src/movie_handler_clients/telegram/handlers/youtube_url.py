"""Handle a pasted video URL (YouTube and any other yt-dlp-supported site).

The user drops a link like ``https://www.youtube.com/watch?v=...`` or a
Vimeo / Twitch / TikTok / … URL. We probe via ``yt-dlp-mcp.probe``,
render a preview card with thumbnail + title + channel + duration and
a single «↓ Скачать» button. Clicking it spawns a background yt-dlp
download on the media host; the bot's poller picks the row up on the
next 60-sec tick and registers the finished file with media-watch-web,
exactly like the rutracker flow.

Composite media id:
- ``yt-<video_id>`` for YouTube hosts.
- ``dl-<sha1(canonical_url)[:12]>`` for everything else yt-dlp can
  extract (Vimeo, Twitch, TikTok, …).

rutracker URLs are claimed by ``rutracker_url.py`` because that router
is registered earlier in ``bot.py``; this handler's filter is broad
(`https?://...`) but it never sees rutracker because aiogram dispatches
to the first matching router.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import secrets
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from ...core.i18n import t
from ...core.mcp_client import MCPClientError
from ...core.state_db import StateDb
from ...core.yt_dlp_client import YtDlpMCPClient
from ..ydl_cache import YtDlpCache, YtDlpEntry

router = Router(name="youtube_url")
log = structlog.get_logger(__name__)

_START_TIMEOUT_SECONDS = 45.0
# Budget for fetching a preview thumbnail ourselves when Telegram won't.
_THUMBNAIL_TIMEOUT_SECONDS = 8.0
# Telegram's own limit for a photo upload is 10 MB; a preview that heavy is
# not worth the wait either way.
_THUMBNAIL_MAX_BYTES = 5 * 1024 * 1024


# Any http(s) URL kicks the handler — yt-dlp's extractor list is the
# real authority on what's supported, so we don't try to whitelist
# domains. The rutracker router runs first (registered before this in
# bot.py) and consumes its own URLs.
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
# YouTube host detection — picks the media_id prefix. Covers the four
# canonical hostnames the YouTube apps emit.
_YT_HOST_RE = re.compile(
    r"(?:^|\.)(?:youtube\.com|youtu\.be|youtube-nocookie\.com|m\.youtube\.com)$",
    re.IGNORECASE,
)
# Playlist URL → list_playlist flow (no preview, just a clickable list
# the user copies a video link from). Matches both /playlist and /watch
# pages that carry a list= param.
_PLAYLIST_RE = re.compile(r"[?&]list=[\w-]+", re.IGNORECASE)
# Bare rutracker URLs are already claimed by the rutracker_url router
# upstream; this regex is purely belt-and-suspenders so a misordered
# router registration doesn't double-process.
_RUTRACKER_RE = re.compile(r"rutracker\.org/forum/viewtopic\.php\?", re.IGNORECASE)
# Yandex.Video preview pages — yt-dlp recognises them but the
# extractor is broken upstream (Unable to extract data_raw). Skip the
# round-trip and send a useful hint right away.
_YANDEX_PREVIEW_RE = re.compile(r"yandex\.[a-z]+/video/preview/", re.IGNORECASE)


@router.message(F.text.regexp(_URL_RE))
async def on_url(
    message: Message,
    yt_dlp: YtDlpMCPClient | None,
    ydl_cache: YtDlpCache,
) -> None:
    text = message.text or ""
    m = _URL_RE.search(text)
    if m is None:
        return
    url = m.group(0).rstrip(").,;")
    if _RUTRACKER_RE.search(url):
        return  # owned by rutracker_url.py
    if _YANDEX_PREVIEW_RE.search(url):
        # yt-dlp's YandexVideoPreview extractor is broken (Apr 2026);
        # tailored message saves the user a fruitless probe + the
        # generic «ссылка не распознана» which is misleading when we
        # actually know the host.
        await message.answer(t("ydl.yandex_preview_hint"))
        return

    if yt_dlp is None:
        await message.answer(t("ydl.unsupported"))
        return

    tg_user_id = message.from_user.id if message.from_user else None

    pending = await message.answer(t("ydl.fetching"))

    # Whatever goes wrong past this point, the «Смотрю видео…» bubble must
    # not be left hanging — that reads as a frozen bot. MCP failures have
    # tailored copy; anything else gets the generic apology plus a traceback
    # in the log.
    try:
        if _PLAYLIST_RE.search(url):
            await _handle_playlist(pending, yt_dlp, url, tg_user_id=tg_user_id)
        else:
            await _probe_and_render(
                message, pending, yt_dlp, ydl_cache, url, tg_user_id=tg_user_id
            )
    except asyncio.CancelledError:
        log.warning("ydl.cancelled", url=url)
        raise
    except Exception:
        log.exception("ydl.preview_crashed", url=url)
        await _safe_edit(pending, t("ydl.internal_error"))


async def _probe_and_render(
    message: Message,
    pending: Message,
    yt_dlp: YtDlpMCPClient,
    ydl_cache: YtDlpCache,
    url: str,
    *,
    tg_user_id: int | None,
) -> None:
    """Probe ``url`` and replace ``pending`` with a preview card or a reason."""
    try:
        payload = await yt_dlp.probe(url, tg_user_id=tg_user_id)
    except MCPClientError as exc:
        log.warning("ydl.probe_failed", url=url, error=str(exc))
        await pending.edit_text(t("ydl.probe_failed"))
        return

    if err := payload.get("error"):
        await pending.edit_text(t(_probe_error_key(err)))
        return

    probe = payload.get("probe") or {}
    if probe.get("is_live"):
        await pending.edit_text(t("ydl.live_unsupported"))
        return

    video_id = str(probe.get("video_id") or "")
    title = str(probe.get("title") or "?")
    channel = str(probe.get("channel") or probe.get("uploader") or "")
    duration = _format_duration(probe.get("duration_seconds"))
    thumb_url = _pick_thumbnail(probe.get("thumbnails") or [])

    if not video_id:
        await pending.edit_text(t("ydl.unsupported"))
        return

    token = secrets.token_urlsafe(6)
    ydl_cache.put(token, YtDlpEntry(url=url, title=title))

    if channel:
        caption = t("ydl.preview", title=_esc(title), channel=_esc(channel), duration=duration)
    else:
        caption = t("ydl.preview_no_channel", title=_esc(title), duration=duration)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("ydl.confirm_button"), callback_data=f"ydl:{token}")],
        ]
    )

    # Photo + caption replaces the «Смотрю видео…» bubble — but only once the
    # photo is actually accepted. Deleting the bubble first left nothing on
    # screen when Telegram refused the picture. Telegram caps captions at 1024
    # chars; titles in the wild stay well under.
    if thumb_url and await _send_photo_card(message, thumb_url, caption, keyboard):
        await pending.delete()
        return

    await pending.edit_text(
        caption,
        parse_mode="HTML",
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )


async def _send_photo_card(
    message: Message,
    thumb_url: str,
    caption: str,
    keyboard: InlineKeyboardMarkup,
) -> bool:
    """Send the preview card with its thumbnail. False if the card must go out
    as plain text instead.

    Two attempts, because Telegram fetches a photo URL from its own servers
    and some hosts refuse it (static.1tv.ru answers Telegram with something
    that isn't the image — «wrong type of the web page content» — while
    serving us a clean `image/jpeg`). So we hand over the bytes ourselves
    before giving up on the picture.
    """
    try:
        await message.answer_photo(
            photo=thumb_url, caption=caption, parse_mode="HTML", reply_markup=keyboard
        )
        return True
    except TelegramBadRequest as exc:
        log.info("ydl.thumbnail_url_rejected", thumb=thumb_url, error=str(exc))

    photo = await _fetch_thumbnail(thumb_url)
    if photo is None:
        return False
    try:
        await message.answer_photo(
            photo=photo, caption=caption, parse_mode="HTML", reply_markup=keyboard
        )
        return True
    except TelegramBadRequest as exc:
        log.info("ydl.thumbnail_upload_rejected", thumb=thumb_url, error=str(exc))
        return False


async def _fetch_thumbnail(thumb_url: str) -> BufferedInputFile | None:
    """Download a thumbnail for upload. ``None`` when it isn't worth sending.

    Bounded on both time and size: a preview card is never worth stalling the
    handler or holding megabytes for.
    """
    try:
        async with httpx.AsyncClient(timeout=_THUMBNAIL_TIMEOUT_SECONDS) as client:
            resp = await client.get(thumb_url, follow_redirects=True)
        resp.raise_for_status()
    except Exception as exc:
        log.info("ydl.thumbnail_fetch_failed", thumb=thumb_url, error=str(exc))
        return None

    if not resp.headers.get("content-type", "").startswith("image/"):
        log.info("ydl.thumbnail_not_an_image", thumb=thumb_url)
        return None
    if len(resp.content) > _THUMBNAIL_MAX_BYTES:
        log.info("ydl.thumbnail_too_large", thumb=thumb_url, size=len(resp.content))
        return None

    name = PurePosixPath(urlparse(thumb_url).path).name or "thumb.jpg"
    return BufferedInputFile(resp.content, filename=name)


@router.callback_query(F.data.startswith("ydl:"))
async def on_confirm(
    cq: CallbackQuery,
    yt_dlp: YtDlpMCPClient | None,
    ydl_cache: YtDlpCache,
    state_db: StateDb,
    admin_user_ids: set[int],
) -> None:
    token = (cq.data or "")[4:]
    entry = ydl_cache.get(token)
    if entry is None or cq.message is None:
        await cq.answer()
        return
    if yt_dlp is None:
        await cq.answer(t("ydl.unsupported"), show_alert=True)
        return

    tg_user_id = cq.from_user.id if cq.from_user else None
    await cq.answer()

    # Hide the «Скачать» button so the user can't double-tap during the
    # round-trip.
    try:
        await cq.message.edit_reply_markup(reply_markup=None)  # type: ignore[union-attr]
    except Exception:
        pass

    try:
        payload = await asyncio.wait_for(
            yt_dlp.start_download(entry.url, tg_user_id=tg_user_id),
            timeout=_START_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        log.warning("ydl.start_timeout", url=entry.url)
        await cq.message.answer(t("ydl.start_timeout"))
        return
    except MCPClientError as exc:
        log.warning("ydl.start_failed", url=entry.url, error=str(exc))
        await cq.message.answer(t("ydl.start_failed", detail=str(exc)))
        return

    if err := payload.get("error"):
        await cq.message.answer(t("ydl.start_failed", detail=_err_msg(err)))
        return

    task = payload.get("task") or {}
    task_id = str(task.get("task_id") or "")
    if not task_id:
        await cq.message.answer(t("ydl.start_failed", detail="empty task_id"))
        return

    # Persist into state.sqlite so the poller can register the file with
    # media-watch-web on completion. We reuse the `info_hash` column as
    # a generic per-task identifier — for yt-dlp rows it carries the
    # task_id (16 hex chars), for rutracker rows it carries the BT info
    # hash. The poller dispatches by `source`.
    if cq.from_user is None or tg_user_id is None:
        return
    display_name = " ".join(
        p for p in (cq.from_user.first_name, cq.from_user.last_name) if p
    ).strip() or (cq.from_user.username or "")
    chat_id = cq.message.chat.id if cq.message.chat else tg_user_id
    user = state_db.upsert_telegram_user(
        tg_user_id=tg_user_id,
        display_name=display_name,
        chat_id=chat_id,
        is_admin=tg_user_id in admin_user_ids,
        meta={
            "username": cq.from_user.username,
            "first_name": cq.from_user.first_name,
            "last_name": cq.from_user.last_name,
        },
    )
    state_db.add_download(
        user_id=user.id,
        info_hash=task_id,
        kind="movie",
        title=entry.title,
        media_id=_compose_media_id(entry.url, str(task.get("video_id") or "")),
        source="yt-dlp",
    )

    await cq.message.answer(
        t("ydl.queued", title=_esc(entry.title)),
        parse_mode="HTML",
    )


async def _safe_edit(pending: Message, text: str) -> None:
    """Best-effort report into the pending bubble; never raises.

    Editing loses to a bubble that's already been deleted — which is exactly
    what happens when the crash lands between the delete and the card — so
    fall back to a fresh message in the same chat. Only a dead chat can make
    both fail, and then there's nothing left to salvage.
    """
    try:
        await pending.edit_text(text)
        return
    except Exception:
        log.debug("ydl.pending_edit_failed", exc_info=True)
    try:
        await pending.answer(text)
    except Exception:
        log.debug("ydl.pending_answer_failed", exc_info=True)


async def _handle_playlist(
    pending: Message,
    yt_dlp: YtDlpMCPClient,
    url: str,
    *,
    tg_user_id: int | None,
) -> None:
    try:
        payload = await yt_dlp.list_playlist(url, tg_user_id=tg_user_id)
    except MCPClientError as exc:
        log.warning("ydl.list_playlist_failed", url=url, error=str(exc))
        await pending.edit_text(t("ydl.unsupported"))
        return
    if payload.get("error"):
        await pending.edit_text(t("ydl.unsupported"))
        return

    entries = payload.get("entries") or []
    if not entries:
        await pending.edit_text(t("ydl.unsupported"))
        return

    title = str(payload.get("playlist_title") or "?")
    total = int(payload.get("total_entries") or len(entries))
    n = len(entries)

    lines: list[str] = []
    lines.append(
        t(
            "ydl.playlist_header",
            title=_esc(title),
            n=total,
            video_word=_plural_videos(total),
        )
    )
    if total > n:
        lines.append(t("ydl.playlist_truncated_hint", limit=n, total=total))
    lines.append("")
    for i, entry in enumerate(entries, start=1):
        e_title = str(entry.get("title") or "?")
        e_url = str(entry.get("url") or "")
        e_dur = _format_duration(entry.get("duration_seconds"))
        lines.append(
            t(
                "ydl.playlist_entry",
                n=i,
                url=_esc(e_url),
                title=_esc(e_title),
                duration=e_dur,
            )
        )

    await pending.edit_text(
        "\n".join(lines),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


def _format_duration(seconds: Any) -> str:
    if not isinstance(seconds, int) or seconds <= 0:
        return t("ydl.duration_unknown")
    if seconds >= 3600:
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}"
    m, s = divmod(seconds, 60)
    return f"{m}:{s:02d}"


def _pick_thumbnail(thumbnails: list[Any]) -> str | None:
    """Pick the highest-resolution thumbnail with a usable URL.

    yt-dlp orders thumbnails by ``preference``; the last entry tends to
    be the recommended one, but we still pick by resolution to handle
    the YouTube case where the «maxresdefault» variant has the largest
    width but no preference set.
    """
    best_url: str | None = None
    best_area = -1
    for th in thumbnails:
        if not isinstance(th, dict):
            continue
        url = th.get("url")
        if not isinstance(url, str) or not url:
            continue
        w = int(th.get("width") or 0)
        h = int(th.get("height") or 0)
        area = w * h
        if area > best_area:
            best_area = area
            best_url = url
    return best_url


def _compose_media_id(url: str, video_id: str) -> str:
    host = urlparse(url).hostname or ""
    if _YT_HOST_RE.search(host) and video_id:
        return f"yt-{video_id}"
    return "dl-" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


def _plural_videos(n: int) -> str:
    # Russian numeric agreement for «видео» — invariant in nominative
    # plural, but the word in front of it changes ("1 видео", "2 видео",
    # "5 видео"). We surface the count word the i18n template builds
    # around; the plural-aware piece is just the noun.
    return "видео"


def _esc(s: str) -> str:
    from html import escape

    return escape(s)


def _err_msg(err: object) -> str:
    if isinstance(err, dict):
        return str(err.get("message") or err.get("code") or err)
    return str(err)


def _probe_error_key(err: object) -> str:
    """Map extractor failures without mislabelling a valid URL as unsupported."""
    if not isinstance(err, dict):
        return "ydl.probe_failed"
    code = str(err.get("code") or "").lower()
    message = str(err.get("message") or "").lower()
    if "not a bot" in message or "confirm you’re not a bot" in message:
        return "ydl.youtube_blocked"
    if code in {"invalid_argument", "unsupported"}:
        return "ydl.unsupported"
    return "ydl.probe_failed"
