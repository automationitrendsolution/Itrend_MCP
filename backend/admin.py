from django.contrib import admin

from .models import MCPDeployment, MCPServer


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
