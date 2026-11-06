# Development Progress

| Phase | Name | Status |
|---|---|---|
| 1 | Project foundation | ✅ Complete (2026-11-04) |
| 2 | MCP registry | ✅ Complete (2026-11-05) |
| 3 | First remote MCP | ✅ Complete (2026-11-05) as a local process; container and Nginx deferred |
| 4 | Authentication and permissions | ✅ Complete (2026-11-06) |
| 5 | Deployment manager | 🟡 Part 1 complete (2026-11-06): your own MCPs from code, start/stop as local processes. ZIP/GitHub/Docker on hold |
| 6 | Monitoring | Not started |
| 7 | Production deployment | Not started |

---

## Phase 5 (part 1) – Your own MCPs: add through code, start/stop, MCP pages

### What was developed
- **MCPs are added through code.** Each MCP is a folder `mcp_servers/<slug>/`
  with an `mcp.json` (name, description, start command, port). The website
  finds new and changed folders automatically (every 30 s), with the
  **Rescan MCP folders** button, or with `manage.py sync_mcps`. Guide:
  `mcp_servers/README.md`. The MySQL MCP got its `mcp.json`.
- **Start / Stop / Restart** (platform administrators only). Each MCP runs as
  its own process in its folder on its own port, with a clean environment
  (no website secrets). The whole process tree is stopped. The live status
  (Running / Stopped / Stopped unexpectedly / Started outside the platform,
  plus healthy or not) refreshes every 5 seconds.
- **Sidebar:** clicking **MCP Servers** opens the list of MCPs you can see,
  each with a status dot; clicking one opens its page.
