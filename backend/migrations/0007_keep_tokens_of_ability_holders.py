"""
Connecting to an MCP no longer comes with the "Run MCP servers" / "Manage MCP access" abilities;
it needs Connect on that MCP (or an MCP open to all employees). So that no token in use stops
working, everyone who holds such a token today gets Connect on that MCP written down explicitly.
"""

from django.db import migrations
from django.db.models import Q
from django.utils import timezone

ABILITY_CODES = ("manage_mcps", "manage_access")
RANK = {"view": 1, "connect": 2, "manage": 3, "admin": 4}


def keep_tokens_working(apps, schema_editor):
    AccessToken = apps.get_model("backend", "AccessToken")
    MCPPermission = apps.get_model("backend", "MCPPermission")
    live = AccessToken.objects.filter(revoked_at__isnull=True).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())
    ).filter(
        user__is_active=True, user__is_superuser=False,
        user__user_permissions__content_type__app_label="backend",
        user__user_permissions__codename__in=ABILITY_CODES,
    ).select_related("user", "mcp_server").distinct()
    for token in live:
        server = token.mcp_server
        is_service = hasattr(token.user, "service_account")
        if server.access_policy == "all_users" and not is_service:
            continue
        permission = MCPPermission.objects.filter(user=token.user, mcp_server=server).first()
        if permission is None:
            MCPPermission.objects.create(user=token.user, mcp_server=server, permission_level="connect")
        elif RANK.get(permission.permission_level, 0) < RANK["connect"]:
            permission.permission_level = "connect"
            permission.save(update_fields=["permission_level"])


class Migration(migrations.Migration):
    dependencies = [("backend", "0006_grant_abilities_to_existing_admins")]

    operations = [migrations.RunPython(keep_tokens_working, migrations.RunPython.noop)]
