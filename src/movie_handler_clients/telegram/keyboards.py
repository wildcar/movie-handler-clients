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
    title = str(item.get("title") or "—")
    parts: list[str] = [title]
    year = item.get("year")
    if year:
        parts.append(str(year))
    country = item.get("country")
    if isinstance(country, str) and country:
        parts.append(country)
    label = ", ".join(parts)
    rating = item.get("rating")
    if isinstance(rating, int | float) and float(rating) > 0:
        val = float(rating)
        badge = "🔴" if val < 5 else "🟡" if val < 7 else "🟢"
        label += f" {badge} {val:.1f}"
    return label


_5GB = 5 * 1024**3
_15GB = 15 * 1024**3


def torrent_list_keyboard(
    results: list[dict[str, object]], *, imdb_id: str = ""
) -> InlineKeyboardMarkup:
    """TorrentsV2: three pinned recommendations + 'show all' button.

    Pins the best (most seeders) torrent from each size/quality bucket:
    ≤5 GB · 5–15 GB · HDR/4K. Remaining torrents are hidden behind a
    «Показать ещё N ▾» button backed by the torrent cache.
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

    def _is_hdr_4k(r: dict) -> bool:
        return bool(r.get("hdr")) or "4k" in str(r.get("quality") or "").lower()

    small = _best(lambda r: 0 < _size(r) <= _5GB)
    mid = _best(lambda r: _5GB < _size(r) <= _15GB)
    hdr = _best(_is_hdr_4k)

    pinned_ids: set[int] = set()
    pinned: list[tuple[str, dict]] = []
    for label, pick in [("До 5 GB", small), ("5–15 GB", mid), ("HDR", hdr)]:
        if pick is not None:
            tid = int(pick["topic_id"])  # type: ignore[arg-type]
            if tid not in pinned_ids:
                pinned_ids.add(tid)
                pinned.append((label, pick))

    rest_count = sum(1 for r in valid if r.get("topic_id") not in pinned_ids)

    rows: list[list[InlineKeyboardButton]] = []
    for label, r in pinned:
        topic_id = int(r["topic_id"])  # type: ignore[arg-type]
        # Single-line button: Telegram clients truncate multi-line labels
        # mid-text (iOS shows "До 5 GB.."), so we flatten everything into
        # one compact row — bucket label, size, quality, seeders.
        parts: list[str] = [label]
        size_b = r.get("size_bytes")
        if isinstance(size_b, int) and size_b > 0:
            parts.append(_human_size_spaced(size_b))
        quality = r.get("quality")
        if quality:
            parts.append(str(quality))
        seeders = r.get("seeders")
        if isinstance(seeders, int):
            parts.append(f"👤{seeders}")
        btn_label = " · ".join(parts)
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
    """Full torrent list — all entries, classic one-button-per-row layout."""
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


_TRAILER_ICONS = {"trailer": "🎬", "teaser": "🎞", "clip": "📼", "featurette": "🎥"}
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


def details_keyboard(imdb_id: str, query_id: str | None) -> InlineKeyboardMarkup:
    # DetailsV2: [Back | Trailer] on top row, [⬇ Download] full-width below.
    top_row: list[InlineKeyboardButton] = []
    if query_id:
        top_row.append(InlineKeyboardButton(text=t("details.button_back"), callback_data=f"b:{query_id}"))
    top_row.append(InlineKeyboardButton(text=t("details.button_trailer"), callback_data=f"t:{imdb_id}"))
    rows: list[list[InlineKeyboardButton]] = [
        top_row,
        [InlineKeyboardButton(text=t("details.button_download"), callback_data=f"dl:{imdb_id}")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)
