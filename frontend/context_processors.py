"""Data every page needs: the sidebar's MCP list and which section the page belongs to."""

from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from backend.models import MCPMonitorEvent, MCPServer
from backend.services import catalog

ALERTS_SHOWN = 5

# Page (URL name) → navigation section: (key, label shown in breadcrumbs, URL name of the section's main page).
# Prefixes are matched in order, so more specific entries come first.
SECTIONS = [
    ("frontend:dashboard", ("overview", "Overview", "frontend:dashboard")),
    ("frontend:mcp-", ("mcps", "MCP Registry", "frontend:mcp-list")),
    ("frontend:user-token-create", ("users", "Users", "frontend:user-list")),
    ("frontend:token-", ("tokens", "Access Tokens", "frontend:token-list")),
    ("frontend:user-", ("users", "Users", "frontend:user-list")),
    ("frontend:service-account-", ("users", "Users", "frontend:user-list")),
    ("frontend:permission-", ("permissions", "Permissions", "frontend:permission-list")),
    ("frontend:activity-", ("audit", "Audit Logs", "frontend:activity-list")),
    ("frontend:monitoring", ("monitoring", "Monitoring", "frontend:monitoring")),
    ("frontend:profile", ("profile", "Profile", "frontend:profile")),
]


def _section(view_name: str) -> dict | None:
    for prefix, (key, label, url_name) in SECTIONS:
        if view_name.startswith(prefix):
            return {"key": key, "label": label, "url": reverse(url_name)}
    return None


def sidebar(request) -> dict:
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    catalog.sync_if_due()  # picks up new or changed mcp_servers/<slug>/mcp.json files

    match = request.resolver_match
    view_name = match.view_name if match else ""
    current_pk = None
    if view_name.startswith("frontend:mcp-") and "pk" in match.kwargs:
        current_pk = match.kwargs["pk"]
    from backend.services import monitoring
    from .views import with_last_check

    from backend.permissions import capabilities_of

    caps = capabilities_of(user)
    data = {
        "caps": caps,
        # Annotated with the latest check, so each MCP's dot uses the same status rule as everywhere else.
        "sidebar_mcps": with_last_check(MCPServer.objects.visible_to(user)).order_by("name"),
        "sidebar_mcp_pk": current_pk,
        "sidebar_in_mcps": view_name.startswith("frontend:mcp-"),
        "nav_section": _section(view_name),
    }
    if (caps["view_monitoring"] or caps["manage_mcps"]) and MCPServer.objects.filter(status=MCPServer.Status.RUNNING, deployment_type=MCPServer.DeploymentType.LOCAL).exists():
        # The one system banner: platform-wide problems only. Pages refer to it instead of repeating it.
        if not monitoring.worker_is_running():
            seen = monitoring.worker_last_seen()
            data["system_banner"] = {
                "text": "The monitor is not running — no health checks or automatic restarts"
                + (f" since {timezone.localtime(seen):%H:%M}." if seen else "."),
                "command": "uv run python manage.py monitor_mcps",
            }
    if caps["view_monitoring"]:
        # The sidebar's platform status line (bottom): is the monitor running?
        data["sidebar_platform"] = {"ok": monitoring.worker_is_running()}
        # The header's notifications: real monitoring warnings and errors of the last 24 hours.
        recent = MCPMonitorEvent.objects.filter(
            created_at__gte=timezone.now() - timedelta(hours=24),
            level__in=[MCPMonitorEvent.Level.WARNING, MCPMonitorEvent.Level.ERROR],
        ).select_related("mcp_server")
        data["alerts"] = list(recent[:ALERTS_SHOWN])
        data["alert_count"] = recent.count()
    return data
