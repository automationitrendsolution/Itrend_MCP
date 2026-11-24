"""
MySQL access. The MCP never writes, whatever rights its MySQL login has (even full access):
the session is read-only and every query runs in a READ ONLY transaction with a time limit,
so MySQL itself refuses any change. (The SQL guard in sql_guard.py refuses writes before that.)
"""

import datetime
import decimal
import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol

import pymysql
import pymysql.cursors

from .config import DatabaseSettings

logger = logging.getLogger(__name__)


class DatabaseUnavailableError(Exception):
    """The database could not be reached. Details are logged, not shown to clients."""


class QueryError(Exception):
    """MySQL rejected the query (syntax error, unknown column, timeout, ...)."""


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[dict[str, Any]]
    truncated: bool

    def as_dict(self) -> dict[str, Any]:
        return {"columns": self.columns, "rows": self.rows, "row_count": len(self.rows), "truncated": self.truncated}


class Database(Protocol):
    max_rows: int

    def query(self, sql: str, params: tuple | None = None, *, max_rows: int | None = None) -> QueryResult: ...


def _to_json_value(value: Any) -> Any:
    if isinstance(value, (datetime.date, datetime.datetime, datetime.time)):
        return value.isoformat()
    if isinstance(value, datetime.timedelta):
        return str(value)
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    return value


SCHEMA_CACHE_SECONDS = 300


class MySQLDatabase:
    def __init__(self, settings: DatabaseSettings) -> None:
        self.settings = settings
        self.max_rows = settings.max_rows
        self._schemas: tuple[float, set[str]] | None = None

    def visible_databases(self) -> set[str]:
        """Names of all databases this MySQL login can see (cached for a few minutes)."""
        now = time.monotonic()
        if self._schemas and self._schemas[0] > now:
            return self._schemas[1]
        result = self.query("SHOW DATABASES", max_rows=10_000)
        names = {str(next(iter(row.values()))) for row in result.rows}
        self._schemas = (now + SCHEMA_CACHE_SECONDS, names)
        return names

    def _connect(self) -> pymysql.connections.Connection:
        try:
            return pymysql.connect(
                host=self.settings.host,
                port=self.settings.port,
                user=self.settings.user,
                password=self.settings.password,
                database=self.settings.database,
                connect_timeout=self.settings.connect_timeout,
                read_timeout=max(1, self.settings.query_timeout_ms // 1000 + 5),
                charset="utf8mb4",
                cursorclass=pymysql.cursors.DictCursor,
                autocommit=False,
            )
        except pymysql.err.OperationalError as exc:
            logger.error("Cannot connect to MySQL at %s:%s: %s", self.settings.host, self.settings.port, exc)
            raise DatabaseUnavailableError("The database is not available right now.") from exc

    def query(self, sql: str, params: tuple | None = None, *, max_rows: int | None = None) -> QueryResult:
        limit = min(max_rows or self.max_rows, self.max_rows)
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION MAX_EXECUTION_TIME = %s", (self.settings.query_timeout_ms,))
                # Read-only twice over: the session and the transaction. MySQL refuses any change
                # (INSERT, UPDATE, DELETE, CREATE, DROP, ...) even for a login with full access.
                cursor.execute("SET SESSION TRANSACTION READ ONLY")
                cursor.execute("START TRANSACTION READ ONLY")
                cursor.execute(sql, params)
                columns = [column[0] for column in cursor.description or ()]
                rows = cursor.fetchmany(limit + 1)
            connection.rollback()
        except pymysql.err.OperationalError as exc:
            code = exc.args[0] if exc.args else None
            if code in (2006, 2013):  # server gone away / lost connection
                logger.error("MySQL connection lost: %s", exc)
                raise DatabaseUnavailableError("The database connection was lost.") from exc
            raise QueryError(str(exc.args[1]) if len(exc.args) > 1 else str(exc)) from exc
        except pymysql.err.MySQLError as exc:
            raise QueryError(str(exc.args[1]) if len(exc.args) > 1 else str(exc)) from exc
        finally:
            connection.close()

        truncated = len(rows) > limit
        rows = [{key: _to_json_value(value) for key, value in row.items()} for row in rows[:limit]]
        return QueryResult(columns=columns, rows=rows, truncated=truncated)
