# history — movie-handler-clients

Per-repo task log. Each code-change task adds a short entry **before** work
starts. Cross-repo context lives in the workspace root's `history.md`.

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
