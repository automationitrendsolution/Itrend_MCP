# Architecture

This document explains how the iTrend MCP Platform is put together and why.
It is updated at the end of every phase.

## The problem being solved

MCP servers currently run on the PPC production server and compete with the
PPC application for CPU, memory and database connections. The platform moves
MCP execution to a separate Linux server. The PPC server becomes an MCP
*client* that calls the remote MCP servers over HTTPS.

## The four layers

```
                    Internet / PPC server / employees
                                   │  HTTPS
                    ┌──────────────▼──────────────┐
  Layer 2 Gateway   │            Nginx             │  TLS, routing, streaming,
                    └───────┬──────────────┬───────┘  token check via Django
                            │              │
          /, /api/v1/, /admin/       /mysql/, /itrend/, /powerbi/ ...
                            │              │
  Layer 1          ┌────────▼───────┐ ┌────▼─────────┐ ┌──────────────┐
  Management       │ Django website │ │ mcp-mysql    │ │ mcp-itrend   │  Layer 3
  website          │ + REST API     │ │ container    │ │ container    │  MCP execution
                   └────────┬───────┘ └──────────────┘ └──────────────┘  (one per MCP)
                            │
  Layer 4          ┌────────▼───────┐
  Database         │  MySQL 8       │  users, registry, permissions,
                   └────────────────┘  token hashes, audit logs
```

1. **Management website (Django).** Login, users, MCP registry, permissions,
   tokens, monitoring pages and audit logs. Django never runs uploaded MCP code.
2. **Gateway (Nginx).** Terminates HTTPS and routes each URL prefix to the right
   service. Nginx cannot understand our tokens by itself, so for MCP traffic it
   asks Django to approve each request (the `auth_request` pattern, Phase 4).
3. **MCP execution.** Each MCP runs in its own container with its own
   dependencies, environment, CPU and memory limits, health check and logs.
   One MCP failing cannot take down another one, or the website.
4. **Database (MySQL).** Platform metadata only. Plain-text tokens and MCP
   database passwords are never stored here.

## Code layout

The project has exactly two Django apps. **Anything the user sees goes in
`frontend/`; everything behind it goes in `backend/`.**

```
config/                  settings (from environment variables) and root URLs
frontend/                Django app: the website
    views.py             page views (login, dashboard, MCP pages, HTMX partials)
    urls.py              website URLs  (namespace "frontend")
    forms.py             website forms
    templatetags/        template helpers (status badge colours, activity sentences)
    context_processors.py the MCP list shown in the sidebar
    markdown.py          shows an MCP's README as its User manual (raw HTML disabled)
    templates/           all HTML; Django finds it automatically (APP_DIRS)
    static/              CSS, JS, images, vendored Bootstrap/Icons/HTMX
backend/                 Django app: data, logic and API
    models/              database models, one file per topic
        registry.py          MCPServer, MCPDeployment
        access.py            PermissionLevel, MCPPermission, AccessToken, ServiceAccount
        activity.py          MCPActivityLog (audit log)
    signals.py           writes logins, logouts and failed logins to the activity log
    validators.py        slug, URL and configuration rules
    permissions.py       who may do what (website and API)
    admin.py             Django admin registrations
    migrations/          database migrations
    services/            business logic, shared by website and API
        registry.py          create / update / delete / list MCP servers
        catalog.py           finds your MCPs: mcp_servers/<slug>/mcp.json → registry entries
        runner.py            starts / stops / watches each MCP as a local process; its logs
        mcplogs.py           turns an MCP's raw output into readable runs and events
        gateway.py           /mcp/<slug>/: checks the token, forwards to the MCP, streams back
        access.py            permission levels: who may view / connect / manage / administer an MCP
        tokens.py            create, revoke and check (introspect) access tokens
        accounts.py          user activation, service accounts
        audit.py             activity log + middleware that records the client IP
        health.py            platform health checks
        login_protection.py  failed-login lockout
    serializers.py       REST API input/output
    exceptions.py        one error format for the whole API
    views.py             REST API views
    urls.py              REST API URLs at /api/v1/  (namespace "backend")
    management/commands/sync_mcps.py   register MCP folders from the command line
mcp_servers/             MCP servers; each is a separate uv project, NOT part of Django
    mysql/               read-only MySQL MCP (own pyproject.toml, uv.lock, .venv, tests)
tests/                   automated tests
```

