# History

Newest first. Each entry ≤5 lines using the format in `AGENTS.md`. Repo-local log;
cross-repo context lives in `../AGENTS/HISTORY.md`.

---

## 2026-07-21 · Distinguish YouTube anti-bot failures from invalid links
- What: Pasted-video probe errors now report recognised-but-rejected YouTube URLs separately from unsupported URLs; added regression coverage for the reported `youtu.be` link.
- Why: YouTube's `Sign in to confirm you’re not a bot` response was misleadingly shown as «ссылка не распознана».
- Files: `AGENTS/SPEC.md`, `AGENTS/STATE.md`, `core/i18n.py`, `handlers/youtube_url.py`, `tests/unit/test_youtube_url.py`.
- Next: Rotate production YouTube cookies or add a PO-token provider if anti-bot rejection persists.

## 2026-06-23 · Migrate harness to agent-template layout
- What: Added `AGENTS.md`, `CLAUDE.md` pointer, `AGENTS/{SPEC,STATE,HISTORY,MEMORY,ENV}.md`, `docs/adr/TEMPLATE.md`; folded `history.md`/`env.md` in.
- Why: Adopt the standard `wildcar/agent-template` harness across the workspace.
- Files: `AGENTS.md`, `CLAUDE.md`, `AGENTS/*`, `docs/adr/TEMPLATE.md`.
- Next: Keep README as the human setup guide; harness drives agent work.

## 2026-06-14 · Self-healing MCP clients + admin connect-loss notifications
- What: `BaseMCPClient` no longer fatal on startup; background supervisor reconnects every 15 s, `call_tool` reconnects on demand; admins pinged `mcp_down`/`mcp_up`.
- Why: A media-host blip at bot restart left clients `None` for the process life, silently breaking downloads.
- Files: `core/mcp_client.py`, `telegram/bot.py`, `core/i18n.py`, tests.
- Next: `None` now means URL unset (truly "not_configured"); configured-but-down clients raise and callers degrade.

## 2026-04-27 · Pasted-YouTube-URL flow + generic yt-dlp downloads
- What: `handlers/youtube_url.py` claims any URL; playlist→`list_playlist` text list, single→`probe`→preview→`start_download`; poller `_process_one` dispatches by `source`.
- Why: Same mid-pipeline preview/confirm UX for any yt-dlp-supported URL via the new `yt-dlp-mcp`.
- Files: `core/yt_dlp_client.py`, `handlers/youtube_url.py`, `telegram/bot.py`, `telegram/ydl_cache.py`, `core/i18n.py`, tests.
- Next: Pair deploy with media-watch-web (media_id regex extended to `dl-…`); live streams refused upfront.

## 2026-04-27 · kind=cartoon plumbing + 🎨 marker
- What: `Kind` extended to movie|series|cartoon; details/`/list`/`tdl:` route animated movies to `Cartoon/` with a 🎨 icon.
- Why: Centralised cartoon detection in movie-metadata-mcp; bot just plumbs the kind through to rtorrent + media-watch.
- Files: `handlers/details.py`, `core/formatters.py`, `telegram/keyboards.py`, `core/i18n.py`.
- Next: Pair deploy with movie-metadata-mcp + rtorrent-mcp + media-watch-web.

## 2026-04-26 · Torrent picker — flat top-10 list
- What: Replaced bucketed «3 пина + Показать ещё» with one seeders-sorted top-10 list; labels `2,3 Гб • раздают 133 • 720p • SDR` (HDR binary, source tags dropped).
- Why: Bucketed layout was hard to scan and mixed resolution with unused source tags.
- Files: `telegram/keyboards.py`, `core/formatters.py`, `core/i18n.py`.
- Next: Removed `pinned_torrents`/`torall:` handler and now-unused i18n keys.

## 2026-04-26 · Composite media-id + pasted-rutracker-URL flow
- What: Added `downloads.media_id` (`rt-<topic_id>`); `imdb_id` demoted to nullable metadata; new `handlers/rutracker_url.py` (topic URL → `get_topic_info` → metadata match → `tdl:` confirm).
- Why: imdb_id PK collided across releases of one film; users wanted to paste a topic URL.
- Files: `core/state_db.py`, `core/media_watch_client.py`, `handlers/rutracker_url.py`.
- Next: Schema reset via `user_version`→2 (drops downloads/watch/notifications); pair with media-watch-web `wipe-records.php`.

