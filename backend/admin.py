from django.contrib import admin

from .models import AccessToken, MCPActivityLog, MCPDeployment, MCPPermission, MCPServer, ServiceAccount


class MCPDeploymentInline(admin.TabularInline):
    model = MCPDeployment
    extra = 0
    fields = ("deployment_status", "source_revision", "image_name", "created_at", "deployed_at")
    readonly_fields = ("created_at",)


@admin.register(MCPServer)
class MCPServerAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "deployment_type", "status", "health_status", "access_policy", "created_at")
    list_filter = ("deployment_type", "status", "health_status", "access_policy")
    search_fields = ("name", "slug", "description")
    readonly_fields = ("created_by", "created_at", "updated_at")
    inlines = [MCPDeploymentInline]

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        return (*fields, "slug", "deployment_type") if obj else fields

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(MCPDeployment)
class MCPDeploymentAdmin(admin.ModelAdmin):
    list_display = ("mcp_server", "deployment_status", "source_revision", "created_at", "deployed_at")
    list_filter = ("deployment_status", "deployment_type")
    search_fields = ("mcp_server__name", "source_revision")


@admin.register(MCPPermission)
class MCPPermissionAdmin(admin.ModelAdmin):
    list_display = ("user", "mcp_server", "permission_level", "granted_by", "updated_at")
    list_filter = ("permission_level", "mcp_server")
    search_fields = ("user__username", "mcp_server__name", "mcp_server__slug")
    raw_id_fields = ("user",)
    readonly_fields = ("granted_by", "created_at", "updated_at")

    def save_model(self, request, obj, form, change):
        obj.granted_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(AccessToken)
class AccessTokenAdmin(admin.ModelAdmin):
    """Tokens are created on the website (so the value is shown once). Here they can only be inspected."""

    list_display = ("name", "token_prefix", "user", "mcp_server", "expires_at", "revoked_at", "last_used_at")
    list_filter = ("mcp_server",)
    search_fields = ("name", "token_prefix", "user__username")
    exclude = ("token_hash",)
    readonly_fields = (
        "user", "mcp_server", "name", "token_prefix", "created_by", "created_at",
        "expires_at", "revoked_at", "revoked_by", "last_used_at",
    )

    def has_add_permission(self, request):
        return False


@admin.register(ServiceAccount)
class ServiceAccountAdmin(admin.ModelAdmin):
    list_display = ("user", "description", "created_by", "created_at")
    readonly_fields = ("user", "created_by", "created_at")

    def has_add_permission(self, request):
        # Created on the website so the account gets no usable password.
        return False


@admin.register(MCPActivityLog)
class MCPActivityLogAdmin(admin.ModelAdmin):
    """The activity log is append-only: nobody can add, change or delete entries here."""

    list_display = ("timestamp", "username", "action", "result", "mcp_slug", "source_ip")
    list_filter = ("action", "result")
    search_fields = ("username", "mcp_slug", "source_ip")
    date_hierarchy = "timestamp"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
