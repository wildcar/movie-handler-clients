"""Inline keyboards for the Telegram bot."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..core.i18n import t


def search_results_keyboard(items: list[dict[str, object]], query_id: str) -> InlineKeyboardMarkup:
    """One button per search hit — localized title + year, kind icon as prefix.

    Items arrive already sorted by the handler (by kind, then year desc).
    """
    rows: list[list[InlineKeyboardButton]] = []
    for item in items:
        imdb_id = item.get("imdb_id")
        if not imdb_id:
            continue
        icon = "📺" if item.get("kind") == "series" else "🎬"
        title = str(item.get("title") or "—")
        year = item.get("year")
        label = f"{icon} {title}"
        if year:
            label += f" ({year})"
        rows.append(
            [InlineKeyboardButton(text=label[:64], callback_data=f"d:{imdb_id}:{query_id}")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def details_keyboard(imdb_id: str, query_id: str | None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text=t("details.button_trailer"), callback_data=f"t:{imdb_id}"),
            InlineKeyboardButton(text=t("details.button_download"), callback_data=f"dl:{imdb_id}"),
        ]
    ]
    if query_id:
        rows.append(
            [InlineKeyboardButton(text=t("details.button_back"), callback_data=f"b:{query_id}")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)
