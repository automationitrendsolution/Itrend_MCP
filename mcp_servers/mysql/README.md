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

- **Platform token required** on every MCP request (`Authorization: Bearer itmcp_…`).
  Tokens are generated on the iTrend MCP Platform website (Access Tokens), are
  personal (or belong to a service account), work for this MCP only, expire,
  and can be revoked. Tokens in the URL are not accepted.
- **The platform decides.** For each token, this server asks the platform
  (`POST /api/v1/tokens/introspect/`, authenticated with
  `MCP_INTROSPECTION_KEY`) whether it may be used here right now. The platform
  checks expiry, revocation, the owner's account and their permission, and
  writes the connection to its activity log.
- **Short cache.** A "yes" is remembered for `MCP_TOKEN_CACHE_SECONDS`
  (default 30), so revoking a token or removing someone's permission takes
  effect within 30 seconds. A "no" is never cached.
- **Fails closed.** If the platform cannot be reached, every request is refused.
- **Listens on 127.0.0.1 only** by default.
- **DNS-rebinding protection:** requests with an unexpected `Host` header are refused.
- `GET /health` needs no token and returns only `ok` / `degraded`.

## Connect to it

Use the platform's address, never the MCP's own port: every request is then
checked, logged and counted by the platform, and it is the only address other
computers can reach.

| | |
|---|---|
| Address | `http://127.0.0.1:8000/mcp/mysql/` (in production `https://mcp.itrendsolution.com/mcp/mysql/`) |
| Header | `Authorization: Bearer <your token>` |
| Token | Website → this MCP → **Generate token** (shown once) |

Ready-made settings for Claude Code, Claude Desktop and VS Code are in the
**Connect in 3 steps** panel next to this text.

**Built-in demo client** (uses the official MCP Python client), from `mcp_servers\mysql`:

```powershell
$env:MCP_TOKEN = "itmcp_..."                              # the token you copied
$env:MCP_PUBLIC_URL = "http://127.0.0.1:8000/mcp/mysql/"   # through the platform
uv run python -m mysql_mcp.demo_client
uv run python -m mysql_mcp.demo_client "SELECT * FROM customers LIMIT 3"
```

**MCP Inspector** (visual tester, needs Node.js): run
`npx @modelcontextprotocol/inspector`, choose transport **Streamable HTTP**, URL
`http://127.0.0.1:8000/mcp/mysql/`, and add the header
`Authorization: Bearer <your token>`.

## For administrators

### Setup

```powershell
cd mcp_servers\mysql
uv sync

# Local demo: creates database mcp_demo, a SELECT-only user, and this folder's .env
uv run python scripts/setup_demo_db.py
```

The script also puts the same `MCP_INTROSPECTION_KEY` in the platform's `.env`
and in this folder's `.env` (restart the website afterwards). The platform finds
this MCP by itself from `mcp.json` (slug **`mysql`**, port 8101); there is nothing
to register. Give people access on its **Who has access** tab (Connect or higher).

To expose a real database instead, create a read-only account and fill in `.env`
from `.env.example`:

```sql
CREATE USER 'mcp_readonly'@'%' IDENTIFIED BY '<strong password>';
GRANT SELECT ON your_database.* TO 'mcp_readonly'@'%';
```

### Run

Normally from the website: this MCP's page → **Start** (also Stop and Restart).
The platform then watches it, shows its output on the **Logs** tab and restarts
it if it crashes (while the monitor runs).

For development you can also run it in a terminal; the website then shows it as
"started outside the platform" and does not stop or restart it:

```powershell
uv run mysql-mcp
```

Either way it listens on `http://127.0.0.1:8101/mcp`, on this computer only. The
platform website must be running too (`MCP_PLATFORM_URL`, default
`http://127.0.0.1:8000`), because it checks every token. The demo client and
MCP Inspector also work against `http://127.0.0.1:8101/mcp` directly, for
testing on this computer; such requests skip the platform's request counts.

### Tests

```powershell
uv run pytest
```

| File | Covers |
|---|---|
| `test_sql_guard.py` | Allowed and blocked SQL, including comment, multi-statement and file tricks |
| `test_tools.py` | All three tools through the official client, in-process: schema, results, errors, injection attempts |
| `test_http.py` | Real HTTP server: health, missing/wrong token, token in URL refused, bad Host refused, SSE streaming with session ID, current and legacy clients end to end |
| `test_mysql_integration.py` | Real MySQL demo database: data, row limit, JSON-safe values, SELECT-only user, read-only transaction even for admin, query timeout. Skipped if the demo database is not set up |
