# State

Repo-local snapshot. Overwrite each iteration. Cross-repo view → `../AGENTS/STATE.md`.

## Goal

The client / orchestration hub of `movie_handler`: turn chat or a pasted URL into
search → confirm → download → playback-link, via MCP clients + `state.sqlite`.
Telegram today; web (FastAPI+WS) and VK later.

## Now

- Telegram bot live end to end: all three download entry points (free-text search,
  pasted rutracker URL, pasted yt-dlp URL), 60 s completion poller + ~hourly prune,
  self-healing MCP clients with admin down/up notifications.
- Commands: `/start` `/status` `/list` (user menu); `/notify_toggle` `/global_list`
  (admin); `/whoami` (hidden). `state.sqlite` schema at `user_version=2`.
- Pasted-video errors distinguish unsupported URLs from recognised URLs rejected by
  YouTube anti-bot protection or another upstream extraction failure; download
  confirmation reports a visible error if `start_download` exceeds 45 seconds.
- MCP sessions are owned by a dedicated task each, so a stale-session reconnect
  from a handler task no longer cancels the bot's main task (that crash-looped the
  process on 2026-07-25). Tool calls carry a read timeout (60 s; 40 s for yt-dlp
  metadata), and the «Смотрю видео…» bubble is always resolved — an unexpected
  failure becomes `ydl.internal_error` with a traceback in the log.
- Preview thumbnails survive hosts that Telegram itself can't fetch: the bot
  downloads the image and uploads the bytes, then degrades to a text-only card.
  The bubble is deleted only after the photo card is accepted.
- Harness migrated to the `agent-template` layout.
- Cloudflare-challenge hand-off: on `cloudflare_challenge` / `manual_auth_required`
  from rutracker the bot mints a one-time token (30 min, file shared with
  `challenge-gate` on the same host) and sends admins a «Пройти проверку» button
  linking to the gated noVNC page (`https://rtcc.wildcar.org/enter/<token>`).
  Non-admins get a "wait" note; feature off when the two env vars are unset.

## Next

- (when needed) Web (FastAPI + WebSocket) frontend in this repo.
- (when needed) VK (`vkbottle`) frontend in this repo.

## Open questions

- —

## Deferred

- **`/reretry` admin command.** After 5 failed `/api/register` attempts a row is
  `register_failed` and the bot stops. Add `/reretry [<info_hash>]` in
  `telegram/handlers/` resetting matching rows to
  `complete_pending_register, register_attempts=0` (admin can reset any row, users
  only their own) + a `state_db.reset_register_state(download_id)` repo method. The
  60 s poller then picks them up automatically.
