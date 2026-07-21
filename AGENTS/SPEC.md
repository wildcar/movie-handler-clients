# movie-handler-clients — functional & technical specification

Source of truth for *what this repo does* and *how it is built*. Cross-repo
contract (the MCP servers, end-to-end flows across hosts, `media_id` shape) lives
in `../AGENTS/SPEC.md`; this document is the client-side detail.

## Purpose

The client / orchestration hub of `movie_handler`. It turns natural-language chat
("Найди фильм *Дюна 2* и скачай его") or a pasted video/torrent URL into a
search → confirm → download → playback-link flow, by calling the MCP servers over
streamable-HTTP and tracking everything in `state.sqlite`. Today it is an
**aiogram 3.x Telegram bot** (long-polling). It is an MCP **client**, not a server,
and runs a deterministic algorithmic workflow — no LLM agent.

## Stack

- Python ≥ 3.11, `asyncio`, `uv`.
- `aiogram` 3.x (Telegram, long-polling).
- Official Anthropic `mcp` SDK as a streamable-HTTP **client**.
- `pydantic` v2 + `pydantic-settings` (config/secrets via `.env`).
- `structlog` (JSON in prod, to stderr).
- Persistence: stdlib `sqlite3` (`check_same_thread=False` + one lock) for
  `state.sqlite`; `aiosqlite` for the MCP traffic log.
- Lint/types/tests: `ruff`, `mypy --strict`, `pytest` + `pytest-asyncio` + `respx`.

## Shared `core/`

Platform-agnostic so future web/VK frontends reuse it:

- `config.py` — `Settings` from env + `.env`.
- `logging_conf.py` — `structlog` bootstrap.
- `i18n.py` — Russian UI strings via `t(key, **kwargs)`, keyed for a future
  language switcher (handlers never embed literal Russian).
- `formatters.py` — HTML rendering for Telegram (poster cards, rating links to
  TMDB/IMDb/КиноПоиск, progress bars, size/seeders/resolution labels).
- `mcp_client.py` — `BaseMCPClient`: **self-healing** streamable-HTTP MCP client.
  Starts disconnected (a failed connect at startup is non-fatal); a background
  supervisor reconnects every 15 s; `call_tool` reconnects on demand and drops the
  session on a disconnect-looking error so the supervisor heals it. Pings an
  optional notifier `down` once on drop and `up` once on recovery. Exposes
  `name`, `connected`, `set_notifier`, `start_supervisor`.
- Per-server thin wrappers: `trailer_client.py`, `torrent_client.py` (rutracker),
  `rtorrent_client.py`, `yt_dlp_client.py`, `media_watch_client.py` (httpx client
  for media-watch-web `/api/register`, `/api/records`).
- `state_db.py` — the `state.sqlite` repository facade (see Persistent state).
- `traffic_log.py` — SQLite request/response log of every MCP call, lazy TTL purge.

## Download entry points

Three ways to start a download; all converge on the same confirm + poller path.

- **(a) Free-text search** → metadata → torrent picker → confirm.
- **(b) Pasted rutracker topic URL** (`handlers/rutracker_url.py`) → `get_topic_info`
  → metadata search on the cleaned title → match candidates → confirm. Router
  registered **before** `search` so the broad text filter doesn't swallow URLs.
- **(c) Pasted any-yt-dlp URL** (`handlers/youtube_url.py`) → `probe` → preview →
  confirm. Broad `https?://…`; registered **after** rutracker so its links don't
  double-fire. `?list=` → `list_playlist` (plain-text list). Live streams refused.
  Extractor/upstream failures are reported separately from genuinely unsupported
  URLs; YouTube anti-bot responses explicitly say that the link itself was recognised.

(a) and (b) join the `tdl:<topic_id>:<imdb_id>` confirm callback → rutracker
`.torrent` fetch → `rtorrent.add_torrent` (with `kind` → destination dir). (c)
goes through `ydl:<token>` (token from `ydl_cache`, to fit Telegram's 64-byte
`callback_data`) → `yt-dlp-mcp.start_download`. Both insert a `downloads` row.

## Search → confirm pipeline

1. **Search.** Free text → `search_movie` → aggregated candidates (rating /
   country / kind), cached in `SearchCache`.
2. **Details.** Tap a hit → `get_movie_details` → poster + facts + buttons
   `[← К списку]` `[Трейлер]` `[↓ Скачать]`. Title icon 🎬 / 🎨 / 📺 by kind.
   `kind` prefers the originating search result (movie vs series) and only
   overrides to `cartoon` from details — keeps series out of the movie dir.
3. **Download (movie/cartoon).** `↓ Скачать` → rutracker search by `{title} {year}`
   with a **no-year fallback** (some releases tag production year, not premiere) →
   flat top-10 list sorted by seeders, each row `2,3 Гб • раздают 133 • 720p • SDR`
   (Russian comma sizes; resolution normalised, `4K→2160p`; HDR binary; source tags
   dropped).
4. **Download (series).** `↓ Скачать` → season picker (count from
   `MovieDetails.number_of_seasons`); picking a season searches by **title only** at
   `limit=50`, then client-side `_parse_seasons` keeps releases whose parsed season
   set contains the pick (bundles like «Сезон: 1-5» match). Never year-qualify a
   series query.
