"""Tiny i18n shim — Russian strings today, structured so a language switcher
can be added later by keying ``_messages`` on locale.
"""

from __future__ import annotations

from typing import Final

RU: Final[dict[str, str]] = {
    "start.greeting": (
        "Помогу найти фильм или сериал, и скачать его. "
        "Понимаю ссылки на видеоролики и скачиваю их. "
        "Помогу с плейлистами с видео. "
        "Скачанное доступно для просмотра здесь или через Plex. "
        "Храниться долго не будет: скачали — посмотрели.\n\n"
        "Пришлите название фильма или ссылку на видео или плейлист"
    ),
    "search.empty_query": "Пришлите название фильма текстом.",
    "search.no_results": "Ничего не нашлось по запросу «{query}».",
    "search.error": "Не удалось выполнить поиск: {detail}",
    "search.results_header": "«{query}»:",
    "search.section.movies": "🎦 Фильмы:",
    "search.section.series": "🧼 Сериалы:",
    "details.error": "Не удалось получить подробности: {detail}",
    "details.not_found": "Похоже, этого фильма больше нет в базе.",
    "details.sources_failed": "⚠️ Недоступны источники: {sources}",
    "details.button_trailer": "Трейлер",
    "details.button_download": "↓ Скачать",
    "details.button_back": "← К списку",
    "stub.trailer": "🎬 Сервис трейлеров временно недоступен.",
    "stub.download": "↓ Сервис скачивания временно недоступен.",
    "download.list_header": ("<b>{query}</b>\n{n} раздач, выберите размер и качество:"),
    "download.searching": "🔎 Ищу раздачи…",
    "download.fetching": "↓ Скачиваю .torrent…",
    "download.no_results": "↓ Ничего не нашлось на rutracker.",
    "download.error": "Не удалось получить раздачи: {detail}",
    "download.captcha": (
        "rutracker просит капчу — войдите вручную в браузере и обновите cookie "
        "bb_session в файле сервиса."
    ),
    "download.not_configured": "На сервере не заданы логин и пароль rutracker.",
    "download.reopen_card": "Откройте карточку фильма заново и нажмите «Скачать».",
    "download.sent_caption": "✅ Торрент-файл готов.",
    "download.sent_to_server": (
        "✅ Поставил на закачку на сервере: <b>{name}</b>\n/status — посмотреть прогресс"
    ),
    "download.sent_to_server_series": (
        "📺 Поставил на закачку (сериал): <b>{name}</b>\n/status — посмотреть прогресс"
    ),
    "download.sent_to_server_noname": (
        "✅ Поставил на закачку на сервере.\n/status — посмотреть прогресс"
    ),
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
    "download.complete_with_link": ("✅ «<b>{name}</b>» загружен\n🎬 {url}"),
    "download.complete_episodes_header": (
        "📺 «<b>{name}</b>» — закачка готова, доступно серий: {n}"
    ),
    "download.complete_episode_line": "S{season:02d}E{episode:02d} — {url}",
    "download.complete_extra_line": "🎬 {url}",
    "download.register_failed": (
        "⚠️ Закачка «<b>{name}</b>» завершилась, но регистрация на media-watch не удалась: {detail}"
    ),
    "whoami.user": "👤 Telegram id: <code>{tg_id}</code>\n🆔 Внутренний id: <code>{id}</code>",
    "whoami.admin_yes": "🛡 Админ: да",
    "whoami.admin_no": "🛡 Админ: нет",
    "download.confirm_button": "↓ Скачать",
    "download.confirm_message": '<b>{title}</b>\n\n<a href="{url}">rutracker</a>',
    "download.confirm_message_no_title": '<a href="{url}">rutracker</a>',
    "download.pick_season": "Какой сезон скачать?",
    "download.season_label": "Сезон {n}",
    "download.season_all": "Все сезоны",
    "download.season_filter_label": "{title} — сезон {season}",
    "status.header": "Список закачек:",
    "status.no_downloads": "Отслеживаемых закачек нет.",
    "status.not_configured": "↓ Сервер закачек не настроен.",
    "list.header": "Медиатека:",
    "list.empty": "Пока ничего нет. Пришлите название фильма и нажмите «↓ Скачать».",
    "list.movie_link": '🎬 <a href="{url}">{title}</a>',
    "list.cartoon_link": '🎨 <a href="{url}">{title}</a>',
    "list.series_link": '📺 <a href="{url}">{title}</a> — {n} {episodes_word}',
    "errors.generic": "Что-то пошло не так. Попробуйте ещё раз чуть позже.",
    "rt_url.fetching": "🔎 Смотрю раздачу на rutracker…",
    "rt_url.topic_failed": "Не удалось получить страницу раздачи: {detail}",
    "rt_url.candidates_header": (
        "📦 <b>{title}</b>\n\nПохоже на один из этих фильмов — выберите, чтобы привязать карточку:"
    ),
    "rt_url.no_match_header": (
        "📦 <b>{title}</b>\n\nНе нашёл в базе фильмов. Можно скачать без привязки."
    ),
    "rt_url.candidate_button": "{title} ({year})",
    "rt_url.candidate_button_no_year": "{title}",
    "rt_url.unlink_button": "Скачать без привязки",
    "ydl.fetching": "🔎 Смотрю видео…",
    "ydl.unsupported": ("Ссылка не распознана как ссылка на трекер или видео."),
    "ydl.preview": ("<b>{title}</b>\n{channel} · {duration}"),
    "ydl.preview_no_channel": "<b>{title}</b>\n{duration}",
    "ydl.confirm_button": "↓ Скачать",
    "ydl.start_failed": "Не удалось поставить на скачивание: {detail}",
    "ydl.queued": ("↓ Поставил на скачивание: <b>{title}</b>\n/status — посмотреть прогресс"),
    "ydl.live_unsupported": "Прямые трансляции не поддерживаются.",
    "ydl.playlist_header": (
        "📋 <b>{title}</b>\n"
        "Плейлист на {n} {video_word}. Скопируйте ссылку нужного "
        "видео и пришлите в чат:"
    ),
    "ydl.playlist_truncated_hint": "(показаны первые {limit} из {total})",
    "ydl.playlist_entry": '{n}. <a href="{url}">{title}</a> ({duration})',
    "ydl.duration_unknown": "—",
    "admin.not_admin": "Команда доступна только администраторам.",
    "admin.notify_on": "🔔 Уведомления о закачках пользователей включены.",
    "admin.notify_off": "🔕 Уведомления о закачках пользователей выключены.",
    "admin.user_downloaded_movie": ('📥 <b>{user}</b> скачал: <a href="{url}">{title}</a>'),
    "admin.user_downloaded_series": ("📥 <b>{user}</b> скачал серии (<b>{title}</b>): {url}"),
    "admin.user_downloaded_noname": ('📥 <b>{user}</b> скачал: <a href="{url}">…</a>'),
    "admin.global_list_header": "🗂 Все закачки пользователей:",
    "admin.global_list_empty": "Ни одной зарегистрированной закачки.",
    "admin.global_list_user_header": "👤 <b>{name}</b>",
    "admin.global_list_movie_line": '  • <a href="{url}">{title}</a>',
    "admin.global_list_series_line": '  • <a href="{url}">{title}</a> ({n} серий)',
}


def t(key: str, /, **kwargs: object) -> str:
    """Return the Russian string for ``key``, formatted with ``kwargs``."""
    template = RU.get(key, key)
    if kwargs:
        return template.format(**kwargs)
    return template
