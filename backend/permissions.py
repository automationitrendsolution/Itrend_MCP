"""Who may do what. Shared by website views and the REST API."""

from rest_framework.permissions import SAFE_METHODS, BasePermission


def can_manage_mcps(user) -> bool:
    """Create, edit and delete MCP servers. Per-MCP MANAGE/ADMIN permissions arrive in Phase 4."""
    return user.is_authenticated and user.is_active and user.is_staff


class IsStaffOrReadOnly(BasePermission):
    def has_permission(self, request, view) -> bool:
        if request.method in SAFE_METHODS:
            return request.user.is_authenticated
        return can_manage_mcps(request.user)
