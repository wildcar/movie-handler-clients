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
from typing import Any, Self

import structlog
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from .traffic_log import TrafficLog

log = structlog.get_logger(__name__)


class MCPClientError(RuntimeError):
    """Raised when an MCP call fails at the transport or protocol level."""


class BaseMCPClient:
    """Generic long-lived streamable-HTTP MCP client with traffic logging.

    Concrete subclasses (e.g. :class:`MovieMetadataMCPClient`) add typed
    wrappers around ``call_tool`` but don't need to reimplement lifecycle.
    """

    def __init__(self, url: str, auth_token: str, traffic_log: TrafficLog) -> None:
        self._url = url
        self._headers = {"Authorization": f"Bearer {auth_token}"}
        self._traffic = traffic_log
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    async def __aenter__(self) -> Self:
        await self._open_session()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._close_session(logged=True)

    async def _open_session(self) -> None:
        stack = AsyncExitStack()
        try:
            read, write, _ = await stack.enter_async_context(
                streamablehttp_client(self._url, headers=self._headers)
            )
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
        except BaseException:
            # BaseExceptionGroup (from anyio task groups) is not a subclass of
            # Exception; catch the broader BaseException so teardown runs.
            await stack.aclose()
            raise
        self._stack = stack
        self._session = session
        log.info("mcp.session_opened", url=self._url)

    async def _close_session(self, *, logged: bool) -> None:
        if self._stack is not None:
            try:
                await self._stack.aclose()
            except (Exception, BaseExceptionGroup):
                # Upstream may have vanished; we only care that our handles
                # are released.
                log.debug("mcp.session_close_error", url=self._url, exc_info=True)
        self._stack = None
        self._session = None
        if logged:
            log.info("mcp.session_closed", url=self._url)

    async def _reconnect(self) -> None:
        """Drop the current session and open a fresh one.

        Used on ``session terminated`` / 404 errors that appear when the
        upstream MCP server restarted and no longer recognises our session
        id. One attempt only; the caller decides whether to retry.
        """
        await self._close_session(logged=False)
        await self._open_session()
        log.info("mcp.session_reopened", url=self._url)

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
            raise RuntimeError(f"{type(self).__name__} must be entered as a context manager")

        started = time.perf_counter()
        error: str | None = None
        payload: dict[str, Any] | None = None
        try:
            try:
                result = await self._session.call_tool(name, arguments)
            except Exception as exc:
                # The upstream server was restarted or our session id expired —
                # both land here as "Session terminated" / 404 from the HTTP
                # transport. One reconnect + retry. Any other failure bubbles
                # up unchanged.
                if not _is_session_terminated(exc):
                    raise
                log.info("mcp.session_stale_retrying", url=self._url)
                await self._reconnect()
                assert self._session is not None
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


def _is_session_terminated(exc: BaseException) -> bool:
    """Heuristic for the 'server lost our session id' family of errors.

    Streamable-HTTP transport surfaces a restart as a plain string
    "Session terminated" on calls with a stale session id; the upstream
    server returns 404 before that message is generated, so we also
    trigger on that number in the text.
    """
    text = str(exc).lower()
    return "session terminated" in text or "404" in text


class MovieMetadataMCPClient(BaseMCPClient):
    """Typed client for movie-metadata-mcp. Kept as a distinct class so tools
    that should only see the metadata server can't accidentally be given the
    trailer one (or vice versa)."""
