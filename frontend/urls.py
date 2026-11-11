from django.urls import path

from . import views

app_name = "frontend"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("dashboard/system-status/", views.system_status, name="system-status"),
    path("pulse/", views.pulse, name="pulse"),
    # Accounts
    path("accounts/login/", views.LoginView.as_view(), name="login"),
    path("accounts/logout/", views.LogoutView.as_view(), name="logout"),
    path("accounts/profile/", views.profile, name="profile"),
    # MCP servers
    path("mcps/", views.mcp_list, name="mcp-list"),
    path("mcps/rescan/", views.mcp_rescan, name="mcp-rescan"),
    path("mcps/<int:pk>/", views.mcp_detail, name="mcp-detail"),
    path("mcps/<int:pk>/logs/", views.mcp_logs, name="mcp-logs"),
    path("mcps/<int:pk>/logs/output/", views.mcp_log_output, name="mcp-log-output"),
    path("mcps/<int:pk>/logs/download/", views.mcp_log_download, name="mcp-log-download"),
    path("mcps/<int:pk>/access/", views.mcp_access, name="mcp-access"),
    path("mcps/<int:pk>/access/policy/", views.mcp_access_policy, name="mcp-access-policy"),
    path("mcps/<int:pk>/manual/", views.mcp_manual, name="mcp-manual"),
    path("mcps/<int:pk>/tokens/", views.mcp_tokens, name="mcp-tokens"),
    path("mcps/<int:pk>/settings/", views.mcp_settings, name="mcp-settings"),
    path("mcps/<int:pk>/test/", views.mcp_test, name="mcp-test"),
    path("mcps/<int:pk>/state/", views.mcp_state, name="mcp-state"),
    path("mcps/<int:pk>/control/<str:action>/", views.mcp_control, name="mcp-control"),
    path("mcps/<int:pk>/delete/", views.mcp_delete, name="mcp-delete"),
    # Access tokens
    path("tokens/", views.token_list, name="token-list"),
    path("tokens/add/", views.token_create, name="token-create"),
    path("tokens/<int:pk>/revoke/", views.token_revoke, name="token-revoke"),
    path("tokens/<int:pk>/delete/", views.token_delete, name="token-delete"),
    # Permissions
    path("permissions/", views.permission_list, name="permission-list"),
    path("permissions/grant/", views.permission_grant, name="permission-grant"),
    path("permissions/<int:pk>/remove/", views.permission_revoke, name="permission-revoke"),
    # Users and service accounts
    path("users/", views.user_list, name="user-list"),
    path("users/add/", views.user_create, name="user-create"),
    path("users/service-accounts/add/", views.service_account_create, name="service-account-create"),
    path("users/<int:pk>/abilities/<str:code>/", views.user_capability, name="user-capability"),
    path("users/<int:pk>/", views.user_detail, name="user-detail"),
    path("users/<int:pk>/active/", views.user_set_active, name="user-set-active"),
    path("users/<int:user_pk>/tokens/add/", views.token_create, name="user-token-create"),
    # Activity log
    path("activity/", views.activity_list, name="activity-list"),
    # Monitoring
    path("monitoring/", views.monitoring_overview, name="monitoring"),
    path("monitoring/<int:pk>/", views.monitoring_detail, name="monitoring-detail"),
]
