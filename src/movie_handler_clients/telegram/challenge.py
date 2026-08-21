"""Cloudflare-challenge hand-off: mint one-time noVNC links, notify admins.

rutracker sits behind Cloudflare; when the MCP reports an interactive
challenge (`cloudflare_challenge`) or a lost session (`manual_auth_required`)
a human has to click through it in the server-side Chromium. The bot mints a
random token, drops it where the challenge gate can validate it (see
rutracker-torrent-mcp `deploy/challenge-gate.py`), and hands admins an inline
button pointing at the tokenised noVNC URL. The link lives as long as the
MCP's manual-login grace window keeps the browser on the display.
"""

from __future__ import annotations

import secrets
from pathlib import Path

import structlog
from aiogram.types import (
    InaccessibleMessage,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from ..core.config import get_settings
from ..core.i18n import t

log = structlog.get_logger(__name__)

CHALLENGE_CODES = frozenset({"cloudflare_challenge", "manual_auth_required"})


def _mint_challenge_url() -> str | None:
    """Write a fresh token and return the tokenised entry URL.

    Returns None when the feature is not configured or the token file cannot
    be written — callers fall through to their generic error reply. Each mint
    overwrites the previous token, so only the newest link works.
    """
    settings = get_settings()
    base = settings.rutracker_challenge_url_base
    token_path = settings.rutracker_challenge_token_path
    if not base or not token_path:
        return None
    token = secrets.token_urlsafe(32)
    try:
        path = Path(token_path)
        path.write_text(token + "\n", encoding="utf-8")
        path.chmod(0o600)
    except OSError as exc:
        log.warning("challenge.token_write_failed", path=token_path, error=str(exc))
        return None
    return f"{base.rstrip('/')}/enter/{token}"


async def maybe_handle_challenge(
    message: Message | InaccessibleMessage,
    err: object,
    tg_user_id: int | None,
    admin_user_ids: set[int],
    *,
    edit: bool = False,
) -> bool:
    """Turn a challenge error into an admin hand-off button.

    Returns True when the error was consumed (a challenge link was sent),
    False otherwise — the caller then falls through to its generic error
    reply. Admins get the button in the current chat; for everyone else the
    current chat gets a "please wait" note and the button fans out to admins.
    With ``edit=True`` the reply replaces ``message`` (a pending status
    message) instead of answering below it.
    """
    code = err.get("code") if isinstance(err, dict) else None
    if code not in CHALLENGE_CODES:
        return False
    url = _mint_challenge_url()
    if url is None:
        return False

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t("challenge.button"), url=url)]]
    )
    is_admin = tg_user_id is not None and tg_user_id in admin_user_ids
    text = t("challenge.admin_prompt") if is_admin else t("challenge.user_wait")
    markup = keyboard if is_admin else None
    if edit and not isinstance(message, InaccessibleMessage):
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)
    log.info("challenge.link_minted", code=code, requester_is_admin=is_admin)
    if is_admin:
        return True

    bot = message.bot
    if bot is None:  # detached object — nothing more we can do
        return True
    for admin_id in admin_user_ids:
        try:
            await bot.send_message(admin_id, t("challenge.admin_pinged"), reply_markup=keyboard)
        except Exception as exc:
            log.warning("challenge.admin_notify_failed", admin_id=admin_id, error=str(exc))
    return True
