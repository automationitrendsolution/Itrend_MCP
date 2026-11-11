from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from ..validators import (
    http_url_validator,
    slug_format_validator,
    validate_configuration,
    validate_slug_not_reserved,
)


class MCPServerQuerySet(models.QuerySet):
    def visible_to(self, user) -> "MCPServerQuerySet":
        """
        MCP servers a user may see (permission level VIEW or higher).

        People who run MCPs or manage access see everything. People see servers open to all employees, and any
        server they have an explicit permission on. Service accounts only see
        servers they were explicitly granted. Rules: backend/services/access.py.
        """
        if not user.is_authenticated or not user.is_active:
            return self.none()
        from backend.permissions import sees_all_mcps

        if sees_all_mcps(user):
            return self
        granted = Q(permissions__user=user)
        if hasattr(user, "service_account"):
            return self.filter(granted).distinct()
        return self.filter(granted | Q(access_policy=MCPServer.AccessPolicy.ALL_USERS)).distinct()

    def search(self, term: str) -> "MCPServerQuerySet":
        term = term.strip()
        if not term:
            return self
        return self.filter(Q(name__icontains=term) | Q(slug__icontains=term) | Q(description__icontains=term))


class MCPServer(models.Model):
    """An MCP server registered on the platform, hosted here or elsewhere."""

    class DeploymentType(models.TextChoices):
        LOCAL = "local", "Code in mcp_servers/"
        ZIP = "zip", "ZIP upload"
        GITHUB = "github", "GitHub repository"
        EXTERNAL = "external", "Existing MCP URL"

    class TransportType(models.TextChoices):
        STREAMABLE_HTTP = "streamable_http", "Streamable HTTP"
        SSE = "sse", "HTTP + SSE (legacy)"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        STOPPED = "stopped", "Stopped"
        FAILED = "failed", "Failed"

    class HealthStatus(models.TextChoices):
        UNKNOWN = "unknown", "Unknown"
        HEALTHY = "healthy", "Healthy"
        UNHEALTHY = "unhealthy", "Unhealthy"

    class AccessPolicy(models.TextChoices):
        RESTRICTED = "restricted", "Restricted (only people given permission)"
        ALL_USERS = "all_users", "All employees (can connect)"

    name = models.CharField(max_length=100, unique=True)
    slug = models.CharField(
        max_length=50,
        unique=True,
        validators=[slug_format_validator, validate_slug_not_reserved],
        help_text="Used in the connection URL. Cannot be changed later.",
    )
    description = models.TextField(blank=True)
    deployment_type = models.CharField(max_length=20, choices=DeploymentType.choices)
    transport_type = models.CharField(
        max_length=20, choices=TransportType.choices, default=TransportType.STREAMABLE_HTTP
    )
    endpoint = models.URLField(
        max_length=500,
        blank=True,
        validators=[http_url_validator],
        help_text="External MCPs only: the URL where the server is already running.",
    )
    internal_endpoint = models.CharField(
        max_length=500,
        blank=True,
        help_text="Address the gateway forwards to, e.g. http://mcp-mysql:8000/mcp. Administrators only.",
    )
    configuration = models.JSONField(
        default=dict,
        blank=True,
        validators=[validate_configuration],
        help_text="Non-secret settings as JSON. Never store passwords or tokens here.",
    )
    access_policy = models.CharField(
        max_length=20, choices=AccessPolicy.choices, default=AccessPolicy.RESTRICTED
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    # Local MCPs: the folder under MCP_SERVERS_DIR, and the process the platform started.
    source_dir = models.CharField(max_length=100, blank=True, help_text="Folder name under mcp_servers/.")
    process_id = models.PositiveIntegerField(null=True, blank=True)
    # The process start time, so a recycled process ID is never mistaken for our MCP.
    process_create_time = models.FloatField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    health_status = models.CharField(max_length=20, choices=HealthStatus.choices, default=HealthStatus.UNKNOWN)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = MCPServerQuerySet.as_manager()

    class Meta:
        ordering = ["name"]
        verbose_name = "MCP server"
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["health_status"]),
            models.Index(fields=["deployment_type"]),
            models.Index(fields=["access_policy"]),
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def is_external(self) -> bool:
        return self.deployment_type == self.DeploymentType.EXTERNAL

    @property
    def is_local(self) -> bool:
        return self.deployment_type == self.DeploymentType.LOCAL

    @property
    def connection_url(self) -> str:
        """The URL an MCP client connects to."""
        if self.is_external:
            return self.endpoint
        return f"{settings.MCP_PUBLIC_BASE_URL.rstrip('/')}/{self.slug}/"

    def clean(self) -> None:
        super().clean()
        if self.is_external and not self.endpoint:
            raise ValidationError({"endpoint": "An external MCP needs the URL where it is running."})
        if not self.is_external and self.endpoint:
            raise ValidationError({"endpoint": "Only external MCPs have an endpoint URL. Hosted MCPs get one automatically."})


class MCPDeployment(models.Model):
    """
    One deployment attempt of a platform-hosted MCP server.

    The latest deployment time of a server is read from its newest deployment
    rather than stored twice.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        IN_PROGRESS = "in_progress", "In progress"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    mcp_server = models.ForeignKey(MCPServer, on_delete=models.CASCADE, related_name="deployments")
    deployment_type = models.CharField(max_length=20, choices=MCPServer.DeploymentType.choices)
    deployment_status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    container_id = models.CharField(max_length=100, blank=True)
    image_name = models.CharField(max_length=255, blank=True)
    source_revision = models.CharField(
        max_length=100, blank=True, help_text="Git commit SHA or ZIP file checksum."
    )
    error_message = models.TextField(blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    deployed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["mcp_server", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.mcp_server} – {self.get_deployment_status_display()} ({self.created_at:%Y-%m-%d %H:%M})"
