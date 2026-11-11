"""
Access tokens for MCP servers.

- Tokens are 256-bit random values from the `secrets` module, e.g.
  "itmcp_3q8Z…". Only their SHA-256 hash is stored, so a database leak does
  not reveal usable tokens. (A slow password hash is unnecessary here: the
  tokens are random, not human-chosen, so they cannot be guessed.)
- Each token works for exactly one MCP server. It expires after the chosen
  period, or never ("lifetime") when the creator chooses so.
- A token only works while its owner is active and still has CONNECT
  permission on that MCP, so removing someone's access takes effect at once.
"""

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from backend.models import AccessToken, MCPServer

from . import access, audit

logger = logging.getLogger(__name__)

TOKEN_PREFIX = "itmcp_"
PREFIX_DISPLAY_LENGTH = 14  # "itmcp_" + 8 random characters
NEVER_EXPIRES = 0  # "Lifetime": the token works until it is revoked or deleted
EXPIRY_CHOICES_DAYS = (7, 30, 90, 180, 365, NEVER_EXPIRES)
DEFAULT_EXPIRY_DAYS = 90

# Repeated events (every MCP request checks the token) are logged at most this often.
CONNECTION_LOG_INTERVAL_SECONDS = 300
LAST_USED_UPDATE_SECONDS = 60


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def generate_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def list_tokens(*, user=None, server: MCPServer | None = None):
    tokens = AccessToken.objects.select_related("user", "mcp_server", "created_by")
    if user is not None:
        tokens = tokens.filter(user=user)
    if server is not None:
        tokens = tokens.filter(mcp_server=server)
    return tokens


def can_create_for(actor, owner) -> bool:
    """People create their own tokens; administrators create tokens for service accounts."""
    if not actor.is_authenticated or not actor.is_active:
        return False
    if actor.pk == owner.pk:
        return not access.is_service_account(owner)
    from backend.permissions import has_capability

    return has_capability(actor, "manage_users") and access.is_service_account(owner)


def can_revoke(actor, token: AccessToken) -> bool:
    from backend.permissions import has_capability

    if not (actor.is_authenticated and actor.is_active):
        return False
    if actor.pk == token.user_id or actor.is_superuser:
        return True
    # "Manage users" may stop other people's tokens, but not a superuser's.
    return has_capability(actor, "manage_users") and not token.user.is_superuser


@transaction.atomic
def create_token(*, owner, server: MCPServer, name: str, expires_in_days: int, created_by) -> tuple[AccessToken, str]:
    """
    Create a token. Returns the saved token and the plain-text value.

    The plain-text value must be shown to the user once and then discarded.
    """
    if not can_create_for(created_by, owner):
        raise PermissionDenied("You cannot create tokens for this account.")
    if expires_in_days not in EXPIRY_CHOICES_DAYS:
        raise ValidationError({"expires_in_days": "Choose one of the offered expiry periods."})
    if server.is_external:
        raise ValidationError(
            {"mcp_server": "External MCPs do not pass through the platform, so platform tokens cannot protect them."}
        )
    if not access.can_connect(owner, server):
        raise ValidationError({"mcp_server": "This account does not have CONNECT permission on this MCP."})
    if created_by.pk != owner.pk and not access.can_connect(created_by, server):
        # Otherwise "Manage users" could borrow a service account's access to an MCP they cannot use.
        raise ValidationError({"mcp_server": "You can only create service-account tokens for MCPs you can connect to yourself."})
    name = name.strip()
    if not name:
        raise ValidationError({"name": "Give the token a name so you can recognise it later."})

    plaintext = generate_token()
    token = AccessToken.objects.create(
        user=owner,
        mcp_server=server,
        name=name[:100],
        token_prefix=plaintext[:PREFIX_DISPLAY_LENGTH],
        token_hash=hash_token(plaintext),
        created_by=created_by,
        expires_at=None if expires_in_days == NEVER_EXPIRES else timezone.now() + timedelta(days=expires_in_days),
    )
    audit.record(
        audit.Action.TOKEN_CREATED,
        user=created_by,
        mcp_server=server,
        token_id=token.pk,
        token_name=token.name,
        token_prefix=token.token_prefix,
        owner=owner.get_username(),
        expires_at=token.expires_at.isoformat() if token.expires_at else "never",
    )
    return token, plaintext


