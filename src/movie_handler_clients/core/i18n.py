"""Tiny i18n shim — Russian strings today, structured so a language switcher
can be added later by keying ``_messages`` on locale.
"""

from __future__ import annotations

from typing import Final

RU: Final[dict[str, str]] = {
    "start.greeting": (
        "Привет! Я помогу найти фильм, посмотреть трейлер или скачать его.\n\n"
        "Просто пришли название фильма — например, «Дюна» или «Dune 2021»."
    ),
    "search.empty_query": "Пришли название фильма текстом.",
    "search.no_results": "Ничего не нашлось по запросу «{query}».",
    "search.error": "Не удалось выполнить поиск: {detail}",
    "search.results_header": "Запрос «{query}»:",
    "search.section.movies": "🎬 Фильмы:",
    "search.section.series": "📺 Сериалы:",
    "details.error": "Не удалось получить подробности: {detail}",
    "details.not_found": "Похоже, этого фильма больше нет в базе.",
    "details.sources_failed": "⚠️ Недоступны источники: {sources}",
    "details.button_trailer": "🎬 Трейлер",
    "details.button_download": "⬇️ Скачать",
    "details.button_back": "← К списку",
    "stub.trailer": "🎬 Поиск трейлеров появится в следующей версии.",
    "stub.download": "⬇️ Скачивание появится в следующей версии.",
    "errors.generic": "Что-то пошло не так. Попробуй ещё раз чуть позже.",
}


def t(key: str, /, **kwargs: object) -> str:
    """Return the Russian string for ``key``, formatted with ``kwargs``."""
    template = RU.get(key, key)
    if kwargs:
        return template.format(**kwargs)
    return template
