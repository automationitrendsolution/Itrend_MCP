"""Version 1 of the REST API, mounted at /api/v1/."""

from django.urls import path
from rest_framework.routers import SimpleRouter

from . import views

app_name = "backend"

router = SimpleRouter()
router.register("mcps", views.MCPServerViewSet, basename="mcp")

urlpatterns = [
    path("health/", views.HealthView.as_view(), name="health"),
    *router.urls,
]
