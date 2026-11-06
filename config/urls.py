"""Root URL configuration for the iTrend MCP Platform."""

from django.contrib import admin
from django.urls import include, path, re_path

from backend.views import mcp_gateway

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include("backend.urls")),
    # The MCP gateway. Both /mcp/<slug> and /mcp/<slug>/… work (no slash redirects: they break POST).
    re_path(r"^mcp/(?P<slug>[a-z][a-z0-9-]{1,48}[a-z0-9])(?P<rest>/.*)?$", mcp_gateway, name="mcp-gateway"),
    path("", include("frontend.urls")),
]
