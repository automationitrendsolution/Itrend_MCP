# iTrend MCP Platform

A central website for registering, deploying, securing and monitoring MCP
(Model Context Protocol) servers for iTrend Solutions. MCP servers run on a
dedicated Linux host instead of the PPC production server. The PPC server and
other approved clients connect to them over HTTPS with individual access tokens.

Target URL: `https://mcp.itrendsolution.com`

> **Status:** Phases 1 (foundation), 2 (MCP registry), 3 (first remote MCP) and 4 (authentication and permissions) are complete. Phase 5 part 1 (your own MCPs: add through code, start/stop from the website) is done. See
> [DEVELOPMENT_PROGRESS.md](DEVELOPMENT_PROGRESS.md) for what works today and
> [ARCHITECTURE.md](ARCHITECTURE.md) for how the system fits together.

## Requirements

- Python 3.12 (installed automatically by uv)
- [uv](https://docs.astral.sh/uv/)
- MySQL 8.0 or later running locally, with an empty database (e.g. `CREATE DATABASE itrend_mcp CHARACTER SET utf8mb4;`)

## First-time setup

```powershell
# 1. Install dependencies into .venv
uv sync

# 2. Create your environment file and fill in DJANGO_SECRET_KEY and the DB_* MySQL settings
copy .env.example .env
uv run python -c "import secrets; print(secrets.token_urlsafe(50))"

# 3. Create the database tables
uv run python manage.py migrate

# 4. Create your administrator account
uv run python manage.py createsuperuser

# 5. Run the site at http://127.0.0.1:8000
uv run python manage.py runserver
```

### Without MySQL (SQLite fallback)

For quick local work only, set `DB_ENGINE=sqlite` in `.env`. Django then uses
`db.sqlite3` in the project folder. This is refused when `DJANGO_DEBUG=False`,
so it can never be used in production by accident.

## Everyday commands

| Task | Command |
|---|---|
| Run the dev server | `uv run python manage.py runserver` |
| Run the MCP monitor (second terminal) | `uv run python manage.py monitor_mcps` |
| Check every MCP once | `uv run python manage.py monitor_mcps --once` |
| Run all tests | `uv run python manage.py test` |
| Check configuration | `uv run python manage.py check` |
| Production settings check | `$env:DJANGO_DEBUG="False"; uv run python manage.py check --deploy` |
| Apply migrations | `uv run python manage.py migrate` |
| Add a dependency | `uv add <package>` |

## Useful URLs (development)

| URL | Purpose |
|---|---|
| `/` | Dashboard (login required) |
| `/accounts/login/` | Sign in |
| `/accounts/profile/` | Your profile |
| `/admin/` | Django admin (staff only) |
| `/mcps/` | All MCP servers. Each MCP has tabs: Overview, Logs, Who has access, User manual; administrators can Start / Stop / Restart |
| `/tokens/` | Your access tokens: generate and revoke |
| `/users/` | Users and service accounts (administrators) |
| `/permissions/` | All MCP permissions (administrators) |
| `/activity/` | Activity log as a readable timeline (administrators) |
| `/monitoring/` | Health, CPU/RAM, traffic and incidents of every MCP; per-MCP limits and automatic restarts (administrators) |
| `/api/v1/mcps/<id>/health/` | Latest health check and last 24 hours of one MCP you can see |
| `/api/v1/mcps/` | MCP registry REST API, read-only (login required) |
| `/api/v1/tokens/` | Your tokens: list, create (`POST`), revoke (`DELETE /api/v1/tokens/<id>/`) |
| `/api/v1/users/` | User list (administrators) |
| `/api/v1/tokens/introspect/` | Token check for MCP servers (needs `MCP_INTROSPECTION_KEY`, not for people) |
| `/mcp/<slug>/` | **MCP gateway**: the connection link for each MCP. Needs `Authorization: Bearer <token>` |
| `/api/v1/health/` | Platform health check (public, returns 200 or 503) |

## Adding your own MCP

Put it in its own folder under `mcp_servers/` with an `mcp.json` file. The
platform finds it automatically. Step by step: [mcp_servers/README.md](mcp_servers/README.md).

```powershell
uv run python manage.py sync_mcps   # optional: register new folders right now
```

## Monitoring

The website shows what the **monitor** measured. The monitor is a separate
program; run it next to the website:

```powershell
uv run python manage.py monitor_mcps
```

It really talks to each running MCP (not just "is the process alive"), measures
its CPU and RAM, records incidents, and restarts MCPs that crashed or stopped
answering, at most 3 times an hour by default. MCPs stopped by an administrator,
or started in a terminal, are never restarted. Without it, the Monitoring page
shows a warning and no new checks are made. Details: [ARCHITECTURE.md](ARCHITECTURE.md#phase-6--monitoring).

## Project layout

```
config/        Django settings and root URLs
frontend/      Django app for the website: views, forms, templates, static files
backend/       Django app for models, business logic (services/) and the REST API
mcp_servers/   MCP servers, each a separate project (mysql/: see its README)
tests/         Automated tests
docs/          Additional documentation
logs/          Log files when DJANGO_LOG_TO_FILE=True (not committed)
scripts/       Helper scripts
```

Rule of thumb: anything the user sees belongs in `frontend/`, and everything
behind it belongs in `backend/`. See [ARCHITECTURE.md](ARCHITECTURE.md#code-layout).
