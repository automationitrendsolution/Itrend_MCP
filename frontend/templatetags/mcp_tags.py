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
}


@register.filter
def badge_class(value: str) -> str:
    """Bootstrap badge class for a status value, e.g. {{ server.status|badge_class }}."""
    return BADGE_CLASSES.get(value, "text-bg-secondary")