**Where new code goes:**

| Kind of code | Location |
|---|---|
| A new page or HTMX partial | `frontend/views.py` + `frontend/urls.py` + a template in `frontend/templates/` |
| A website form | `frontend/forms.py` |
| CSS / JS / images | `frontend/static/` |
| A database model | the matching file in `backend/models/`, exported from `backend/models/__init__.py` |
| Business logic (registry, tokens, deployment, monitoring, audit…) | a module in `backend/services/`, e.g. `services/registry.py` |
| A REST API endpoint | `backend/views.py` + `backend/urls.py`, input validation in `backend/serializers.py` |

**Rules:**
- `frontend` may import from `backend` (for example, a page calls a service).
  `backend` never imports from `frontend`.
- Views only handle HTTP. They call `backend.services`, so the website and
  the API share one implementation of every rule.
- When one file gets too large, it becomes a package inside the same app
  (Phase 4 did this for `backend/models.py` → `backend/models/`). No new apps
  are created for this.

## Key decisions

### Phase 1

| Decision | Why |
|---|---|
| **Django 5.2 LTS** | Supported until April 2028, and it supports MySQL 8.0 (Django 6.x needs MySQL 8.4+). Stability matters more than new features for an internal platform. |
| **MySQL 8 (local server for development)** | Uses the MySQL server the team already runs, so no extra setup is needed. Strict mode and utf8mb4 are on. SQLite remains as a fallback for quick local work and is blocked when `DEBUG=False`. |
| **Settings from environment variables** (`python-dotenv` loads `.env` locally) | The same code runs everywhere; secrets never live in Git. The app refuses to start without `DJANGO_SECRET_KEY`. |
| **Bootstrap, Bootstrap Icons and HTMX served from our own static files** | No runtime dependency on third-party CDNs, which also makes a strict Content-Security-Policy possible later. |
| **HTMX instead of a JavaScript framework** | The server renders HTML fragments and HTMX swaps them into the page. This keeps all logic in Python and templates. |
| **Login lockout using the cache** | After 5 failures (configurable) for one username from one IP, or 20 from one IP, login is refused for 15 minutes. Locking by username *and* IP stops attackers locking real users out from elsewhere. |
| **Logout only by POST** | Stops other websites logging users out with a hidden link. |
| **DRF requires login by default** | Each API view must opt *out* of authentication explicitly (only `/api/v1/health/` does). |
| **Public health check reveals only up/down** | Load balancers need it unauthenticated; anything more would help attackers. Details go to server logs. |

### Phase 2 – MCP registry

| Decision | Why |
|---|---|
| **Hosted MCPs get their URL from the slug** (`MCP_PUBLIC_BASE_URL/<slug>/`); only external MCPs store `endpoint` | The URL is never stored twice, so it cannot go stale if the domain changes. |
| **Slug and deployment type are fixed after creation** | The slug is part of every client's connection URL; changing it would silently break them. Enforced in the service, the form and the API. |
| **Reserved slugs** (`admin`, `api`, `static`, `mcps`, …) | MCP URLs share the domain with the website, so an MCP must never take over a website path. |
| **New MCPs are "Restricted" by default** | Safe default: until per-user permissions arrive (Phase 4), restricted MCPs are visible to administrators only. "All employees" makes one visible to every logged-in user. |
| **Hidden MCPs return 404, not 403** | A 403 would confirm the MCP exists. 404 stops people discovering MCPs by guessing IDs. |
| **Configuration rejects secret-looking keys** (`password`, `token`, `api_key`, …) | The registry is not a safe place for credentials. Secrets get a proper store in Phase 5. |
| **Internal endpoint and configuration are administrator-only**, on the website and in the API | They describe internal infrastructure that ordinary users and clients do not need. |
| **External MCPs carry a visible warning** | Their traffic does not pass through the platform, so platform tokens and permissions cannot protect them. |
| **Last deployment time is read from the deployment history**, not stored on the server | Avoids two copies of the same fact that could disagree. |
| **One API error format**: `{"error": {"code", "message", "details"}}` | Clients handle every error the same way; unexpected errors never leak technical details. |
| **Status and health are set by hand for now** | They become automatic with deployments (Phase 5) and health checks (Phase 6). The form says so. |

### Phase 3 – First remote MCP

