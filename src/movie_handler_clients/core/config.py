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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached Settings instance (parses env only once)."""
    return Settings()
