"""Data every page needs: the MCP list shown under "MCP Servers" in the sidebar."""

from backend.models import MCPServer
from backend.services import catalog


def sidebar(request) -> dict:
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    catalog.sync_if_due()  # picks up new or changed mcp_servers/<slug>/mcp.json files

    match = request.resolver_match
    current_pk = None
    if match and match.view_name.startswith("frontend:mcp-") and "pk" in match.kwargs:
        current_pk = match.kwargs["pk"]
    return {
        "sidebar_mcps": MCPServer.objects.visible_to(user).only("pk", "name", "status").order_by("name"),
        "sidebar_mcp_pk": current_pk,
        "sidebar_in_mcps": bool(match and match.view_name.startswith("frontend:mcp-")),
    }
