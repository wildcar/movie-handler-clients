"""Render MCP payloads into Telegram-friendly HTML strings."""

from __future__ import annotations

from html import escape
from typing import Any

from .i18n import t

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

    Layout: ``<b>Title</b>, YYYY, Country 🟢 7.9`` — mirrors the list in
    the SearchV2 design. Country / rating are rendered only when the
    metadata MCP provides them (rating is `vote_average`, country is
    mapped from origin_country / original_language on the MCP side).
    """
    title = escape(str(item.get("title") or "—"))
    year = item.get("year")
    country = item.get("country")
    rating = item.get("rating")

    head = f"<b>{title}</b>"
    tail_parts: list[str] = []
    if year:
        tail_parts.append(escape(str(year)))
    if isinstance(country, str) and country:
        tail_parts.append(escape(country))
    line = head + (", " + ", ".join(tail_parts) if tail_parts else "")

    if isinstance(rating, int | float) and rating > 0:
        try:
            val = float(rating)
        except (TypeError, ValueError):
            val = 0.0
        if val > 0:
            line += f" {_rating_badge(val, 10.0)} {val:.1f}"

    return line


def plural_ru(n: int, forms: tuple[str, str, str]) -> str:
    """Russian plural selector: (1, 2–4, 5+). Used for 'N фильм(а/ов)' etc."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    if 2 <= n % 10 <= 4 and (n % 100 < 10 or n % 100 >= 20):
        return forms[1]
    return forms[2]


def format_details(payload: dict[str, Any]) -> str:
    """Render a ``get_movie_details`` envelope into an HTML caption (DetailsV2 layout)."""
    movie = payload.get("details") or {}
    kind = movie.get("kind") or "movie"
    icon = "📺" if kind == "series" else "🎬"
    title = escape(str(movie.get("title") or "—"))
    original = movie.get("original_title")
    year = movie.get("year")
    runtime = movie.get("runtime_minutes")
    genres = movie.get("genres") or []
    directors = movie.get("directors") or []
    cast = movie.get("cast") or []
    overview_ru = movie.get("overview_ru") or movie.get("overview")
    ratings = movie.get("ratings") or []

    lines: list[str] = []

    # Title line with icon
    lines.append(f"{icon} <b>{title}</b>")

    # Original title + year as italic subtitle
    sub: list[str] = []
    if original and str(original) != str(movie.get("title")):
        sub.append(escape(str(original)))
    if year:
        sub.append(str(year))
    if sub:
        lines.append(f"<i>{' · '.join(sub)}</i>")

    # Labeled metadata rows
    def row(label: str, value: str) -> str:
        return f"<b>{label}:</b> {value}"

    if runtime:
        h, m = divmod(int(runtime), 60)
        rt = f"{h} ч {m} мин" if h else f"{m} мин"
        lines.append(row("Длительность", rt))
    if genres:
        lines.append(row("Жанры", ", ".join(escape(str(g)) for g in genres)))
    if directors:
        lines.append(row("Режиссёр", escape(", ".join(str(d) for d in directors[:2]))))
    if cast:
        lines.append(row("В ролях", escape(", ".join(str(a) for a in cast[:4]))))

    # Compact ratings: 🟢 IMDb 8.5 · 🟡 TMDB 7.9
    rating_parts: list[str] = []
    for r in ratings:
        val = r.get("value")
        scale = r.get("scale") or 10.0
        src = r.get("source", "?")
        if val is None:
            continue
        try:
            val_f, scale_f = float(val), float(scale)
        except (TypeError, ValueError):
            continue
        badge = _rating_badge(val_f, scale_f)
        label = _RATING_LABELS.get(str(src), str(src))
        number = _format_rating_value(val_f, scale_f)
        rating_parts.append(f"{badge} {label} {number}")
    if rating_parts:
        lines.append(row("Рейтинги", " · ".join(rating_parts)))

    if overview_ru:
        lines.append("")
        lines.append(escape(str(overview_ru)))

    failed = payload.get("sources_failed") or []
    if failed:
        lines.append("")
        lines.append(t("details.sources_failed", sources=", ".join(str(s) for s in failed)))

    return "\n".join(lines)


