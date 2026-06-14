"""Handler tests — call the functions directly with mocked Message/CQ objects.

aiogram 3 has no first-party test harness, so we stub the Bot/Message surface
with ``unittest.mock`` rather than running a real Dispatcher.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from movie_handler_clients.telegram.handlers import details as details_mod
from movie_handler_clients.telegram.handlers import search as search_mod
from movie_handler_clients.telegram.search_cache import SearchCache


def _cq_user(user_id: int = 42) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        first_name="Test",
        last_name="User",
        username="test_user",
    )


def _cq_message(chat_id: int = 100) -> SimpleNamespace:
    return SimpleNamespace(
        answer_document=AsyncMock(),
        answer=AsyncMock(),
        chat=SimpleNamespace(id=chat_id),
    )


def _msg(text: str, user_id: int = 42) -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        from_user=SimpleNamespace(id=user_id),
        answer=AsyncMock(),
    )


async def test_on_start_sends_greeting() -> None:
    msg = _msg("/start")
    await search_mod.on_start(msg)  # type: ignore[arg-type]
    msg.answer.assert_awaited_once()
    (body,), _ = msg.answer.call_args
    # Greeting copy updated 2026-04-27 — check for the stable
    # «Помогу найти…» opening rather than a single short word.
    assert "Помогу" in body
    assert "плейлист" in body


async def test_on_text_empty_short_circuits() -> None:
    msg = _msg("   ")
    mcp = AsyncMock()
    cache = SearchCache()
    await search_mod.on_text(msg, mcp=mcp, search_cache=cache)  # type: ignore[arg-type]
    mcp.call_tool.assert_not_awaited()
    msg.answer.assert_awaited_once()


def test_clean_query_strips_quotes_and_detects_kind() -> None:
    assert search_mod._clean_query("Сериал «Отыграть назад»") == (
        "Отыграть назад",
        "series",
    )
    assert search_mod._clean_query('фильм "Дюна"') == ("Дюна", "movie")
    assert search_mod._clean_query("Матрица шоу") == ("Матрица", "series")
    assert search_mod._clean_query("Dune") == ("Dune", None)
    assert search_mod._clean_query("  «  »  ") == ("", None)


def test_split_title_year_extracts_4digit_year() -> None:
    assert search_mod._split_title_year("Дюна 2021") == ("Дюна", 2021)
    assert search_mod._split_title_year("Dune, 2021") == ("Dune", 2021)
    assert search_mod._split_title_year("Blade Runner 1982 director's cut") == (
        "Blade Runner director's cut",
        1982,
    )
    assert search_mod._split_title_year("Dune") == ("Dune", None)
    assert search_mod._split_title_year("Room 237") == ("Room 237", None)  # 3 digits


async def test_on_text_shows_error_when_mcp_fails() -> None:
    msg = _msg("Dune")
    mcp = AsyncMock()
    mcp.call_tool = AsyncMock(side_effect=_MCPErr("boom"))
    cache = SearchCache()

    await search_mod.on_text(msg, mcp=mcp, search_cache=cache)  # type: ignore[arg-type]

    msg.answer.assert_awaited_once()
    (body,), _ = msg.answer.call_args
    assert "boom" in body


class _MCPErr(Exception):
    pass


# patch MCPClientError symbol so the handler catches our stub class
@pytest.fixture(autouse=True)
def _patch_mcp_err(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(search_mod, "MCPClientError", _MCPErr)
    monkeypatch.setattr(details_mod, "MCPClientError", _MCPErr)


async def test_base_mcp_client_reconnects_on_session_terminated() -> None:
    """A stale session id should trigger one silent reconnect + retry."""
    from unittest.mock import MagicMock

    from movie_handler_clients.core import mcp_client as mc

    client = mc.BaseMCPClient(url="http://127.0.0.1:0/mcp", auth_token="t", traffic_log=AsyncMock())
    # Mark as entered — we're not actually going over the wire.
    client._session = AsyncMock()
    client._stack = MagicMock()
    client._stack.aclose = AsyncMock()

    call_results = [Exception("Session terminated"), MagicMock(structuredContent={"ok": True})]
    client._session.call_tool = AsyncMock(side_effect=call_results)

    reopen_called: list[int] = []

    async def fake_open() -> None:
        reopen_called.append(1)
        client._session = AsyncMock()
        client._session.call_tool = AsyncMock(return_value=call_results[1])
        client._stack = MagicMock()
        client._stack.aclose = AsyncMock()

    monkey_target = "_open_session"
    import types

    client._open_session = types.MethodType(lambda self: fake_open(), client)  # type: ignore[assignment]
    _ = monkey_target

    payload = await client.call_tool("x", {"a": 1}, tg_user_id=None)
    assert payload == {"ok": True}
    assert reopen_called == [1]


async def test_client_startup_is_nonfatal_when_server_down() -> None:
    """An unreachable server at startup must not raise — the client begins
    disconnected and recovers later."""
    import types

    from movie_handler_clients.core import mcp_client as mc

    client = mc.BaseMCPClient(
        url="http://127.0.0.1:0/mcp", auth_token="t", traffic_log=AsyncMock(), name="X"
    )

    async def boom() -> None:
        raise ConnectionError("connection refused")

    client._open_session = types.MethodType(lambda self: boom(), client)  # type: ignore[assignment]

    async with client:  # must not raise
        assert client.connected is False


async def test_call_tool_raises_mcp_error_while_disconnected() -> None:
    """A disconnected client surfaces a transport error callers can degrade
    on, not a bare RuntimeError or crash."""
    import types

    from movie_handler_clients.core import mcp_client as mc

    client = mc.BaseMCPClient(url="u", auth_token="t", traffic_log=AsyncMock(), name="X")

    async def boom() -> None:
        raise ConnectionError("connection refused")

    client._open_session = types.MethodType(lambda self: boom(), client)  # type: ignore[assignment]

    with pytest.raises(mc.MCPClientError):
        await client.call_tool("x", {})


async def test_supervisor_heals_and_notifies_down_then_up() -> None:
    """While down the supervisor retries and announces 'down' once; on
    recovery it announces 'up' once — in that order."""
    import asyncio
    import types

    from movie_handler_clients.core import mcp_client as mc

    events: list[tuple[str, str]] = []
    up = asyncio.Event()

    async def notifier(event: str, name: str) -> None:
        events.append((event, name))
        if event == "up":
            up.set()

    client = mc.BaseMCPClient(url="u", auth_token="t", traffic_log=AsyncMock(), name="X")
    client._RECONNECT_DELAY = 0  # type: ignore[assignment]
    client.set_notifier(notifier)

    attempts = {"n": 0}

    async def open_session() -> None:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ConnectionError("not yet")
        client._session = AsyncMock()

    client._open_session = types.MethodType(lambda self: open_session(), client)  # type: ignore[assignment]

    assert client._session is None
    client.start_supervisor()
    await asyncio.wait_for(up.wait(), timeout=2)
    await client._stop_supervisor()

    assert ("down", "X") in events
    assert ("up", "X") in events
    assert events.index(("down", "X")) < events.index(("up", "X"))
