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
  Database         │  PostgreSQL    │  users, registry, permissions,
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
4. **Database (PostgreSQL).** Platform metadata only. Plain-text tokens and MCP
   database passwords are never stored here.

## Code layout

| Location | Responsibility | Phase |
|---|---|---|
| `config/` | Settings (all from environment variables) and root URLs | 1 |
| `backend/accounts/` | Login, logout, profile, failed-login lockout | 1 |
| `backend/dashboard/` | Dashboard page and its HTMX partials | 1 |
| `backend/api/` | Versioned REST API under `/api/v1/` | 1 |
| `backend/registry/` | MCP server registry | 2 |
| `backend/access_control/` | Per-MCP permissions, bearer tokens | 4 |
| `backend/audit/` | Activity and audit logs | 4 |
| `backend/integrations/` | ZIP upload, GitHub, external MCP URLs | 5 |
| `backend/mcp_manager/` | Container lifecycle, health, monitoring | 5–6 |
| `frontend/templates/`, `frontend/static/` | All HTML, CSS, JS | 1+ |
| `mcp_servers/` | Source of platform-hosted MCP servers | 3 |
| `deployment/` | Docker Compose, Nginx | 1, 3, 7 |

**Conventions inside each app:**
- `views.py` handles HTTP only: read the request, call a service, render.
- `services.py` (or a clearly named module such as `health.py`) holds
  business logic, so the website and the API share one implementation.
- `forms.py` validates website forms; `serializers.py` validates API input.

Later-phase apps currently exist only as packages with a docstring. They are
added to `INSTALLED_APPS` in the phase that builds them.

## Key decisions

### Phase 1

| Decision | Why |
|---|---|
| **Django 5.2 LTS** | Long-term support until April 2028. Stability matters more than new features for an internal platform. |
| **PostgreSQL in Docker for development** | Development matches production. SQLite remains as a fallback for quick local work and is blocked when `DEBUG=False`. |
| **Settings from environment variables** (`python-dotenv` loads `.env` locally) | The same code runs everywhere; secrets never live in Git. The app refuses to start without `DJANGO_SECRET_KEY`. |
| **Bootstrap, Bootstrap Icons and HTMX served from our own static files** | No runtime dependency on third-party CDNs, which also makes a strict Content-Security-Policy possible later. |
| **HTMX instead of a JavaScript framework** | The server renders HTML fragments and HTMX swaps them into the page. This keeps all logic in Python and templates. |
| **Login lockout using the cache** | After 5 failures (configurable) for one username from one IP, or 20 from one IP, login is refused for 15 minutes. Locking by username *and* IP stops attackers locking real users out from elsewhere. |
| **Logout only by POST** | Stops other websites logging users out with a hidden link. |
| **DRF requires login by default** | Each API view must opt *out* of authentication explicitly (only `/api/v1/health/` does). |
| **Public health check reveals only up/down** | Load balancers need it unauthenticated; anything more would help attackers. Details go to server logs. |

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
