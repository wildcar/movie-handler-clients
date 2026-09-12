"""Cloudflare-challenge hand-off: mint one-time noVNC links, notify admins.

rutracker sits behind Cloudflare; when the MCP reports an interactive
challenge (`cloudflare_challenge`) or a lost session (`manual_auth_required`)
a human has to click through it in the server-side Chromium. The bot mints a
random token, drops it where the challenge gate can validate it (see
rutracker-torrent-mcp `deploy/challenge-gate.py`), and hands admins an inline
button pointing at the tokenised noVNC URL. The link lives as long as the
MCP's manual-login grace window keeps the browser on the display.

Passing the challenge (or signing in) leaves no trace the bot could watch
for, so the hand-off carries a second button — «Проверку прошёл» — that
replays the very action the challenge interrupted. Callers hand in a
``retry`` coroutine factory; it is parked in a short-lived in-process
registry keyed by a random token that travels in the callback data.
"""

from __future__ import annotations

import secrets
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from pathlib import Path

import structlog
from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InaccessibleMessage,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from ..core.config import get_settings
from ..core.i18n import t

log = structlog.get_logger(__name__)

router = Router(name="challenge")

CHALLENGE_CODES = frozenset({"cloudflare_challenge", "manual_auth_required"})

RETRY_PREFIX = "chok:"
# Matches the challenge-gate cookie/token TTL: once the link dies the
# browser leaves the display anyway, so a retry parked longer than that
# could only fail again.
_RETRY_TTL = 1800.0
# The registry is per-process and tiny on purpose — a parked retry holds
# whole handler closures (clients, caches, the original callback query).
_RETRY_MAX = 32

RetryCallable = Callable[[CallbackQuery], Awaitable[None]]

_retries: OrderedDict[str, tuple[float, RetryCallable]] = OrderedDict()


def _park_retry(retry: RetryCallable) -> str:
    """Store `retry` and return the token that names it in callback data."""
    now = time.monotonic()
    for key, (expires, _) in list(_retries.items()):
        if expires <= now:
            del _retries[key]
    while len(_retries) >= _RETRY_MAX:
        _retries.popitem(last=False)
    key = secrets.token_urlsafe(8)
    _retries[key] = (now + _RETRY_TTL, retry)
    return key


def _take_retry(key: str) -> RetryCallable | None:
    """Pop a parked retry; None when it never existed or has expired."""
    entry = _retries.pop(key, None)
    if entry is None:
        return None
    expires, retry = entry
    return retry if expires > time.monotonic() else None


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
    retry: RetryCallable | None = None,
) -> bool:
    """Turn a challenge error into an admin hand-off button.

    Returns True when the error was consumed (a challenge link was sent),
    False otherwise — the caller then falls through to its generic error
    reply. Admins get the button in the current chat; for everyone else the
    current chat gets a "please wait" note and the button fans out to admins.
    With ``edit=True`` the reply replaces ``message`` (a pending status
    message) instead of answering below it.

    ``retry`` — when given — is parked and offered as a second button, so
    the human who just solved the challenge can replay the interrupted
    action instead of retracing the whole flow by hand. It is called with
    the *new* callback query (the button press), and must therefore reply
    relative to that message rather than to anything captured here.
    """
    code = err.get("code") if isinstance(err, dict) else None
    if code not in CHALLENGE_CODES:
        return False
    url = _mint_challenge_url()
    if url is None:
        return False

    rows = [[InlineKeyboardButton(text=t("challenge.button"), url=url)]]
    if retry is not None:
        key = _park_retry(retry)
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("challenge.retry_button"),
                    callback_data=f"{RETRY_PREFIX}{key}",
                )
            ]
        )
    keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
    is_admin = tg_user_id is not None and tg_user_id in admin_user_ids
    # A lost session needs a sign-in, not just a Turnstile click — saying
    # «Cloudflare» there sent operators back to a page that looked fine.
    logged_out = code == "manual_auth_required"
    if is_admin:
        text = t("challenge.login_prompt" if logged_out else "challenge.admin_prompt")
    else:
        text = t("challenge.login_user_wait" if logged_out else "challenge.user_wait")
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
            await bot.send_message(
                admin_id,
                t("challenge.login_admin_pinged" if logged_out else "challenge.admin_pinged"),
                reply_markup=keyboard,
            )
        except Exception as exc:
            log.warning("challenge.admin_notify_failed", admin_id=admin_id, error=str(exc))
    return True


@router.callback_query(F.data.startswith(RETRY_PREFIX))
async def on_challenge_passed(cq: CallbackQuery) -> None:
    """«Проверку прошёл» — replay the action the challenge interrupted.

    The parked retry is popped, so a double tap can only run the action
    once; the challenge keyboard goes with it, because both its buttons
    are spent (the noVNC link stays reachable from the earlier message).
    The retry owns ``cq.answer`` — see the comment below.
    """
    key = (cq.data or "")[len(RETRY_PREFIX) :]
    retry = _take_retry(key)
    if retry is None:
        await cq.answer(t("challenge.retry_expired"), show_alert=True)
        return
    # The spinner is left for the retry to clear: a callback query takes
    # exactly one answer, and every replayed action opens with its own.
    if cq.message is not None and not isinstance(cq.message, InaccessibleMessage):
        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception:  # message too old to edit — the retry still runs
            pass
    log.info("challenge.retry_fired", user_id=cq.from_user.id if cq.from_user else None)
    await retry(cq)