5. **Pick release.** Preview = full title + rutracker topic link + a single
   confirm button (`tdl:`). The `.torrent` fetch + rtorrent push fire only after
   confirm; the confirm button is removed on the second tap to block double-fire.

## Completion poller (bot-side)

Every **60 s** the bot iterates `state.list_pending()` and dispatches by
`download.source` in `_process_one`:

- `source != "yt-dlp"` → `rtorrent.get_download_status(info_hash)`; on complete
  prefer `base_path` over `directory` for the content path.
- `source == "yt-dlp"` → `yt-dlp-mcp.get_download_status(task_id)`; pick `output_path`.

Both converge on `_register_and_notify`: `media_watch.register(media_id, …)` →
save `watch_records` + `mark_registered` + send the watch URL. On failure, retry
up to `MAX_REGISTER_ATTEMPTS = 5` ticks, then `register_failed` (admin must reset —
see deferred `/reretry`). Every **60th tick (~1 h)** the bot pulls `GET /api/records`
and prunes dead `watch_records` / orphan `downloads` (files deleted on disk fall
out of `/list`).

## Persistent state

**`state.sqlite`** (`STATE_DB_PATH`, default `.cache/state.sqlite`), schema versioned
via `PRAGMA user_version` (current **2**). On a version mismatch the migration drops
`downloads`, `watch_records`, `notifications` (preserving `users` + `user_identities`)
— no backfill; the bot re-registers fresh.

- `users` — `id`, `display_name`, `is_admin` (bootstrapped from `ADMIN_TELEGRAM_IDS`),
  `notify_downloads` (admin opt-in DM on others' completions, via `/notify_toggle`).
- `user_identities` — PK `(platform, external_id)`, `chat_id`, `meta`; ready for
  VK/web frontends without schema change.
- `downloads` — `info_hash` UNIQUE (**overloaded**: 40-char BT hash for rutracker,
  16-char yt-dlp `task_id`; `_normalise_info_hash` upper-cases only the BT shape),
  `media_id` (composite cross-server key), `imdb_id` (nullable, metadata only),
  `kind`, `title`, `description`, `poster_url`, `state`
  (`downloading`/`complete_pending_register`/`registered`/`register_failed`/`cancelled`),
  `source`, `register_attempts`, timestamps.
- `watch_records` — one per playable item; `(download_id, media_watch_id)` UNIQUE;
  `watch_url`, `stream_url`, `file_path`, `season`, `episode`.
- `notifications` — sent-notification ledger.

**In-memory caches** (`telegram/`): `SearchCache`, `TitleCache`
(`(title, year, kind, seasons)`), `TorrentCache`, `TrailerCache`, `MovieMetaCache`
(poster + description stashed at details time to skip a re-fetch on download),
`YtDlpCache` (token → `(url, title)`).

## Slash commands & bot menu

`bot.set_my_commands` registers the user menu: `/start`, `/status`, `/list`.
Admins additionally get `/notify_toggle` and `/global_list` via per-chat
`BotCommandScopeChat`. `/whoami` works but is hidden.

- `/start` — greeting; covers all three entry points.
- `/status` — in-flight downloads with a progress bar (both rtorrent + yt-dlp
  branches). "not_configured" means the server URL is genuinely unset (a
  configured-but-down client raises and degrades, it isn't "not configured").
- `/list` — the user library, one row per title (🎬/🎨/📺), each a Telegram
  hyperlink to `/watch/<media_id>`; series collapsed to one line linking to
  `/series/<media_id>`.
- `/whoami` — user id + admin flag (hidden).
- `/notify_toggle` (admin) — flip per-admin DM-on-any-completion.
- `/global_list` (admin) — every user's downloads; chunked to respect Telegram's
  4096-char message cap.

## Project structure

```
src/movie_handler_clients/
  core/         config, logging_conf, i18n, formatters, mcp_client, traffic_log,
                state_db, trailer_client, torrent_client, rtorrent_client,
                yt_dlp_client, media_watch_client
  telegram/
    bot.py      entrypoint: routers, MCP clients + supervisors + admin notifier,
                set_my_commands, 60 s completion poller + ~hourly prune
    keyboards.py, *_cache.py (search/title/torrent/trailer/movie_meta/ydl)
    handlers/   search, details, status, list, whoami, admin (notify_toggle/
                global_list), rutracker_url, youtube_url
deploy/         systemd units (movie-handler-telegram + per-MCP) + update.sh
tests/          unit/ (handlers, formatters, traffic log, caches, completion flow),
                integration/ (live Telegram getMe + MCP tools/list, opt-in)
```

## Current state

- ✅ Telegram bot live: all three download entry points, completion poller, prune,
  self-healing MCP clients, admin notifications.
- ⏳ Web (FastAPI + WebSocket) frontend — not started.
- ⏳ VK (`vkbottle`) frontend — not started.
- ⏳ Deferred `/reretry` admin command — see `AGENTS/STATE.md`.

## Data sources & dependencies

MCP servers (over streamable-HTTP with Bearer `MCP_AUTH_TOKEN`): `movie-metadata-mcp`
(8765), `movie-trailer-mcp` (8766), `rutracker-torrent-mcp` (8767), `rtorrent-mcp`
(8768, optional), `yt-dlp-mcp` (8769, optional). HTTP: media-watch-web `/api/*`.
See `../AGENTS/SPEC.md` for the cross-repo map and `AGENTS/ENV.md` for URLs/tokens.
