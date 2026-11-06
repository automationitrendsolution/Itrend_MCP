"""
Monitoring data (Phase 6).

    MCPHealthCheck      one real check of one MCP: did its MCP endpoint answer,
                        how fast, and how much CPU/RAM its processes used
    MCPRequestStat      gateway traffic per MCP in 5-minute buckets (counts only,
                        never request contents)
    MCPMonitorEvent     something an administrator should know about: went down,
                        recovered, restarted automatically, over a limit …
    MCPMonitorSettings  per-MCP limits and restart rules (optional; defaults apply
                        when an MCP has none)

Written by the monitor worker (manage.py monitor_mcps) and the gateway; see
backend/services/monitoring.py and backend/services/traffic.py.
"""

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from .registry import MCPServer


class MCPHealthCheck(models.Model):
    class Status(models.TextChoices):
        HEALTHY = "healthy", "Healthy"
        UNHEALTHY = "unhealthy", "Unhealthy"  # the process runs, but the MCP does not answer properly
        DOWN = "down", "Down"  # the process stopped although nobody stopped it

    mcp_server = models.ForeignKey(MCPServer, on_delete=models.CASCADE, related_name="health_checks")
    status = models.CharField(max_length=20, choices=Status.choices)
    checked_at = models.DateTimeField(auto_now_add=True)
    response_time_ms = models.FloatField(null=True, blank=True)
    error_message = models.CharField(max_length=500, blank=True)
    # Measured on the MCP's whole process tree. Empty when it could not be measured
    # (first sample, or an MCP started outside the platform); never guessed.
    cpu_percent = models.FloatField(null=True, blank=True, help_text="Share of the whole server's CPU.")
    memory_mb = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ["-checked_at", "-id"]
        indexes = [
            models.Index(fields=["mcp_server", "-checked_at"]),
            models.Index(fields=["checked_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.mcp_server} {self.status} {self.checked_at:%Y-%m-%d %H:%M:%S}"


class MCPRequestStat(models.Model):
    """Requests that went through the gateway to one MCP during one 5-minute bucket."""

    mcp_server = models.ForeignKey(MCPServer, on_delete=models.CASCADE, related_name="request_stats")
    bucket_start = models.DateTimeField()
    request_count = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0, help_text="MCP failed, was down, too slow or too busy (HTTP 5xx/429).")
    denied_count = models.PositiveIntegerField(default=0, help_text="Refused by the gateway: missing or invalid token.")
    total_response_ms = models.FloatField(default=0)
    max_response_ms = models.FloatField(default=0)

    class Meta:
        ordering = ["-bucket_start"]
        constraints = [models.UniqueConstraint(fields=["mcp_server", "bucket_start"], name="unique_request_stat_bucket")]
        indexes = [models.Index(fields=["bucket_start"])]

    def __str__(self) -> str:
        return f"{self.mcp_server} {self.bucket_start:%Y-%m-%d %H:%M} {self.request_count} requests"


class MCPMonitorEvent(models.Model):
    class Kind(models.TextChoices):
        WENT_DOWN = "went_down", "Stopped unexpectedly"
        UNHEALTHY = "unhealthy", "Stopped answering"
        RECOVERED = "recovered", "Recovered"
        AUTO_RESTARTED = "auto_restarted", "Restarted automatically"
        RESTART_FAILED = "restart_failed", "Automatic restart failed"
        GAVE_UP = "gave_up", "Automatic restarts paused"
        MEMORY_LIMIT = "memory_limit", "Over its memory limit"
        CPU_LIMIT = "cpu_limit", "Over its CPU limit"

    class Level(models.TextChoices):
        INFO = "info", "Info"
        WARNING = "warning", "Warning"
        ERROR = "error", "Error"

    mcp_server = models.ForeignKey(MCPServer, on_delete=models.CASCADE, related_name="monitor_events")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    level = models.CharField(max_length=10, choices=Level.choices, default=Level.INFO)
    message = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["mcp_server", "-created_at"]),
            models.Index(fields=["kind", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.mcp_server} {self.kind} {self.created_at:%Y-%m-%d %H:%M:%S}"


class MCPMonitorSettings(models.Model):
    """
    Limits and restart rules for one MCP.

    Enforced today: request timeout and concurrent requests (by the gateway),
    automatic restarts and the memory limit (by the monitor worker, which
    restarts an MCP that stays over it). The CPU limit raises a warning only.
    Hard operating-system limits arrive with containers.
    """

    mcp_server = models.OneToOneField(MCPServer, on_delete=models.CASCADE, related_name="monitor_settings")
    health_check_interval_seconds = models.PositiveIntegerField(
        default=30, validators=[MinValueValidator(10), MaxValueValidator(3600)],
        help_text="How often the monitor checks this MCP (10–3600 seconds).",
    )
    request_timeout_seconds = models.PositiveIntegerField(
        default=600, validators=[MinValueValidator(5), MaxValueValidator(3600)],
        help_text="How long one request may wait for the MCP's answer (5–3600 seconds).",
    )
    max_concurrent_requests = models.PositiveIntegerField(
        default=20, validators=[MinValueValidator(1), MaxValueValidator(1000)],
        help_text="Requests handled at the same time; more get “busy, try again” (HTTP 429).",
    )
    memory_limit_mb = models.PositiveIntegerField(
        null=True, blank=True, validators=[MinValueValidator(32), MaxValueValidator(65536)],
        help_text="Restart the MCP when it uses more than this for two checks in a row. Empty = no limit.",
    )
    cpu_limit_percent = models.PositiveIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(100)],
        help_text="Warn when the MCP uses more than this share of the server's CPU. Empty = no limit.",
    )
    auto_restart = models.BooleanField(
        default=True, help_text="Restart the MCP when it crashes, stops answering or exceeds its memory limit."
    )
    restart_after_failed_checks = models.PositiveIntegerField(
        default=3, validators=[MinValueValidator(1), MaxValueValidator(20)],
        help_text="Failed checks in a row before an MCP that stopped answering is restarted.",
    )
    max_restarts_per_hour = models.PositiveIntegerField(
        default=3, validators=[MinValueValidator(1), MaxValueValidator(20)],
        help_text="After this many automatic restarts in one hour the monitor stops trying and raises an alert.",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "MCP monitoring settings"
        verbose_name_plural = "MCP monitoring settings"

    def __str__(self) -> str:
        return f"Monitoring settings for {self.mcp_server}"
