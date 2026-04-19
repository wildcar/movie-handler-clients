"""Render MCP payloads into Telegram-friendly HTML strings."""

from __future__ import annotations

from html import escape
from typing import Any

_RATING_LABELS = {
    "tmdb": "TMDB",
    "imdb": "IMDb",
    "metacritic": "Metacritic",
    "kinopoisk": "КиноПоиск",
}


def _rating_badge(value: float, scale: float) -> str:
    """Pick a colored dot for the rating. Thresholds are normalized to /10.

    - red   🔴 : < 5 (<50 for metacritic-like /100 scales)
    - yellow🟡 : [5, 7) ([50, 70))
    - green 🟢 : [7, 10] ([70, 100])

    Telegram message HTML doesn't support CSS colors, so we use an emoji
    glyph adjacent to the number — works on both light and dark themes.
    """

    normalized = value * 10.0 / scale if scale else value
    if normalized < 5:
        return "🔴"
    if normalized < 7:
        return "🟡"
    return "🟢"


def _format_rating_value(value: float, scale: float) -> str:
    """Render the number itself. Ratings on /10 keep one decimal; /100 is rounded."""

    if scale >= 50:  # Metacritic-style scale — show as integer.
        return f"{round(value)}"
    return f"{value:.1f}"


def _rating_line(ratings: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for r in ratings:
        src = r.get("source", "?")
        val = r.get("value")
        scale = r.get("scale") or 10.0
        if val is None:
            continue
        try:
            val_f = float(val)
            scale_f = float(scale)
        except (TypeError, ValueError):
            continue
        label = _RATING_LABELS.get(str(src), str(src))
        badge = _rating_badge(val_f, scale_f)
        number = _format_rating_value(val_f, scale_f)
        parts.append(f"{label}: {badge} {number}/{int(scale_f)}")
    return " • ".join(parts)


def format_search_item(item: dict[str, Any]) -> str:
    """One-line entry for the search-results list.

    Shows the localized (ru-RU) title with the original title in parens when
    they differ, plus the year. Overview is deliberately omitted — the list
    gets long; full description lives in the details card.
    """
    title = escape(str(item.get("title") or "—"))
    original = item.get("original_title")
    year = item.get("year")
    line = f"<b>{title}</b>"
    if original and str(original) != str(item.get("title")):
        line += f" <i>({escape(str(original))})</i>"
    if year:
        line += f" — {escape(str(year))}"
    return line


def format_details(payload: dict[str, Any]) -> str:
    """Render a ``get_movie_details`` envelope into an HTML caption."""
    movie = payload.get("details") or {}
    kind = movie.get("kind") or "movie"
    icon = "📺" if kind == "series" else "🎬"
    title = escape(str(movie.get("title") or "—"))
    original = movie.get("original_title")
    year = movie.get("year")
    runtime = movie.get("runtime_minutes")
    genres = movie.get("genres") or []
    overview_ru = movie.get("overview_ru") or movie.get("overview")
    ratings = movie.get("ratings") or []

    lines: list[str] = []
    head = f"{icon} <b>{title}</b>"
    if original and str(original) != str(movie.get("title")):
        head += f" <i>({escape(str(original))})</i>"
    if year:
        head += f" — {escape(str(year))}"
    lines.append(head)

    meta_bits: list[str] = []
    if runtime:
        meta_bits.append(f"{runtime} мин")
    if genres:
        meta_bits.append(", ".join(escape(str(g)) for g in genres))
    if meta_bits:
        lines.append(" • ".join(meta_bits))

    rating = _rating_line(ratings)
    if rating:
        lines.append(f"⭐ {escape(rating)}")

    if overview_ru:
        lines.append("")
        lines.append(escape(str(overview_ru)))

    failed = payload.get("sources_failed") or []
    if failed:
        from .i18n import t

        lines.append("")
        lines.append(t("details.sources_failed", sources=", ".join(str(s) for s in failed)))

    return "\n".join(lines)
