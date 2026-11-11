"""
Who may do what. Shared by website views and the REST API.

Platform abilities are switched on or off per user by a superuser (Users → a user →
Abilities). They are stored as ordinary Django permissions on PlatformCapability,
so superusers have every ability automatically.

Per-MCP rules (VIEW / CONNECT / MANAGE / ADMIN) live in backend/services/access.py.
"""

from rest_framework.permissions import SAFE_METHODS, BasePermission

# (code, label, what it allows). The order is the order shown on the user page.
CAPABILITIES = [
    ("manage_mcps", "Run MCP servers",
     "Start, stop and restart MCPs, rescan the MCP folders and remove MCPs. Sees every MCP."),
    ("manage_access", "Manage MCP access",
     "Give and remove people's access to MCPs and choose who can use each one. Sees every MCP."),
    ("view_monitoring", "See monitoring",
     "Monitoring pages, health alerts and MCP logs."),
    ("manage_users", "Manage users",
     "Add users, turn accounts on or off, add service accounts and create their tokens."),
    ("view_audit_log", "See the audit log",
     "Every sign-in, permission change and token change."),
]
CAPABILITY_CODES = [code for code, _, _ in CAPABILITIES]


def has_capability(user, code: str) -> bool:
    """Is this ability switched on for the user? Superusers have all of them."""
    if user is None or not user.is_authenticated or not user.is_active:
        return False
    return user.has_perm(f"backend.{code}")


def capabilities_of(user) -> dict:
    """{code: True/False} for templates, e.g. caps.manage_users."""
    return {code: has_capability(user, code) for code in CAPABILITY_CODES}


def administers_all_mcps(user) -> bool:
    """People who run MCPs or manage access have Admin on every MCP (but connecting still needs a grant)."""
    return has_capability(user, "manage_mcps") or has_capability(user, "manage_access")


def sees_all_mcps(user) -> bool:
    """Every MCP is listed for administrators and for people who see monitoring."""
    return administers_all_mcps(user) or has_capability(user, "view_monitoring")


def can_manage_mcps(user) -> bool:
    """Start/stop/restart, rescan and remove MCPs."""
    return has_capability(user, "manage_mcps")


class MCPServerPermission(BasePermission):
    """Read-only: any active, logged-in user (the list only contains MCPs they may view)."""

    def has_permission(self, request, view) -> bool:
        return request.user.is_authenticated and request.user.is_active and request.method in SAFE_METHODS


class IsAdministrator(BasePermission):
    def has_permission(self, request, view) -> bool:
        return can_manage_mcps(request.user)
