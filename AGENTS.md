# Agent Instructions

Primary entrypoint for any agent (Claude, Codex, DeepSeek, etc.) working in the
**`movie-handler-clients`** repo. Read this first.

## Workspace

This repo is part of the **`movie_handler`** workspace — a coordination root that
holds seven sibling git repos. Cross-repo architecture, end-to-end flows, hosts,
and shared agreements live in `../AGENTS.md` and `../AGENTS/SPEC.md`. **This file
is authoritative for anything inside this repo.** Open the root harness only when
you need the cross-repo picture (how the bot, MCP servers, and watch-web fit
together); open this one for everything local.

## Project

**`movie-handler-clients`** — the **client** layer of `movie_handler`. It is an MCP
*client* of the servers (metadata, trailer, rutracker, rtorrent, yt-dlp), **not** a
server itself. Ships an **aiogram 3.x Telegram bot** today (long-polling); a FastAPI
+ WebSocket web frontend and a VK (`vkbottle`) frontend are later stages. The bot
runs a deterministic algorithmic workflow — it does not wrap an LLM agent.

## Document Map

| File | Role |
|------|------|
| `AGENTS.md` | This entrypoint. Repo map, workflow, rules. |
| `CLAUDE.md` | Compatibility pointer to `AGENTS.md`. |
| `AGENTS/SPEC.md` | This repo's functional + technical spec: pipelines, state schema, commands, structure. |
| `AGENTS/STATE.md` | Current snapshot: goal, now, next, open questions, deferred. Overwritten each iteration. |
| `AGENTS/HISTORY.md` | Append-only iteration log, newest first. |
| `AGENTS/MEMORY.md` | Durable repo-local facts + working agreements. The ONLY agent memory store here. |
| `AGENTS/ENV.md` | Repo-local env: tokens, MCP URLs, state-db path. Cross-repo host detail → `../AGENTS/ENV.md`. |
| `docs/adr/` | Architecture Decision Records — one file per significant decision (`docs/adr/TEMPLATE.md`). |
| `README.md` | Human-facing setup / run / deploy guide. |

## Environment

- OS / shell: Ubuntu 24.04 / `bash`, user `keeper` (passwordless sudo).
- Commit identity: `wildcar <wildcar@mail.ru>`.
- Remote: `github.com/wildcar/movie-handler-clients`.
- Deploys to the **bot host** (`r1117636`, the current/dev server) as a systemd unit
  (`movie-handler-telegram`).
- Repo-local env in `AGENTS/ENV.md`; cross-repo hosts in `../AGENTS/ENV.md`.

## Startup Checklist

1. Read `AGENTS.md` (this file).
2. Read `AGENTS/SPEC.md` for the bot's pipelines, state schema, and commands.
3. Read `AGENTS/STATE.md` for the live snapshot.
4. Read the top 3–5 entries in `AGENTS/HISTORY.md`.
5. Read `AGENTS/MEMORY.md` (working agreements + durable facts).
6. Check `git status --short` before editing. Open `AGENTS/ENV.md` for tokens / URLs.

## Change Workflow

For every iteration that changes code or behavior:

1. If the functional contract changes — update `AGENTS/SPEC.md` first.
2. Make the changes.
3. Overwrite `AGENTS/STATE.md`; if the cross-repo picture shifted, also update
   the root `../AGENTS/STATE.md`.
4. Prepend a new entry to `AGENTS/HISTORY.md` (≤5 lines, format below). For changes
   that alter the cross-repo picture, also prepend a one-line entry to `../AGENTS/HISTORY.md`.
5. Run `ruff` + `mypy` + `pytest` locally, then commit and push (see Project Rules).

### `AGENTS/HISTORY.md` entry format (≤5 lines, newest first)

```
## YYYY-MM-DD · <short iteration title>
- What: <one line — what changed>
- Why: <one line — reason / task>
- Files: <key paths, comma-separated>
- Next: <one line — what was planned right after>
```

When you ship a deferred item from `STATE.md`, write a normal HISTORY entry and
remove the item from `STATE.md`.

## Memory

`AGENTS/MEMORY.md` is the **single** store of durable agent memory in this repo.
Do not use external or per-tool memory stores — memory must travel with the repo.

- Read it at session start; append a short bullet when you learn a durable fact or
  agreement and commit it with the related change.
- Split of concerns: durable facts/agreements → `MEMORY.md`; current snapshot →
  `STATE.md`; iteration log → `HISTORY.md`.
- One bullet = one fact; for agreements add a brief **why**. Convert relative dates
  to absolute. Don't record what is already in code, git, or SPEC/STATE/HISTORY.

## Language Rules

- Source code, technical docs, code comments: **English**.
- Conversation with the user: **Russian**.
- End-user UI text: **Russian**, keyed in `core/i18n.py` so a language selector
  can be added later. Don't silently translate docs already written in Russian.

## Project Rules

- **Client, not a server.** This repo speaks to the MCP servers over
  streamable-HTTP; it never registers MCP tools of its own.
- **`media_id` is the cross-server key**, shape `<source>-<id>` (`rt-<topic_id>`,
  `yt-<video_id>`, `dl-<sha1(url)[:12]>`). `imdb_id` is metadata only — never a
  download key.
- **`info_hash` column is overloaded** — BT info hash (40-char hex, upper-cased)
  for rutracker rows, yt-dlp `task_id` (16-char lower hex) for yt-dlp rows. Only
  `_normalise_info_hash` upper-cases, and only the BT shape. Don't add new
  `.upper()` calls.
- **Secrets only via env vars** (`pydantic-settings`, `env_file=".env"`), never
  hard-coded. Ship `.env.example`.
- **Every commit passes `ruff` + `mypy --strict` + `pytest` locally before push.**
  Commit + push to `main` directly after verification — no feature branch, no
  asking. `git pull --ff-only` on the prod host.

## Stack & Commands

Python ≥ 3.11, `asyncio`, `aiogram` 3.x, official Anthropic `mcp` SDK (as a
streamable-HTTP **client**), `httpx`, `pydantic` + `pydantic-settings`, `structlog`
(JSON in prod), `sqlite3`/`aiosqlite`, `uv` for deps.

```bash
uv sync --frozen                          # install / sync deps
uv run movie-handler-telegram             # run the Telegram bot (long-polling)
uv run pytest && uv run ruff check && uv run mypy src
uv run pytest -m integration              # live Telegram getMe + running MCP probe
```

## Code Style

- Match surrounding code: comment density, naming, idiom.
- Python: `ruff` format + lint (line-length 100), `mypy --strict`. Russian UI text
  uses Cyrillic look-alikes — `RUF001/002/003` are intentionally ignored.