@transaction.atomic
def revoke_token(token: AccessToken, *, revoked_by) -> AccessToken:
    if not can_revoke(revoked_by, token):
        raise PermissionDenied("You cannot revoke this token.")
    if token.revoked_at:
        return token
    token.revoked_at = timezone.now()
    token.revoked_by = revoked_by
    token.save(update_fields=["revoked_at", "revoked_by"])
    audit.record(
        audit.Action.TOKEN_REVOKED,
        user=revoked_by,
        mcp_server=token.mcp_server,
        token_id=token.pk,
        token_name=token.name,
        token_prefix=token.token_prefix,
        owner=token.user.get_username(),
    )
    return token


@transaction.atomic
def delete_token(token: AccessToken, *, deleted_by) -> None:
    """Remove a token completely. It stops working at once. The audit log keeps a record."""
    if not can_revoke(deleted_by, token):
        raise PermissionDenied("You cannot delete this token.")
    audit.record(
        audit.Action.TOKEN_DELETED,
        user=deleted_by,
        mcp_server=token.mcp_server,
        token_id=token.pk,
        token_name=token.name,
        token_prefix=token.token_prefix,
        owner=token.user.get_username(),
    )
    token.delete()


# ---------------------------------------------------------------------------
# Checking a token presented to an MCP server
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Introspection:
    active: bool
    reason: str = ""
    token: AccessToken | None = None
    level: str | None = None


def introspect(raw_token: str, mcp_slug: str) -> Introspection:
    """
    Decide whether `raw_token` may be used on the MCP server `mcp_slug` right now.

    Called by MCP servers (through the API) for every request they receive.
    """
    if not raw_token.startswith(TOKEN_PREFIX):
        return Introspection(False, "unknown_token")
    token = AccessToken.objects.select_related("user", "mcp_server").filter(token_hash=hash_token(raw_token)).first()
    if token is None:
        logger.warning("Unknown token presented for MCP %r", mcp_slug)
        return Introspection(False, "unknown_token")

    reason = _deny_reason(token, mcp_slug)
    if reason:
        audit.record_at_most_every(
            CONNECTION_LOG_INTERVAL_SECONDS,
            f"deny:{token.pk}:{mcp_slug}:{reason}",
            audit.Action.MCP_CONNECTION,
            user=token.user,
            mcp_server=token.mcp_server,
            result=audit.Result.DENIED,
            reason=reason,
            requested_mcp=mcp_slug,
            token_id=token.pk,
            token_prefix=token.token_prefix,
        )
        return Introspection(False, reason, token)

    now = timezone.now()
    if token.last_used_at is None or now - token.last_used_at > timedelta(seconds=LAST_USED_UPDATE_SECONDS):
        AccessToken.objects.filter(pk=token.pk).update(last_used_at=now)
    audit.record_at_most_every(
        CONNECTION_LOG_INTERVAL_SECONDS,
        f"allow:{token.pk}",
        audit.Action.MCP_CONNECTION,
        user=token.user,
        mcp_server=token.mcp_server,
        token_id=token.pk,
        token_prefix=token.token_prefix,
    )
    return Introspection(True, token=token, level=access.connect_level(token.user, token.mcp_server))


def _deny_reason(token: AccessToken, mcp_slug: str) -> str:
    if token.revoked_at:
        return "revoked"
    if token.expires_at is not None and token.expires_at <= timezone.now():
        return "expired"
    if token.mcp_server.slug != mcp_slug:
        return "wrong_mcp"
    if not token.user.is_active:
        return "user_inactive"
    if not access.can_connect(token.user, token.mcp_server):
        return "no_permission"
    return ""
