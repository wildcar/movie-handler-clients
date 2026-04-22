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
    "search.results_header": "«{query}»:",
    "search.section.movies": "🎦 Фильмы:",
    "search.section.series": "🧼 Сериалы:",
    "details.error": "Не удалось получить подробности: {detail}",
    "details.not_found": "Похоже, этого фильма больше нет в базе.",
    "details.sources_failed": "⚠️ Недоступны источники: {sources}",
    "details.button_trailer": "🎬 Трейлер",
    "details.button_download": "⬇️ Скачать",
    "details.button_back": "← К списку",
    "stub.trailer": "🎬 Сервис трейлеров временно недоступен.",
    "stub.download": "⬇️ Сервис скачивания временно недоступен.",
    "download.list_header": "<b>{query}</b>\n{n} раздач, выбери для скачивания:",
    "download.searching": "🔎 Ищу раздачи…",
    "download.fetching": "⬇️ Скачиваю .torrent…",
    "download.no_results": "⬇️ Ничего не нашлось на rutracker.",
    "download.error": "Не удалось получить раздачи: {detail}",
    "download.captcha": (
        "rutracker просит капчу — зайди вручную в браузере и обнови cookie "
        "bb_session в файле сервиса."
    ),
    "download.not_configured": "На сервере не заданы логин и пароль rutracker.",
    "download.reopen_card": "Открой карточку фильма заново и нажми «Скачать».",
    "download.sent_caption": "✅ Торрент-файл готов.",
    "download.sent_to_server": "✅ Поставил на закачку на сервере: <b>{name}</b>",
    "download.sent_to_server_series": "📺 Поставил на закачку (сериал): <b>{name}</b>",
    "download.sent_to_server_noname": "✅ Поставил на закачку на сервере.",
    "trailer.not_found": "🎬 Трейлеры не найдены.",
    "trailer.error": "Не удалось получить трейлеры: {detail}",
    "trailer.alternatives": "Другие варианты:",
    "trailer.language.ru": "🇷🇺 Русский",
    "trailer.language.en": "🇬🇧 English",
    "trailer.language.other": "🌐 {code}",
    "trailer.language.unknown": "🌐 Язык не указан",
    "trailer.kind.trailer": "Трейлер",
    "trailer.kind.teaser": "Тизер",
    "trailer.kind.clip": "Клип",
    "trailer.kind.featurette": "Фичуретка",
    "trailer.kind.other": "Видео",
    "download.complete": "✅ Закачка завершена: <b>{name}</b>",
    "download.complete_noname": "✅ Закачка завершена.",
    "download.show_all": "Показать ещё {n} ▾",
    "download.all_header": "Все раздачи:",
    "status.header": "Твои закачки:",
    "status.no_downloads": "У тебя нет отслеживаемых закачек.",
    "status.not_configured": "⬇️ Сервер закачек не настроен.",
    "errors.generic": "Что-то пошло не так. Попробуй ещё раз чуть позже.",
}


def t(key: str, /, **kwargs: object) -> str:
    """Return the Russian string for ``key``, formatted with ``kwargs``."""
    template = RU.get(key, key)
    if kwargs:
        return template.format(**kwargs)
    return template
