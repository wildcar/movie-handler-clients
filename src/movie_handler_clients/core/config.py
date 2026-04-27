"""Runtime configuration loaded from environment variables and `.env`."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Immutable application settings.

    All values originate from environment variables (or a local `.env` file).
    Secrets are never accepted as tool arguments — only through this object.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    telegram_bot_token: str = Field(
        ...,
        description="Token issued by @BotFather for the Telegram bot.",
    )
    movie_metadata_mcp_url: str = Field(
        "http://127.0.0.1:8765/mcp",
        description="Streamable-HTTP endpoint of the movie-metadata-mcp server.",
    )
    movie_trailer_mcp_url: str = Field(
        "http://127.0.0.1:8766/mcp",
        description="Streamable-HTTP endpoint of the movie-trailer-mcp server.",
    )
    rutracker_torrent_mcp_url: str = Field(
        "http://127.0.0.1:8767/mcp",
        description="Streamable-HTTP endpoint of the rutracker-torrent-mcp server.",
    )
    rtorrent_mcp_url: str | None = Field(
        None,
        description=(
            "Streamable-HTTP endpoint of rtorrent-mcp on the media server. "
            "When unset, the download flow falls back to sending the .torrent "
            "file to the user directly."
        ),
    )
    yt_dlp_mcp_url: str | None = Field(
        None,
        description=(
            "Streamable-HTTP endpoint of yt-dlp-mcp on the media server. "
            "When unset, the bot can't process YouTube / Vimeo / Twitch / … "
            "URLs and falls back to «ссылка не распознана»."
        ),
    )
    mcp_auth_token: str = Field(
        ...,
        description="Bearer token sent to every MCP server request.",
    )
    log_db_path: Path = Field(
        Path(".cache/mcp_traffic.sqlite"),
        description="SQLite file used to log MCP request/response pairs.",
    )
    log_ttl_days: int = Field(
        30,
        ge=1,
        description="How long MCP traffic rows are kept before lazy purge.",
    )
    log_level: str = Field("INFO", description="Root log level for structlog.")

    state_db_path: Path = Field(
        Path(".cache/state.sqlite"),
        description=("SQLite file storing users, downloads and watch links across bot restarts."),
    )
    media_watch_base_url: str | None = Field(
        None,
        description=(
            "Public base URL of media-watch-web (e.g. https://v.wildcar.ru). "
            "When unset, the bot still tracks downloads but skips the "
            "media-watch register call on completion."
        ),
    )
    media_watch_api_token: str | None = Field(
        None,
        description="Bearer token for the media-watch-web /api/register endpoint.",
    )
    admin_telegram_ids: str = Field(
        "",
        description=(
            "Comma-separated Telegram user ids that get is_admin=1 on "
            "first interaction (or on the next interaction if already "
            "in the DB)."
        ),
    )

    def admin_user_ids(self) -> set[int]:
        out: set[int] = set()
        for raw in self.admin_telegram_ids.split(","):
            raw = raw.strip()
            if not raw:
                continue
            try:
                out.add(int(raw))
            except ValueError:
                continue
        return out


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached Settings instance (parses env only once)."""
    return Settings()
