"""Handler tests — call the functions directly with mocked Message/CQ objects.

aiogram 3 has no first-party test harness, so we stub the Bot/Message surface
with ``unittest.mock`` rather than running a real Dispatcher.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from movie_handler_clients.telegram.handlers import details as details_mod
from movie_handler_clients.telegram.handlers import search as search_mod
from movie_handler_clients.telegram.search_cache import SearchCache


def _msg(text: str, user_id: int = 42) -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        from_user=SimpleNamespace(id=user_id),
        answer=AsyncMock(),
    )


async def test_on_start_sends_greeting() -> None:
    msg = _msg("/start")
    await search_mod.on_start(msg)  # type: ignore[arg-type]
    msg.answer.assert_awaited_once()
    (body,), _ = msg.answer.call_args
    assert "Привет" in body


async def test_on_text_empty_short_circuits() -> None:
    msg = _msg("   ")
    mcp = AsyncMock()
    cache = SearchCache()
    await search_mod.on_text(msg, mcp=mcp, search_cache=cache)  # type: ignore[arg-type]
    mcp.call_tool.assert_not_awaited()
    msg.answer.assert_awaited_once()


def test_clean_query_strips_quotes_and_detects_kind() -> None:
    assert search_mod._clean_query("Сериал «Отыграть назад»") == (
        "Отыграть назад",
        "series",
    )
    assert search_mod._clean_query('фильм "Дюна"') == ("Дюна", "movie")
    assert search_mod._clean_query("Матрица шоу") == ("Матрица", "series")
    assert search_mod._clean_query("Dune") == ("Dune", None)
    assert search_mod._clean_query("  «  »  ") == ("", None)


def test_split_title_year_extracts_4digit_year() -> None:
    assert search_mod._split_title_year("Дюна 2021") == ("Дюна", 2021)
    assert search_mod._split_title_year("Dune, 2021") == ("Dune", 2021)
    assert search_mod._split_title_year("Blade Runner 1982 director's cut") == (
        "Blade Runner director's cut",
        1982,
    )
    assert search_mod._split_title_year("Dune") == ("Dune", None)
    assert search_mod._split_title_year("Room 237") == ("Room 237", None)  # 3 digits


async def test_on_text_renders_results(sample_search_payload: dict) -> None:
    msg = _msg("Dune")
    mcp = AsyncMock()
    mcp.call_tool = AsyncMock(return_value=sample_search_payload)
    cache = SearchCache()

    await search_mod.on_text(msg, mcp=mcp, search_cache=cache)  # type: ignore[arg-type]

    mcp.call_tool.assert_awaited_once_with("search_movie", {"title": "Dune"}, tg_user_id=42)
    msg.answer.assert_awaited_once()
    kwargs = msg.answer.call_args.kwargs
    args = msg.answer.call_args.args
    body = args[0] if args else kwargs.get("text", "")
    assert "Дюна" in body
    assert "Dune" in body  # original in parens
    assert kwargs["parse_mode"] == "HTML"
    assert kwargs["reply_markup"].inline_keyboard


async def test_on_text_filters_by_kind_hint() -> None:
    payload = {
        "results": [
            {"kind": "movie", "imdb_id": "tt1", "title": "X film", "year": 2020},
            {"kind": "series", "imdb_id": "tt2", "title": "X show", "year": 2021},
        ],
    }
    msg = _msg("сериал X")
    mcp = AsyncMock()
    mcp.call_tool = AsyncMock(return_value=payload)

    await search_mod.on_text(msg, mcp=mcp, search_cache=SearchCache())  # type: ignore[arg-type]

    # "сериал" prefix was stripped from the title sent to MCP ...
    mcp.call_tool.assert_awaited_once_with("search_movie", {"title": "X"}, tg_user_id=42)
    # ... and only the series was rendered.
    body = msg.answer.call_args.args[0]
    assert "X show" in body
    assert "X film" not in body


async def test_on_text_groups_and_sorts(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "results": [
            {"kind": "movie", "imdb_id": "tt1", "title": "Film A", "year": 2015},
            {"kind": "movie", "imdb_id": "tt2", "title": "Film B", "year": 2023},
            {"kind": "series", "imdb_id": "tt3", "title": "Show A", "year": 2020},
            {"kind": "series", "imdb_id": "tt4", "title": "Show B", "year": 2024},
        ],
    }
    msg = _msg("anything")
    mcp = AsyncMock()
    mcp.call_tool = AsyncMock(return_value=payload)
    cache = SearchCache()

    await search_mod.on_text(msg, mcp=mcp, search_cache=cache)  # type: ignore[arg-type]

    body = msg.answer.call_args.args[0]
    # Section headers present and in the right order.
    i_movies = body.index("Фильмы")
    i_series = body.index("Сериалы")
    assert i_movies < i_series
    # Within a section, newer year appears first.
    assert body.index("Film B") < body.index("Film A")
    assert body.index("Show B") < body.index("Show A")
    # Keyboard order matches: movies (desc), then series (desc).
    kb = msg.answer.call_args.kwargs["reply_markup"].inline_keyboard
    labels = [row[0].text for row in kb]
    assert labels[0].endswith("(2023)")  # Film B
    assert labels[1].endswith("(2015)")  # Film A
    assert labels[2].endswith("(2024)")  # Show B
    assert labels[3].endswith("(2020)")  # Show A


async def test_on_text_shows_error_when_mcp_fails() -> None:
    msg = _msg("Dune")
    mcp = AsyncMock()
    mcp.call_tool = AsyncMock(side_effect=_MCPErr("boom"))
    cache = SearchCache()

    await search_mod.on_text(msg, mcp=mcp, search_cache=cache)  # type: ignore[arg-type]

    msg.answer.assert_awaited_once()
    (body,), _ = msg.answer.call_args
    assert "boom" in body


class _MCPErr(Exception):
    pass


# patch MCPClientError symbol so the handler catches our stub class
@pytest.fixture(autouse=True)
def _patch_mcp_err(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(search_mod, "MCPClientError", _MCPErr)
    monkeypatch.setattr(details_mod, "MCPClientError", _MCPErr)


async def test_trailer_callback_falls_back_to_stub_when_no_client() -> None:
    cq = SimpleNamespace(
        data="t:tt1160419",
        from_user=SimpleNamespace(id=42),
        message=SimpleNamespace(answer=AsyncMock()),
        answer=AsyncMock(),
    )
    await details_mod.on_trailer(cq, trailer=None)  # type: ignore[arg-type]
    cq.answer.assert_awaited_once()
    (body,), kwargs = cq.answer.call_args
    assert "недоступен" in body
    assert kwargs.get("show_alert") is True


async def test_trailer_callback_sends_one_message_per_trailer() -> None:
    cq = SimpleNamespace(
        data="t:tt1160419",
        from_user=SimpleNamespace(id=42),
        message=SimpleNamespace(answer=AsyncMock()),
        answer=AsyncMock(),
    )
    trailer = AsyncMock()
    trailer.find_trailer = AsyncMock(
        return_value={
            "results": [
                {
                    "url": "https://www.youtube.com/watch?v=a",
                    "title": "T1",
                    "language": "ru",
                    "kind": "trailer",
                    "source": "tmdb",
                },
                {
                    "url": "https://www.youtube.com/watch?v=b",
                    "title": "T2",
                    "language": "en",
                    "kind": "teaser",
                    "source": "tmdb",
                },
            ],
            "sources_failed": [],
            "error": None,
        }
    )

    await details_mod.on_trailer(cq, trailer=trailer)  # type: ignore[arg-type]

    trailer.find_trailer.assert_awaited_once_with("tt1160419", tg_user_id=42)
    assert cq.message.answer.await_count == 2
    first_body = cq.message.answer.await_args_list[0].args[0]
    assert "T1" in first_body
    assert "youtube.com/watch?v=a" in first_body


async def test_trailer_callback_answers_not_found_when_empty() -> None:
    cq = SimpleNamespace(
        data="t:tt9",
        from_user=SimpleNamespace(id=42),
        message=SimpleNamespace(answer=AsyncMock()),
        answer=AsyncMock(),
    )
    trailer = AsyncMock()
    trailer.find_trailer = AsyncMock(
        return_value={"results": [], "sources_failed": [], "error": None}
    )

    await details_mod.on_trailer(cq, trailer=trailer)  # type: ignore[arg-type]

    cq.answer.assert_awaited_once()
    (body,), _ = cq.answer.call_args
    assert "не найдены" in body.lower()
    cq.message.answer.assert_not_awaited()


async def test_download_callback_falls_back_to_stub_when_no_client() -> None:
    from movie_handler_clients.telegram.title_cache import TitleCache

    cq = SimpleNamespace(
        data="dl:tt1160419",
        from_user=SimpleNamespace(id=42),
        message=SimpleNamespace(answer=AsyncMock()),
        answer=AsyncMock(),
    )
    await details_mod.on_download(cq, torrent=None, title_cache=TitleCache())  # type: ignore[arg-type]
    cq.answer.assert_awaited_once()
    (body,), kwargs = cq.answer.call_args
    assert "недоступ" in body
    assert kwargs.get("show_alert") is True


async def test_download_callback_lists_torrents() -> None:
    from movie_handler_clients.telegram.title_cache import TitleCache

    cache = TitleCache()
    cache.put("tt1160419", "Дюна", 2021)
    cq = SimpleNamespace(
        data="dl:tt1160419",
        from_user=SimpleNamespace(id=42),
        message=SimpleNamespace(answer=AsyncMock()),
        answer=AsyncMock(),
    )
    torrent = AsyncMock()
    torrent.search_torrents = AsyncMock(
        return_value={
            "results": [
                {
                    "topic_id": 1,
                    "title": "Dune (2021) 1080p HDR",
                    "quality": "1080p",
                    "size_bytes": 20 * 1024**3,
                    "hdr": True,
                    "seeders": 1234,
                },
                {
                    "topic_id": 2,
                    "title": "Dune (2021) 720p",
                    "quality": "720p",
                    "size_bytes": 5 * 1024**3,
                    "hdr": False,
                    "seeders": 200,
                },
            ],
            "error": None,
        }
    )

    await details_mod.on_download(cq, torrent=torrent, title_cache=cache)  # type: ignore[arg-type]

    torrent.search_torrents.assert_awaited_once_with("Дюна 2021", limit=10, tg_user_id=42)
    cq.message.answer.assert_awaited_once()
    body = cq.message.answer.call_args.args[0]
    assert "Dune (2021) 1080p HDR" in body
    # Buttons carry quality / size / HDR / seeders.
    kb = cq.message.answer.call_args.kwargs["reply_markup"].inline_keyboard
    labels = [row[0].text for row in kb]
    assert any("1080p" in lbl and "HDR" in lbl and "🌱1234" in lbl for lbl in labels)


async def test_download_callback_surfaces_captcha() -> None:
    from movie_handler_clients.telegram.title_cache import TitleCache

    cache = TitleCache()
    cache.put("tt1160419", "Дюна", 2021)
    cq = SimpleNamespace(
        data="dl:tt1160419",
        from_user=SimpleNamespace(id=42),
        message=SimpleNamespace(answer=AsyncMock()),
        answer=AsyncMock(),
    )
    torrent = AsyncMock()
    torrent.search_torrents = AsyncMock(
        return_value={"results": [], "error": {"code": "captcha_required", "message": "..."}}
    )

    await details_mod.on_download(cq, torrent=torrent, title_cache=cache)  # type: ignore[arg-type]

    cq.answer.assert_awaited_once()
    (body,), kwargs = cq.answer.call_args
    assert "капч" in body.lower()
    assert kwargs.get("show_alert") is True


async def test_torrent_pick_sends_document() -> None:
    cq = SimpleNamespace(
        data="tor:42",
        from_user=SimpleNamespace(id=42),
        message=SimpleNamespace(answer_document=AsyncMock()),
        answer=AsyncMock(),
    )
    torrent = AsyncMock()
    torrent.get_torrent_file = AsyncMock(
        return_value={
            "file": {
                "topic_id": 42,
                "filename": "[rutracker.org].t42.torrent",
                "content_base64": "ZA==",  # base64 for b"d"
                "size_bytes": 1,
            },
            "error": None,
        }
    )

    await details_mod.on_torrent_pick(cq, torrent=torrent)  # type: ignore[arg-type]

    torrent.get_torrent_file.assert_awaited_once_with(42, tg_user_id=42)
    cq.message.answer_document.assert_awaited_once()
