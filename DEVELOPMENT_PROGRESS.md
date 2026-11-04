# Development Progress

| Phase | Name | Status |
|---|---|---|
| 1 | Project foundation | ✅ Complete (2026-11-04) |
| 2 | MCP registry | ⏳ Next |
| 3 | First remote MCP | Not started |
| 4 | Authentication and permissions | Not started |
| 5 | Deployment manager | Not started |
| 6 | Monitoring | Not started |
| 7 | Production deployment | Not started |

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