- **MCP page with tabs:** *Overview* (description, local address that works
  now, public link for later, your tokens, how the platform runs it);
  *Logs* (live server output, refreshed every 3 s, administrators only, plus
  the activity on this MCP); *Who has access* (grant/remove, and who can use
  it: Restricted / All employees); *User manual* (connect in 3 steps with
  ready-to-copy Claude Code and JSON config, plus the MCP's README).
- **Readable server log** (Logs tab): one block per run (Start → Stop) with
  who started it, how long it ran and how it ended; events in plain words with
  time and level (Info / Warning / Error); health checks and requests counted
  instead of listed; tracebacks folded under "Technical details"; "Problems
  only" filter; the raw file under **Full log** (download).
  `backend/services/mcplogs.py`, tests in `tests/test_mcp_logs.py`.
- **MCP gateway** at `/mcp/<slug>/` (`backend/services/gateway.py`): checks
  the bearer token and permission, logs the connection, forwards the request
  to the MCP on `127.0.0.1:<port>` and streams the answer back (JSON or
  Server-Sent Events). Tells the MCP who is calling (`X-ITrend-User`,
  `X-ITrend-Permission`). Answers 401 (no/invalid token), 404 (unknown MCP),
  503 (MCP stopped), 504 (too slow). The *Connection link* on every MCP page and
  in the User manual now points here (`MCP_PUBLIC_BASE_URL`, default
  `http://127.0.0.1:8000/mcp`). New dependency: `httpx` 0.28.1.
  Tests: `tests/test_gateway.py` (mock MCP + a real process). Manual check:
  the official MCP client listed tools, ran `list_tables` and a JOIN query and
  was refused a `DELETE` through `http://127.0.0.1:8000/mcp/mysql/` with a real
  token; no token / made-up token → 401, unknown MCP → 404.
- **Removed:** the Add MCP page, the Edit page and the slug checker. The MCP
  REST API is now read-only.
- **Activity Logs redesigned:** a timeline of plain sentences ("bob was refused
  by MySQL MCP: the token has expired"), grouped by day, with colour icons,
  a 24-hour summary, category chips (Sign-ins, Connections, Access & tokens,
  MCP servers, Users), "Only problems" and search.

### Files created
- `backend/services/catalog.py`, `backend/services/runner.py`,
  `backend/management/commands/sync_mcps.py`
- `backend/migrations/0003_local_mcp_processes.py`
- `frontend/context_processors.py`, `frontend/markdown.py`
- Templates: `mcps/base_mcp.html`, `mcps/logs.html`, `mcps/access.html`,
  `mcps/manual.html`, `mcps/partials/{state,console}.html`,
  `activity/partials/{timeline,events,event,mcp_name}.html`
- `mcp_servers/README.md`, `mcp_servers/mysql/mcp.json`
- `tests/test_local_mcps.py`

### Files removed
- `mcps/create.html`, `mcps/edit.html`, `mcps/partials/form.html`,
  `mcps/partials/slug_check.html`, `activity/partials/table.html`;
  `MCPServerForm` in `frontend/forms.py`

### Files modified
- `backend/models/registry.py` (deployment type *Code in mcp_servers/*,
  process fields), `backend/models/activity.py` (MCP started/stopped),
  `backend/services/registry.py` (stops a local MCP before deleting it),
  `backend/{views,serializers,permissions,validators}.py` (read-only MCP API,
  more reserved slugs), `config/settings.py`, `.gitignore` (`var/`),
  `frontend/{views,urls,forms}.py`, `frontend/templatetags/mcp_tags.py`,
  `frontend/static/{css/app.css,js/app.js}`, templates `partials/sidebar.html`,
  `mcps/{detail,list}.html`, `mcps/partials/table.html`, `activity/list.html`,
  `dashboard/index.html`; tests `test_registry_pages.py`, `test_registry_api.py`,
  `test_access_pages.py`, `test_tokens_api.py`; `.env.example`, `README.md`,
  `ARCHITECTURE.md`

### Dependencies
- `psutil` 7.2.2: find and stop an MCP's whole process tree safely.
- `markdown-it-py` 4.2.0: show the MCP's README as the User manual (raw HTML off).

### Database changes
Migration `backend.0003_local_mcp_processes`, applied to `itrend_mcp`:
- `MCPServer`: new fields `source_dir`, `process_id`, `process_create_time`,
  `started_at`; new deployment type `local`.
- Activity log: new actions `mcp_started`, `mcp_stopped`.
- Data: the `mysql` entry was updated from its `mcp.json` (type *Code in mcp_servers/*).

### Tests executed
- Website: `uv run python manage.py test`: **209 tests, all passing** (189 before the readable log and the gateway)
  (was 180; tests for the removed Add/Edit pages and API writes were replaced,
  and `test_local_mcps.py` was added).
  The runner tests start and stop a real process (a small web server).
- Real MySQL MCP: started through the runner from a test copy of `mcp.json`
  on port 8103 → healthy, its output appeared in the log, Stop freed the port.
- Every new page rendered against the real database (all `200`). The MySQL
  MCP already running in a terminal on 8101 was shown as
  *Running (started outside the platform) · healthy* and was not touched.

### Commands
```powershell
uv sync
uv run python manage.py migrate
uv run python manage.py sync_mcps      # optional; the website also rescans by itself
uv run python manage.py runserver
```

### Known limitations
- Links work for people who can reach this website. Other computers need the
  website to be hosted or opened to the network (next step).
- MCPs are not restarted automatically after a crash or reboot.
- ZIP/GitHub deployment and Docker are still on hold.

### Next
Phase 5 part 2 (if wanted): automatic restart, per-MCP introspection keys,
ZIP/GitHub sources. Or Phase 6 (monitoring).

---

## Phase 4 – Authentication and permissions

### What was developed
- **Per-MCP permissions** with four levels from the brief: **View** (see it),
  **Connect** (generate tokens and connect), **Manage** (edit its details),
  **Admin** (grant and remove access). Administrators have Admin everywhere;
  MCPs open to *All employees* give every person Connect.
- **Personal access tokens** (`itmcp_…`): 256-bit random, one MCP each, expire
  after 7–365 days, shown once, stored only as a SHA-256 hash, revocable, with
  "last used" time. A token stops working as soon as its owner loses
  permission or is deactivated.
- **Service accounts** (e.g. `svc-ppc-server`) for systems such as the PPC
  server: no password, cannot log in, only explicit permissions, tokens
  created by administrators.
- **Activity log** (`MCPActivityLog`): logins, failed and locked-out logins,
  logouts, MCP create/update/delete, permission grants/removals, token
  creation/revocation, MCP connections (allowed and denied, with reason),
  user activation and service-account creation. Records the client IP.
  Append-only in the admin.
- **Token introspection endpoint** `POST /api/v1/tokens/introspect/` for MCP
  servers, protected by `MCP_INTROSPECTION_KEY`.
- **MySQL MCP now uses platform tokens** (`PlatformTokenVerifier`): asks the
  platform about each token, caches "yes" for 30 s, fails closed if the
  platform is unreachable, and binds tokens to its URL
  (`validate_token_resource=True`). The shared `MCP_API_KEY` is gone.
- **Website pages:** Access Tokens (generate, copy once, revoke); MCP detail
  shows your access level, your tokens and, for MCP admins, *Who has access*
  with grant/remove; Users (search, activate/deactivate, permissions, tokens);
  Add service account; Permissions (all grants, filters, grant form);
  Activity Logs (HTMX filters). Confirmation dialogs before revoking or removing.
- **REST API:** `GET/POST /api/v1/tokens/`, `DELETE /api/v1/tokens/<id>/`,
  `GET /api/v1/users/` (administrators). `PATCH /api/v1/mcps/<id>/` now
  allowed with Manage permission (access policy stays read-only for non-administrators).

### Files created
- `backend/models/__init__.py`, `backend/models/access.py`, `backend/models/activity.py`
  (`backend/models.py` moved to `backend/models/registry.py`)
- `backend/services/access.py`, `tokens.py`, `accounts.py`, `audit.py`; `backend/signals.py`
- `backend/migrations/0002_access_tokens_activity.py`
- Templates: `tokens/{list,create,created}.html`, `tokens/partials/table.html`,
  `permissions/list.html`, `permissions/partials/{table,grant_form}.html`,
  `users/{list,detail,service_account_create}.html`,
  `activity/list.html`, `activity/partials/table.html`, `partials/pager.html`
- Tests: `tests/test_access.py`, `test_tokens.py`, `test_tokens_api.py`,
  `test_access_pages.py`, `test_activity.py`
- MySQL MCP: `tests/test_auth.py`

### Files modified
- Platform: `config/settings.py` (audit middleware, `MCP_INTROSPECTION_KEY`,
  quieter test logs), `backend/{apps,admin,permissions,serializers,views,urls}.py`,
  `backend/services/registry.py` (audit), `frontend/{forms,views,urls}.py`,
  `frontend/templatetags/mcp_tags.py`, `frontend/static/js/app.js`
  (confirmation dialogs), templates `partials/sidebar.html`,
  `mcps/detail.html`, `mcps/partials/form.html`, `tests/factories.py`,
  `.env.example`, `README.md`, `ARCHITECTURE.md`, `DEVELOPMENT_PROGRESS.md`
- MySQL MCP: `src/mysql_mcp/{auth,config,server,demo_client}.py`,
  `scripts/setup_demo_db.py`, `tests/{conftest,test_http}.py`, `.env.example`, `README.md`

### Dependencies
None added. The MCP uses `httpx2`, which the MCP SDK already installs.

### Database changes
Migration `backend.0002_access_tokens_activity`, applied to the local `itrend_mcp` database:
- New tables: `backend_mcppermission` (unique per user + MCP),
  `backend_accesstoken` (unique `token_hash`), `backend_serviceaccount`,
  `backend_mcpactivitylog` (indexes on time, action, user, MCP).
- `MCPServer.access_policy` labels updated (no data change).
- Data: the MySQL MCP was registered in the registry (slug `mysql`, restricted).
- `.env` (platform and MySQL MCP): `MCP_INTROSPECTION_KEY` added by
  `scripts/setup_demo_db.py`. Not committed.

### Tests executed
- Website: `uv run python manage.py test`: **180 tests, all passing** (79 new).
- MySQL MCP: `uv run pytest`: **71 tests, all passing** (17 new), none skipped.
- Manual end-to-end test, real platform + real MySQL MCP + official MCP client,
  with a temporary service account (deleted afterwards):
  - platform token → `list_tables` and a JOIN revenue query returned data; `DELETE` refused;
  - no token, unknown token and the old Phase 3 style key → `401`;
  - permission removed → still `200` for up to 30 s (cache), `401` after 31 s;
  - permission granted again → `200`; token revoked → `401` after 31 s;
  - activity log showed creation, grant, connection (success) and denials
    with reasons `no_permission` and `revoked`, with the client IP.

| Brief requirement | Covered by |
|---|---|
| User-specific MCP permissions (VIEW/CONNECT/MANAGE/ADMIN) | `test_access.py`, `test_access_pages.py` |
| Bearer-token authentication | `test_tokens_api.py` (introspection), MCP `test_auth.py`, `test_http.py` |
| Token generation, shown once, hash only | `test_tokens.py`, `test_access_pages.py` |
| Token expiration / revocation | `test_tokens.py`, MCP `test_http.py`, end-to-end run |
| Expired / revoked token rejected by the MCP (section 18, left open in Phase 3) | end-to-end run, `test_http.py` |
| External service identities | `test_access_pages.py` (service accounts), `test_access.py` |
| Audit logs | `test_activity.py`, `test_tokens.py` |
| Unauthorised user cannot reach an MCP by guessing its URL | `test_access.py`, existing 404 tests |

### Commands
```powershell
uv run python manage.py migrate
cd mcp_servers\mysql; uv run python scripts/setup_demo_db.py   # adds MCP_INTROSPECTION_KEY to both .env files
# restart the website, then: Access Tokens → Generate token → MySQL MCP
uv run mysql-mcp
$env:MCP_TOKEN = "itmcp_..."; uv run python -m mysql_mcp.demo_client
```

### Known limitations
- Revocation reaches a running MCP within 30 s (its token cache), not instantly.
- All MCP servers share one introspection key.
- New people are still created in the Django admin.
- Connection URLs still point to `https://mcp.itrendsolution.com/<slug>/`, which
  needs the Nginx gateway (Phase 7); locally, clients use `http://127.0.0.1:8101/mcp`.
- Cache-based lockout and log throttling are per process until the shared
  cache in Phase 7.

### Next phase
**Phase 5 – Deployment manager** (ZIP/GitHub build, start/stop/restart).
Docker and deployment are on hold, so Phase 5 needs a decision first.

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
