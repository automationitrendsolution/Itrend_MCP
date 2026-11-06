"""
Read-only MySQL MCP server, served over Streamable HTTP.

Run:  uv run mysql-mcp
URL:  http://127.0.0.1:8101/mcp  (send "Authorization: Bearer <token from the iTrend MCP Platform>")
"""

import logging
import re
from typing import Any

from mcp.server.auth.provider import TokenVerifier

import anyio
import uvicorn
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse

from .auth import READ_SCOPE, PlatformTokenVerifier
from .config import Settings, load_settings
from .db import Database, DatabaseUnavailableError, MySQLDatabase, QueryError
from .sql_guard import UnsafeQueryError, check_read_only

logger = logging.getLogger(__name__)

INSTRUCTIONS = (
    "Read-only access to a MySQL database. Start with list_tables, then describe_table "
    "to see columns, then run_query with a single SELECT statement. Results are limited in size."
)

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

_TABLE_NAME = re.compile(r"^[A-Za-z0-9_$]{1,64}$")


# Typed results give clients a JSON schema for each tool and machine-readable output.
class QueryOutput(BaseModel):
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool


class TableDescription(QueryOutput):
    table: str


async def _run(database: Database, sql: str, params: tuple | None = None, max_rows: int | None = None) -> QueryOutput:
    """Run a query in a worker thread, turning failures into messages the client can act on."""
    try:
        result = await anyio.to_thread.run_sync(lambda: database.query(sql, params, max_rows=max_rows))
    except DatabaseUnavailableError as exc:
        raise ToolError(str(exc)) from exc
    except QueryError as exc:
        raise ToolError(f"MySQL rejected the query: {exc}") from exc
    return QueryOutput(**result.as_dict())


def build_verifier(settings: Settings) -> PlatformTokenVerifier:
    return PlatformTokenVerifier(
        platform_url=settings.platform_url,
        introspection_key=settings.introspection_key,
        mcp_slug=settings.mcp_slug,
        resource_url=settings.public_url,
        cache_seconds=settings.token_cache_seconds,
    )


def build_server(
    settings: Settings, database: Database | None = None, token_verifier: TokenVerifier | None = None
) -> MCPServer:
    database = database or MySQLDatabase(settings.database)

    server = MCPServer(
        name="iTrend MySQL MCP",
        instructions=INSTRUCTIONS,
        version="0.2.0",
        token_verifier=token_verifier or build_verifier(settings),
        auth=AuthSettings(
            issuer_url=settings.platform_url,
            resource_server_url=settings.public_url,
            required_scopes=[READ_SCOPE],
            # Refuse tokens that were not issued for this server.
            validate_token_resource=True,
        ),
    )

    @server.tool(annotations=READ_ONLY)
    async def list_tables() -> QueryOutput:
        """List the tables in the database, with approximate row counts and comments."""
        return await _run(
            database,
            "SELECT TABLE_NAME AS table_name, TABLE_ROWS AS approx_rows, TABLE_COMMENT AS comment "
            "FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() ORDER BY TABLE_NAME",
        )

    @server.tool(annotations=READ_ONLY)
    async def describe_table(table: str) -> TableDescription:
        """Show the columns of one table: name, type, whether it can be NULL, key and comment."""
        if not _TABLE_NAME.match(table):
            raise ToolError("Table names may only contain letters, numbers, _ and $.")
        result = await _run(
            database,
            "SELECT COLUMN_NAME AS column_name, COLUMN_TYPE AS type, IS_NULLABLE AS nullable, "
            "COLUMN_KEY AS `key`, COLUMN_DEFAULT AS default_value, COLUMN_COMMENT AS comment "
            "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
            "ORDER BY ORDINAL_POSITION",
            (table,),
        )
        if not result.rows:
            raise ToolError(f"Table '{table}' does not exist. Use list_tables to see the available tables.")
        return TableDescription(table=table, **result.model_dump())

    @server.tool(annotations=READ_ONLY)
    async def run_query(sql: str, max_rows: int = 100) -> QueryOutput:
        """
        Run one read-only SQL statement (SELECT, WITH, SHOW, DESCRIBE or EXPLAIN) and return the rows.

        max_rows limits the result (the server also enforces its own maximum). If "truncated" is
        true, more rows exist: narrow the query with WHERE or LIMIT.
        """
        if max_rows < 1:
            raise ToolError("max_rows must be at least 1.")
        try:
            statement = check_read_only(sql)
        except UnsafeQueryError as exc:
            raise ToolError(str(exc)) from exc
        return await _run(database, statement, max_rows=max_rows)

    @server.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> JSONResponse:
        """Unauthenticated liveness check for the platform. Reveals no details."""
        try:
            await anyio.to_thread.run_sync(lambda: database.query("SELECT 1", max_rows=1))
        except (DatabaseUnavailableError, QueryError):
            return JSONResponse({"status": "degraded", "database": "error"}, status_code=503)
        return JSONResponse({"status": "ok", "database": "ok"})

    return server


def build_app(
    settings: Settings, database: Database | None = None, token_verifier: TokenVerifier | None = None
) -> Starlette:
    server = build_server(settings, database, token_verifier)
    return server.streamable_http_app(
        host=settings.host,
        # Rejects requests whose Host header is not expected (DNS-rebinding protection).
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(settings.allowed_hosts),
            allowed_origins=[f"http://{host}" for host in settings.allowed_hosts]
            + [f"https://{host}" for host in settings.allowed_hosts],
        ),
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = load_settings()
    logger.info(
        "Starting MySQL MCP '%s' on http://%s:%s/mcp (database %s, tokens checked by %s)",
        settings.mcp_slug, settings.host, settings.port, settings.database.database, settings.platform_url,
    )
    uvicorn.run(build_app(settings), host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
