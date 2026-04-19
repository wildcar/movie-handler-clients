"""MCP client for ``movie-metadata-mcp`` over streamable HTTP.

The client keeps one long-lived MCP session open for the lifetime of the bot
process. Every ``call_tool`` invocation is timed and recorded into the
``TrafficLog`` — both successes and failures.
"""

from __future__ import annotations

import json
import time
from contextlib import AsyncExitStack
from types import TracebackType
from typing import Any

import structlog
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from .traffic_log import TrafficLog

log = structlog.get_logger(__name__)


class MCPClientError(RuntimeError):
    """Raised when an MCP call fails at the transport or protocol level."""


class MovieMetadataMCPClient:
    """Thin wrapper around an MCP ``ClientSession`` plus traffic logging."""

    def __init__(self, url: str, auth_token: str, traffic_log: TrafficLog) -> None:
        self._url = url
        self._headers = {"Authorization": f"Bearer {auth_token}"}
        self._traffic = traffic_log
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    async def __aenter__(self) -> MovieMetadataMCPClient:
        stack = AsyncExitStack()
        try:
            read, write, _ = await stack.enter_async_context(
                streamablehttp_client(self._url, headers=self._headers)
            )
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
        except Exception:
            await stack.aclose()
            raise
        self._stack = stack
        self._session = session
        log.info("mcp.session_opened", url=self._url)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = None
        self._session = None
        log.info("mcp.session_closed")

    # ------------------------------------------------------------------
    # tool calls
    # ------------------------------------------------------------------
    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        tg_user_id: int | None = None,
    ) -> dict[str, Any]:
        """Invoke an MCP tool and return its parsed JSON payload.

        Raises :class:`MCPClientError` on transport / protocol failures.
        Tool-level errors (returned as ``{"error": {...}}`` in the payload)
        are passed through to the caller unchanged so they can be rendered
        to the end user.
        """
        if self._session is None:
            raise RuntimeError("MovieMetadataMCPClient must be entered as a context manager")

        started = time.perf_counter()
        error: str | None = None
        payload: dict[str, Any] | None = None
        try:
            result = await self._session.call_tool(name, arguments)
            payload = _extract_payload(result)
            return payload
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise MCPClientError(error) from exc
        finally:
            duration_ms = int((time.perf_counter() - started) * 1000)
            try:
                await self._traffic.record(
                    tool=name,
                    request=arguments,
                    response=payload,
                    duration_ms=duration_ms,
                    tg_user_id=tg_user_id,
                    error=error,
                )
            except Exception:
                log.exception("mcp.traffic_log_failed", tool=name)


def _extract_payload(result: Any) -> dict[str, Any]:
    """Pull the JSON dict out of an MCP ``CallToolResult``.

    Prefers ``structuredContent`` (newer SDKs), falls back to parsing the
    first text content item.
    """
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and structured:
        return structured

    content = getattr(result, "content", None) or []
    for item in content:
        text = getattr(item, "text", None)
        if isinstance(text, str):
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(decoded, dict):
                return decoded

    raise MCPClientError("MCP response did not contain a JSON payload")
