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


def pinned_torrents(results: list[dict[str, object]]) -> list[dict[str, object]]:
    """Return the three bucketed picks in order: ≤5 GB → 5–15 GB → HDR.

    Each bucket is won by the highest-seeder entry; duplicates across
    buckets are kept only in their first appearance so we never pin the
    same row twice.
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

    picks = [
        _best(lambda r: 0 < _size(r) <= _5GB),
        _best(lambda r: _5GB < _size(r) <= _15GB),
        _best(lambda r: bool(r.get("hdr"))),
    ]

    seen: set[int] = set()
    out: list[dict[str, object]] = []
    for p in picks:
        if p is None:
            continue
        tid = int(p["topic_id"])  # type: ignore[arg-type]
        if tid in seen:
            continue
        seen.add(tid)
        out.append(p)
    return out


def torrent_list_keyboard(
    results: list[dict[str, object]], *, imdb_id: str = "", expand_all: bool = False
) -> InlineKeyboardMarkup:
    """Three pinned picks + «Показать ещё» button by default. When
    ``expand_all`` is set, pin row(s) come first (icons retained) and
    every remaining release is appended as its own row — used to
    re-render the same message after the user taps «Показать ещё»."""
    pinned = pinned_torrents(results)
    pinned_ids = {int(r["topic_id"]) for r in pinned}  # type: ignore[arg-type]
    rest = [
        r for r in results
        if isinstance(r.get("topic_id"), int) and r.get("topic_id") not in pinned_ids
    ]

    rows: list[list[InlineKeyboardButton]] = []
    for i, r in enumerate(pinned):
        topic_id = int(r["topic_id"])  # type: ignore[arg-type]
        icon = _PINNED_ICONS[i] if i < len(_PINNED_ICONS) else ""
        btn_label = f"{icon} {_format_torrent_label(r)}".strip()
        cb = f"tor:{topic_id}:{imdb_id}" if imdb_id else f"tor:{topic_id}"
        rows.append([InlineKeyboardButton(text=btn_label[:64], callback_data=cb)])

    if expand_all:
        for r in rest:
            topic_id = int(r["topic_id"])  # type: ignore[arg-type]
            btn_label = _format_torrent_label(r)
            cb = f"tor:{topic_id}:{imdb_id}" if imdb_id else f"tor:{topic_id}"
            rows.append([InlineKeyboardButton(text=btn_label[:64], callback_data=cb)])
    elif rest:
        from ..core.i18n import t as _t
        cb_all = f"torall:{imdb_id}" if imdb_id else "torall:"
        rows.append([InlineKeyboardButton(
            text=_t("download.show_all", n=len(rest)),
            callback_data=cb_all,
        )])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def rutracker_url_candidates_keyboard(
    topic_id: int,
    candidates: list[dict[str, object]],
) -> InlineKeyboardMarkup:
    """Buttons rendered after a user pastes a rutracker URL: one row per
    matched movie/series from movie-metadata-mcp (carries the imdb id),
    plus a final «Скачать без привязки» row that goes straight to the
    confirm step with no imdb attached."""
    rows: list[list[InlineKeyboardButton]] = []
    for c in candidates:
        imdb_id = str(c.get("imdb_id") or "")
        if not imdb_id:
            continue
        title = str(c.get("title") or "?")
        year = c.get("year")
        if isinstance(year, int):
            label = t("rt_url.candidate_button", title=title, year=year)
        else:
            label = t("rt_url.candidate_button_no_year", title=title)
        rows.append([
            InlineKeyboardButton(text=label[:64], callback_data=f"tdl:{topic_id}:{imdb_id}"),
        ])
    rows.append([
        InlineKeyboardButton(text=t("rt_url.unlink_button"), callback_data=f"tdl:{topic_id}"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def torrent_confirm_keyboard(topic_id: int, imdb_id: str) -> InlineKeyboardMarkup:
    """Single «Скачать» button shown under the release confirmation
    message. Callback `tdl:` triggers the actual rutracker fetch +
    rtorrent push (the bare `tor:` callback only opens the preview)."""
    cb = f"tdl:{topic_id}:{imdb_id}" if imdb_id else f"tdl:{topic_id}"
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t("download.confirm_button"), callback_data=cb),
    ]])


def torrent_all_keyboard(
    results: list[dict[str, object]], *, imdb_id: str = ""
) -> InlineKeyboardMarkup:
    """List — same label format as pinned buttons, no icon prefix.

    Callers typically filter out the already-pinned entries before
    passing ``results`` in, so «Ещё раздачи» really shows the remainder.
    """
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


def season_picker_keyboard(imdb_id: str, seasons: int) -> InlineKeyboardMarkup:
    """One button per season. No «All seasons» button — the per-result
    button labels carry only resolution / release type, so a list mixing
    single-season releases with multi-season bundles would be ambiguous
    («Сезон: 1-5» indistinguishable from «Сезон: 3»). The bot filters
    results to releases that cover exactly the chosen season."""
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for season in range(1, seasons + 1):
        row.append(
            InlineKeyboardButton(
                text=t("download.season_label", n=season),
                callback_data=f"dls:{imdb_id}:{season}",
            )
        )
        # Lay seasons out in rows of 4 so 12-season shows don't blow up the
        # button column.
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
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
