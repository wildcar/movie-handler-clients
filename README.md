# movie-handler-clients

Frontend clients for the **movie_handler** system. Currently ships the
**Telegram bot** (aiogram 3.x, long-polling); the web and VK clients will be
added in later iterations.

The bot implements a simple algorithmic workflow — it does **not** wrap an
LLM agent. It talks to `movie-metadata-mcp` over streamable-HTTP and will
talk to the trailer / rutracker / rtorrent MCPs once those repos ship.

## Workflow

```
/start                → greeting + prompt
free text             → search_movie via MCP → numbered list of hits
tap a hit             → get_movie_details via MCP → poster + ratings + overview
  [🎬 Трейлер]         → stub: "функция появится в следующей версии"
  [⬇️ Скачать]         → stub: "функция появится в следующей версии"
  [← К списку]        → re-render the previous search
```

Every MCP call is logged to SQLite (`LOG_DB_PATH`, 30-day TTL by default).

## Stack

- Python ≥ 3.11, `asyncio`, `uv` package manager
- `aiogram` 3.x (Telegram)
- Official Anthropic `mcp` SDK as an MCP **client** (streamable-HTTP)
- `pydantic` v2 + `pydantic-settings` for config
- `aiosqlite` for the traffic log
- `structlog` JSON logging to stderr

## Repo layout

```
src/movie_handler_clients/
    core/
        config.py         # Settings (env + .env)
        logging_conf.py   # structlog bootstrap
        i18n.py           # Russian strings (future switcher)
        formatters.py     # HTML rendering for Telegram
        mcp_client.py     # streamable-HTTP MCP client + traffic log
        traffic_log.py    # SQLite request/response log, TTL purge
    telegram/
        bot.py            # entrypoint
        keyboards.py
        search_cache.py   # in-process cache for "← back to list"
        handlers/
            search.py     # /start + free-text search
            details.py    # details card + trailer/download/back callbacks
deploy/
    movie-metadata-mcp.service
    movie-handler-telegram.service
    README.md
tests/
    unit/                 # handlers, formatters, traffic log, cache
    integration/          # Telegram getMe + MCP tools/list (opt-in)
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

## Environment variables

| Name                       | Required | Default                       | Description                                        |
|----------------------------|:--------:|-------------------------------|----------------------------------------------------|
| `TELEGRAM_BOT_TOKEN`       | ✅       | —                             | Bot token from @BotFather.                         |
| `MOVIE_METADATA_MCP_URL`   |          | `http://127.0.0.1:8765/mcp`   | Streamable-HTTP URL of the metadata MCP server.    |
| `MCP_AUTH_TOKEN`           | ✅       | —                             | Bearer token; must match the MCP server.           |
| `LOG_DB_PATH`              |          | `.cache/mcp_traffic.sqlite`   | Where MCP call traces are stored.                  |
| `LOG_TTL_DAYS`             |          | `30`                          | Lazy-purge cutoff for the traffic log.             |
| `LOG_LEVEL`                |          | `INFO`                        | structlog filter level.                            |

See `.env.example` for how to obtain each secret.

## Tests

```bash
uv run pytest                      # unit tests
uv run pytest -m integration       # live Telegram getMe + running MCP probe
```

Integration tests auto-skip when the relevant credentials are missing, so
they are safe to run anywhere.

## Deployment (systemd)

See [`deploy/README.md`](deploy/README.md) for the full recipe. Both
services run on a single Ubuntu host; the bot reaches the MCP server over
`127.0.0.1`.

## Language rules

Source code, comments, and docs are in English. End-user UI strings live
in `core/i18n.py` (Russian) and are keyed so a future language selector
can swap them out without touching handlers.
