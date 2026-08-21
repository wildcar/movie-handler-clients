"""Unit tests for the Cloudflare-challenge hand-off."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from movie_handler_clients.telegram import challenge


class _FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, Any]] = []

    async def send_message(self, chat_id: int, text: str, reply_markup: Any = None) -> None:
        self.sent.append((chat_id, text, reply_markup))


class _FakeMessage:
    def __init__(self) -> None:
        self.answers: list[tuple[str, Any]] = []
        self.edits: list[tuple[str, Any]] = []
        self.bot = _FakeBot()

    async def answer(self, text: str, reply_markup: Any = None) -> None:
        self.answers.append((text, reply_markup))

    async def edit_text(self, text: str, reply_markup: Any = None) -> None:
        self.edits.append((text, reply_markup))


class _FakeSettings:
    def __init__(self, base: str | None, token_path: str | None) -> None:
        self.rutracker_challenge_url_base = base
        self.rutracker_challenge_token_path = token_path


def _configure(
    monkeypatch: pytest.MonkeyPatch, base: str | None, token_path: str | None
) -> None:
    monkeypatch.setattr(
        challenge, "get_settings", lambda: _FakeSettings(base, token_path)
    )


@pytest.mark.asyncio
async def test_non_challenge_code_is_not_consumed(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, "https://rtcc.example", "/nonexistent/token")
    msg = _FakeMessage()
    err = {"code": "upstream_error", "message": "boom"}
    assert not await challenge.maybe_handle_challenge(msg, err, 1, {1})  # type: ignore[arg-type]
    assert msg.answers == []


@pytest.mark.asyncio
async def test_unconfigured_feature_falls_through(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, None, None)
    msg = _FakeMessage()
    err = {"code": "cloudflare_challenge", "message": "..."}
    assert not await challenge.maybe_handle_challenge(msg, err, 1, {1})  # type: ignore[arg-type]
    assert msg.answers == []


@pytest.mark.asyncio
async def test_admin_gets_button_and_token_is_written(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    token_file = tmp_path / "token"
    _configure(monkeypatch, "https://rtcc.example/", str(token_file))
    msg = _FakeMessage()
    err = {"code": "cloudflare_challenge", "message": "..."}
    assert await challenge.maybe_handle_challenge(msg, err, 42, {42})  # type: ignore[arg-type]

    token = token_file.read_text(encoding="utf-8").strip()
    assert token
    [(_text, markup)] = msg.answers
    assert markup is not None
    button = markup.inline_keyboard[0][0]
    assert button.url == f"https://rtcc.example/enter/{token}"
    # admin already has the button — no fan-out
    assert msg.bot.sent == []


@pytest.mark.asyncio
async def test_regular_user_waits_and_admins_get_the_link(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    token_file = tmp_path / "token"
    _configure(monkeypatch, "https://rtcc.example", str(token_file))
    msg = _FakeMessage()
    err = {"code": "manual_auth_required", "message": "..."}
    assert await challenge.maybe_handle_challenge(msg, err, 7, {1, 2})  # type: ignore[arg-type]

    [(_text, markup)] = msg.answers
    assert markup is None  # no button for non-admins
    assert {chat_id for chat_id, _, _ in msg.bot.sent} == {1, 2}
    token = token_file.read_text(encoding="utf-8").strip()
    for _, _, admin_markup in msg.bot.sent:
        assert admin_markup.inline_keyboard[0][0].url.endswith(f"/enter/{token}")


@pytest.mark.asyncio
async def test_edit_replaces_pending_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure(monkeypatch, "https://rtcc.example", str(tmp_path / "token"))
    msg = _FakeMessage()
    err = {"code": "cloudflare_challenge", "message": "..."}
    assert await challenge.maybe_handle_challenge(msg, err, 42, {42}, edit=True)  # type: ignore[arg-type]
    assert msg.answers == []
    assert len(msg.edits) == 1
