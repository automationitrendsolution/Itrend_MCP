"""Data every page needs: the sidebar's MCP list and which section the page belongs to."""

from django.urls import reverse

from backend.models import MCPServer
from backend.services import catalog

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
    return {
        "sidebar_mcps": MCPServer.objects.visible_to(user).only("pk", "name", "status").order_by("name"),
        "sidebar_mcp_pk": current_pk,
        "sidebar_in_mcps": view_name.startswith("frontend:mcp-"),
        "nav_section": _section(view_name),
    }
