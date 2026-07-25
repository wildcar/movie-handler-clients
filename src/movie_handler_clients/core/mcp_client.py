"""MCP client for ``movie-metadata-mcp`` over streamable HTTP.

The client keeps one long-lived MCP session open for the lifetime of the bot
process. Every ``call_tool`` invocation is timed and recorded into the
``TrafficLog`` — both successes and failures.

The client is **self-healing**: an unreachable server at startup is not fatal
(``__aenter__`` begins life disconnected), and a background supervisor keeps
retrying the connection while down. State changes (down / recovered) are
surfaced through an optional notifier so the bot can ping the admins.

The session lives in its own dedicated asyncio task — see
:meth:`BaseMCPClient._run_session` for why that matters.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from datetime import timedelta
from types import TracebackType
from typing import Any, Self

import structlog
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from .traffic_log import TrafficLog

log = structlog.get_logger(__name__)

# Notifier signature: (event, client_name) where event is "down" | "up".
Notifier = Callable[[str, str], Awaitable[None]]


class MCPClientError(RuntimeError):
    """Raised when an MCP call fails at the transport or protocol level."""


class BaseMCPClient:
    """Generic long-lived streamable-HTTP MCP client with traffic logging.

    Concrete subclasses (e.g. :class:`MovieMetadataMCPClient`) add typed
    wrappers around ``call_tool`` but don't need to reimplement lifecycle.
    """

    # Seconds between reconnect attempts while disconnected; also the idle
    # poll cadence of the supervisor while healthy.
    _RECONNECT_DELAY = 15.0
    # How long a tool call may wait for its response before we give up on
    # it. The MCP SDK turns this into an ``McpError``, not a cancellation,
    # so the session survives and the caller gets a normal failure it can
    # render. Subclasses / individual calls narrow it where the server has
    # a tighter budget of its own.
    _CALL_TIMEOUT = 60.0
    # Grace period for the session task to unwind its HTTP stack on close.
    # A vanished upstream can wedge the teardown; after this we cancel it.
    _CLOSE_TIMEOUT = 10.0

    def __init__(
        self,
        url: str,
        auth_token: str,
        traffic_log: TrafficLog,
        *,
        name: str | None = None,
    ) -> None:
        self._url = url
        self._headers = {"Authorization": f"Bearer {auth_token}"}
        self._traffic = traffic_log
        self._session: ClientSession | None = None
        self._name = name or type(self).__name__
        # Session ownership: the task that entered the transport stack, plus
        # the event any other task uses to ask it to unwind.
        self._session_task: asyncio.Task[None] | None = None
        self._close_event: asyncio.Event | None = None
        # Self-healing machinery.
        self._conn_lock = asyncio.Lock()
        self._supervisor: asyncio.Task[None] | None = None
        self._notifier: Notifier | None = None
        self._down_announced = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def connected(self) -> bool:
        return self._session is not None

    def set_notifier(self, notifier: Notifier | None) -> None:
        """Register a coroutine called on connection state changes."""
        self._notifier = notifier

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    async def __aenter__(self) -> Self:
        try:
            await self._open_session()
        except (Exception, BaseExceptionGroup) as exc:
            # Non-fatal: begin life disconnected. Once the bot wires a
            # notifier and calls start_supervisor(), the background watchdog
            # keeps retrying and announces recovery; until then call_tool
            # reconnects on demand.
            log.warning("mcp.connect_failed", url=self._url, name=self._name, error=str(exc))
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._stop_supervisor()
        await self._close_session(logged=True)

    async def _open_session(self) -> None:
        """Spawn the session-owner task and wait for the MCP handshake."""
        loop = asyncio.get_running_loop()
        ready: asyncio.Future[None] = loop.create_future()
        close_event = asyncio.Event()
        task = asyncio.create_task(
            self._run_session(ready, close_event), name=f"mcp-session:{self._name}"
        )
        try:
            await ready
        except BaseException:
            # Handshake failed (or we were cancelled waiting for it) — retire
            # the half-built session before propagating.
            close_event.set()
            try:
                await self._await_session_task(task)
            except BaseException:
                # We're being cancelled ourselves and can't await anything;
                # cancelling the owner task is enough to keep it from
                # outliving us (it unwinds its own stack on the way out).
                task.cancel()
                raise
            raise
        self._session_task = task
        self._close_event = close_event

    async def _run_session(
        self, ready: asyncio.Future[None], close_event: asyncio.Event
    ) -> None:
        """Own the transport stack for the whole life of one session.

        The streamable-HTTP transport builds an anyio task group whose cancel
        scope belongs to whichever task entered it, and anyio only permits
        that scope to be exited from the *same* task. Closing it from a
        Telegram handler task (which is what a mid-call reconnect used to do)
        delivered the cancellation to the bot's main task instead and took the
        whole process down — see the 2026-07-25 incident. So the stack is
        entered and exited here, in one dedicated task, and every other task
        only ever signals ``close_event``.

        The task also outlives the handshake: if the transport itself fails
        later, the ``async with`` unwinds here, ``_session`` drops to ``None``
        and the supervisor reconnects.
        """
        session: ClientSession | None = None
        try:
            async with AsyncExitStack() as stack:
                read, write, _ = await stack.enter_async_context(
                    streamablehttp_client(self._url, headers=self._headers)
                )
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                self._session = session
                log.info("mcp.session_opened", url=self._url)
                if not ready.done():
                    ready.set_result(None)
                await close_event.wait()
        except BaseException as exc:
            # Nothing may escape this task: an unretrieved failure here would
            # surface as a stray "Task exception was never retrieved", and a
            # CancelledError means we were retired on purpose.
            if not ready.done():
                ready.set_exception(exc)
            elif not isinstance(exc, asyncio.CancelledError):
                log.warning(
                    "mcp.session_lost", url=self._url, name=self._name, error=str(exc)
                )
        finally:
            # Only disown the session we opened — a newer one may already be live.
            if self._session is session:
                self._session = None

    async def _close_session(self, *, logged: bool) -> None:
        task = self._session_task
        close_event = self._close_event
        self._session_task = None
        self._close_event = None
        if close_event is not None:
            close_event.set()
        if task is not None:
            await self._await_session_task(task)
        self._session = None
        if logged:
            log.info("mcp.session_closed", url=self._url)

    async def _await_session_task(self, task: asyncio.Task[None]) -> None:
        """Wait for the owner task to unwind, cancelling it if it wedges.

        ``wait_for`` cancels the task on timeout, which is safe precisely
        because the cancellation lands in the task that owns the cancel scope.
        """
        try:
            await asyncio.wait_for(task, timeout=self._CLOSE_TIMEOUT)
        except TimeoutError:
            log.warning("mcp.session_close_timeout", url=self._url, name=self._name)
        except (Exception, BaseExceptionGroup):
            # Upstream may have vanished; we only care that our handles
            # are released.
            log.debug("mcp.session_close_error", url=self._url, exc_info=True)

    async def _ensure_session(self) -> None:
        """Open a session if we don't currently have one. Concurrency-safe:
        the lock keeps the supervisor and an on-demand ``call_tool`` from
        opening two sessions at once."""
        if self._session is not None:
            return
        async with self._conn_lock:
            if self._session is not None:
                return
            await self._open_session()

    async def _reconnect(self) -> None:
        """Drop the current session and open a fresh one.

        Used on ``session terminated`` / 404 errors that appear when the
        upstream MCP server restarted and no longer recognises our session
        id. One attempt only; the caller decides whether to retry.
        """
        async with self._conn_lock:
            await self._close_session(logged=False)
            await self._open_session()
        log.info("mcp.session_reopened", url=self._url)

    # ------------------------------------------------------------------
    # self-healing supervisor
    # ------------------------------------------------------------------
    def start_supervisor(self) -> None:
        """Launch the background reconnect watchdog (idempotent)."""
        if self._supervisor is None or self._supervisor.done():
            self._supervisor = asyncio.create_task(
                self._supervise(), name=f"mcp-supervisor:{self._name}"
            )

    async def _stop_supervisor(self) -> None:
        task = self._supervisor
        self._supervisor = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            log.debug("mcp.supervisor_stop_error", url=self._url, exc_info=True)

    async def _supervise(self) -> None:
        """Keep the session alive: while disconnected, retry on a fixed
        cadence and notify the admins once on the way down and once on the
        way back up. The session may also be restored on demand by
        ``call_tool``; the recovery announcement fires regardless of who
        reopened it."""
        while True:
            if self._session is None:
                if not self._down_announced:
                    self._down_announced = True
                    log.warning("mcp.down", url=self._url, name=self._name)
                    await self._emit("down")
                try:
                    await self._ensure_session()
                except (Exception, BaseExceptionGroup) as exc:
                    log.warning(
                        "mcp.reconnect_failed", url=self._url, name=self._name, error=str(exc)
                    )
                    await asyncio.sleep(self._RECONNECT_DELAY)
                    continue
            if self._down_announced and self._session is not None:
                self._down_announced = False
                log.info("mcp.reconnected", url=self._url, name=self._name)
                await self._emit("up")
            await asyncio.sleep(self._RECONNECT_DELAY)

    async def _emit(self, event: str) -> None:
        if self._notifier is None:
            return
        try:
            await self._notifier(event, self._name)
        except Exception:
            log.warning("mcp.notify_failed", url=self._url, name=self._name, exc_info=True)

    # ------------------------------------------------------------------
    # tool calls
    # ------------------------------------------------------------------
    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        tg_user_id: int | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Invoke an MCP tool and return its parsed JSON payload.

        Raises :class:`MCPClientError` on transport / protocol failures,
        including a server that never answers within ``timeout_seconds``
        (default :attr:`_CALL_TIMEOUT`) — no caller is left waiting forever.
        Tool-level errors (returned as ``{"error": {...}}`` in the payload)
        are passed through to the caller unchanged so they can be rendered
        to the end user.
        """
        budget = timeout_seconds if timeout_seconds is not None else self._CALL_TIMEOUT
        read_timeout = timedelta(seconds=budget)
        if self._session is None:
            # Disconnected (failed startup or a dropped link). Try once,
            # right now, so a user action doesn't have to wait for the
            # supervisor's next tick. If still down, surface a transport
            # error the caller already knows how to degrade on.
            try:
                await self._ensure_session()
            except (Exception, BaseExceptionGroup) as exc:
                raise MCPClientError(f"{self._name} is not connected: {exc}") from exc

        started = time.perf_counter()
        error: str | None = None
        payload: dict[str, Any] | None = None
        try:
            try:
                assert self._session is not None
                result = await self._session.call_tool(
                    name, arguments, read_timeout_seconds=read_timeout
                )
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
                result = await self._session.call_tool(
                    name, arguments, read_timeout_seconds=read_timeout
                )
            payload = _extract_payload(result)
            return payload
        except (Exception, BaseExceptionGroup) as exc:
            # BaseExceptionGroup (from the transport's anyio task group) is not
            # an Exception; catch it too so a transport blow-up reaches the
            # caller as a plain MCPClientError instead of an opaque group.
            error = f"{type(exc).__name__}: {exc}"
            if _is_request_timeout(exc):
                # A slow tool, not a dead link — keep the session.
                log.warning("mcp.call_timeout", url=self._url, tool=name, error=error)
            elif _is_disconnect(exc):
                # The link looks dead — drop the session so the supervisor
                # picks it up, reconnects, and announces recovery.
                await self._close_session(logged=False)
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
    first text content item. When the server sets ``isError=True`` the content
    is plain text, not JSON — we re-raise it as an MCPClientError so callers
    see a readable message instead of a parse failure.
    """
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and structured:
        return structured

    is_error = bool(getattr(result, "isError", False))
    content = getattr(result, "content", None) or []
    texts: list[str] = []

    for item in content:
        text = getattr(item, "text", None)
        if not isinstance(text, str):
            continue
        texts.append(text)
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, dict):
            return decoded

    if is_error:
        raise MCPClientError(" ".join(texts) or "tool returned an error")

    log.warning("mcp.payload_unreadable", content_texts=[t[:120] for t in texts])
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


def _is_request_timeout(exc: BaseException) -> bool:
    """True for 'the server didn't answer in time' — our own read timeout.

    The MCP SDK raises this as an ``McpError`` carrying the message it
    builds in ``send_request``. It says nothing about the health of the
    link, so it must not be mistaken for a disconnect (which would drop a
    perfectly good session every time a tool runs long).
    """
    return "timed out while waiting for response" in str(exc).lower()


def _is_disconnect(exc: BaseException) -> bool:
    """Broad heuristic for 'the link to the server is gone'.

    A true positive costs one extra reconnect (the supervisor reopens the
    session); a false positive on a genuine tool error is harmless because
    tool-level errors come back as payloads, not as exceptions from
    ``call_tool``. So we err on the inclusive side.
    """
    if _is_session_terminated(exc):
        return True
    text = str(exc).lower()
    keywords = (
        "connect",
        "connection",
        "timeout",
        "timed out",
        "disconnect",
        "reset",
        "broken pipe",
        "unreachable",
        "refused",
        "task group",
        "taskgroup",
        "eof",
    )
    if any(k in text for k in keywords):
        return True
    type_name = type(exc).__name__.lower()
    return "connect" in type_name or "timeout" in type_name or "disconnect" in type_name


class MovieMetadataMCPClient(BaseMCPClient):
    """Typed client for movie-metadata-mcp. Kept as a distinct class so tools
    that should only see the metadata server can't accidentally be given the
    trailer one (or vice versa)."""
