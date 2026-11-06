# MySQL MCP

A **read-only** MCP server that lets AI clients explore and query one MySQL
database. It speaks **Streamable HTTP**, so it runs as its own process (later
its own container) and clients such as the PPC server connect to it over the
network instead of running it locally.

It is a standalone project with its own `pyproject.toml`, `uv.lock` and `.venv`.
None of its packages are installed into the iTrend MCP Platform website.

## Tools

| Tool | What it does |
|---|---|
| `list_tables` | Tables in the database, with approximate row counts and comments |
| `describe_table(table)` | Columns of one table: type, nullable, key, default, comment |
| `run_query(sql, max_rows=100)` | Runs one read-only statement (`SELECT`, `WITH`, `SHOW`, `DESCRIBE`, `EXPLAIN`) |

Every result has the same shape: `columns`, `rows`, `row_count`, `truncated`.

## How read-only is enforced

Three independent layers, so one mistake cannot lead to data changes:

1. **Database account.** The MCP connects as a MySQL user with only `SELECT`
   rights on one database. MySQL itself refuses anything else.
2. **Read-only transaction.** Every query runs inside
   `START TRANSACTION READ ONLY` with `MAX_EXECUTION_TIME`. Even an admin
   account could not write through this server.
3. **SQL guard** (`sql_guard.py`). Refuses writes, multiple statements,
   comments, `INTO OUTFILE`, `LOAD_FILE`, `SLEEP`, `FOR UPDATE` and similar
   before anything reaches MySQL.

Results are capped by `MYSQL_MAX_ROWS`, and connection details never appear in
error messages.

## Security of the HTTP endpoint

- **Bearer token required** on every MCP request (`Authorization: Bearer …`).
  Tokens in the URL are not accepted. The comparison is constant-time.
  *Phase 3 uses one shared key (`MCP_API_KEY`). Phase 4 replaces it with
  personal, expiring, revocable tokens issued by the platform.*
- **Listens on 127.0.0.1 only** by default.
- **DNS-rebinding protection:** requests with an unexpected `Host` header are refused.
- `GET /health` needs no token and returns only `ok` / `degraded`.

## Setup

```powershell
cd mcp_servers\mysql
uv sync

# Local demo: creates database mcp_demo, a SELECT-only user, and this folder's .env
uv run python scripts/setup_demo_db.py
```

To expose a real database instead, create a read-only account and fill in `.env`
from `.env.example`:

```sql
CREATE USER 'mcp_readonly'@'%' IDENTIFIED BY '<strong password>';
GRANT SELECT ON your_database.* TO 'mcp_readonly'@'%';
```

## Run

```powershell
uv run mysql-mcp
```

The server listens on `http://127.0.0.1:8101/mcp`.

## Try it

**Built-in demo client** (uses the official MCP Python client):

```powershell
uv run python -m mysql_mcp.demo_client
uv run python -m mysql_mcp.demo_client "SELECT * FROM customers LIMIT 3"
```

**MCP Inspector** (visual tester, needs Node.js): run
`npx @modelcontextprotocol/inspector`, choose transport **Streamable HTTP**, URL
`http://127.0.0.1:8101/mcp`, and add the header
`Authorization: Bearer <MCP_API_KEY>`.

**Claude Code:**

```powershell
claude mcp add --transport http itrend-mysql http://127.0.0.1:8101/mcp --header "Authorization: Bearer <MCP_API_KEY>"
```

**Any client that uses an `mcp.json`-style config** (VS Code and others):

```json
{
  "servers": {
    "itrend-mysql": {
      "type": "http",
      "url": "http://127.0.0.1:8101/mcp",
      "headers": { "Authorization": "Bearer <MCP_API_KEY>" }
    }
  }
}
```

## Tests

```powershell
uv run pytest
```

| File | Covers |
|---|---|
| `test_sql_guard.py` | Allowed and blocked SQL, including comment, multi-statement and file tricks |
| `test_tools.py` | All three tools through the official client, in-process: schema, results, errors, injection attempts |
| `test_http.py` | Real HTTP server: health, missing/wrong token, token in URL refused, bad Host refused, SSE streaming with session ID, current and legacy clients end to end |
| `test_mysql_integration.py` | Real MySQL demo database: data, row limit, JSON-safe values, SELECT-only user, read-only transaction even for admin, query timeout. Skipped if the demo database is not set up |
