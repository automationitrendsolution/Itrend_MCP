# Changelog

All notable changes to this project are recorded here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Phase 1 – Project foundation (2026-11-04)

#### Added
- Settings driven by environment variables, with `.env.example`.
- PostgreSQL configuration, a Docker Compose file for local PostgreSQL, and an
  SQLite fallback for development only.
- Django REST Framework (login required by default, pagination, rate limits)
  and the public `GET /api/v1/health/` endpoint.
- Login, logout (POST only) and profile pages.
- Failed-login lockout per username+IP and per IP.
- Dashboard with sidebar navigation and an HTMX-refreshed platform status panel.
- Locally served Bootstrap 5.3.3, Bootstrap Icons 1.11.3 and HTMX 2.0.4.
- Custom 403, 404 and 500 pages.
- Production security settings: secure cookies, HSTS, SSL redirect, frame denial.
- 38 automated tests in `tests/`.
- README, ARCHITECTURE, CHANGELOG and DEVELOPMENT_PROGRESS documents.

#### Changed
- Database moved from MySQL to PostgreSQL. `mysqlclient` removed.
- `backend/` and `frontend/` restructured: `backend/` is now a package of
  Django apps, and `frontend/` holds only templates and static files.
- Django pinned to the 5.2 LTS series.

#### Security
- `SECRET_KEY` moved out of source code into `.env`, and a new key generated.
