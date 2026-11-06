# Development Progress

| Phase | Name | Status |
|---|---|---|
| 1 | Project foundation | ✅ Complete (2026-11-04) |
| 2 | MCP registry | ✅ Complete (2026-11-05) |
| 3 | First remote MCP | ✅ Complete (2026-11-05) as a local process; container and Nginx deferred |
| 4 | Authentication and permissions | ⏳ Next |
| 5 | Deployment manager | Not started |
| 6 | Monitoring | Not started |
| 7 | Production deployment | Not started |

---

## Phase 3 – First remote MCP

**Decision (2026-11-05):** run the first MCP as a normal local process instead of
a Docker container. The code is container-ready; only the packaging is deferred.

### What was developed
- **`mcp_servers/mysql/`**: a standalone, read-only MySQL MCP server built
  with the official MCP Python SDK (2.3), served over **Streamable HTTP** at
  `http://127.0.0.1:8101/mcp`. It has its own `pyproject.toml`, `uv.lock` and
  `.venv`; the website's environment does not contain the MCP SDK.
- **Tools:** `list_tables`, `describe_table`, `run_query`, all marked read-only,
  with typed (structured) output.
- **Read-only, three ways:** SELECT-only MySQL account, every query inside a
  `READ ONLY` transaction with a time limit, and a SQL guard that refuses
  writes, multiple statements, comments, file access and locking.
- **Secure connection:** bearer token required on every request (the SDK's
  standard `TokenVerifier`, returning proper `401` + `WWW-Authenticate`),
  localhost-only by default, DNS-rebinding protection, unauthenticated
  `/health` that reveals only up/down.
- **Demo setup script** creating database `mcp_demo`, user `mcp_readonly`
  (SELECT on `mcp_demo` only) and the MCP's `.env`.
- **Demo client** using the official MCP client, plus connection instructions
  for MCP Inspector, Claude Code and `mcp.json`-style clients.

### Files created
`mcp_servers/mysql/`: `pyproject.toml`, `uv.lock`, `.python-version`,
`.env.example`, `README.md`, `scripts/setup_demo_db.py`,
`src/mysql_mcp/{__init__,config,sql_guard,db,auth,server,demo_client}.py`,
`tests/{__init__,conftest,test_sql_guard,test_tools,test_http,test_mysql_integration}.py`

### Files modified (platform)
`ARCHITECTURE.md`, `DEVELOPMENT_PROGRESS.md`, `README.md`.
No website code changed.

### Dependencies (MySQL MCP only)
`mcp[cli]` 2.3.0, `pymysql`, `python-dotenv`; dev: `pytest`. Nothing was added
to the website.

### Database changes
On the local MySQL server (not the platform database):
- New database `mcp_demo` with demo tables `customers`, `products`, `orders`.
- New MySQL user `mcp_readonly@localhost` with `SELECT` on `mcp_demo.*` only.

### Tests executed
- MySQL MCP: `cd mcp_servers/mysql; uv run pytest`: **54 tests, all passing**,
  none skipped (including the real-MySQL integration tests).
- Website: `uv run python manage.py test`: **101 tests, all passing** (unchanged).
- Manual end-to-end test with the official MCP client against the running server:
  - connected (protocol 2026-07-28) and listed the three tools;
  - `list_tables` and a JOIN/GROUP BY revenue query returned the demo data;
  - `DELETE FROM orders` and `SELECT … INTO OUTFILE` were refused by the SQL guard;
  - `SELECT … FROM itrend_mcp.auth_user` was refused **by MySQL** (no privilege);
  - no token and wrong token → `401`; forged `Host` header → `421`.

| Brief requirement (section 18) | Covered by |
|---|---|
| Connect to MySQL MCP, initialize session | `test_http.py` (SSE + `Mcp-Session-Id`), end-to-end clients |
| List tools / call a tool | `test_tools.py`, `test_http.py` |
| Unauthorized request | `test_http.py` (no token, wrong token, token in URL) |
| Streaming behaviour | `test_http.py` (`text/event-stream` response) |
| Expired / revoked token | Phase 4 (tokens do not expire yet) |

### Commands
```powershell
cd mcp_servers\mysql
uv sync
uv run python scripts/setup_demo_db.py   # first time only
uv run mysql-mcp                         # start the server
uv run python -m mysql_mcp.demo_client   # in a second terminal
uv run pytest
```

### Known limitations
- One shared API key, which never expires. Phase 4 adds personal tokens with
  expiry and revocation, checked by the platform.
- Runs as a plain local process: no container, CPU/memory limits or restart
  policy yet. The server must be started by hand.
