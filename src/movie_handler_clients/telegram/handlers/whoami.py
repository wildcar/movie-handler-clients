"""/whoami — report the current Telegram user's id and admin flag."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from ...core.i18n import t
from ...core.state_db import StateDb

router = Router(name="whoami")


@router.message(Command("whoami"))
async def on_whoami(
    message: Message,
    state_db: StateDb,
    admin_user_ids: set[int],
) -> None:
    if message.from_user is None:
        return
    tg_user_id = message.from_user.id
    display_name = " ".join(
        p for p in (message.from_user.first_name, message.from_user.last_name) if p
    ).strip() or (message.from_user.username or "")
    chat_id = message.chat.id if message.chat else None

    user = state_db.upsert_telegram_user(
        tg_user_id=tg_user_id,
        display_name=display_name,
        chat_id=chat_id,
        is_admin=tg_user_id in admin_user_ids,
        meta={
            "username": message.from_user.username,
            "first_name": message.from_user.first_name,
            "last_name": message.from_user.last_name,
        },
    )

    body = t("whoami.user", tg_id=tg_user_id, id=user.id)
    body += "\n" + (t("whoami.admin_yes") if user.is_admin else t("whoami.admin_no"))
    await message.answer(body, parse_mode="HTML")


__all__ = ["router"]
