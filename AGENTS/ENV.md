# Environment

Repo-local env for `movie-handler-clients`. Cross-repo host facts, deploy
recipes, and credential layout live in **`../AGENTS/ENV.md`** — read that for
host details (bot/current server `r1117636`, media host `homesrv` / public
`v.wildcar.ru`), tool
versions, and the prod `git pull --ff-only` workflow. This file is only the
repo-specific bits.

## Deploy target

Runs on the **bot host** (`r1117636`, this current/dev server) as the
`movie-handler-telegram` systemd unit
(user `movie`, `/opt/movie-handler-clients`). Long-polling — no inbound port.
Reaches the bot-host MCPs over `127.0.0.1` and the media-host MCPs / media-watch
over `wildcar.ru`. Systemd units + `update.sh` are in `deploy/`.

## Environment variables

Loaded by `pydantic-settings` from `.env` (gitignored). See `.env.example`.

| Name | Required | Default | Description |
|------|:--------:|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | ✅ | — | Bot token from @BotFather. |
| `MCP_AUTH_TOKEN` | ✅ | — | Bearer token; must match every MCP server. |
| `MOVIE_METADATA_MCP_URL` | | `http://127.0.0.1:8765/mcp` | Metadata MCP. |
| `MOVIE_TRAILER_MCP_URL` | | `http://127.0.0.1:8766/mcp` | Trailer MCP. |
| `RUTRACKER_TORRENT_MCP_URL` | | `http://127.0.0.1:8767/mcp` | rutracker MCP. |
| `RTORRENT_MCP_URL` | | unset | rtorrent MCP. When unset, the picked .torrent is sent to the user as a Telegram document instead of pushed. |
| `YT_DLP_MCP_URL` | | unset | yt-dlp MCP. When unset, the pasted-URL download flow is disabled. |
| `MEDIA_WATCH_BASE_URL` | | unset | media-watch-web base URL. When unset, completions notify but skip register. |
| `MEDIA_WATCH_API_TOKEN` | | unset | Bearer for media-watch-web `/api/*`. |
| `STATE_DB_PATH` | | `.cache/state.sqlite` | Users / downloads / watch records (survives restarts). |
| `LOG_DB_PATH` | | `.cache/mcp_traffic.sqlite` | MCP request/response trace log. |
| `LOG_TTL_DAYS` | | `30` | Lazy-purge cutoff for the traffic log. |
| `ADMIN_TELEGRAM_IDS` | | unset | CSV of Telegram ids flagged `is_admin` on next interaction. Demotion is not automatic. |
| `LOG_LEVEL` | | `INFO` | structlog filter level. |

## Notes

- Never commit a real `.env`. `.gitignore` already covers `.env`, `.cache/`,
  `*.sqlite`, `.venv/`, and the lint/type/test caches.
- Self-healing MCP clients (see `AGENTS/MEMORY.md`): an unset URL disables that
  client; a set-but-unreachable URL is a live client that reconnects in the
  background and degrades gracefully meanwhile.
