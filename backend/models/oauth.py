"""
OAuth sign-in for MCP clients that cannot send a fixed token header, such as
Claude in the browser (claude.ai custom connectors).

The client registers itself (OAuthClient), sends the person to our sign-in and
consent page, and receives a normal platform AccessToken bound to one MCP. So
everything else (gateway, introspection, revoking on the Tokens page, audit log)
works exactly as for tokens people create by hand.
"""

from django.conf import settings
from django.db import models

from .access import AccessToken
from .registry import MCPServer


class OAuthClient(models.Model):
    """An application that registered itself (RFC 7591), e.g. "Claude"."""

    client_id = models.CharField(max_length=64, unique=True)
    client_secret_hash = models.CharField(max_length=64, blank=True, help_text="Empty for public clients (PKCE only).")
    client_name = models.CharField(max_length=100)
    redirect_uris = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "OAuth client"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.client_name} ({self.client_id[:8]}…)"


class OAuthCode(models.Model):
    """A one-time authorization code: valid for a few minutes, used once."""

    code_hash = models.CharField(max_length=64, unique=True)
    client = models.ForeignKey(OAuthClient, on_delete=models.CASCADE, related_name="+")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    mcp_server = models.ForeignKey(MCPServer, on_delete=models.CASCADE, related_name="+")
    redirect_uri = models.TextField()
    code_challenge = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "OAuth code"


class OAuthGrant(models.Model):
    """
    One "Allow" given to a client. Holds the refresh token; the access token is the
    linked AccessToken, which is renewed in place on every refresh. Revoking or
    deleting that AccessToken therefore ends the whole connection.
    """

    access_token = models.OneToOneField(AccessToken, on_delete=models.CASCADE, related_name="oauth_grant")
    client = models.ForeignKey(OAuthClient, on_delete=models.CASCADE, related_name="grants")
    refresh_hash = models.CharField(max_length=64, unique=True)
    refresh_expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "OAuth grant"
