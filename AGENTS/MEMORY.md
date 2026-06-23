# Memory

Durable repo-local facts and working agreements NOT derivable from code, git, or
SPEC/STATE/HISTORY. The ONLY agent memory store in this repo — read at session
start; append a short bullet when you learn something durable and commit it with
the related change. Cross-repo facts live in `../AGENTS/MEMORY.md` (don't duplicate).

MEMORY.md = durable facts/agreements; current state → STATE.md; iteration log → HISTORY.md.

## Working agreements

- After code changes, commit and push to `main` directly — no feature branch, no
  asking. **Why:** the maintainer treats `main` as the working line on this dev
  host. Run `ruff`+`mypy`+`pytest` before pushing.
- Conversation with the maintainer is Russian; code/docs/comments English;
  end-user UI Russian (keyed in `core/i18n.py` for a future switcher).

## Project facts

- **Self-healing MCP client semantics.** `BaseMCPClient` starts disconnected; a
  background supervisor reconnects every 15 s and `call_tool` reconnects on demand.
  So a client is `None` **only** when its URL is unset (feature disabled →
  `/status` "not_configured"). "Configured but down" is a live-but-disconnected
  client whose calls raise `MCPClientError`, and callers degrade (rtorrent → send
  the .torrent to the user, etc.). Admins get one `mcp_down` ping on drop, one
  `mcp_up` on recovery.
- **`info_hash` column is overloaded** — BT info hash (40-char hex, upper-cased)
  for rutracker rows, yt-dlp `task_id` (16-char lower hex) for yt-dlp rows.
  `_normalise_info_hash` upper-cases **only** the BT shape — never add new
  `info_hash.upper()` calls elsewhere.
- **Completion poller uses `base_path`, not `directory`.** All rtorrent downloads
  share one `directory`; using it makes the scanner pick the largest file in the
  shared dir for every torrent. Always prefer `d.base_path` for the content path.
- **Never year-qualify a series rutracker query.** Russian rutracker tags
  multi-season releases with year ranges (`2008-2013`) and doesn't use scene `S0N`;
  year-qualified series searches return zero. Movies keep the year (with a no-year
  fallback because rutracker often tags production year, not premiere year).
- **`media_id` is the cross-server key** (`rt-<topic_id>`, `yt-<video_id>`,
  `dl-<sha1[:12]>`); `imdb_id` is nullable metadata, never a download key.
- **Schema migrations are destructive by agreement.** A `PRAGMA user_version`
  bump drops `downloads`/`watch_records`/`notifications` (users + identities
  survive); no backfill — the bot re-registers fresh. Pair with media-watch-web's
  `wipe-records.php` when resetting.
