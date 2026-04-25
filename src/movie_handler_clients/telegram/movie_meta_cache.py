"""Stash extra metadata fields (description + poster URL) keyed by IMDb id.

Populated when the user opens the details card; consumed when the
download tap pushes a Download row into ``state_db`` so the registration
to media-watch-web on completion can carry the same poster/description
the user already saw, without a second ``get_movie_details`` round-trip.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass


@dataclass(frozen=True)
class MovieMeta:
    description: str = ""
    poster_url: str = ""


class MovieMetaCache:
    def __init__(self, capacity: int = 2048) -> None:
        self._store: OrderedDict[str, MovieMeta] = OrderedDict()
        self._capacity = capacity

    def put(self, imdb_id: str, *, description: str = "", poster_url: str = "") -> None:
        self._store[imdb_id] = MovieMeta(
            description=description or "",
            poster_url=poster_url or "",
        )
        self._store.move_to_end(imdb_id)
        while len(self._store) > self._capacity:
            self._store.popitem(last=False)

    def get(self, imdb_id: str) -> MovieMeta | None:
        meta = self._store.get(imdb_id)
        if meta is not None:
            self._store.move_to_end(imdb_id)
        return meta


__all__ = ["MovieMeta", "MovieMetaCache"]
