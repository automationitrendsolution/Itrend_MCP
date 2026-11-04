"""
Platform health checks.

These check the management platform itself (Django + database). Health of the
individual MCP servers is handled separately by backend.mcp_manager (Phase 6).
"""

import logging
import time
from dataclasses import asdict, dataclass

from django.db import DatabaseError, connection

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ComponentHealth:
    status: str  # "ok" or "error"
    response_time_ms: float | None = None


@dataclass(frozen=True)
class PlatformHealth:
    status: str  # "ok" or "degraded"
    database: ComponentHealth

    @property
    def is_healthy(self) -> bool:
        return self.status == "ok"

    def as_dict(self) -> dict:
        return asdict(self)


def check_database() -> ComponentHealth:
    started = time.perf_counter()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except DatabaseError:
        # Details stay in the server log; callers only see "error".
        logger.exception("Database health check failed")
        return ComponentHealth(status="error")
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    return ComponentHealth(status="ok", response_time_ms=elapsed_ms)


def check_platform() -> PlatformHealth:
    database = check_database()
    status = "ok" if database.status == "ok" else "degraded"
    return PlatformHealth(status=status, database=database)