| Decision | Why |
|---|---|
| **Each MCP is its own uv project** in `mcp_servers/<name>/` | Its packages never enter the website's environment, and one MCP cannot break another's dependencies. It can later be built into its own container image unchanged. |
| **Local process for now, not Docker** | Docker and deployment are on hold. Nothing in the code depends on how it is started. |
| **Official MCP Python SDK 2.x, Streamable HTTP** | The standard remote transport; works for both current and older (initialize-handshake) clients, as the tests show. |
| **Authentication through the SDK's `TokenVerifier`** | Gives standards-compliant `401` + `WWW-Authenticate` responses. Phase 4 swaps the verifier, not the server. |
| **Read-only enforced by MySQL, not just by our code** | A SELECT-only account and READ ONLY transactions mean a bug in the SQL guard still cannot change data. |
| **A separate `mcp_demo` database for testing** | The MCP must never be pointed at the platform's own database, which holds users and password hashes. |

### Phase 4 – Authentication and permissions

How a request to an MCP is authorised:

```
MCP client ──Bearer itmcp_…──▶ MySQL MCP ──POST /api/v1/tokens/introspect/──▶ Django platform
                                  │          (token + "mysql", authenticated     │ checks: token exists (by hash),
                                  │           with MCP_INTROSPECTION_KEY)        │ not revoked, not expired, issued for
                                  ◀────────── {"active": true/false} ────────────┘ this MCP, owner active, owner has
                                  │                                               CONNECT; writes activity log
                     401 or runs the tool
```

| Decision | Why |
|---|---|
| **Four levels: View < Connect < Manage < Admin**, each including the one before | Follows the brief. View = see it; Connect = get tokens; Manage = edit details; Admin = grant access. Deleting MCPs, changing access policy and internal settings stay with platform administrators. |
| **Effective level = highest of: staff → Admin; "All employees" → Connect; explicit grant** | One rule, computed in one place (`services/access.py`), used by the website, the API and token checks. |
| **Service accounts are Django users with no usable password**, plus a `ServiceAccount` record | Permissions and tokens work the same for people and systems, but a service account can never log in, never gets the "All employees" access, and its tokens are created by an administrator. |
| **One token = one MCP, always expires (7–365 days)** | Least privilege: a leaked token exposes one MCP for a limited time. |
| **Only a SHA-256 hash of each token is stored**; the token is shown once, in the response to the form, never stored in the session | A database leak reveals no usable tokens. Tokens are 256-bit random values, so a fast hash is safe (slow hashes are for human passwords). |
| **Tokens are checked live against current permissions** | Removing someone's permission or deactivating them stops their tokens at once, without having to find and revoke each token. |
| **MCP servers ask the platform (introspection, like OAuth RFC 7662)** instead of reading the database | MCPs stay isolated: no database credentials, no Django code. The same endpoint can serve the Nginx gateway later. |
| **The MCP caches "yes" for 30 seconds and fails closed** | Avoids one platform call per message; revocation still takes effect within 30 s; if the platform is down, nobody gets in. |
| **Introspection answers only `active: false`, never why** | The reason is in the activity log for administrators; callers learn nothing that helps an attacker. |
| **Activity log is append-only** and copies the username and MCP slug into each row | Cannot be edited in the admin; entries stay readable after a user or MCP is deleted. |
| **Repeated connection events are logged at most once per token every 5 minutes** | Every MCP message checks the token; logging each one would flood the database. |

### Phase 5 (part 1) – Your own MCPs, added through code

```
mcp_servers/<slug>/mcp.json ──catalog.sync()──▶ MCPServer row (name, description, port; Restricted, Stopped)
                                                    │
Administrator clicks Start ──runner.start()──▶ runs "command" in the folder, MCP_PORT=<port>,
                                                output → var/mcp_logs/<slug>.log (Logs tab)
Status badge (every 5 s) ──runner.get_state()──▶ is our process alive? does /health answer?
```

