import json
import socket
import threading
import time
from collections.abc import Iterator

import httpx2
import pytest
import uvicorn

from mysql_mcp.auth import PlatformTokenVerifier
from mysql_mcp.config import DatabaseSettings, Settings
from mysql_mcp.db import DatabaseUnavailableError, QueryResult
from mysql_mcp.server import build_app

INTROSPECTION_KEY = "introspection-" + "k" * 40
VALID_TOKEN = "itmcp_" + "v" * 43


class FakePlatform:
    """Stands in for the platform's /api/v1/tokens/introspect/ endpoint."""

    def __init__(self) -> None:
        self.calls = 0
        self.down = False
        self.tokens = {
            VALID_TOKEN: {
                "active": True, "mcp": "mysql", "username": "alice", "token_id": 1,
                "permission": "connect", "expires_at": int(time.time()) + 3600,
            }
        }

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.calls += 1
        if self.down:
            raise httpx2.ConnectError("platform down")
        if request.url.path != "/api/v1/tokens/introspect/":
            return httpx2.Response(404)
        if request.headers.get("authorization") != f"Bearer {INTROSPECTION_KEY}":
            return httpx2.Response(403, json={"error": {"code": "permission_denied"}})
        body = json.loads(request.content)
        answer = self.tokens.get(body["token"])
        if answer and answer["mcp"] == body["mcp"]:
            return httpx2.Response(200, json=answer)
        return httpx2.Response(200, json={"active": False})

    def verifier(self, settings: Settings, **kwargs) -> PlatformTokenVerifier:
        options = {"cache_seconds": 0, **kwargs}
        return PlatformTokenVerifier(
            platform_url=settings.platform_url,
            introspection_key=INTROSPECTION_KEY,
            mcp_slug=settings.mcp_slug,
            resource_url=settings.public_url,
            transport=httpx2.MockTransport(self.handler),
            **options,
        )


class FakeDatabase:
    """Stands in for MySQL so tool and HTTP behaviour can be tested without a database."""

    max_rows = 50

    def __init__(self) -> None:
        self.queries: list[tuple[str, tuple | None, int | None]] = []
        self.available = True

    def query(self, sql, params=None, *, max_rows=None):
        self.queries.append((sql, params, max_rows))
        if not self.available:
            raise DatabaseUnavailableError("The database is not available right now.")
        if "information_schema.COLUMNS" in sql:
            rows = [{"column_name": "id", "type": "int"}] if params == ("customers",) else []
            return QueryResult(columns=["column_name", "type"], rows=rows, truncated=False)
        return QueryResult(columns=["n"], rows=[{"n": 1}], truncated=False)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database=DatabaseSettings("localhost", 3306, "u", "p", "d"),
        introspection_key=INTROSPECTION_KEY,
        allowed_hosts=("127.0.0.1:*", "localhost:*"),
    )


@pytest.fixture
def fake_db() -> FakeDatabase:
    return FakeDatabase()


@pytest.fixture
def platform() -> FakePlatform:
    return FakePlatform()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def live_server(settings, fake_db, platform) -> Iterator[str]:
    """A real HTTP server on a random local port, backed by the fake database and fake platform."""
    port = _free_port()
    app = build_app(settings, fake_db, platform.verifier(settings))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)