def format_torrent_list(results: list[dict[str, Any]]) -> str:
    """Build the full HTML body for the torrent pick-list.

    Layout: optional italic "common prefix" line (what all releases share,
    to avoid noise), then one line per release: ``<code>#ID</code> · <a>diff</a>``.
    The quality/size/HDR/seeders bits are not duplicated here — they live
    on the keyboard button beneath each row.
    """
    titles = [str(r.get("title") or "") for r in results]
    common = _common_prefix_words(titles) if len(titles) >= 2 else ""
    lines: list[str] = []
    if common:
        lines.append(f"<i>{escape(common.rstrip(' ,:;–—-'))}</i>")
    for r in results:
        topic_id = r.get("topic_id")
        raw_title = str(r.get("title") or "")
        diff = raw_title[len(common) :] if common else raw_title
        # Strip stray punctuation left dangling by the prefix cut (e.g. a
        # closing ``]`` whose opening ``[`` was lifted into the header).
        diff = diff.strip(" ,:;–—-]")
        if not diff:
            diff = raw_title
        size_b = r.get("size_bytes")
        size_tag = _human_size_gb(size_b) if isinstance(size_b, int) and size_b > 0 else ""
        url = str(r.get("url") or "").strip()
        diff_html = escape(diff)
        link = f'<a href="{escape(url)}">{diff_html}</a>' if url else diff_html
        id_badge = f"<code>#{topic_id}</code>" if topic_id is not None else ""
        head = " · ".join(p for p in (id_badge, size_tag) if p)
        lines.append(f"{head} · {link}" if head else link)
    return "\n".join(lines)


def _human_size_gb(n: int) -> str:
    if n >= 1024**4:
        return f"{n / 1024**4:.1f} TB"
    if n >= 1024**3:
        return f"{n / 1024**3:.1f} GB"
    if n >= 1024**2:
        return f"{n // 1024**2} MB"
    return f"{n} B"


def _common_prefix_words(titles: list[str]) -> str:
    """Longest common prefix, trimmed at the last word/punct boundary.

    Returning a fragment that ends mid-word looks jagged; we snap back
    to the last whitespace/punctuation so the shown prefix reads as a
    clean phrase.
    """
    if not titles:
        return ""
    base = titles[0]
    n = len(base)
    for s in titles[1:]:
        m = min(n, len(s))
        i = 0
        while i < m and base[i] == s[i]:
            i += 1
        n = i
        if n == 0:
            return ""
    prefix = base[:n]
    # Snap to last word/punct boundary so we don't cut inside a word.
    for i in range(len(prefix) - 1, -1, -1):
        if prefix[i] in " ,:;/-—–":
            return prefix[: i + 1]
    return ""


def format_trailer_caption(trailer: dict[str, Any]) -> str:
    """One Telegram message per trailer — title + language + URL.

    The URL is placed on its own line and NOT wrapped in a link tag, so
    Telegram builds its own preview card with the YouTube player embedded.
    """

    icon = {
        "trailer": "🎬",
        "teaser": "🎞",
        "clip": "📼",
        "featurette": "🎥",
    }.get(str(trailer.get("kind") or "trailer"), "🎬")

    kind_label = t(f"trailer.kind.{trailer.get('kind') or 'trailer'}")
    lang = trailer.get("language")
    if lang == "ru":
        lang_label = t("trailer.language.ru")
    elif lang == "en":
        lang_label = t("trailer.language.en")
    elif isinstance(lang, str) and lang:
        lang_label = t("trailer.language.other", code=lang.upper())
    else:
        lang_label = t("trailer.language.unknown")

    title = escape(str(trailer.get("title") or kind_label))
    url = str(trailer.get("url") or "").strip()

    parts = [f"{icon} <b>{title}</b>", f"{kind_label} · {lang_label}"]
    if url:
        parts.append(url)
    return "\n".join(parts)
