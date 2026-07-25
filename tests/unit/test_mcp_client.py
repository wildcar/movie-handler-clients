"""Session lifecycle of :class:`BaseMCPClient`.

The transport is faked, but faked *faithfully*: the real streamable-HTTP
client wraps an anyio task group, and anyio pins a task group's cancel scope
to the task that entered it. Exiting it from another task is what took the
bot down on 2026-07-25 — a stale-session reconnect ran inside a Telegram
handler task and the cancellation landed in the bot's main task instead.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, ClassVar

import anyio
import pytest

from movie_handler_clients.core import mcp_client as mcp_client_mod
from movie_handler_clients.core.mcp_client import BaseMCPClient, MCPClientError
from movie_handler_clients.core.yt_dlp_client import YtDlpMCPClient


class FakeTrafficLog:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def record(self, **kwargs: Any) -> None:
        self.rows.append(kwargs)


class FakeResult:
    isError = False
    content: ClassVar[list[Any]] = []

    def __init__(self, payload: dict[str, Any]) -> None:
        self.structuredContent = payload


class FakeSession:
    """Stand-in for ``mcp.ClientSession``, recording call kwargs."""

    instances: ClassVar[list[FakeSession]] = []

    def __init__(self, read: Any, write: Any) -> None:
        self.initialized = False
        self.calls: list[tuple[str, dict[str, Any], Any]] = []
        FakeSession.instances.append(self)

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def initialize(self) -> None:
        self.initialized = True

    async def call_tool(
        self, name: str, arguments: dict[str, Any], read_timeout_seconds: Any = None
    ) -> FakeResult:
        self.calls.append((name, arguments, read_timeout_seconds))
        return FakeResult({"ok": True})


@asynccontextmanager
async def fake_transport(url: str, headers: dict[str, str] | None = None) -> AsyncIterator[Any]:
    """Mirror the shape that matters in ``streamablehttp_client``.

    A task group with a live child (there, the GET event-stream reader), torn
    down by cancelling the group's scope. The scope belongs to the task that
    entered the context manager, so cancelling it from anywhere else delivers
    the cancellation to *that* task — the production failure mode.
    """
    async with anyio.create_task_group() as tg:

        async def idle() -> None:
            await anyio.sleep_forever()

        tg.start_soon(idle)
        try:
            yield (None, None, None)
        finally:
            tg.cancel_scope.cancel()


@pytest.fixture(autouse=True)
def _patch_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSession.instances.clear()
    monkeypatch.setattr(mcp_client_mod, "streamablehttp_client", fake_transport)
    monkeypatch.setattr(mcp_client_mod, "ClientSession", FakeSession)


def _client() -> BaseMCPClient:
    return BaseMCPClient(
        url="http://localhost:1/mcp",
        auth_token="token",
        traffic_log=FakeTrafficLog(),  # type: ignore[arg-type]
        name="fake",
    )


async def test_reconnect_from_another_task_leaves_the_caller_alive() -> None:
    client = _client()
    await client._open_session()
    first = client._session

    # A Telegram handler task hitting a stale session id does this.
    await asyncio.create_task(client._reconnect())

    assert client.connected
    assert client._session is not first
    # The task that opened the session (this one) must be untouched: if the
    # cancellation had leaked, the await below would raise CancelledError.
    await asyncio.sleep(0)
    await client._close_session(logged=False)
    assert not client.connected


async def test_close_from_another_task_leaves_the_caller_alive() -> None:
    client = _client()
    await client._open_session()

    await asyncio.create_task(client._close_session(logged=True))

    assert not client.connected
    await asyncio.sleep(0)


async def test_failed_handshake_leaves_no_session_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()

    async def failing_initialize(self: FakeSession) -> None:
        raise RuntimeError("handshake refused")

    monkeypatch.setattr(FakeSession, "initialize", failing_initialize)

    # The transport's task group re-raises as a group; callers of
    # _open_session catch both shapes.
    with pytest.raises((RuntimeError, BaseExceptionGroup)) as excinfo:
        await client._open_session()
    assert "handshake refused" in str(excinfo.value.exceptions[0])  # type: ignore[union-attr]

    assert not client.connected
    assert client._session_task is None


async def test_call_tool_passes_a_read_timeout() -> None:
    client = _client()
    await client._open_session()
    try:
        assert await client.call_tool("ping", {}) == {"ok": True}
        _, _, timeout = FakeSession.instances[-1].calls[-1]
        assert timeout is not None
        assert timeout.total_seconds() == BaseMCPClient._CALL_TIMEOUT

        await client.call_tool("ping", {}, timeout_seconds=5.0)
        _, _, timeout = FakeSession.instances[-1].calls[-1]
        assert timeout.total_seconds() == 5.0
    finally:
        await client._close_session(logged=False)


async def test_yt_dlp_probe_uses_the_metadata_timeout() -> None:
    client = YtDlpMCPClient(
        url="http://localhost:1/mcp",
        auth_token="token",
        traffic_log=FakeTrafficLog(),  # type: ignore[arg-type]
    )
    await client._open_session()
    try:
        await client.probe("https://www.1tv.ru/-/skrlsx")
        _, _, timeout = FakeSession.instances[-1].calls[-1]
        assert timeout.total_seconds() == YtDlpMCPClient._METADATA_TIMEOUT
    finally:
        await client._close_session(logged=False)


async def test_request_timeout_is_reported_without_dropping_the_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slow tool is not a dead link — the session must survive it."""
    client = _client()
    await client._open_session()
    session = client._session

    async def timing_out(*args: Any, **kwargs: Any) -> FakeResult:
        # Verbatim shape of the MCP SDK's read-timeout error.
        raise RuntimeError(
            "Timed out while waiting for response to CallToolRequest. Waited 40 seconds."
        )

    monkeypatch.setattr(FakeSession, "call_tool", timing_out)

    with pytest.raises(MCPClientError, match="Timed out"):
        await client.call_tool("probe", {"url": "x"})

    assert client._session is session
    await client._close_session(logged=False)
