"""Version 1 of the REST API, mounted at /api/v1/."""

from django.urls import path
from rest_framework.routers import SimpleRouter

from . import views

app_name = "backend"

router = SimpleRouter()
router.register("mcps", views.MCPServerViewSet, basename="mcp")
router.register("tokens", views.AccessTokenViewSet, basename="token")

urlpatterns = [
    path("health/", views.HealthView.as_view(), name="health"),
    path("users/", views.UserListView.as_view(), name="user-list"),
    # Listed before the router so "introspect" is not read as a token id.
    path("tokens/introspect/", views.TokenIntrospectionView.as_view(), name="token-introspect"),
    *router.urls,
]
