"""Short-lived url+title cache for the yt-dlp pasted-URL flow.

The confirm callback can't carry a raw URL (Telegram callback_data is
capped at 64 bytes; YouTube URLs frequently exceed that with playlist /
tracking params). We mint a short token at preview time, stash the URL
(and the resolved title for the «Поставил на скачивание» message)
under it, and the callback ships only the token.

Tokens are 8 chars of urlsafe base64 → ~48 bits, no collision risk for
the cache size we keep.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass


@dataclass(frozen=True)
class YtDlpEntry:
    url: str
    title: str


class YtDlpCache:
    def __init__(self, capacity: int = 256) -> None:
        self._store: OrderedDict[str, YtDlpEntry] = OrderedDict()
        self._capacity = capacity

    def put(self, token: str, entry: YtDlpEntry) -> None:
        self._store[token] = entry
        self._store.move_to_end(token)
        while len(self._store) > self._capacity:
            self._store.popitem(last=False)

    def get(self, token: str) -> YtDlpEntry | None:
        entry = self._store.get(token)
        if entry is not None:
            self._store.move_to_end(token)
        return entry
