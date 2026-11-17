"""Root URL configuration for the iTrend MCP Platform."""

from django.contrib import admin
from django.urls import include, path, re_path

from backend import oauth_views
from backend.views import mcp_gateway

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include("backend.urls")),
    # OAuth sign-in for MCP clients such as Claude in the browser (backend/oauth_views.py).
    # Slashes are optional: clients build these addresses themselves.
    re_path(r"^\.well-known/oauth-protected-resource/mcp/(?P<slug>[a-z][a-z0-9-]{1,48}[a-z0-9])/?$",
            oauth_views.protected_resource_metadata, name="oauth-resource-metadata"),
    re_path(r"^\.well-known/oauth-authorization-server/?$", oauth_views.authorization_server_metadata, name="oauth-metadata"),
    re_path(r"^oauth/register/?$", oauth_views.register, name="oauth-register"),
    re_path(r"^oauth/authorize/?$", oauth_views.authorize, name="oauth-authorize"),
    re_path(r"^oauth/token/?$", oauth_views.token, name="oauth-token"),
    # The MCP gateway. Both /mcp/<slug> and /mcp/<slug>/… work (no slash redirects: they break POST).
    re_path(r"^mcp/(?P<slug>[a-z][a-z0-9-]{1,48}[a-z0-9])(?P<rest>/.*)?$", mcp_gateway, name="mcp-gateway"),
    path("", include("frontend.urls")),
]
