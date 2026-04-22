"""Inline keyboards for the Telegram bot."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..core.i18n import t


def search_results_keyboard(items: list[dict[str, object]], query_id: str) -> InlineKeyboardMarkup:
    """One button per search hit in the SearchV2 format:
    ``Title, YYYY, Country 🟢 7.9``.

    Items arrive already sorted by the handler (movies first, then series,
    each bucket descending by year).
    """
    rows: list[list[InlineKeyboardButton]] = []
    for item in items:
        imdb_id = item.get("imdb_id")
        if not imdb_id:
            continue
        rows.append(
            [InlineKeyboardButton(
                text=_search_button_label(item)[:64],
                callback_data=f"d:{imdb_id}:{query_id}",
            )]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _search_button_label(item: dict[str, object]) -> str:
    icon = "🧼" if item.get("kind") == "series" else "🎦"
    title = str(item.get("title") or "—")
    parts: list[str] = [title]
    year = item.get("year")
    if year:
        parts.append(str(year))
    country = item.get("country")
    if isinstance(country, str) and country:
        parts.append(country)
    label = f"{icon} " + ", ".join(parts)
    rating = item.get("rating")
    if isinstance(rating, int | float) and float(rating) > 0:
        val = float(rating)
        badge = "🔴" if val < 5 else "🟡" if val < 7 else "🟢"
        label += f" {badge} {val:.1f}"
    return label


_5GB = 5 * 1024**3
_15GB = 15 * 1024**3
_PINNED_ICONS = ("🌕", "🌎", "🌞")  # 1st / 2nd / 3rd pinned buckets


def _format_torrent_label(r: dict[str, object]) -> str:
    """Unified button label: ``5.5 GB · HDR · 1080p · WEB-DL · ⬆️ 92``.

    Each segment is skipped when the underlying field is missing, so a
    minimally-parsed row still renders a clean row without empty dots.
    """
    parts: list[str] = []
    size_b = r.get("size_bytes")
    if isinstance(size_b, int) and size_b > 0:
        parts.append(_human_size_spaced(size_b))
    if r.get("hdr"):
        parts.append("HDR")
    quality = r.get("quality")
    if quality:
        parts.append(str(quality))
    source = r.get("source")
    if source:
        parts.append(str(source))
    seeders = r.get("seeders")
    if isinstance(seeders, int):
        parts.append(f"⬆️ {seeders}")
    return " · ".join(parts)


def torrent_list_keyboard(
    results: list[dict[str, object]], *, imdb_id: str = ""
) -> InlineKeyboardMarkup:
    """TorrentsV2: three pinned picks + «Показать ещё» button.

    Picks highest-seeder torrent in each bucket: (1) ≤5 GB, (2) 5–15 GB,
    (3) HDR present. Each pinned button gets a 🌕/🌎/🌞 prefix; the label
    format is identical to the full-list buttons so the two views read
    the same — only the category ordering differs.
    """
    valid = [r for r in results if isinstance(r.get("topic_id"), int)]

    def _size(r: dict) -> int:
        s = r.get("size_bytes")
        return int(s) if isinstance(s, int) else 0

    def _seeds(r: dict) -> int:
        s = r.get("seeders")
        return int(s) if isinstance(s, int) else 0

    def _best(pred) -> dict | None:
        matches = [r for r in valid if pred(r)]
        return max(matches, key=_seeds) if matches else None

    small = _best(lambda r: 0 < _size(r) <= _5GB)
    mid = _best(lambda r: _5GB < _size(r) <= _15GB)
    hdr = _best(lambda r: bool(r.get("hdr")))

    pinned_ids: set[int] = set()
    pinned: list[dict] = []
    for pick in (small, mid, hdr):
        if pick is not None:
            tid = int(pick["topic_id"])  # type: ignore[arg-type]
            if tid not in pinned_ids:
                pinned_ids.add(tid)
                pinned.append(pick)

    rest_count = sum(1 for r in valid if r.get("topic_id") not in pinned_ids)

    rows: list[list[InlineKeyboardButton]] = []
    for i, r in enumerate(pinned):
        topic_id = int(r["topic_id"])  # type: ignore[arg-type]
        icon = _PINNED_ICONS[i] if i < len(_PINNED_ICONS) else ""
        btn_label = f"{icon} {_format_torrent_label(r)}".strip()
        cb = f"tor:{topic_id}:{imdb_id}" if imdb_id else f"tor:{topic_id}"
        rows.append([InlineKeyboardButton(text=btn_label[:64], callback_data=cb)])

    if rest_count > 0:
        from ..core.i18n import t as _t
        cb_all = f"torall:{imdb_id}" if imdb_id else "torall:"
        rows.append([InlineKeyboardButton(
            text=_t("download.show_all", n=rest_count),
            callback_data=cb_all,
        )])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def torrent_all_keyboard(
    results: list[dict[str, object]], *, imdb_id: str = ""
) -> InlineKeyboardMarkup:
    """Full list — same label format as pinned buttons, no icon prefix."""
    rows: list[list[InlineKeyboardButton]] = []
    for r in results:
        topic_id = r.get("topic_id")
        if not isinstance(topic_id, int):
            continue
        label = _format_torrent_label(r)
        cb = f"tor:{topic_id}:{imdb_id}" if imdb_id else f"tor:{topic_id}"
        rows.append([InlineKeyboardButton(text=label[:64], callback_data=cb)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _human_size_spaced(n: int) -> str:
    """Human size with a space before the unit (design-matching)."""
    if n >= 1024**4:
        return f"{n / 1024**4:.1f} TB"
    if n >= 1024**3:
        return f"{n / 1024**3:.1f} GB"
    if n >= 1024**2:
        return f"{n // 1024**2} MB"
    if n >= 1024:
        return f"{n // 1024} KB"
    return f"{n} B"


_TRAILER_ICONS = {"trailer": "🎦", "teaser": "🎞", "clip": "📼", "featurette": "🎥"}
_TRAILER_KIND_LABEL = {
    "trailer": "Трейлер",
    "teaser": "Тизер",
    "clip": "Клип",
    "featurette": "Фичуретка",
}


def trailer_alternatives_keyboard(
    trailers: list[dict[str, object]], *, imdb_id: str, start_index: int = 1
) -> InlineKeyboardMarkup:
    """Keyboard for the secondary trailers (all but the main one).

    ``start_index`` is the offset into the full trailers list — the main
    trailer lives at index 0 and is posted as a preview message, so we
    start at 1 here. The callback carries the absolute index so the pick
    handler can look up the trailer in the cache.
    """
    rows: list[list[InlineKeyboardButton]] = []
    for i, trl in enumerate(trailers, start=start_index):
        kind = str(trl.get("kind") or "trailer")
        icon = _TRAILER_ICONS.get(kind, "🎬")
        kind_label = _TRAILER_KIND_LABEL.get(kind, "Видео")
        title = str(trl.get("title") or kind_label)
        lang = trl.get("language")
        if lang == "ru":
            lang_label = "RU"
        elif lang == "en":
            lang_label = "EN"
        elif isinstance(lang, str) and lang:
            lang_label = lang.upper()
        else:
            lang_label = "—"
        # Single-line (Telegram truncates \n on iOS/Android).
        btn_label = f"{icon} {title} · {kind_label} · {lang_label}"
        rows.append([InlineKeyboardButton(text=btn_label[:64], callback_data=f"tr:{imdb_id}:{i}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def details_keyboard(
    imdb_id: str, query_id: str | None, *, kind: str | None = None
) -> InlineKeyboardMarkup:
    """DetailsV2: [Back | Trailer] on top row, [⬇ Download] full-width below.

    The trailer button's icon mirrors the title kind (🎦 for movies,
    🧼 for series) so the card reads consistently.
    """
    trailer_icon = "🧼" if kind == "series" else "🎦"
    top_row: list[InlineKeyboardButton] = []
    if query_id:
        top_row.append(InlineKeyboardButton(text=t("details.button_back"), callback_data=f"b:{query_id}"))
    top_row.append(InlineKeyboardButton(
        text=f"{trailer_icon} {t('details.button_trailer')}",
        callback_data=f"t:{imdb_id}",
    ))
    rows: list[list[InlineKeyboardButton]] = [
        top_row,
        [InlineKeyboardButton(text=t("details.button_download"), callback_data=f"dl:{imdb_id}")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)
