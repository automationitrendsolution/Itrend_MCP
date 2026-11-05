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


def is_slug_available(slug: str) -> bool:
    return not MCPServer.objects.filter(slug=slug).exists()


@transaction.atomic
def create_server(*, created_by, **fields) -> MCPServer:
    server = MCPServer(created_by=created_by, **fields)
    server.full_clean()
    server.save()
    logger.info("MCP server %r (%s) created by %s", server.name, server.slug, created_by)
    return server


@transaction.atomic
def update_server(server: MCPServer, *, updated_by, **fields) -> MCPServer:
    blocked = IMMUTABLE_FIELDS.intersection(
        name for name, value in fields.items() if value != getattr(server, name)
    )
    if blocked:
        raise ValueError(f"These fields cannot be changed after creation: {', '.join(sorted(blocked))}")
    for name, value in fields.items():
        setattr(server, name, value)
    server.full_clean()
    server.save()
    logger.info("MCP server %r (%s) updated by %s", server.name, server.slug, updated_by)
    return server


@transaction.atomic
def delete_server(server: MCPServer, *, deleted_by) -> None:
    # Stopping and removing a hosted server's container is added in Phase 5.
    logger.info("MCP server %r (%s) deleted by %s", server.name, server.slug, deleted_by)
    server.delete()
