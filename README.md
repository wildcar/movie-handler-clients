# movie-handler-clients

Frontend clients for the **movie_handler** system. Currently ships the
**Telegram bot** (aiogram 3.x, long-polling); the web and VK clients will be
added in later iterations.

The bot implements a simple algorithmic workflow — it does **not** wrap an
LLM agent. It is a streamable-HTTP MCP client of the metadata, trailer,
rutracker, rtorrent, and yt-dlp servers, and registers finished downloads
with `media-watch-web` for browser playback.

## Workflow

Three ways to start a download:

```
free text             → search_movie → list → get_movie_details → card:
  [🎬 Трейлер]         → find_trailer (movie-trailer-mcp)
  [⬇️ Скачать]         → rutracker search → pick a release → confirm
                         (series: season picker first)
  [← К списку]        → re-render the previous search
rutracker topic URL   → get_topic_info → match metadata → confirm
video URL (YouTube/…) → probe (yt-dlp-mcp) → preview card → confirm
```

A confirmed pick is pushed to `rtorrent-mcp` (torrents) or `yt-dlp-mcp`
(video URLs). A 60-second poller follows each download to completion, then
registers it with `media-watch-web` and sends the user a watch link.

Commands: `/start` (what the bot can do), `/status` (in-flight downloads with
a progress bar), `/list` (your library). `/whoami` works but is hidden from
the menu; admins also get `/notify_toggle` and `/global_list`.

Every MCP call is logged to SQLite (`LOG_DB_PATH`, 30-day TTL); users,
downloads, and watch records persist separately in `STATE_DB_PATH`.

## Stack

- Python ≥ 3.11, `asyncio`, `uv` package manager
- `aiogram` 3.x (Telegram)
- Official Anthropic `mcp` SDK as an MCP **client** (streamable-HTTP)
- `pydantic` v2 + `pydantic-settings` for config
- `aiosqlite` for the traffic log and the state store
- `structlog` JSON logging to stderr

## Repo layout

```
src/movie_handler_clients/
    core/
        config.py            # Settings (env + .env)
        logging_conf.py      # structlog bootstrap
        i18n.py              # Russian strings (future switcher)
        formatters.py        # HTML rendering for Telegram
        mcp_client.py        # self-healing streamable-HTTP MCP client base
        traffic_log.py       # SQLite request/response log, TTL purge
        state_db.py          # users, downloads, watch records (STATE_DB_PATH)
        trailer_client.py    # movie-trailer-mcp client
        torrent_client.py    # rutracker-torrent-mcp client
        rtorrent_client.py   # rtorrent-mcp client
        yt_dlp_client.py     # yt-dlp-mcp client
        media_watch_client.py # media-watch-web /api/register client
    telegram/
        bot.py               # entrypoint + 60s completion poller
        keyboards.py
        *_cache.py           # in-process caches (search, title, torrent, …)
        handlers/
            search.py        # /start + free-text search
            details.py       # details card + trailer/download/back
            rutracker_url.py # pasted rutracker topic URL
            youtube_url.py   # pasted video URL → yt-dlp
            status.py        # /status
            list.py          # /list
            admin.py         # /notify_toggle, /global_list
            whoami.py        # /whoami (hidden)
deploy/                      # systemd units
tests/
    unit/                    # handlers, formatters, traffic log, caches
    integration/             # Telegram getMe + MCP tools/list (opt-in)
```

## Local setup

```bash
# 1. Fill secrets
cp .env.example .env
$EDITOR .env

# 2. Install deps (Python ≥ 3.11, uv ≥ 0.11.7)
uv sync --frozen

# 3. Start movie-metadata-mcp in streamable-HTTP mode in another terminal
#    (see wildcar/movie-metadata-mcp). Example:
#      MCP_TRANSPORT=streamable-http FASTMCP_PORT=8765 uv run movie-metadata-mcp

# 4. Run the Telegram bot
uv run movie-handler-telegram
```

The bot starts even when the optional MCP servers are unreachable: their
clients begin disconnected and a background supervisor reconnects on a timer,
so features light up as each server comes online.

## Environment variables

| Name                        | Required | Default                       | Description                                                          |
|-----------------------------|:--------:|-------------------------------|----------------------------------------------------------------------|
| `TELEGRAM_BOT_TOKEN`        | ✅       | —                             | Bot token from @BotFather.                                           |
| `MCP_AUTH_TOKEN`            | ✅       | —                             | Bearer token sent to every MCP server; must match the servers.      |
| `MOVIE_METADATA_MCP_URL`    |          | `http://127.0.0.1:8765/mcp`   | metadata MCP (search + details).                                     |
| `MOVIE_TRAILER_MCP_URL`     |          | `http://127.0.0.1:8766/mcp`   | trailer MCP.                                                         |
| `RUTRACKER_TORRENT_MCP_URL` |          | `http://127.0.0.1:8767/mcp`   | rutracker MCP.                                                       |
| `RTORRENT_MCP_URL`          |          | — (unset)                     | rtorrent MCP on the media host. Unset → send the `.torrent` to the user instead of pushing it. |
| `YT_DLP_MCP_URL`            |          | — (unset)                     | yt-dlp MCP on the media host. Unset → pasted video URLs are rejected.|
| `MEDIA_WATCH_BASE_URL`      |          | — (unset)                     | media-watch-web base URL. Unset → completions skip registration.     |
| `MEDIA_WATCH_API_TOKEN`     |          | — (unset)                     | Bearer token for media-watch-web `/api/register`.                    |
| `STATE_DB_PATH`             |          | `.cache/state.sqlite`         | Users, downloads, watch records (persists across restarts).          |
| `LOG_DB_PATH`               |          | `.cache/mcp_traffic.sqlite`   | MCP request/response traffic log.                                    |
| `LOG_TTL_DAYS`              |          | `30`                          | Lazy-purge cutoff for the traffic log.                               |
| `LOG_LEVEL`                 |          | `INFO`                        | structlog filter level.                                              |
| `ADMIN_TELEGRAM_IDS`        |          | — (unset)                     | Comma-separated Telegram ids promoted to admin on next interaction.  |

See `.env.example` for how to obtain each secret.

## Tests

```bash
uv run pytest                      # unit tests
uv run pytest -m integration       # live Telegram getMe + running MCP probe
```

Integration tests auto-skip when the relevant credentials are missing, so
they are safe to run anywhere.

## Deployment (systemd)

See [`deploy/README.md`](deploy/README.md) for the full recipe. The bot and
the bot-host MCP servers run on one Ubuntu host (reached over `127.0.0.1`);
the rtorrent / yt-dlp MCPs and `media-watch-web` live on the media host.

## Language rules

Source code, comments, and docs are in English. End-user UI strings live
in `core/i18n.py` (Russian) and are keyed so a future language selector
can swap them out without touching handlers.
