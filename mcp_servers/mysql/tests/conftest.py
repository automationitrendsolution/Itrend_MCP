import socket
import threading
import time
from collections.abc import Iterator

import pytest
import uvicorn

from mysql_mcp.config import DatabaseSettings, Settings
from mysql_mcp.db import DatabaseUnavailableError, QueryResult
from mysql_mcp.server import build_app

API_KEY = "test-key-" + "x" * 40


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
        api_key=API_KEY,
        allowed_hosts=("127.0.0.1:*", "localhost:*"),
    )


@pytest.fixture
def fake_db() -> FakeDatabase:
    return FakeDatabase()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def live_server(settings, fake_db) -> Iterator[str]:
    """A real HTTP server on a random local port, backed by the fake database."""
    port = _free_port()
    app = build_app(settings, fake_db)
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
