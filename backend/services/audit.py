"""
Activity (audit) logging.

Call record() from service functions. The client's IP address is picked up
automatically from the current request (see AuditContextMiddleware), so
services do not need to receive the request object.
"""

import ipaddress
import logging
from contextvars import ContextVar

from django.core.cache import cache

from backend.models import MCPActivityLog

logger = logging.getLogger(__name__)

Action = MCPActivityLog.Action
Result = MCPActivityLog.Result

_current_ip: ContextVar[str | None] = ContextVar("audit_current_ip", default=None)


def _valid_ip(value: str | None) -> str | None:
    try:
        return str(ipaddress.ip_address(value)) if value else None
    except ValueError:
        return None


class AuditContextMiddleware:
    """Remembers the client IP of the request being handled, for record()."""

    def __init__(self, get_response) -> None:
        self.get_response = get_response

    def __call__(self, request):
        from backend.services.login_protection import get_client_ip

        reset = _current_ip.set(_valid_ip(get_client_ip(request)))
        try:
            return self.get_response(request)
        finally:
            _current_ip.reset(reset)


def record(
    action: str,
    *,
    user=None,
    username: str = "",
    mcp_server=None,
    result: str = Result.SUCCESS,
    source_ip: str | None = None,
    **metadata,
) -> MCPActivityLog:
    """Add one entry to the activity log."""
    if user is not None and not getattr(user, "is_authenticated", False):
        user = None
    entry = MCPActivityLog.objects.create(
        user=user,
        username=(user.get_username() if user else username)[:150],
        mcp_server=mcp_server,
        mcp_slug=mcp_server.slug if mcp_server else "",
        action=action,
        result=result,
        source_ip=_valid_ip(source_ip) or _current_ip.get(),
        metadata=metadata,
    )
    logger.info("audit %s %s user=%s mcp=%s %s", action, result, entry.username or "-", entry.mcp_slug or "-", metadata or "")
    return entry


def record_at_most_every(seconds: int, key: str, action: str, **kwargs) -> MCPActivityLog | None:
    """
    Like record(), but writes at most one entry per `key` every `seconds`.

    Used for events that repeat on every request, such as MCP connections,
    so the log stays readable and the database is not flooded.
    """
    if not cache.add(f"audit-throttle:{key}", 1, seconds):
        return None
    return record(action, **kwargs)
