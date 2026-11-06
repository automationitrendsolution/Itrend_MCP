"""Small helpers for creating test data."""

from django.contrib.auth import get_user_model

PASSWORD = "Correct-Horse-Battery-9"


def create_user(username: str = "alice", *, password: str = PASSWORD, **extra):
    return get_user_model().objects.create_user(username=username, password=password, **extra)


def create_server(name: str = "MySQL MCP", slug: str = "mysql", **extra):
    from backend.models import MCPServer

    fields = {
        "deployment_type": MCPServer.DeploymentType.ZIP,
        "access_policy": MCPServer.AccessPolicy.ALL_USERS,
        **extra,
    }
    return MCPServer.objects.create(name=name, slug=slug, **fields)


def restricted_server(name: str = "Secret MCP", slug: str = "secret", **extra):
    from backend.models import MCPServer

    return create_server(name, slug, access_policy=MCPServer.AccessPolicy.RESTRICTED, **extra)


def grant(user, server, level: str):
    from backend.models import MCPPermission

    return MCPPermission.objects.create(user=user, mcp_server=server, permission_level=level)


def create_service_account(name: str = "ppc-server"):
    from backend.services.accounts import create_service_account as create

    return create(name=name, description="Test", created_by=None).user


def create_token(owner, server, *, name: str = "Laptop", days: int = 30, created_by=None):
    """Returns (token, plaintext)."""
    from backend.services.tokens import create_token as create

    return create(owner=owner, server=server, name=name, expires_in_days=days, created_by=created_by or owner)
