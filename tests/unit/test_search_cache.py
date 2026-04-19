from __future__ import annotations

from movie_handler_clients.telegram.search_cache import SearchCache


def test_put_and_get_roundtrip() -> None:
    cache = SearchCache(capacity=8)
    qid = cache.put("Dune", [{"imdb_id": "tt1"}])
    entry = cache.get(qid)
    assert entry is not None
    query, items = entry
    assert query == "Dune"
    assert items[0]["imdb_id"] == "tt1"


def test_capacity_evicts_oldest() -> None:
    cache = SearchCache(capacity=2)
    a = cache.put("A", [])
    b = cache.put("B", [])
    c = cache.put("C", [])
    assert cache.get(a) is None
    assert cache.get(b) is not None
    assert cache.get(c) is not None
