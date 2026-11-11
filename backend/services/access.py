"""
Who may do what with an MCP server.

Permission levels, lowest to highest: VIEW < CONNECT < MANAGE < ADMIN.
A user's level on a server is the highest of:
- ADMIN, for people with the "Run MCP servers" or "Manage MCP access" ability;
- VIEW, for people with the "See monitoring" ability;
- CONNECT, for people (not service accounts) when the server is open to all employees;
- the level explicitly granted to them (MCPPermission).
Inactive users have no access at all.

Connecting (tokens, the gateway) is stricter: abilities do not count, only the
level granted on that MCP or the all-employees policy (see connect_level).
Superusers can connect everywhere.
"""

import logging

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction

from backend.models import MCPPermission, MCPServer, PermissionLevel

from . import audit

logger = logging.getLogger(__name__)

Level = PermissionLevel


def is_service_account(user) -> bool:
    return hasattr(user, "service_account")


def _granted_level(user, server: MCPServer) -> str | None:
    """The level from an explicit permission or the all-employees policy. Abilities are not counted."""
    candidates = [
        MCPPermission.objects.filter(user=user, mcp_server=server).values_list("permission_level", flat=True).first()
    ]
    if server.access_policy == MCPServer.AccessPolicy.ALL_USERS and not is_service_account(user):
        candidates.append(Level.CONNECT)
    best = max(candidates, key=Level.rank)
    return best if Level.rank(best) else None


def _active(user) -> bool:
    return user is not None and user.is_authenticated and user.is_active


def effective_level(user, server: MCPServer) -> str | None:
    """The user's permission level on the server (for pages and management), or None for no access."""
    if not _active(user):
        return None
    from backend.permissions import administers_all_mcps, has_capability

    if administers_all_mcps(user):
        return Level.ADMIN
    level = _granted_level(user, server)
    if has_capability(user, "view_monitoring") and Level.rank(level) < Level.rank(Level.VIEW):
        return Level.VIEW
    return level


def connect_level(user, server: MCPServer) -> str | None:
    """
    The level that counts when connecting (tokens and the gateway). Platform abilities do not give
    data access: someone who manages access must grant themselves Connect like anyone else.
    """
    if not _active(user):
        return None
    if user.is_superuser:
        return Level.ADMIN
    return _granted_level(user, server)


def has_level(user, server: MCPServer, level: str) -> bool:
    return Level.rank(effective_level(user, server)) >= Level.rank(level)


def can_view(user, server: MCPServer) -> bool:
    return has_level(user, server, Level.VIEW)


def can_connect(user, server: MCPServer) -> bool:
    """Generate tokens for the MCP and use it through the gateway."""
    return Level.rank(connect_level(user, server)) >= Level.rank(Level.CONNECT)


def can_manage(user, server: MCPServer) -> bool:
    """Edit the MCP's details."""
    return has_level(user, server, Level.MANAGE)


def can_admin(user, server: MCPServer) -> bool:
    """Grant and remove other people's access to the MCP."""
    return has_level(user, server, Level.ADMIN)


def connectable_servers(user):
    """Hosted MCP servers the user may create tokens for."""
    servers = MCPServer.objects.visible_to(user).exclude(deployment_type=MCPServer.DeploymentType.EXTERNAL)
    return [server for server in servers if can_connect(user, server)]


def grantable_users():
    """Active users (people and service accounts) that can receive permissions."""
    return get_user_model().objects.filter(is_active=True).select_related("service_account").order_by("username")


def list_permissions(*, server: MCPServer | None = None, user=None):
    permissions = MCPPermission.objects.select_related("user", "user__service_account", "mcp_server", "granted_by")
    if server is not None:
        permissions = permissions.filter(mcp_server=server)
    if user is not None:
        permissions = permissions.filter(user=user)
    return permissions


@transaction.atomic
def grant_permission(*, server: MCPServer, user, level: str, granted_by) -> MCPPermission:
    """Give `user` this level on `server`, replacing any level they had before."""
    if level not in Level.values:
        raise ValidationError({"permission_level": "Choose a valid permission level."})
    if not user.is_active:
        raise ValidationError({"user": "Inactive users cannot be given permissions."})

    permission, created = MCPPermission.objects.select_for_update().get_or_create(
        user=user, mcp_server=server, defaults={"permission_level": level, "granted_by": granted_by}
    )
    previous = None if created else permission.permission_level
    if not created and previous != level:
        permission.permission_level = level
        permission.granted_by = granted_by
        permission.save(update_fields=["permission_level", "granted_by", "updated_at"])

    if created or previous != level:
        audit.record(
            audit.Action.PERMISSION_GRANTED,
            user=granted_by,
            mcp_server=server,
            target_user=user.get_username(),
            level=level,
            previous_level=previous,
        )
    return permission


@transaction.atomic
def revoke_permission(permission: MCPPermission, *, revoked_by) -> None:
    audit.record(
        audit.Action.PERMISSION_REVOKED,
        user=revoked_by,
        mcp_server=permission.mcp_server,
        target_user=permission.user.get_username(),
        level=permission.permission_level,
    )
    permission.delete()