| Decision | Why |
|---|---|
| **MCPs are added through code** (`mcp.json` per folder), not a web form | The user builds the MCPs; the code is the single source of truth. Name, description, command and port come from the file. Who has access stays on the website. |
| **No Add/Edit pages; the MCP API is read-only** | One way to add an MCP avoids two sources of truth. Access policy moved to the *Who has access* tab. |
| **Each MCP runs as a plain local process** (no Docker, as decided) | Simple on Windows; the same command developers already use (`uv run …`). |
| **Only platform administrators can Start/Stop and see process output** | Starting programs on the server is powerful; output can contain internal details. MCP-level Admins see the activity of their MCP. |
| **MCPs get a clean environment**: system paths plus `MCP_PORT`, `MCP_SLUG` | The website's secret key and database password are never passed to an MCP; each MCP reads its own `.env`. |
| **Process ID and start time are both remembered** | A recycled process ID belonging to another program is never stopped by mistake. |
| **The whole process tree is stopped** (`psutil`) | `uv run` starts child processes; stopping only the parent would leave the MCP running. |
| **A copy started in a terminal is detected, not touched** | If the port already answers and it is not ours, the page shows "started outside the platform" instead of killing someone else's process. |
| **Folders are rescanned at most every 30 s on page loads**, plus a Rescan button and `manage.py sync_mcps` | New MCPs appear without restarting the website, without scanning on every request. |
| **The User manual is the MCP's README, rendered with raw HTML disabled** | Authors write normal Markdown; nothing in a README can run scripts in the website. |
| **Activity Logs redesigned as a timeline of plain sentences**, grouped by day, with category chips and "only problems" | Easier to read than a table of codes; the same events and data are recorded as before. |

### MCP gateway (part of Phase 7, built early)

```
MCP client ──POST http://<platform>/mcp/mysql/  Bearer itmcp_…──▶ Django (backend/views.mcp_gateway)
                 1. slug → MCPServer (404 if unknown or external)
                 2. tokens.introspect(token, slug) → 401 if not allowed (reason only in the activity log)
                 3. forward method, body and a fixed list of headers + X-ITrend-User / X-ITrend-Permission
                    ──▶ http://127.0.0.1:8101/mcp   (503 if the MCP is not running, 504 if too slow)
                 4. stream the answer back as it arrives (JSON or Server-Sent Events)
```

| Decision | Why |
|---|---|
| **The gateway is part of the website** (`/mcp/<slug>/`), not a separate program | One thing to run on Windows, no Docker. Token checks reuse the same code as everything else. In production Nginx simply passes `/mcp/` to Django. |
| **Allow-list of headers in both directions** | Cookies, Host and internal headers never cross; clients cannot fake `X-ITrend-User`. |
| **Answers are streamed, never buffered** (`httpx`, `iter_raw`, `X-Accel-Buffering: no`) | MCP replies can be Server-Sent Events that stay open; buffering would break them. |
| **No CSRF and no session on the gateway; bearer token only** | MCP clients are programs, not browsers. |
| **MCPs still check tokens themselves if they want** (the MySQL MCP does) | Defence in depth; MCPs written without token code are still protected by the gateway as long as they listen on 127.0.0.1. |

## Security baseline (Phase 1)

- Secret key, database password and debug mode come from `.env`, which Git ignores.
- Password validators: minimum 12 characters, not common, not numeric-only,
  not similar to the username.
- Sessions: HTTP-only cookies, 8-hour lifetime, end when the browser closes.
- With `DEBUG=False`: secure cookies, HTTPS redirect, HSTS for one year.
- `X-Frame-Options: DENY`, `nosniff` and a `same-origin` referrer policy.
- CSRF protection on every form, and on HTMX requests through the
  `hx-headers` attribute in `base.html`.
- Error pages (403/404/500) never show technical details.
- Only `REMOTE_ADDR` is trusted as the client IP. The Nginx configuration in
  Phase 7 must set it to the real client address.

## Known limitations to resolve later

- The cache is per-process memory, so lockout and API limits are counted
  separately in each worker. Phase 7 moves to a shared cache.
- The token cache in each MCP means revocation takes up to
  `MCP_TOKEN_CACHE_SECONDS` (30 s) to take effect.
- One introspection key is shared by all MCP servers. Phase 5 can give each
  hosted MCP its own key.
- New *people* are still created in the Django admin; the website manages
  activation, permissions and service accounts.
- The gateway runs inside the development server, so each open event stream
  uses one thread. For many users, run Django under a production server (Phase 7).
- People on other computers can use the links only once the website itself is
  reachable from their network (next step: hosting).
- MCPs do not restart by themselves after a crash or a computer reboot; an
  administrator clicks Start.
- MCP log files are rotated at 5 MB (one old copy kept).
