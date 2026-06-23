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
- Harness migrated to the `agent-template` layout.

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
