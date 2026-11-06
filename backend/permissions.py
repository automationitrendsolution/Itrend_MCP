"""
Who may do what. Shared by website views and the REST API.

Per-MCP rules (VIEW / CONNECT / MANAGE / ADMIN) live in backend/services/access.py.
"""

from rest_framework.permissions import SAFE_METHODS, BasePermission


def can_manage_mcps(user) -> bool:
    """Start/stop MCPs, manage users and permissions, see the activity log: administrators only."""
    return user.is_authenticated and user.is_active and user.is_staff


class MCPServerPermission(BasePermission):
    """Read-only: any active, logged-in user (the list only contains MCPs they may view)."""

    def has_permission(self, request, view) -> bool:
        return request.user.is_authenticated and request.user.is_active and request.method in SAFE_METHODS


class IsAdministrator(BasePermission):
    def has_permission(self, request, view) -> bool:
        return can_manage_mcps(request.user)