- No Nginx gateway yet, so the public URL `https://mcp.itrendsolution.com/mysql/`
  does not work. Clients connect to `http://127.0.0.1:8101/mcp` directly.
- The platform does not start, stop or health-check the MCP yet (Phases 5–6).

### Next phase
**Phase 4 – Authentication and permissions:** per-user MCP permissions,
personal access tokens (shown once, stored hashed, with expiry and revocation),
service identities for external servers such as PPC, and audit logs. The
MySQL MCP's `StaticKeyVerifier` is then replaced by a verifier that checks
platform-issued tokens.

---

## Phase 2 – MCP registry

### What was developed
- **Database models** `MCPServer` and `MCPDeployment`, with indexes on the
  fields used for filtering.
- **MCP pages** (website):
  - List with live search, status and deployment-type filters, and paging. HTMX
    refreshes only the table, and the URL updates so results can be bookmarked.
  - Detail page with a copyable connection URL, details, deployment history,
    administrator-only internal settings, and a delete confirmation dialog.
  - Add page with three choices (ZIP upload, GitHub, existing URL). Only the
    fields that apply to the chosen type are shown, and the slug is checked
    live as you type (HTMX).
  - Edit page. Slug and deployment type are locked.
- **Permissions:** administrators (staff) add, edit and delete. Normal users see
  only MCPs marked "All employees". Hidden MCPs return 404.
- **REST API** `/api/v1/mcps/`: list (paged, `?search=`, `?status=`,
  `?deployment_type=`), create, view, PATCH, delete. Internal fields are shown to
  administrators only. Every error uses one format.
- **Dashboard:** real counts (total, running, stopped, unhealthy) and the most
  recently added MCPs.
- **Django admin:** both models registered, with deployments shown inline.

### Files created
- `backend/`: `validators.py`, `permissions.py`, `serializers.py`, `exceptions.py`,
  `services/registry.py`, `migrations/0001_initial.py`
- `frontend/templatetags/mcp_tags.py`
- `frontend/templates/mcps/`: `list.html`, `detail.html`, `create.html`, `edit.html`,
  `partials/table.html`, `partials/form.html`, `partials/slug_check.html`
- `frontend/templates/partials/form_field.html`
- `tests/`: `test_registry_models.py`, `test_registry_pages.py`, `test_registry_api.py`

### Files modified
- `backend/models.py`, `backend/admin.py`, `backend/views.py`, `backend/urls.py`
- `frontend/views.py`, `frontend/urls.py`, `frontend/forms.py`
- `frontend/templates/dashboard/index.html`, `dashboard/partials/stat_card.html`,
  `partials/sidebar.html`
- `frontend/static/css/app.css`, `frontend/static/js/app.js`
- `config/settings.py` (`MCP_PUBLIC_BASE_URL`, API error handler), `.env.example`
- `tests/factories.py`, `tests/test_dashboard.py`

### Dependencies
None added.

### Database changes
Migration `backend.0001_initial`:
- `backend_mcpserver`: name and slug are unique; indexes on status, health,
  deployment type and access policy.
- `backend_mcpdeployment`: linked to its MCP server (deleted with it); index on
  server + newest first.

Run `uv run python manage.py migrate` after pulling this change.

### Tests executed
`uv run python manage.py test`: **101 tests, all passing** (38 from Phase 1 and
63 new), on MySQL 8.0.46.

| File | Covers |
|---|---|
| `test_registry_models.py` | Slug format and reserved words, uniqueness, endpoint rules per type, http/https only, connection URL, secret detection in configuration, visibility rules, service create/update/delete, locked fields, filters |
| `test_registry_pages.py` | Login required, visible-only listing, search and filters, HTMX partial, paging, 404 for hidden MCPs, internal fields hidden from normal users, 403 on every management URL for normal users, create/edit/delete flows, slug check |
| `test_registry_api.py` | Login required, paging, visibility, staff-only fields, staff-only writes, error format, model rules, secret rejection, PATCH, locked slug, PUT refused, delete |
| `test_dashboard.py` (updated) | Real counts, counts limited to visible MCPs |

Also run:
- `manage.py check`: no issues. `makemigrations --check`: no pending changes.
- Live smoke test: registered an MCP through the form as an administrator, then
  loaded the dashboard, list, search (normal and HTMX), detail, edit, add, slug
  check and API. All returned 200 with the expected content. Test data was removed.
- Pages have not yet been checked visually in a browser.

### Known limitations
- Status and health are set by hand until Phases 5 and 6.
- Normal users see only "All employees" MCPs until per-user permissions (Phase 4).
- Deleting a hosted MCP removes only its record. Stopping its container comes in Phase 5.
- Registry changes are written to the application log, not yet to an audit table (Phase 4).
- No generated API documentation yet. The endpoints are described in the API views' docstrings.

