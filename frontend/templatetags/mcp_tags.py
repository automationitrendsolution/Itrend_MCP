from django import template

register = template.Library()

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
}

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
