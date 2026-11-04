"""Root URL configuration for the iTrend MCP Platform."""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("backend.accounts.urls")),
    path("api/v1/", include("backend.api.urls")),
    path("", include("backend.dashboard.urls")),
]
