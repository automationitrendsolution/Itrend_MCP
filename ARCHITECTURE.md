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
    templatetags/        template helpers (status badge colours)
    templates/           all HTML; Django finds it automatically (APP_DIRS)
    static/              CSS, JS, images, vendored Bootstrap/Icons/HTMX
backend/                 Django app: data, logic and API
    models.py            MCPServer, MCPDeployment
    validators.py        slug, URL and configuration rules
    permissions.py       who may do what (website and API)
    admin.py             Django admin registrations
    migrations/          database migrations
    services/            business logic, shared by website and API
        registry.py          create / update / delete / list MCP servers
        health.py            platform health checks
        login_protection.py  failed-login lockout
    serializers.py       REST API input/output
    exceptions.py        one error format for the whole API
    views.py             REST API views
    urls.py              REST API URLs at /api/v1/  (namespace "backend")
mcp_servers/             source of platform-hosted MCP servers (Phase 3)
tests/                   automated tests
```

**Where new code goes:**

| Kind of code | Location |
|---|---|
| A new page or HTMX partial | `frontend/views.py` + `frontend/urls.py` + a template in `frontend/templates/` |
| A website form | `frontend/forms.py` |
| CSS / JS / images | `frontend/static/` |
| A database model | `backend/models.py` |
| Business logic (registry, tokens, deployment, monitoring, audit…) | a module in `backend/services/`, e.g. `services/registry.py` |
| A REST API endpoint | `backend/views.py` + `backend/urls.py`, input validation in `backend/serializers.py` |

**Rules:**
- `frontend` may import from `backend` (for example, a page calls a service).
  `backend` never imports from `frontend`.
- Views only handle HTTP. They call `backend.services`, so the website and
  the API share one implementation of every rule.
- When one file gets too large, it becomes a package inside the same app
  (for example `backend/models.py` → `backend/models/registry.py`,
  `backend/models/tokens.py`, …). No new apps are created for this.

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
- Audit logging to the database arrives in Phase 4. Until then, logins and
  failures go to the application log only.
