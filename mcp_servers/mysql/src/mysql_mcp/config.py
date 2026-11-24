"""
Settings, read from environment variables (and this project's .env file).

One database (as before):
    MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE

Several databases: list short names, then give each its own settings. Anything a
database does not set is taken from the shared MYSQL_* values above:
    MYSQL_DATABASES=powerbi,sales
    MYSQL_POWERBI_DATABASE=powerbidb      (default: the short name)
    MYSQL_POWERBI_LABEL=Power BI          (shown on the platform; default: the short name)
    MYSQL_POWERBI_USER=..., MYSQL_POWERBI_PASSWORD=..., MYSQL_POWERBI_HOST=..., MYSQL_POWERBI_PORT=...

Who may use which database is switched on per person on the platform's Access page.
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parents[2]

MIN_KEY_LENGTH = 32
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


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
class DatabaseEntry:
    """One database this MCP offers: its short name (used on the platform), a label and how to connect."""

    key: str
    label: str
    settings: DatabaseSettings


@dataclass(frozen=True)
class Settings:
    databases: tuple[DatabaseEntry, ...]
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


def _database(prefix: str, *, database: str, key: str) -> DatabaseSettings:
    def get(name: str, default: str = "") -> str:
        return os.getenv(f"{prefix}{name}") or os.getenv(f"MYSQL_{name}") or default

    user, password = get("USER"), get("PASSWORD")
    if not user or not password:
        raise ConfigError(f"Database '{key}': set {prefix}USER and {prefix}PASSWORD (or the shared MYSQL_USER / MYSQL_PASSWORD).")
    return DatabaseSettings(
        host=get("HOST", "localhost"),
        port=int(get("PORT", "3306")),
        user=user,
        password=password,
        database=database,
        query_timeout_ms=int(os.getenv("MYSQL_QUERY_TIMEOUT_MS", "10000")),
        max_rows=int(os.getenv("MYSQL_MAX_ROWS", "500")),
    )


def load_databases() -> tuple[DatabaseEntry, ...]:
    keys = _list("MYSQL_DATABASES", "")
    if not keys:
        name = _required("MYSQL_DATABASE")
        key = name.lower() if KEY_RE.match(name.lower()) else "default"
        return (DatabaseEntry(key, name, _database("MYSQL_", database=name, key=key)),)

    entries = []
    for key in keys:
        if not KEY_RE.match(key):
            raise ConfigError(f"MYSQL_DATABASES: '{key}' must be lowercase letters, digits and _, starting with a letter.")
        if any(entry.key == key for entry in entries):
            raise ConfigError(f"MYSQL_DATABASES lists '{key}' twice.")
        prefix = f"MYSQL_{key.upper()}_"
        label = (os.getenv(f"{prefix}LABEL") or key).strip()[:100]
        entries.append(DatabaseEntry(key, label, _database(prefix, database=os.getenv(f"{prefix}DATABASE") or key, key=key)))
    return tuple(entries)


def load_settings() -> Settings:
    load_dotenv(PROJECT_DIR / ".env")

    introspection_key = _required("MCP_INTROSPECTION_KEY")
    if len(introspection_key) < MIN_KEY_LENGTH:
        raise ConfigError(f"MCP_INTROSPECTION_KEY must be at least {MIN_KEY_LENGTH} characters.")

    host = os.getenv("MCP_HOST", "127.0.0.1")
    port = int(os.getenv("MCP_PORT", "8101"))
    return Settings(
        databases=load_databases(),
        introspection_key=introspection_key,
        mcp_slug=os.getenv("MCP_SLUG", "mysql"),
        host=host,
        port=port,
        public_url=os.getenv("MCP_PUBLIC_URL", f"http://{host}:{port}/mcp"),
        platform_url=os.getenv("MCP_PLATFORM_URL", "http://127.0.0.1:8000"),
        allowed_hosts=tuple(_list("MCP_ALLOWED_HOSTS", "127.0.0.1:*,localhost:*")),
        token_cache_seconds=float(os.getenv("MCP_TOKEN_CACHE_SECONDS", "30")),
    )
