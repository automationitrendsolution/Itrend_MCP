"""
Read-only MySQL MCP server, served over Streamable HTTP.

Run:  uv run mysql-mcp
URL:  http://127.0.0.1:8101/mcp  (send "Authorization: Bearer <token from the iTrend MCP Platform>")

It can offer several databases (see config.py). Each person only sees and queries the
databases switched on for them on the platform's Access page; the platform sends that
list with every token check.
"""

import logging
import re
from collections.abc import Callable, Mapping
from typing import Any

import anyio
import uvicorn
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import TokenVerifier
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
from .config import DatabaseEntry, Settings, load_settings
from .db import Database, DatabaseUnavailableError, MySQLDatabase, QueryError
from .sql_guard import UnsafeQueryError, check_own_database, check_read_only

logger = logging.getLogger(__name__)

INSTRUCTIONS = (
    "Read-only access to one or more MySQL databases. Start with list_databases to see which ones "
    "you may use, then list_tables, then describe_table to see columns, then run_query with a single "
    "SELECT statement. Pass the database name to each tool when you have more than one. "
    "Results are limited in size."
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


class DatabaseInfo(BaseModel):
    name: str
    label: str


class DatabaseList(BaseModel):
    databases: list[DatabaseInfo]


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


def databases_from_token() -> list[str]:
    """Database names the caller's token allows ("*" = all). No token or no list = none."""
    token = get_access_token()
    if token is None or not token.claims:
        return []
    return list(token.claims.get("databases") or [])


def build_server(
    settings: Settings,
    databases: Mapping[str, Database] | Database | None = None,
    token_verifier: TokenVerifier | None = None,
    allowed: Callable[[], list[str]] = databases_from_token,
) -> MCPServer:
    entries: dict[str, DatabaseEntry] = {entry.key: entry for entry in settings.databases}
    if databases is None:
        connections: dict[str, Database] = {key: MySQLDatabase(entry.settings) for key, entry in entries.items()}
    elif isinstance(databases, Mapping):
        connections = dict(databases)
    else:  # one Database for a one-database configuration (tests)
        connections = {next(iter(entries)): databases}

    server = MCPServer(
        name="iTrend MySQL MCP",
        instructions=INSTRUCTIONS,
        version="0.3.0",
        token_verifier=token_verifier or build_verifier(settings),
        auth=AuthSettings(
            issuer_url=settings.platform_url,
            resource_server_url=settings.public_url,
            required_scopes=[READ_SCOPE],
            # Refuse tokens that were not issued for this server.
            validate_token_resource=True,
        ),
    )

    def usable() -> list[str]:
        names = allowed()
        return list(entries) if "*" in names else [key for key in entries if key in names]

    def pick(name: str | None) -> tuple[DatabaseEntry, Database]:
        keys = usable()
        if not keys:
            raise ToolError(
                "You have not been given any database on this MCP yet. "
                "Ask an administrator to switch one on for you on the platform's Access page."
            )
        if not name:
            if len(keys) > 1:
                raise ToolError(f"Choose a database with the database argument: {', '.join(keys)}.")
            name = keys[0]
        if name not in keys:
            # Same answer for "does not exist" and "not yours": nothing to learn by guessing.
            raise ToolError(f"Database '{name}' is not available to you. You can use: {', '.join(keys)}.")
        return entries[name], connections[name]

    @server.tool(annotations=READ_ONLY)
    async def list_databases() -> DatabaseList:
        """List the databases you may use. Pass one of these names as `database` to the other tools."""
        return DatabaseList(databases=[DatabaseInfo(name=key, label=entries[key].label) for key in usable()])

    @server.tool(annotations=READ_ONLY)
    async def list_tables(database: str | None = None) -> QueryOutput:
        """List the tables in a database, with approximate row counts and comments."""
        _, connection = pick(database)
        return await _run(
            connection,
            "SELECT TABLE_NAME AS table_name, TABLE_ROWS AS approx_rows, TABLE_COMMENT AS comment "
            "FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() ORDER BY TABLE_NAME",
        )

    @server.tool(annotations=READ_ONLY)
    async def describe_table(table: str, database: str | None = None) -> TableDescription:
        """Show the columns of one table: name, type, whether it can be NULL, key and comment."""
        _, connection = pick(database)
        if not _TABLE_NAME.match(table):
            raise ToolError("Table names may only contain letters, numbers, _ and $.")
        result = await _run(
            connection,
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
    async def run_query(sql: str, database: str | None = None, max_rows: int = 100) -> QueryOutput:
        """
        Run one read-only SQL statement (SELECT, WITH, SHOW, DESCRIBE or EXPLAIN) on one database.

        Refer to tables by their plain names; other databases cannot be reached. max_rows limits
        the result (the server also enforces its own maximum). If "truncated" is true, more rows
        exist: narrow the query with WHERE or LIMIT.
        """
        entry, connection = pick(database)
        if max_rows < 1:
            raise ToolError("max_rows must be at least 1.")
        try:
            statement = check_read_only(sql)
            check_own_database(statement, entry.settings.database, await _other_databases(entry, connection))
        except UnsafeQueryError as exc:
            raise ToolError(str(exc)) from exc
        return await _run(connection, statement, max_rows=max_rows)

    async def _other_databases(entry: DatabaseEntry, connection: Database) -> set[str]:
        """Every database name this login could reach, so a query cannot name one of them."""
        names = {e.settings.database for e in entries.values()}
        visible = getattr(connection, "visible_databases", None)
        if visible is not None:
            try:
                names |= await anyio.to_thread.run_sync(visible)
            except (DatabaseUnavailableError, QueryError):
                pass  # the query itself will fail too; configured names are still checked
        return names - {entry.settings.database}

    @server.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> JSONResponse:
        """Unauthenticated liveness check for the platform. Reveals no names, only ok/error per database."""
        results = {}
        for index, connection in enumerate(connections.values()):
            try:
                await anyio.to_thread.run_sync(lambda c=connection: c.query("SELECT 1", max_rows=1))
                results[index] = "ok"
            except (DatabaseUnavailableError, QueryError):
                results[index] = "error"
        working = sum(1 for value in results.values() if value == "ok")
        if working == 0:
            return JSONResponse({"status": "degraded", "database": "error"}, status_code=503)
        status = "ok" if working == len(results) else "partial"
        return JSONResponse({"status": status, "database": "ok", "databases_ok": working, "databases_total": len(results)})

    @server.custom_route("/access-items", methods=["GET"])
    async def access_items(request: Request) -> JSONResponse:
        """
        The databases offered, for the platform's Access page (where people get switched on per
        database). Only reachable on this machine: the gateway forwards /mcp/... paths only.
        """
        return JSONResponse(
            {"label": "Databases", "items": [{"key": entry.key, "label": entry.label} for entry in entries.values()]}
        )

    return server


def build_app(
    settings: Settings,
    databases: Mapping[str, Database] | Database | None = None,
    token_verifier: TokenVerifier | None = None,
) -> Starlette:
    server = build_server(settings, databases, token_verifier)
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
        "Starting MySQL MCP '%s' on http://%s:%s/mcp (databases: %s; tokens checked by %s)",
        settings.mcp_slug, settings.host, settings.port,
        ", ".join(entry.key for entry in settings.databases), settings.platform_url,
    )
    uvicorn.run(build_app(settings), host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
