# Changelog

All notable changes to the iTrend MCP Platform. Newest first. Details for each
phase are in [DEVELOPMENT_PROGRESS.md](DEVELOPMENT_PROGRESS.md).

## 2026-11-06 – Phase 6: Monitoring

### Added
- Monitor worker `manage.py monitor_mcps`: real MCP health checks, CPU/RAM per
  MCP, incidents, and safe automatic restarts (crash, stopped answering, over
  the memory limit; at most 3 per hour by default).
- Gateway traffic counts per MCP (requests, failed, refused, response time).
- Per-MCP limits: check interval, request timeout, requests at the same time,
  memory limit, CPU warning level, restart rules.
- Monitoring pages `/monitoring/` and `/monitoring/<id>/` (administrators).
- API `GET /api/v1/mcps/<id>/health/`.
- Migration `0004_monitoring` (four new tables).

### Changed
- The gateway answers `429 mcp_busy` when an MCP already has its maximum number
  of requests in progress (default 20; it was unlimited before).

## 2026-11-06 – Phase 5 (part 1): Your own MCPs from code
- MCPs are added as `mcp_servers/<slug>/mcp.json`; start/stop/restart as local
  processes; MCP pages with tabs; readable server log; MCP gateway `/mcp/<slug>/`.

## 2026-11-06 – Phase 4: Authentication and permissions
- Per-MCP permissions, personal access tokens, service accounts, activity log,
  token introspection.

## 2026-11-05 – Phase 3: First remote MCP
- Read-only MySQL MCP over Streamable HTTP (`mcp_servers/mysql/`).

## 2026-11-05 – Phase 2: MCP registry
- MCP models, pages and REST API.

## 2026-11-04 – Phase 1: Project foundation
- Django project, login, dashboard, REST framework, health API.
