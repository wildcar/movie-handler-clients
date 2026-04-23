"""Shared test fixtures."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from movie_handler_clients.core.traffic_log import TrafficLog


@pytest_asyncio.fixture
async def traffic_log(tmp_path: Path) -> AsyncIterator[TrafficLog]:
    log = TrafficLog(tmp_path / "traffic.sqlite", ttl_days=30)
    await log.open()
    try:
        yield log
    finally:
        await log.close()


@pytest.fixture
def sample_search_payload() -> dict[str, object]:
    return {
        "query": "Dune",
        "results": [
            {
                "kind": "movie",
                "imdb_id": "tt1160419",
                "tmdb_id": 438631,
                "title": "Дюна",
                "original_title": "Dune",
                "year": 2021,
                "poster_url": "https://image.tmdb.org/t/p/w500/abc.jpg",
                "overview": "A noble family becomes embroiled in a war for control…",
            }
        ],
        "sources_failed": [],
        "error": None,
    }


@pytest.fixture
def sample_details_payload() -> dict[str, object]:
    return {
        "details": {
            "kind": "movie",
            "imdb_id": "tt1160419",
            "tmdb_id": 438631,
            "kinopoisk_id": 1318972,
            "title": "Дюна",
            "original_title": "Dune",
            "year": 2021,
            "runtime_minutes": 155,
            "genres": ["Science Fiction", "Adventure"],
            "overview": "Paul Atreides, a brilliant and gifted young man…",
            "overview_ru": "Пауль Атрейдес — одарённый юноша…",
            "poster_url": "https://image.tmdb.org/t/p/w500/abc.jpg",
            "ratings": [
                {"source": "tmdb", "value": 7.78, "scale": 10, "votes": 11000},
                {"source": "imdb", "value": 8.0, "scale": 10, "votes": 1_000_000},
                {"source": "metacritic", "value": 74, "scale": 100, "votes": None},
                {"source": "kinopoisk", "value": 7.676, "scale": 10, "votes": 833000},
            ],
        },
        "sources_failed": [],
        "error": None,
    }
