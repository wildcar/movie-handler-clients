# history — movie-handler-clients

Per-repo task log. Each code-change task adds a short entry **before** work
starts. Cross-repo context lives in the workspace root's `history.md`.

---

## 2026-04-25

### Use rtorrent's `base_path` (not `directory`) for media-watch register

- Caught in prod: every movie registered after the first ended up
  pointing to whatever the largest video file was in the shared
  download directory. Reason — `download.directory` is just the
  parent dir (`/mnt/.../Movie/`), not the per-torrent payload path.
  Our scanner picked the biggest file from the shared dir, so a 4K
  Matrix release "stole" all subsequent registrations.
- Fix: prefer `download.base_path` (the actual content path) over
  `download.directory` in the completion poller. Falls back to
  `directory` when base_path is empty (e.g. magnet that hasn't
  resolved metadata yet).
- Requires the bumped rtorrent-mcp that exposes `base_path`.

---

## 2026-04-25

### Persistent state and media-watch-web hand-off on completion

- Replace the in-memory `DownloadTracker` with a SQLite-backed
  `DownloadStore` (default `.cache/state.sqlite`). Persists across bot
  restarts so in-flight downloads aren't dropped on deploy.
- New tables: `users`, `user_identities` (platform + external_id keyed,
  ready for VK/web frontends), `downloads`, `watch_records`,
  `notifications`. Users carry an `is_admin` flag bootstrapped from the
  new `ADMIN_TELEGRAM_IDS` env (CSV).
- New httpx client for the media-watch-web HTTP API
  (`MEDIA_WATCH_BASE_URL` + `MEDIA_WATCH_API_TOKEN`).
- On torrent add: persist `Download` row with `imdb_id`, `kind`, `title`,
  `description`, `poster_url`. A new `MovieMetaCache` stashes the latter
  two when the user opens the details card so the download tap doesn't
  need a second `get_movie_details` round-trip.
- Completion handler in `_poll_completions` now pulls `directory` from
  `get_download_status`, POSTs to `/api/register`, persists each returned
  watch link in `watch_records`, and sends the user a message containing
  the `watch_url`(s). Registration failures are retried on subsequent
  ticks up to 5 times before the row is marked `register_failed`.
- New `/whoami` handler exposes the user's id and admin flag.

---

## 2026-04-23

### Link rating labels in the details card
- Update the Telegram details formatter so rating source labels link to the
  canonical title page on TMDB, IMDb, and КиноПоиск. Per maintainer choice,
  Metacritic stays plain text because we do not have a reliable canonical URL
  in the current payload.
- Extend formatter tests to assert the generated HTML anchors.

### Preserve series kind for rtorrent routing
- Fix the Telegram details flow so the `kind` cached for a title prefers the
  originating search result (`movie` vs `series`) instead of trusting only
  the details payload. This prevents series downloads from being routed into
  the movie directory when metadata degrades or reports the wrong kind.
- Add a focused unit test covering the series-to-rtorrent path.
