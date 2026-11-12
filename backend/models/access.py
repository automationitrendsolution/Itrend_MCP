"""Who may use which MCP: permissions, access tokens and service accounts."""

from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from .registry import MCPServer


class PermissionLevel(models.TextChoices):
    """Each level includes everything the levels before it allow."""

    VIEW = "view", "View"
    CONNECT = "connect", "Connect"
    MANAGE = "manage", "Manage"
    ADMIN = "admin", "Admin"

    @classmethod
    def rank(cls, level: str | None) -> int:
        """0 for no access, then 1 (view) to 4 (admin), so levels can be compared."""
        return cls.values.index(level) + 1 if level in cls.values else 0


PERMISSION_LEVEL_HELP = {
    PermissionLevel.VIEW: "See the MCP and its connection URL.",
    PermissionLevel.CONNECT: "View, plus generate access tokens and connect.",
    PermissionLevel.MANAGE: "Connect, plus edit the MCP's details.",
    PermissionLevel.ADMIN: "Manage, plus grant and remove other people's access.",
}


class MCPPermission(models.Model):
    """An explicit grant of one permission level on one MCP server to one user."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mcp_permissions")
    mcp_server = models.ForeignKey(MCPServer, on_delete=models.CASCADE, related_name="permissions")
    permission_level = models.CharField(max_length=10, choices=PermissionLevel.choices)
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "MCP permission"
        ordering = ["mcp_server__name", "user__username"]
        constraints = [
            models.UniqueConstraint(fields=["user", "mcp_server"], name="unique_permission_per_user_and_mcp"),
        ]

    def __str__(self) -> str:
        return f"{self.user} – {self.get_permission_level_display()} on {self.mcp_server}"


class PlatformCapability(models.Model):
    """
    Not a table. Holds the platform abilities a superuser switches on or off per user
    (see backend/permissions.py CAPABILITIES). Stored as Django permissions.
    """

    class Meta:
        managed = False
        default_permissions = ()
        permissions = [
            ("manage_mcps", "Run MCP servers"),
            ("manage_access", "Manage MCP access"),
            ("view_monitoring", "See monitoring"),
            ("manage_users", "Manage users"),
            ("view_audit_log", "See the audit log"),
        ]


class ServiceAccount(models.Model):
    """
    A non-human identity, such as the PPC server.

    It is a normal Django user with no usable password, so it can never log in
    to the website. It only uses MCPs through tokens that an administrator
    creates for it, and only on MCPs it was explicitly granted.
    """

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="service_account")
    description = models.TextField(blank=True, help_text="Which system uses this account and who owns it.")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["user__username"]

    def __str__(self) -> str:
        return self.user.get_username()


# A live token nobody has used for this many days is flagged as unused.
UNUSED_AFTER_DAYS = 30


class AccessTokenQuerySet(models.QuerySet):
    def active(self):
        return self.filter(revoked_at__isnull=True).filter(
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=timezone.now())
        )

    def unused(self):
        """Active tokens not used for UNUSED_AFTER_DAYS (never-used ones count from when they were created)."""
        cutoff = timezone.now() - timedelta(days=UNUSED_AFTER_DAYS)
        return self.active().filter(
            models.Q(last_used_at__lt=cutoff) | models.Q(last_used_at__isnull=True, created_at__lt=cutoff)
        )


class AccessToken(models.Model):
    """
    A bearer token for one MCP server.

    Only a SHA-256 hash of the token is stored. The full token is shown once,
    when it is created, and can never be displayed again.
    """

    class State(models.TextChoices):
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Expired"
        REVOKED = "revoked", "Revoked"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="access_tokens")
    mcp_server = models.ForeignKey(MCPServer, on_delete=models.CASCADE, related_name="access_tokens")
    name = models.CharField(max_length=100, help_text="What this token is for, e.g. “Claude Desktop on my laptop”.")
    token_prefix = models.CharField(max_length=16, help_text="The first characters, to recognise the token.")
    token_hash = models.CharField(max_length=64, unique=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True, help_text="Empty: the token never expires.")
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    last_used_at = models.DateTimeField(null=True, blank=True)

    objects = AccessTokenQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["mcp_server", "-created_at"]),
            models.Index(fields=["expires_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.token_prefix}…)"

    @property
    def state(self) -> str:
        if self.revoked_at:
            return self.State.REVOKED
        if self.expires_at is not None and self.expires_at <= timezone.now():
            return self.State.EXPIRED
        return self.State.ACTIVE

    @property
    def is_active(self) -> bool:
        return self.state == self.State.ACTIVE

    @property
    def idle_days(self) -> int:
        """Days since the token was last used (or created, if never used)."""
        return (timezone.now() - (self.last_used_at or self.created_at)).days

    @property
    def is_unused(self) -> bool:
        return self.is_active and self.idle_days >= UNUSED_AFTER_DAYS
