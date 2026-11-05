# Changelog

All notable changes to this project are recorded here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Phase 2 – MCP registry (2026-11-05)

#### Added
- `MCPServer` and `MCPDeployment` models with migration `backend.0001_initial`.
- MCP list (live search, filters, paging), detail, add, edit and delete pages.
- Live slug availability check and type-specific form fields.
- `/api/v1/mcps/` REST API (list, create, retrieve, PATCH, delete) with
  administrator-only write access and internal fields.
- Consistent API error format `{"error": {"code", "message", "details"}}`.
- Real MCP counts and recently added MCPs on the dashboard.
- `MCP_PUBLIC_BASE_URL` setting.
- 63 new tests (101 in total).

#### Security
- Slugs cannot reuse website paths and cannot be changed after creation.
- Configuration rejects keys that look like secrets.
- MCPs a user may not see return 404, so they cannot be discovered by guessing.
- Internal endpoint and configuration hidden from non-administrators.

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
