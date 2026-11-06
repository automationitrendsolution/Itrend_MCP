"""Settings, read from environment variables (and this project's .env file)."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parents[2]

MIN_KEY_LENGTH = 32


class ConfigError(Exception):
    pass


def _required(name: str) -> str:
    value = os.getenv(name, "")
    if not value:
        raise ConfigError(f"Environment variable {name} is required (see .env.example).")
    return value


def _list(name: str, default: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


@dataclass(frozen=True)
class DatabaseSettings:
    host: str
    port: int
    user: str
    password: str = field(repr=False)
    database: str
    connect_timeout: int = 5
    query_timeout_ms: int = 10_000
    max_rows: int = 500


@dataclass(frozen=True)
class Settings:
    database: DatabaseSettings
    # Shared secret this server sends when asking the platform to check a token.
    introspection_key: str = field(repr=False)
    # This MCP's slug in the platform registry. Tokens are issued per slug.
    mcp_slug: str = "mysql"
    host: str = "127.0.0.1"
    port: int = 8101
    public_url: str = "http://127.0.0.1:8101/mcp"
    platform_url: str = "http://127.0.0.1:8000"
    allowed_hosts: tuple[str, ...] = ()
    token_cache_seconds: float = 30


def load_settings() -> Settings:
    load_dotenv(PROJECT_DIR / ".env")

    introspection_key = _required("MCP_INTROSPECTION_KEY")
    if len(introspection_key) < MIN_KEY_LENGTH:
        raise ConfigError(f"MCP_INTROSPECTION_KEY must be at least {MIN_KEY_LENGTH} characters.")

    database = DatabaseSettings(
        host=os.getenv("MYSQL_HOST", "localhost"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=_required("MYSQL_USER"),
        password=_required("MYSQL_PASSWORD"),
        database=_required("MYSQL_DATABASE"),
        query_timeout_ms=int(os.getenv("MYSQL_QUERY_TIMEOUT_MS", "10000")),
        max_rows=int(os.getenv("MYSQL_MAX_ROWS", "500")),
    )
    host = os.getenv("MCP_HOST", "127.0.0.1")
    port = int(os.getenv("MCP_PORT", "8101"))
    return Settings(
        database=database,
        introspection_key=introspection_key,
        mcp_slug=os.getenv("MCP_SLUG", "mysql"),
        host=host,
        port=port,
        public_url=os.getenv("MCP_PUBLIC_URL", f"http://{host}:{port}/mcp"),
        platform_url=os.getenv("MCP_PLATFORM_URL", "http://127.0.0.1:8000"),
        allowed_hosts=tuple(_list("MCP_ALLOWED_HOSTS", "127.0.0.1:*,localhost:*")),
        token_cache_seconds=float(os.getenv("MCP_TOKEN_CACHE_SECONDS", "30")),
    )
