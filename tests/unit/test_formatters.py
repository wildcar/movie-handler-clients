from __future__ import annotations

from movie_handler_clients.core.formatters import format_details, format_search_item


def test_format_search_item_escapes_html() -> None:
    item = {"title": "A <b>hack</b>", "year": 2024}
    out = format_search_item(item)
    assert "&lt;b&gt;hack&lt;/b&gt;" in out
    assert "2024" in out


def test_format_search_item_omits_overview() -> None:
    item = {"title": "X", "year": 2020, "overview": "noisy english text"}
    assert "noisy" not in format_search_item(item)


def test_format_details_includes_all_ratings(sample_details_payload: dict) -> None:
    out = format_details(sample_details_payload)
    assert "Dune" in out
    assert "2021" in out
    assert "155 мин" in out
    for label in ("TMDB", "IMDb", "Metacritic", "КиноПоиск"):
        assert label in out
    assert "Пауль" in out


def test_format_details_flags_failed_sources() -> None:
    payload = {
        "details": {"title": "X", "year": 2020, "ratings": []},
        "sources_failed": ["omdb"],
    }
    assert "omdb" in format_details(payload)
