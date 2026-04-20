"""Tracks in-progress downloads per Telegram user for completion notifications."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class _Entry:
    tg_user_id: int
    title: str  # name at add time, used as notification fallback


class DownloadTracker:
    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}  # hash (upper) → entry

    def track(self, hash_: str, tg_user_id: int, title: str) -> None:
        self._entries[hash_.upper()] = _Entry(tg_user_id=tg_user_id, title=title)

    def untrack(self, hash_: str) -> None:
        self._entries.pop(hash_.upper(), None)

    def all_hashes(self) -> list[str]:
        return list(self._entries)

    def get(self, hash_: str) -> _Entry | None:
        return self._entries.get(hash_.upper())

    def user_hashes(self, tg_user_id: int) -> list[str]:
        return [h for h, e in self._entries.items() if e.tg_user_id == tg_user_id]
