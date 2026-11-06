"""The activity (audit) log: who did what, when, from where, and whether it worked."""

from django.conf import settings
from django.db import models

from .registry import MCPServer


class MCPActivityLog(models.Model):
    """
    One audited event. Rows are only ever added, never edited.

    The username and MCP slug are copied into the row, so the log still makes
    sense after a user or an MCP server is deleted.
    """

    class Action(models.TextChoices):
        LOGIN = "login", "Login"
        LOGIN_FAILED = "login_failed", "Failed login"
        LOGOUT = "logout", "Logout"
        MCP_CREATED = "mcp_created", "MCP created"
        MCP_UPDATED = "mcp_updated", "MCP updated"
        MCP_DELETED = "mcp_deleted", "MCP deleted"
        MCP_STARTED = "mcp_started", "MCP started"
        MCP_STOPPED = "mcp_stopped", "MCP stopped"
        PERMISSION_GRANTED = "permission_granted", "Permission granted"
        PERMISSION_REVOKED = "permission_revoked", "Permission removed"
        TOKEN_CREATED = "token_created", "Token created"
        TOKEN_REVOKED = "token_revoked", "Token revoked"
        MCP_CONNECTION = "mcp_connection", "MCP connection"
        USER_ACTIVATED = "user_activated", "User activated"
        USER_DEACTIVATED = "user_deactivated", "User deactivated"
        SERVICE_ACCOUNT_CREATED = "service_account_created", "Service account created"

    class Result(models.TextChoices):
        SUCCESS = "success", "Success"
        FAILURE = "failure", "Failure"
        DENIED = "denied", "Denied"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="activity"
    )
    username = models.CharField(max_length=150, blank=True)
    mcp_server = models.ForeignKey(
        MCPServer, null=True, blank=True, on_delete=models.SET_NULL, related_name="activity"
    )
    mcp_slug = models.CharField(max_length=50, blank=True)
    action = models.CharField(max_length=30, choices=Action.choices)
    result = models.CharField(max_length=10, choices=Result.choices, default=Result.SUCCESS)
    source_ip = models.GenericIPAddressField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "activity log entry"
        verbose_name_plural = "activity log"
        ordering = ["-timestamp", "-id"]
        indexes = [
            models.Index(fields=["action", "-timestamp"]),
            models.Index(fields=["user", "-timestamp"]),
            models.Index(fields=["mcp_server", "-timestamp"]),
        ]

    def __str__(self) -> str:
        return f"{self.timestamp:%Y-%m-%d %H:%M:%S} {self.username or '-'} {self.action} {self.result}"