### Next phase
**Phase 3 – First remote MCP:** a sample MySQL MCP served over Streamable HTTP
and tested with a real MCP client. The project brief runs each MCP in its own
Docker container behind Nginx. Docker and deployment are on hold for now, so
Phase 3 needed a decision. Outcome: run it as a plain local process (see Phase 3).

---

## Phase 1 – Project foundation

### What was developed
- Django project configured from environment variables, using the local
  MySQL 8.0 server, with an SQLite fallback for development only.
- Two Django apps: `frontend/` (views, forms, templates, static files) and
  `backend/` (models, `services/` business logic, REST API). Also
  `tests/`, `docs/`, `logs/`, `scripts/`, `mcp_servers/`. No Docker or
  deployment files yet; those are deferred.
- Login (with failed-attempt lockout), logout and profile pages.
- Dashboard with sidebar navigation and a platform status panel that
  refreshes every 30 seconds through HTMX.
- Django REST Framework configured, with the public endpoint `GET /api/v1/health/`.
- Production security settings and custom error pages.

### Files created
- `config/settings.py` (rewritten), `config/urls.py` (rewritten)
- `frontend/`: `apps.py`, `forms.py`, `urls.py`, `views.py`
- `backend/`: `apps.py`, `admin.py`, `models.py`, `migrations/`, `urls.py`,
  `views.py`, `services/health.py`, `services/login_protection.py`
- `frontend/templates/`: `base.html`, `403.html`, `404.html`, `500.html`,
  `errors/layout.html`, `partials/{sidebar,nav_upcoming,toasts}.html`,
  `accounts/{login,profile}.html`, `dashboard/index.html`,
  `dashboard/partials/{stat_card,system_status}.html`
- `frontend/static/css/app.css`, `frontend/static/js/app.js`, `frontend/static/vendor/…`
- `tests/`: `factories.py`, `test_accounts.py`, `test_dashboard.py`, `test_api.py`, `test_security.py`
- `.python-version`, `ARCHITECTURE.md`, `CHANGELOG.md`, `DEVELOPMENT_PROGRESS.md`

### Files modified
- `pyproject.toml`, `uv.lock`, `.env.example`, `.gitignore`, `README.md`

### Files removed
- The scaffold placeholder code (a "Hello, world" view and empty `tests.py`
  files). Tests now live in `tests/`.

### Dependencies
| Added |
|---|
| djangorestframework 3.18.3 |
| django-htmx 1.29.0 |

Kept: Django 5.2.x (LTS), mysqlclient, python-dotenv.

### Database changes
- MySQL 8.0 (local server, database `itrend_mcp`), utf8mb4 with strict mode.
- Only Django's built-in tables (auth, sessions, admin, contenttypes). No
  platform models yet; the first ones arrive in Phase 2.

### Tests executed
`uv run python manage.py test`: **38 tests, all passing**, run against
MySQL 8.0.46.

| File | Covers |
|---|---|
| `test_accounts.py` | Login success and failure, inactive users, CSRF, open-redirect protection, password hashing, lockout rules, POST-only logout, profile access |
| `test_dashboard.py` | Login required, rendering, real user count, no invented MCP numbers, staff-only navigation, HTMX status partial and database outage |
| `test_api.py` | Versioned URL, health 200/503, no error details leaked, method restrictions, authentication on by default |
| `test_security.py` | Security headers, HTTP-only session cookie, 404 without debug details, 500 template, admin blocked for non-staff |

Other checks run:
- `manage.py check`: no issues.
- `manage.py check --deploy` with `DJANGO_DEBUG=False`: one warning
  (`security.W021`, HSTS preload). Preload is deliberately opt-in through
  `DJANGO_SECURE_HSTS_PRELOAD` because leaving the browser preload list is very slow.
- Manual smoke test on a running dev server: health API, static files, login,
  dashboard, HTMX status partial and admin all responded correctly.
- Pages have not yet been checked visually in a browser.

### Commands
See [README.md](README.md#first-time-setup).

### Known limitations
- Lockout counters and API rate limits use per-process memory. They become
  shared in Phase 7.
- Logins are written to the application log, not yet to an audit table (Phase 4).
- Sidebar entries for later phases are shown but disabled.
- Static files are served by Django in development. Production serving is set up in Phase 7.

### Next phase
**Phase 2 – MCP registry:** models for MCP servers and deployments, MCP
list/detail/create/edit/delete pages, status display and the `/api/v1/mcps/` REST API.
