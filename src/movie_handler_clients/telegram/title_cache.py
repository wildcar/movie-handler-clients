"""Tiny in-process cache for the title/year of a recently viewed movie.

Used when the user taps «⬇️ Скачать» on a details card: the callback data
only carries the IMDb id, and we need the human-readable title+year to
build a rutracker query without another round-trip to the metadata MCP.
"""

from __future__ import annotations

from collections import OrderedDict


class TitleCache:
    def __init__(self, capacity: int = 2048) -> None:
        self._store: OrderedDict[str, tuple[str, int | None]] = OrderedDict()
        self._capacity = capacity

    def put(self, imdb_id: str, title: str, year: int | None) -> None:
        self._store[imdb_id] = (title, year)
        self._store.move_to_end(imdb_id)
        while len(self._store) > self._capacity:
            self._store.popitem(last=False)

    def get(self, imdb_id: str) -> tuple[str, int | None] | None:
        entry = self._store.get(imdb_id)
        if entry is not None:
            self._store.move_to_end(imdb_id)
        return entry
