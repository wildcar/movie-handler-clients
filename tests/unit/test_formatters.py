from __future__ import annotations

from movie_handler_clients.core.formatters import format_details, format_search_item


def test_format_search_item_escapes_html() -> None:
    item = {"title": "A <b>hack</b>", "year": 2024}
    out = format_search_item(item)
    assert "&lt;b&gt;hack&lt;/b&gt;" in out
    assert "2024" in out


def test_format_search_item_renders_title_and_year() -> None:
    # SearchV2 line layout — bold title + comma-separated facts. The
    # original_title used to appear in parens, but search results moved
    # to a per-row inline keyboard and the message body now carries
    # only this short summary form.
    item = {"title": "Дюна", "original_title": "Dune", "year": 2021}
    out = format_search_item(item)
    assert "Дюна" in out
    assert "2021" in out


def test_format_search_item_omits_overview() -> None:
    item = {"title": "X", "year": 2020, "overview": "noisy english text"}
    assert "noisy" not in format_search_item(item)


def test_format_details_includes_all_ratings(sample_details_payload: dict) -> None:
    out = format_details(sample_details_payload)
    assert "Дюна" in out
    assert "Dune" in out  # original title in parens
    assert "2021" in out
    assert "2 ч 35 мин" in out
    for label in ("TMDB", "IMDb", "Metacritic", "КиноПоиск"):
        assert label in out
    # Compact ratings row: service label + formatted value.
    assert "TMDB</a> 7.8" in out
    assert "IMDb</a> 8.0" in out
    assert "Metacritic 74" in out
    assert "КиноПоиск</a> 7.7" in out
    # Colour badge: everything here is ≥7, so green.
    assert "🟢" in out
    assert "Пауль" in out
    assert '<a href="https://www.themoviedb.org/movie/438631">TMDB</a>' in out
    assert '<a href="https://www.imdb.com/title/tt1160419/">IMDb</a>' in out
    assert '<a href="https://www.kinopoisk.ru/film/1318972/">КиноПоиск</a>' in out
    assert ">Metacritic<" not in out


def test_rating_badges_by_threshold() -> None:
    payload = {
        "details": {
            "title": "X",
            "year": 2020,
            "ratings": [
                {"source": "tmdb", "value": 4.9, "scale": 10},  # red
                {"source": "imdb", "value": 6.9, "scale": 10},  # yellow
                {"source": "kinopoisk", "value": 7.0, "scale": 10},  # green
                {"source": "metacritic", "value": 49, "scale": 100},  # red
                {"source": "metacritic", "value": 69, "scale": 100},  # yellow
                {"source": "metacritic", "value": 70, "scale": 100},  # green
            ],
        },
        "sources_failed": [],
    }
    out = format_details(payload)
    assert out.count("🔴") == 2
    assert out.count("🟡") == 2
    assert out.count("🟢") == 2


def test_format_details_flags_failed_sources() -> None:
    payload = {
        "details": {"title": "X", "year": 2020, "ratings": []},
        "sources_failed": ["omdb"],
    }
    assert "omdb" in format_details(payload)
