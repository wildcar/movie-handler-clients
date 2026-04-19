"""Tiny in-process cache of recent search results.

Used solely to re-render a list of hits when the user taps «← К списку».
Single-process bot, so a plain dict with a soft cap is enough.
"""

from __future__ import annotations

import secrets
from collections import OrderedDict
from typing import Any


class SearchCache:
    def __init__(self, capacity: int = 512) -> None:
        self._store: OrderedDict[str, tuple[str, list[dict[str, Any]]]] = OrderedDict()
        self._capacity = capacity

    def put(self, query: str, items: list[dict[str, Any]]) -> str:
        query_id = secrets.token_urlsafe(8)
        self._store[query_id] = (query, items)
        self._store.move_to_end(query_id)
        while len(self._store) > self._capacity:
            self._store.popitem(last=False)
        return query_id

    def get(self, query_id: str) -> tuple[str, list[dict[str, Any]]] | None:
        entry = self._store.get(query_id)
        if entry is not None:
            self._store.move_to_end(query_id)
        return entry
