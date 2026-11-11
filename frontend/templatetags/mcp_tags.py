from django import template

register = template.Library()


@register.filter
def role_label(account) -> str:
    """Service account, Superuser, Administrator (any platform ability switched on) or Member."""
    from backend.permissions import CAPABILITY_CODES, has_capability

    if hasattr(account, "service_account"):
        return "Service account"
    if account.is_superuser:
        return "Superuser"
    if any(has_capability(account, code) for code in CAPABILITY_CODES):
        return "Administrator"
    return "Member"

BADGE_CLASSES = {
    # MCP server status
    "running": "text-bg-success",
    "pending": "text-bg-secondary",
    "stopped": "text-bg-warning",
    "failed": "text-bg-danger",
    # Health
    "healthy": "text-bg-success",
    "unhealthy": "text-bg-danger",
    "unknown": "text-bg-light border",
    # Monitoring (Phase 6)
    "down": "text-bg-danger",
    "no_data": "text-bg-light border",
    "stale": "text-bg-light border",
    "not_monitored": "text-bg-light border",
    # Deployment status
    "in_progress": "text-bg-info",
    "succeeded": "text-bg-success",
    # Access tokens
    "active": "text-bg-success",
    "expired": "text-bg-secondary",
    "revoked": "text-bg-danger",
    # Activity results
    "success": "text-bg-success",
    "failure": "text-bg-warning",
    "denied": "text-bg-danger",
    # Permission levels
    "view": "text-bg-light border",
    "connect": "text-bg-info",
    "manage": "text-bg-primary",
    "admin": "text-bg-dark",
}


@register.filter
def badge_class(value: str) -> str:
    """Bootstrap badge class for a status value, e.g. {{ server.status|badge_class }}."""
    return BADGE_CLASSES.get(value, "text-bg-secondary")


# ---------------------------------------------------------------------------
# Activity timeline
# ---------------------------------------------------------------------------

EVENT_STYLES = {
    # action: (Bootstrap icon, colour)
    "login": ("bi-box-arrow-in-right", "blue"),
    "logout": ("bi-box-arrow-right", "gray"),
    "login_failed": ("bi-shield-exclamation", "red"),
    "mcp_created": ("bi-plus-circle", "purple"),
    "mcp_updated": ("bi-pencil", "purple"),
    "mcp_deleted": ("bi-trash", "red"),
    "mcp_started": ("bi-play-circle", "green"),
    "mcp_stopped": ("bi-stop-circle", "gray"),
    "permission_granted": ("bi-person-check", "green"),
    "permission_revoked": ("bi-person-dash", "amber"),
    "token_created": ("bi-key", "blue"),
    "token_revoked": ("bi-key", "amber"),
    "mcp_connection": ("bi-plug", "green"),
    "user_activated": ("bi-person-check", "green"),
    "user_deactivated": ("bi-person-slash", "amber"),
    "service_account_created": ("bi-robot", "purple"),
    "token_deleted": ("bi-trash", "amber"),
    "user_created": ("bi-person-plus", "purple"),
    "capability_changed": ("bi-toggles", "purple"),
}


@register.filter
def ability_label(code: str) -> str:
    """manage_users -> "Manage users" (the name shown on the user page)."""
    from backend.permissions import CAPABILITIES

    return next((label for key, label, _ in CAPABILITIES if key == code), str(code).replace("_", " "))

DENY_REASONS = {
    "unknown_token": "the token is not recognised",
    "revoked": "the token was revoked",
    "expired": "the token has expired",
    "wrong_mcp": "the token is for a different MCP",
    "user_inactive": "the account is deactivated",
    "no_permission": "they no longer have permission",
}


@register.filter
def event_icon(entry) -> str:
    if entry.result != "success":
        return "bi-x-octagon" if entry.action != "login_failed" else EVENT_STYLES["login_failed"][0]
    return EVENT_STYLES.get(entry.action, ("bi-dot", "gray"))[0]


@register.filter
def event_color(entry) -> str:
    if entry.result != "success":
        return "red"
    return EVENT_STYLES.get(entry.action, ("bi-dot", "gray"))[1]


@register.filter
def deny_reason(code: str) -> str:
    return DENY_REASONS.get(code, "it was refused")


@register.filter
def field_names(names) -> str:
    """["description", "source_dir"] -> "description, source dir"."""
    if not isinstance(names, list | tuple):
        return ""
    return ", ".join(str(name).replace("_", " ") for name in names)


