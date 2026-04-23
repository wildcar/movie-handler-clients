from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from movie_handler_clients.telegram.handlers import details as details_mod
from movie_handler_clients.telegram.search_cache import SearchCache
from movie_handler_clients.telegram.title_cache import TitleCache


async def test_on_details_prefers_search_result_kind_for_title_cache() -> None:
    search_cache = SearchCache()
    query_id = search_cache.put(
        "Severance",
        [
            {
                "imdb_id": "tt11280740",
                "kind": "series",
                "title": "Severance",
                "year": 2022,
            }
        ],
    )
    title_cache = TitleCache()
    mcp = AsyncMock()
    mcp.call_tool = AsyncMock(
        return_value={
            "details": {
                "imdb_id": "tt11280740",
                "title": "Severance",
                "year": 2022,
                # Simulate degraded metadata: the details payload says "movie",
                # but the selected search result is the authoritative series hit.
                "kind": "movie",
            },
            "sources_failed": ["tmdb"],
            "error": None,
        }
    )
    cq = SimpleNamespace(
        data=f"d:tt11280740:{query_id}",
        from_user=SimpleNamespace(id=42),
        message=SimpleNamespace(answer=AsyncMock(), answer_photo=AsyncMock()),
        answer=AsyncMock(),
    )

    await details_mod.on_details(  # type: ignore[arg-type]
        cq,
        mcp=mcp,
        title_cache=title_cache,
        search_cache=search_cache,
    )

    cached = title_cache.get("tt11280740")
    assert cached is not None
    assert cached[2] == "series"

    reply_markup = cq.message.answer.call_args.kwargs["reply_markup"]
    trailer_button = reply_markup.inline_keyboard[0][-1]
    assert trailer_button.text.startswith("🧼 ")
