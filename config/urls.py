"""Root URL configuration for the iTrend MCP Platform."""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include("backend.urls")),
    path("", include("frontend.urls")),
]