@register.filter
def day_label(value) -> str:
    """Today / Yesterday / Thursday 5 November 2026, in the website's time zone."""
    from datetime import timedelta

    from django.utils import timezone
    from django.utils.formats import date_format

    day = timezone.localtime(value).date()
    today = timezone.localdate()
    if day == today:
        return "Today"
    if day == today - timedelta(days=1):
        return "Yesterday"
    return date_format(day, "l j F Y")


# ---------------------------------------------------------------------------
# Monitoring (Phase 6)
# ---------------------------------------------------------------------------

HEALTH_LABELS = {
    "healthy": "Healthy",
    "unhealthy": "Not answering",
    "down": "Down",
    "stopped": "Stopped",
    "no_data": "Not checked yet",
    "stale": "No recent check",
    "not_monitored": "Not monitored",
}

MONITOR_EVENT_STYLES = {
    # level: (Bootstrap icon, colour)
    "info": ("bi-check-circle", "green"),
    "warning": ("bi-exclamation-triangle", "amber"),
    "error": ("bi-x-octagon", "red"),
}


@register.filter
def health_label(value: str) -> str:
    return HEALTH_LABELS.get(value, str(value).replace("_", " ").capitalize())


@register.filter
def monitor_icon(event) -> str:
    return MONITOR_EVENT_STYLES.get(event.level, ("bi-dot", "gray"))[0]


@register.filter
def monitor_color(event) -> str:
    return MONITOR_EVENT_STYLES.get(event.level, ("bi-dot", "gray"))[1]


@register.filter
def health_key(server) -> str:
    """
    The health to show for an MCP (see backend.services.monitoring.health_of), using
    the latest check annotated on the row as last_check_status / last_check_at.
    """
    from backend.services.monitoring import health_of

    return health_of(server, getattr(server, "last_check_status", None), getattr(server, "last_check_at", None))


# ---------------------------------------------------------------------------
# Switchboard components
# ---------------------------------------------------------------------------

def _last_check_of(server):
    """The latest check as (status, checked_at, error): from the row's annotation, else one query."""
    if hasattr(server, "last_check_status"):
        return server.last_check_status, server.last_check_at, getattr(server, "last_check_error", "") or ""
    from backend.services.monitoring import last_check

    check = last_check(server)
    return (check.status, check.checked_at, check.error_message) if check else (None, None, "")


@register.simple_tag
def mcp_status(server) -> dict:
    """{% mcp_status server as status %} → {"key", "word", "reason"}."""
    from backend.services.monitoring import status_of

    return status_of(server, *_last_check_of(server))


@register.inclusion_tag("partials/status_chip.html")
def status_chip(server):
    """Dot + one word, the reason as a tooltip. The word is always there: colour is never the only signal."""
    return {"status": mcp_status(server)}


@register.simple_tag
def line_ticks(server, animate=True):
    """The last 24 hours as 48 ticks: height = requests, colour = health in that half hour."""
    from django.utils import timezone
    from django.utils.html import escape
    from django.utils.safestring import mark_safe

    from backend.services.monitoring import line_ticks as ticks_for

    ticks = ticks_for(server)
    peak = max((t["requests"] for t in ticks), default=0) or 1
    words = {"live": "healthy", "amber": "degraded", "fault": "down", "none": "no check"}
    parts, total = [], 0
    for i, tick in enumerate(ticks):
        total += tick["requests"]
        height = 2 if not tick["requests"] else max(3, round(tick["requests"] / peak * 18))
        start = timezone.localtime(tick["start"])
        label = f'{start:%H:%M}: {tick["requests"]} request{"s" if tick["requests"] != 1 else ""}, {words[tick["health"]]}'
        parts.append(
            f'<rect class="tick tick--{tick["health"]}" x="{i * 3}" y="{20 - height}" width="2" height="{height}" rx="1" '
            f'style="--i:{i}"><title>{escape(label)}</title></rect>'
        )
    summary = f"Last 24 hours: {total} request{'s' if total != 1 else ''}"
    return mark_safe(  # noqa: S308 - numbers and escaped labels only
        f'<svg class="pg-ticks{" pg-ticks--animate" if animate else ""}" viewBox="0 0 143 20" width="143" height="20" '
        f'role="img" aria-label="{escape(summary)}">{"".join(parts)}</svg>'
    )
