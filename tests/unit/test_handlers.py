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
    assert "Привет" in body


async def test_on_text_empty_short_circuits() -> None:
    msg = _msg("   ")
    mcp = AsyncMock()
    cache = SearchCache()
    await search_mod.on_text(msg, mcp=mcp, search_cache=cache)  # type: ignore[arg-type]
    mcp.call_tool.assert_not_awaited()
    msg.answer.assert_awaited_once()


async def test_on_text_renders_results(sample_search_payload: dict) -> None:
    msg = _msg("Dune")
    mcp = AsyncMock()
    mcp.call_tool = AsyncMock(return_value=sample_search_payload)
    cache = SearchCache()

    await search_mod.on_text(msg, mcp=mcp, search_cache=cache)  # type: ignore[arg-type]

    mcp.call_tool.assert_awaited_once_with("search_movie", {"title": "Dune"}, tg_user_id=42)
    msg.answer.assert_awaited_once()
    kwargs = msg.answer.call_args.kwargs
    args = msg.answer.call_args.args
    body = args[0] if args else kwargs.get("text", "")
    assert "Dune" in body
    assert kwargs["parse_mode"] == "HTML"
    assert kwargs["reply_markup"].inline_keyboard  # one button per hit


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


async def test_trailer_callback_is_stub() -> None:
    cq = SimpleNamespace(data="t:tt1160419", answer=AsyncMock())
    await details_mod.on_trailer_stub(cq)  # type: ignore[arg-type]
    cq.answer.assert_awaited_once()
    (body,), kwargs = cq.answer.call_args
    assert "версии" in body
    assert kwargs.get("show_alert") is True


async def test_download_callback_is_stub() -> None:
    cq = SimpleNamespace(data="dl:tt1160419", answer=AsyncMock())
    await details_mod.on_download_stub(cq)  # type: ignore[arg-type]
    cq.answer.assert_awaited_once()
