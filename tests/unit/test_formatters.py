from __future__ import annotations

from movie_handler_clients.core.formatters import format_details, format_search_item


def test_format_search_item_escapes_html() -> None:
    item = {"title": "A <b>hack</b>", "year": 2024}
    out = format_search_item(item)
    assert "&lt;b&gt;hack&lt;/b&gt;" in out
    assert "2024" in out


def test_format_search_item_shows_original_when_different() -> None:
    item = {"title": "Дюна", "original_title": "Dune", "year": 2021}
    out = format_search_item(item)
    assert "Дюна" in out
    assert "Dune" in out
    assert "2021" in out


def test_format_search_item_omits_original_when_same() -> None:
    item = {"title": "Dune", "original_title": "Dune", "year": 2021}
    out = format_search_item(item)
    # Original title appears only once — no "Dune (Dune)" duplication.
    assert out.count("Dune") == 1


def test_format_search_item_omits_overview() -> None:
    item = {"title": "X", "year": 2020, "overview": "noisy english text"}
    assert "noisy" not in format_search_item(item)


def test_format_details_includes_all_ratings(sample_details_payload: dict) -> None:
    out = format_details(sample_details_payload)
    assert "Дюна" in out
    assert "Dune" in out  # original title in parens
    assert "2021" in out
    assert "155 мин" in out
    for label in ("TMDB", "IMDb", "Metacritic", "КиноПоиск"):
        assert label in out
    # Numeric formatting: /10 scales get one decimal, /100 is rounded.
    assert "7.8/10" in out  # TMDB 7.78
    assert "8.0/10" in out  # IMDb 8.0
    assert "74/100" in out  # Metacritic
    assert "7.7/10" in out  # kinopoisk 7.676
    # Colour badge: everything here is ≥7, so green.
    assert "🟢" in out
    assert "Пауль" in out


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
