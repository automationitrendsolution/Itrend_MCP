"""
MCP registry business logic.

Website views and API views both call these functions, so every rule lives
in one place.
"""

import logging
from dataclasses import dataclass

from django.db import transaction
from django.db.models import Count, Q

from backend.models import MCPServer

from . import audit

logger = logging.getLogger(__name__)

# Fields that may only be set when an MCP server is created.
IMMUTABLE_FIELDS = frozenset({"slug", "deployment_type"})


@dataclass(frozen=True)
class RegistryCounts:
    total: int
    running: int
    stopped: int
    unhealthy: int


def list_servers(user, *, search: str = "", status: str = "", deployment_type: str = ""):
    servers = MCPServer.objects.visible_to(user).search(search).select_related("created_by")
    if status in MCPServer.Status.values:
        servers = servers.filter(status=status)
    if deployment_type in MCPServer.DeploymentType.values:
        servers = servers.filter(deployment_type=deployment_type)
    return servers


def get_counts(user) -> RegistryCounts:
    totals = MCPServer.objects.visible_to(user).aggregate(
        total=Count("id"),
        running=Count("id", filter=Q(status=MCPServer.Status.RUNNING)),
        stopped=Count("id", filter=Q(status=MCPServer.Status.STOPPED)),
        unhealthy=Count("id", filter=Q(health_status=MCPServer.HealthStatus.UNHEALTHY)),
    )
    return RegistryCounts(**totals)


@transaction.atomic
def create_server(*, created_by, **fields) -> MCPServer:
    server = MCPServer(created_by=created_by, **fields)
    server.full_clean()
    server.save()
    audit.record(audit.Action.MCP_CREATED, user=created_by, mcp_server=server, name=server.name)
    return server


@transaction.atomic
def update_server(server: MCPServer, *, updated_by, **fields) -> MCPServer:
    blocked = IMMUTABLE_FIELDS.intersection(
        name for name, value in fields.items() if value != getattr(server, name)
    )
    if blocked:
        raise ValueError(f"These fields cannot be changed after creation: {', '.join(sorted(blocked))}")
    changed = sorted(name for name, value in fields.items() if value != getattr(server, name))
    for name, value in fields.items():
        setattr(server, name, value)
    server.full_clean()
    server.save()
    audit.record(audit.Action.MCP_UPDATED, user=updated_by, mcp_server=server, changed_fields=changed)
    return server


@transaction.atomic
def delete_server(server: MCPServer, *, deleted_by) -> None:
    if server.is_local:
        from . import runner  # local import: runner depends on this module's model only

        runner.stop(server, by=deleted_by)
    audit.record(audit.Action.MCP_DELETED, user=deleted_by, mcp_server=server, name=server.name)
    server.delete()
