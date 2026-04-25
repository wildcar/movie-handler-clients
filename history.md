# history — movie-handler-clients

Per-repo task log. Each code-change task adds a short entry **before** work
starts. Cross-repo context lives in the workspace root's `history.md`.

---

## 2026-04-25

### «Показать ещё» expands in place; release pick gets a confirmation step

- `torall:` now edits the original message's reply_markup instead of
  posting a fresh «Ещё раздачи» card. The «Показать ещё» button
  disappears, every remaining release is appended as its own row
  below the pinned picks, icons retained on the originals.
- `tor:` no longer fetches the .torrent immediately. It sends a
  preview message with the full release title (linked to the
  rutracker topic) and a single «⬇️ Скачать» button. The actual
  rutracker fetch + rtorrent push moved to a new `tdl:` callback
  fired by that button, so the user gets a chance to confirm the
  exact release they're committing to.
- Confirmation button is removed (`edit_reply_markup(None)`) the
  moment the second tap is processed, so a slow rutracker fetch
  can't be re-triggered by a frustrated double-tap.

---

## 2026-04-25

### Strict single-season filter, drop «Все сезоны» button

- The per-result keyboard rows only carry resolution / release type,
  so a list mixing «Сезон: 1-5» bundles with «Сезон: 3» releases is
  visually ambiguous — the user can't tell which is which at pick
  time. Tighten the filter to releases whose parsed season set is
  exactly `{chosen_season}`; bundles drop out.
- Remove the «Все сезоны» button from the season picker — without
  per-result season hints in the next list it produced the same
  ambiguity. The `dla:` callback handler stays as a no-op safety
  net for any older messages still in chats.

---

## 2026-04-25

### Series search: drop year, parse season ranges client-side

- Caught on Breaking Bad / Во все тяжкие: rutracker has dozens of
  releases tagged «Сезон: 1 … Сезон: 5», but the previous query
  «Breaking Bad 2008 S01» returned zero hits because Russian-language
  rutracker doesn't use the `S0N` scene format and tags multi-season
  releases with year ranges (`2008-2013`).
- Series queries now omit the year entirely and skip the `S0N`
  suffix. Instead we fetch a wider page (limit=50) by title alone
  and filter client-side: `_parse_seasons` recognises «Сезон: 3»,
  «Сезон 1-5», «Сезон 4, Эпизод», «1 сезон», `S03`, `S01-S05`,
  `S03E07`. A release whose parsed season set contains the user's
  pick stays in the list — bundles like «Сезон: 1-5» match every
  individual season.
- Movies are unchanged: year-qualified query with the no-year
  fallback we already had.
- Header label for season-filtered results now reads «Title — сезон N»
  instead of the raw rutracker query.

---

## 2026-04-25

### Season picker between «⬇️ Скачать» and rutracker search

- For series the «⬇️ Скачать» button now opens a season picker
  («Сезон 1 … Сезон N» + «Все сезоны») instead of running the
  rutracker search immediately. Selected season is appended to the
  query as `S{nn:02d}` (`Wednesday 2025 S01`) so rutracker filters
  by season-tagged scene releases.
- Season count comes from the metadata MCP — a new
  `number_of_seasons` field on `MovieDetails`. Bot's title-cache
  grew a fourth tuple slot to carry it.
- Movies skip the picker entirely — same behaviour as before.
- «Все сезоны» runs the original whole-show search; useful when the
  scene tags don't match what rutracker uses.
- Refactor: pulled the actual «search → list» flow into
  `_run_torrent_search` helper, shared by the movie download path,
  the per-season download, and the «Все сезоны» fallback.

---

## 2026-04-25

### `/list` command, status hint, bot menu

- New `/list` handler — returns the user's "library" (downloads in
  state `registered`). Each row is a Telegram hyperlink: visible
  text is the metadata title (with `S01E01` for series episodes),
  href is the watch URL on media-watch-web. `disable_web_page_preview`
  is on so the message stays compact.
- "Поставил на закачку на сервере" now ends with a `/status` hint
  on its own line — clickable in Telegram out of the box.
- `bot.set_my_commands` registers the three slash commands as the
  bot's menu (the «/» picker next to the input box):
  `/status`, `/list`, `/whoami`. `/start` stays implicit.
- New repo method `state_db.list_user_registered(user_id)` —
  newest-first by `completed_at`; powers `/list`.

---

## 2026-04-25

### Year-mismatch fallback for rutracker search

- Caught on Чебурашка 2: TMDB and Kinopoisk both report year 2026
  (premiere), but every rutracker release is tagged 2025
  (production). The year-qualified query «Чебурашка 2 2026» returned
  zero hits even though the title was clearly available.
- `on_download` now retries with the bare title when the
  year-qualified search comes back empty. The narrower query is
  still tried first to keep results clean for common-name films.
- Captcha / not_configured errors during the fallback are silent —
  the original error path already covered them on the first attempt.

---

## 2026-04-25

### Use the metadata title (not the rtorrent filename) for registered downloads

- `_try_send_to_rtorrent` was persisting `dl.name` (rtorrent's release
  name = the .torrent filename, e.g. `Marvel.One-Shot.Agent.Carter.
  2013.BDRip.XviD.AC3.RUS.-Nesmertelnoe.oruzhie.avi`) as the
  Download row's title, and that title bubbled all the way into the
  watch page header on media-watch-web.
- Reverse the precedence: prefer the cached title from the metadata
  card («Короткометражка Marvel: Агент Картер»), append the year in
  parens when known, and fall back to the rtorrent name only when
  the cache has been evicted (bot restart between details view and
  download tap).
- Existing rows are not migrated — they keep their filename-style
  title until re-registered.

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