## 2026-04-25 · «Показать ещё» in place + release-pick confirm step
- What: `torall:` edits the original markup instead of a new card; `tor:` shows a preview + single «⬇️ Скачать» that fires the new `tdl:` confirm; button removed on tap.
- Why: Fewer cards, and a confirm step before committing the rtorrent push (block double-tap re-fire).
- Files: `telegram/handlers/details.py`, `telegram/keyboards.py`.
- Next: `tdl:` owns the actual rutracker fetch + rtorrent push.

## 2026-04-25 · Strict single-season filter, drop «Все сезоны»
- What: Season-filtered list keeps only releases whose parsed season set is exactly `{chosen}`; removed «Все сезоны» button (`dla:` kept as no-op).
- Why: Mixing «Сезон: 1-5» bundles with «Сезон: 3» releases was ambiguous at pick time.
- Files: `telegram/handlers/details.py`, `telegram/keyboards.py`.
- Next: —

## 2026-04-25 · Series search: drop year, parse season ranges client-side
- What: Series queries omit year and `S0N`; fetch limit=50 by title, `_parse_seasons` matches «Сезон: 3», «1-5», `S03`, `S03E07`, etc.
- Why: Russian rutracker tags multi-season releases with year ranges, not scene `S0N`; year-qualified queries returned zero (Breaking Bad).
- Files: `telegram/handlers/details.py`.
- Next: Movies unchanged (year-qualified + no-year fallback); header reads «Title — сезон N».

## 2026-04-25 · Season picker before rutracker search
- What: Series «⬇️ Скачать» opens a season picker (count from new `MovieDetails.number_of_seasons`); pulled the search→list flow into `_run_torrent_search`.
- Why: Let the user pick a season before searching; movies skip the picker.
- Files: `telegram/handlers/details.py`, `telegram/title_cache.py`.
- Next: Later tightened to strict single-season filter.

## 2026-04-25 · /list command, status hint, bot menu
- What: New `/list` (registered downloads as watch-URL hyperlinks); completion msg gains a `/status` hint; `set_my_commands` registers the menu.
- Why: Give users a library view and a discoverable command menu.
- Files: `telegram/handlers/list.py`, `core/state_db.py` (`list_user_registered`), `telegram/bot.py`.
- Next: —

## 2026-04-25 · Year-mismatch fallback for rutracker search
- What: `on_download` retries with the bare title when the year-qualified search returns empty.
- Why: TMDB/Kinopoisk report premiere year, rutracker tags production year (Чебурашка 2: 2026 vs 2025) → zero hits.
- Files: `telegram/handlers/details.py`.
- Next: Captcha/not_configured errors stay silent during the fallback.

## 2026-04-25 · Use metadata title (not rtorrent filename) for registered downloads
- What: Reversed precedence — persist the cached metadata title (+year) as the Download title, fall back to the rtorrent name only when the cache was evicted.
- Why: The .torrent filename leaked into the watch-page header.
- Files: `telegram/handlers/details.py`.
- Next: Existing rows keep their old title until re-registered.

## 2026-04-25 · Use rtorrent base_path (not directory) for media-watch register
- What: Completion poller prefers `download.base_path` over `directory`, falling back to `directory` only when empty.
- Why: `directory` is the shared parent dir; the scanner picked the largest file there, so one big release stole all registrations.
- Files: `telegram/bot.py`.
- Next: Requires the rtorrent-mcp that exposes `base_path`.

## 2026-04-25 · Persistent state + media-watch hand-off on completion
- What: Replaced in-memory `DownloadTracker` with SQLite `state.sqlite` (users/identities/downloads/watch_records/notifications); poller POSTs `/api/register`, persists links, retries ≤5; added `MediaWatchClient`, `MovieMetaCache`, `/whoami`.
- Why: Survive restarts and hand finished downloads to media-watch-web.
- Files: `core/state_db.py`, `core/media_watch_client.py`, `telegram/bot.py`, `telegram/handlers/whoami.py`.
- Next: `ADMIN_TELEGRAM_IDS` bootstraps `is_admin`.

## 2026-04-23 · Link rating labels + preserve series kind
- What: Details formatter links rating labels to TMDB/IMDb/КиноПоиск (Metacritic plain); `kind` cache prefers the search result so series aren't routed into the movie dir.
- Why: Clickable rating sources; correct rtorrent routing when metadata degrades.
- Files: `core/formatters.py`, `telegram/handlers/details.py`, tests.
- Next: —
