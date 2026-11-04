# Changelog

All notable changes to this project are recorded here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Phase 1 – Project foundation (2026-11-04)

#### Added
- Settings driven by environment variables, with `.env.example`.
- MySQL configuration (utf8mb4, strict mode) read from `.env`, with an
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
- The project brief's PostgreSQL choice was replaced by MySQL 8, the server
  already used locally. Docker is not used for now.
- Kept the original two-app layout: `frontend/` holds everything user-facing
  (views, forms, templates, static files), and `backend/` holds models,
  business logic in `backend/services/`, and the REST API.
- Django pinned to the 5.2 LTS series.

#### Security
- `SECRET_KEY` moved out of source code into `.env`, and a new key generated.
