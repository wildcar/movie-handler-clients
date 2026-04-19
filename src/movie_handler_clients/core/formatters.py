"""Render MCP payloads into Telegram-friendly HTML strings."""

from __future__ import annotations

from html import escape
from typing import Any


def _rating_line(ratings: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for r in ratings:
        src = r.get("source", "?")
        val = r.get("value")
        scale = r.get("scale")
        if val is None:
            continue
        label = {
            "tmdb": "TMDB",
            "imdb": "IMDb",
            "metacritic": "Metacritic",
            "kinopoisk": "КиноПоиск",
        }.get(str(src), str(src))
        if scale:
            parts.append(f"{label}: {val}/{scale}")
        else:
            parts.append(f"{label}: {val}")
    return " • ".join(parts)


def format_search_item(item: dict[str, Any]) -> str:
    """One-line entry for the search-results list.

    Deliberately skips the overview — the search endpoint returns only the
    English one, and the list already gets long. Full (Russian) overview
    lives in the details card.
    """
    title = escape(str(item.get("title") or "—"))
    year = item.get("year")
    head = f"<b>{title}</b>"
    if year:
        head += f" ({escape(str(year))})"
    return head


def format_details(payload: dict[str, Any]) -> str:
    """Render a ``get_movie_details`` envelope into an HTML caption."""
    movie = payload.get("details") or {}
    title = escape(str(movie.get("title") or "—"))
    year = movie.get("year")
    runtime = movie.get("runtime_minutes")
    genres = movie.get("genres") or []
    overview_ru = movie.get("overview_ru") or movie.get("overview")
    ratings = movie.get("ratings") or []

    lines: list[str] = []
    head = f"<b>{title}</b>"
    if year:
        head += f" ({escape(str(year))})"
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
