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


def torrent_list_keyboard(
    results: list[dict[str, object]], *, imdb_id: str = ""
) -> InlineKeyboardMarkup:
    """One button per torrent.

    Layout: ``#ID  | • SIZE • QUALITY • HDR • 🌱SEEDS``. The leading bar
    (``|``, ``||``, ``|||`` …, one per 10 GB) gives a visual size-comparison
    column; Telegram inline buttons can't be left-aligned, but the bar
    plus a fixed-width ID badge creates a pseudo-column effect.
    """
    rows: list[list[InlineKeyboardButton]] = []
    for r in results:
        topic_id = r.get("topic_id")
        if not isinstance(topic_id, int):
            continue
        parts: list[str] = [f"#{topic_id}"]
        size_b = r.get("size_bytes")
        if isinstance(size_b, int) and size_b > 0:
            parts.append(_size_bar(size_b))
            parts.append(_human_size(size_b))
        quality = r.get("quality")
        if quality:
            parts.append(str(quality))
        if r.get("hdr"):
            parts.append("HDR")
        seeders = r.get("seeders")
        if isinstance(seeders, int):
            parts.append(f"🌱{seeders}")
        label = " • ".join(parts)
        # Include the IMDb id so the pick handler can route the download
        # to the correct directory (movies vs series) on the media server.
        cb = f"tor:{topic_id}:{imdb_id}" if imdb_id else f"tor:{topic_id}"
        rows.append([InlineKeyboardButton(text=label[:64], callback_data=cb)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _size_bar(n: int) -> str:
    """One ``|`` per full 10 GB (1–6). Sub-10GB still gets one bar so the
    column is never empty; 50 GB+ capped at six so buttons stay short."""
    gb = n / 1024**3
    return "|" * max(1, min(6, int(gb // 10) + 1))


def _human_size(n: int) -> str:
    """Rutracker-style human size. Keeps one decimal for GB/TB, none for MB."""
    if n >= 1024**4:
        return f"{n / 1024**4:.1f}TB"
    if n >= 1024**3:
        return f"{n / 1024**3:.1f}GB"
    if n >= 1024**2:
        return f"{n // 1024**2}MB"
    if n >= 1024:
        return f"{n // 1024}KB"
    return f"{n}B"


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
