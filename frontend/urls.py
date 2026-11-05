from django.urls import path

from . import views

app_name = "frontend"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("dashboard/system-status/", views.system_status, name="system-status"),
    path("accounts/login/", views.LoginView.as_view(), name="login"),
    path("accounts/logout/", views.LogoutView.as_view(), name="logout"),
    path("accounts/profile/", views.profile, name="profile"),
    path("mcps/", views.mcp_list, name="mcp-list"),
    path("mcps/add/", views.mcp_create, name="mcp-create"),
    path("mcps/check-slug/", views.mcp_slug_check, name="mcp-slug-check"),
    path("mcps/<int:pk>/", views.mcp_detail, name="mcp-detail"),
    path("mcps/<int:pk>/edit/", views.mcp_edit, name="mcp-edit"),
    path("mcps/<int:pk>/delete/", views.mcp_delete, name="mcp-delete"),
]
