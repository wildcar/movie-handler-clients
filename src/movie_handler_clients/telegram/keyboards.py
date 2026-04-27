"""Inline keyboards for the Telegram bot."""

from __future__ import annotations

import re

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..core.i18n import t

# Cap on how many releases the bot surfaces in the torrent picker.
# rutracker often returns 30+ rows for popular titles; the user only
# ever picked from the top of the list.
_MAX_TORRENT_ROWS = 10


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
            [
                InlineKeyboardButton(
                    text=_search_button_label(item)[:64],
                    callback_data=f"d:{imdb_id}:{query_id}",
                )
            ]
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


def _format_torrent_label(r: dict[str, object]) -> str:
    """Unified button label: ``2,3 Гб • раздают 133 • 720p • SDR``.

    Format is fixed (always four segments) so the user can scan a column
    of releases by position. Resolution is normalised: ``2160p`` even
    when the title says «4K», ``UNKp`` when nothing parseable is found.
    HDR is binary — any HDR / HDR10[+] / Dolby Vision flag → ``HDR``,
    otherwise ``SDR``. Source tags (BDRip / WEB-DL / etc.) are dropped
    intentionally.
    """
    size_b = r.get("size_bytes")
    size_part = _human_size_ru(int(size_b)) if isinstance(size_b, int) and size_b > 0 else "? Б"

    seeders = r.get("seeders")
    seeders_part = f"раздают {int(seeders) if isinstance(seeders, int) else 0}"

    resolution_part = _resolution_label(r)
    hdr_part = "HDR" if r.get("hdr") else "SDR"

    return f"{size_part} • {seeders_part} • {resolution_part} • {hdr_part}"


_RES_NP_RE = re.compile(r"\b(\d{3,4})p\b", re.IGNORECASE)
_RES_K_RE = re.compile(r"\b([248])\s*[Kk]\b")
# 4K → 2160p, 8K → 4320p. (2K stays as a hint: rutracker uploaders use
# it loosely for 1080p remasters; we don't translate.)
_K_TO_P = {"4": "2160p", "8": "4320p"}


def _resolution_label(r: dict[str, object]) -> str:
    quality = str(r.get("quality") or "")
    m = _RES_NP_RE.fullmatch(quality)
    if m:
        return f"{m.group(1)}p"
    title = str(r.get("title") or "")
    m = _RES_NP_RE.search(title)
    if m:
        return f"{m.group(1)}p"
    m = _RES_K_RE.search(title)
    if m:
        return _K_TO_P.get(m.group(1), "UNKp")
    return "UNKp"


def torrent_list_keyboard(
    results: list[dict[str, object]], *, imdb_id: str = ""
) -> InlineKeyboardMarkup:
    """Top releases sorted by seeders, capped at 10 rows.

    The caller passes the raw rutracker-mcp results; we sort by seeders
    descending, drop rows without a topic id, and render at most
    ``_MAX_TORRENT_ROWS`` buttons. No pin/expand logic — the per-row
    label carries enough context for the user to choose at a glance.
    """
    valid = [r for r in results if isinstance(r.get("topic_id"), int)]

    def _seeders(r: dict[str, object]) -> int:
        s = r.get("seeders")
        return s if isinstance(s, int) else 0

    valid.sort(key=_seeders, reverse=True)
    rows: list[list[InlineKeyboardButton]] = []
    for r in valid[:_MAX_TORRENT_ROWS]:
        topic_id_raw = r["topic_id"]
        if not isinstance(topic_id_raw, int):
            continue
        topic_id = topic_id_raw
        cb = f"tor:{topic_id}:{imdb_id}" if imdb_id else f"tor:{topic_id}"
        rows.append(
            [
                InlineKeyboardButton(
                    text=_format_torrent_label(r)[:64],
                    callback_data=cb,
                )
            ]
        )
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
        rows.append(
            [
                InlineKeyboardButton(text=label[:64], callback_data=f"tdl:{topic_id}:{imdb_id}"),
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(text=t("rt_url.unlink_button"), callback_data=f"tdl:{topic_id}"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def torrent_confirm_keyboard(topic_id: int, imdb_id: str) -> InlineKeyboardMarkup:
    """Single «Скачать» button shown under the release confirmation
    message. Callback `tdl:` triggers the actual rutracker fetch +
    rtorrent push (the bare `tor:` callback only opens the preview)."""
    cb = f"tdl:{topic_id}:{imdb_id}" if imdb_id else f"tdl:{topic_id}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("download.confirm_button"), callback_data=cb),
            ]
        ]
    )


def _human_size_ru(n: int) -> str:
    """Russian-style size: ``2,3 Гб`` / ``540 Мб`` / ``999 Кб``.

    GB and TB are formatted with one decimal place using a comma; MB
    and KB drop the fractional part since «540,0 Мб» reads worse than
    «540 Мб» in a tight button label.
    """
    if n >= 1024**4:
        return f"{n / 1024**4:.1f}".replace(".", ",") + " Тб"
    if n >= 1024**3:
        return f"{n / 1024**3:.1f}".replace(".", ",") + " Гб"
    if n >= 1024**2:
        return f"{n // 1024**2} Мб"
    if n >= 1024:
        return f"{n // 1024} Кб"
    return f"{n} Б"


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
    🧼 for series, 🎨 for cartoons) so the card reads consistently.
    """
    trailer_icon = "🧼" if kind == "series" else "🎨" if kind == "cartoon" else "🎦"
    top_row: list[InlineKeyboardButton] = []
    if query_id:
        top_row.append(
            InlineKeyboardButton(text=t("details.button_back"), callback_data=f"b:{query_id}")
        )
    top_row.append(
        InlineKeyboardButton(
            text=f"{trailer_icon} {t('details.button_trailer')}",
            callback_data=f"t:{imdb_id}",
        )
    )
    rows: list[list[InlineKeyboardButton]] = [
        top_row,
        [InlineKeyboardButton(text=t("details.button_download"), callback_data=f"dl:{imdb_id}")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)
