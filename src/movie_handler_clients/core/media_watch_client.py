"""Async HTTP client for the media-watch-web registration API.

Wraps ``POST /api/register`` and ``DELETE /api/records/{id}``. The remote
server validates ``path`` against its own ``media_roots`` whitelist, so
we don't pre-validate here; we just propagate whatever the server says.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)


class MediaWatchError(Exception):
    """Raised on non-success HTTP status or transport failures."""

    def __init__(self, message: str, *, status: int | None = None, body: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = body


@dataclass
class MediaWatchClient:
    base_url: str
    api_token: str
    timeout_seconds: float = 15.0
    _http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> MediaWatchClient:
        self._http = httpx.AsyncClient(
            base_url=self.base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {self.api_token}"},
            timeout=self.timeout_seconds,
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def register(
        self,
        *,
        path: str,
        title: str,
        kind: str,
        imdb_id: str | None = None,
        description: str = "",
        poster_url: str = "",
    ) -> dict[str, Any]:
        """POST /api/register. Returns the parsed JSON body on 200.

        Raises ``MediaWatchError`` on any non-2xx response, with the
        decoded body attached for the caller to log/inspect.
        """
        body: dict[str, Any] = {
            "path": path,
            "title": title,
            "kind": kind,
        }
        if imdb_id:
            body["imdb_id"] = imdb_id
        if description:
            body["description"] = description
        if poster_url:
            body["poster_url"] = poster_url

        resp = await self._client().post("/api/register", json=body)
        return self._unwrap(resp, "register")

    async def delete(self, record_id: str) -> dict[str, Any]:
        resp = await self._client().delete(f"/api/records/{record_id}")
        # 404 here is benign — the record was already gone.
        if resp.status_code == 404:
            return {"id": record_id, "deleted": False}
        return self._unwrap(resp, "delete")

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            raise RuntimeError("MediaWatchClient used outside `async with`")
        return self._http

    @staticmethod
    def _unwrap(resp: httpx.Response, op: str) -> dict[str, Any]:
        try:
            payload = resp.json()
        except ValueError:
            payload = {"raw": resp.text}
        if resp.is_success:
            return payload if isinstance(payload, dict) else {}
        message = (
            payload.get("message")
            if isinstance(payload, dict)
            else f"HTTP {resp.status_code}"
        )
        log.warning("media_watch.error", op=op, status=resp.status_code, body=payload)
        raise MediaWatchError(
            f"media-watch {op} failed: {message}",
            status=resp.status_code,
            body=payload,
        )


__all__ = ["MediaWatchClient", "MediaWatchError"]
