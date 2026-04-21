"""Cache of recent torrent search results keyed by IMDb ID.

Used by the «Показать ещё» callback to expand the pinned torrent list
without re-querying rutracker.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any


class TorrentCache:
    def __init__(self, capacity: int = 256) -> None:
        self._store: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
        self._capacity = capacity

    def put(self, imdb_id: str, results: list[dict[str, Any]]) -> None:
        self._store[imdb_id] = results
        self._store.move_to_end(imdb_id)
        while len(self._store) > self._capacity:
            self._store.popitem(last=False)

    def get(self, imdb_id: str) -> list[dict[str, Any]] | None:
        entry = self._store.get(imdb_id)
        if entry is not None:
            self._store.move_to_end(imdb_id)
        return entry
