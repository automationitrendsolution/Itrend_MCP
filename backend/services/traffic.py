"""
Gateway traffic: request counts and per-MCP request limits (Phase 6).

The gateway view (backend/views.py, mcp_gateway) uses this module to

    1. take a "slot" before forwarding (at most max_concurrent_requests requests
       per MCP at the same time; more get HTTP 429 "busy"),
    2. pass the MCP's own request timeout to the gateway,
    3. count every answer in a 5-minute bucket (MCPRequestStat): requests,
       errors, refusals and response time,
    4. give the slot back when the answer has been fully streamed.

Only numbers are stored, never request or response contents.

Counting must never break a request: database problems are logged and ignored.

Concurrency is counted per website process. With several worker processes
(production) the limit applies to each of them; a shared counter comes with
the shared cache in Phase 7.
"""

import logging
import threading
from collections import defaultdict
from collections.abc import Iterable, Iterator
from datetime import datetime, timedelta

from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import F
from django.db.models.functions import Greatest
from django.utils import timezone

from backend.models import MCPMonitorSettings, MCPRequestStat, MCPServer

from .gateway import GatewayError

logger = logging.getLogger(__name__)

BUCKET_SECONDS = 300
DENIED_STATUSES = {401, 403}

_lock = threading.Lock()
_active: dict[int, int] = defaultdict(int)


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------


def limits_for(server: MCPServer) -> MCPMonitorSettings:
    """The MCP's saved settings, or an unsaved object holding the defaults."""
    try:
        return MCPMonitorSettings.objects.get(mcp_server=server)
    except MCPMonitorSettings.DoesNotExist:
        return MCPMonitorSettings(mcp_server=server)


class Slot:
    """One request in progress on one MCP. release() may safely be called more than once."""

    def __init__(self, server_id: int) -> None:
        self.server_id = server_id
        self._released = False

    def release(self) -> None:
        with _lock:
            if self._released:
                return
            self._released = True
            _active[self.server_id] -= 1
            if _active[self.server_id] <= 0:
                del _active[self.server_id]


def acquire(server: MCPServer, limits: MCPMonitorSettings) -> Slot:
    with _lock:
        if _active[server.pk] >= limits.max_concurrent_requests:
            raise GatewayError(429, "mcp_busy", f"{server.name} is busy right now. Try again in a moment.")
        _active[server.pk] += 1
    return Slot(server.pk)


def in_progress(server: MCPServer) -> int:
    with _lock:
        return _active.get(server.pk, 0)


class ReleasingStream:
    """
    Wraps the MCP's answer so the slot is freed when the answer ends: after the
    last chunk, or when Django closes the response (client went away, or the
    body was never read).
    """

    def __init__(self, chunks: Iterable[bytes], slot: Slot) -> None:
        self.chunks, self.slot = chunks, slot

    def __iter__(self) -> Iterator[bytes]:
        try:
            yield from self.chunks
        finally:
            self.slot.release()

    def close(self) -> None:
        self.slot.release()
        close = getattr(self.chunks, "close", None)
        if close is not None:
            close()


# ---------------------------------------------------------------------------
# Counting
# ---------------------------------------------------------------------------


def bucket_start(moment: datetime) -> datetime:
    seconds = int(moment.timestamp()) // BUCKET_SECONDS * BUCKET_SECONDS
    return datetime.fromtimestamp(seconds, tz=moment.tzinfo or timezone.get_current_timezone())


def record(server: MCPServer, status: int, elapsed_ms: float, *, now: datetime | None = None) -> None:
    """Count one answer the gateway gave for this MCP."""
    is_error = status >= 500 or status == 429
    is_denied = status in DENIED_STATUSES
    elapsed_ms = max(0.0, round(elapsed_ms, 2))
    bucket = bucket_start(now or timezone.now())
    changes = {
        "request_count": F("request_count") + 1,
        "error_count": F("error_count") + int(is_error),
        "denied_count": F("denied_count") + int(is_denied),
        "total_response_ms": F("total_response_ms") + elapsed_ms,
        "max_response_ms": Greatest(F("max_response_ms"), elapsed_ms),
    }
    try:
        rows = MCPRequestStat.objects.filter(mcp_server=server, bucket_start=bucket)
        if rows.update(**changes):
            return
        try:
            with transaction.atomic():
                MCPRequestStat.objects.create(
                    mcp_server=server,
                    bucket_start=bucket,
                    request_count=1,
                    error_count=int(is_error),
                    denied_count=int(is_denied),
                    total_response_ms=elapsed_ms,
                    max_response_ms=elapsed_ms,
                )
        except IntegrityError:  # another request created the bucket a moment ago
            rows.update(**changes)
    except DatabaseError:
        logger.exception("Could not count a request for %s", server.slug)


def totals(server: MCPServer | None = None, *, hours: int = 24) -> dict:
    """Requests, errors, refusals and average/maximum response time over the last `hours`."""
    from django.db.models import Max, Sum

    rows = MCPRequestStat.objects.filter(bucket_start__gte=timezone.now() - timedelta(hours=hours))
    if server is not None:
        rows = rows.filter(mcp_server=server)
    data = rows.aggregate(
        requests=Sum("request_count"),
        errors=Sum("error_count"),
        denied=Sum("denied_count"),
        total_ms=Sum("total_response_ms"),
        max_ms=Max("max_response_ms"),
    )
    requests = data["requests"] or 0
    return {
        "requests": requests,
        "errors": data["errors"] or 0,
        "denied": data["denied"] or 0,
        "avg_response_ms": round(data["total_ms"] / requests, 1) if requests else None,
        "max_response_ms": round(data["max_ms"], 1) if requests and data["max_ms"] is not None else None,
    }
