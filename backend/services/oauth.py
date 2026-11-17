"""
OAuth 2.1 sign-in for MCP clients (the MCP authorization spec), so that Claude in
the browser and similar apps can connect without anyone copying a token.

    1. The client calls the MCP without a token and gets 401 with a link to
       /.well-known/oauth-protected-resource/mcp/<slug>/, which names this site
       as the authorization server.
    2. It registers itself at /oauth/register/ (dynamic client registration).
    3. It opens /oauth/authorize/ in the browser: the person signs in and clicks
       Allow. Only people with Connect on that MCP can allow.
    4. It swaps the one-time code for tokens at /oauth/token/ (PKCE S256 required).

The access token is an ordinary platform AccessToken for that one MCP, so the
gateway, introspection, the Tokens page and the audit log treat it like any
other token. Refreshing renews that same AccessToken in place.
"""

import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlsplit

from django.db import transaction
from django.utils import timezone

from backend.models import AccessToken, MCPServer, OAuthClient, OAuthCode, OAuthGrant

from . import access, audit, tokens

SCOPE = "mcp"
CODE_SECONDS = 300
ACCESS_TOKEN_HOURS = 8
REFRESH_TOKEN_DAYS = 30
REFRESH_PREFIX = "itmcr_"
AUTH_METHODS = ("none", "client_secret_post", "client_secret_basic")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}
MAX_REDIRECT_URIS = 10
RESOURCE_PATH = re.compile(r"^/mcp/(?P<slug>[a-z][a-z0-9-]{1,48}[a-z0-9])/?$")


class OAuthError(Exception):
    """An OAuth error answer: {"error": code, "error_description": description}."""

    def __init__(self, code: str, description: str, status: int = 400) -> None:
        super().__init__(description)
        self.code, self.description, self.status = code, description, status

    def as_dict(self) -> dict:
        return {"error": self.code, "error_description": self.description}


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Addresses
# ---------------------------------------------------------------------------


def resource_url(origin: str, server: MCPServer) -> str:
    return f"{origin}/mcp/{server.slug}/"


def server_for_resource(resource: str, host: str) -> MCPServer | None:
    """The MCP a `resource` URL (https://<this site>/mcp/<slug>/) names, if it is served here."""
    parts = urlsplit(resource or "")
    match = RESOURCE_PATH.match(parts.path)
    if not match or parts.netloc.lower() != host.lower() or parts.query or parts.fragment:
        return None
    server = MCPServer.objects.filter(slug=match["slug"]).first()
    if server is None or server.is_external or not server.internal_endpoint:
        return None
    return server


# ---------------------------------------------------------------------------
# Client registration (RFC 7591)
# ---------------------------------------------------------------------------


def _valid_redirect_uri(uri: object) -> bool:
    if not isinstance(uri, str) or len(uri) > 500:
        return False
    parts = urlsplit(uri)
    if parts.fragment or not parts.hostname:
        return False
    return parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in LOCAL_HOSTS)


def register_client(data: dict) -> tuple[OAuthClient, str, str]:
    """Register a client. Returns (client, plain secret or "", auth method)."""
    if not isinstance(data, dict):
        raise OAuthError("invalid_client_metadata", "Send the client metadata as a JSON object.")
    uris = data.get("redirect_uris")
    if not isinstance(uris, list) or not uris or len(uris) > MAX_REDIRECT_URIS:
        raise OAuthError("invalid_redirect_uri", "redirect_uris must list 1 to 10 addresses.")
    if not all(_valid_redirect_uri(uri) for uri in uris):
        raise OAuthError("invalid_redirect_uri", "Redirect addresses must use https (http only for localhost).")
    if not set(data.get("grant_types") or ["authorization_code"]) <= {"authorization_code", "refresh_token"}:
        raise OAuthError("invalid_client_metadata", "Only authorization_code and refresh_token are supported.")
    if not set(data.get("response_types") or ["code"]) <= {"code"}:
        raise OAuthError("invalid_client_metadata", "Only response_type code is supported.")
    method = data.get("token_endpoint_auth_method") or "client_secret_basic"
    if method not in AUTH_METHODS:
        raise OAuthError("invalid_client_metadata", f"token_endpoint_auth_method must be one of {', '.join(AUTH_METHODS)}.")
    name = str(data.get("client_name") or "MCP client").strip()[:100] or "MCP client"

    secret = "" if method == "none" else secrets.token_urlsafe(32)
    client = OAuthClient.objects.create(
        client_id=secrets.token_urlsafe(24),
        client_secret_hash=_hash(secret) if secret else "",
        client_name=name,
        redirect_uris=uris,
    )
    return client, secret, method


def authenticate_client(client_id: str, client_secret: str) -> OAuthClient:
    client = OAuthClient.objects.filter(client_id=client_id or "").first()
    if client is None:
        raise OAuthError("invalid_client", "Unknown client.", status=401)
    if client.client_secret_hash and not hmac.compare_digest(client.client_secret_hash, _hash(client_secret or "")):
        raise OAuthError("invalid_client", "Wrong client secret.", status=401)
    return client


# ---------------------------------------------------------------------------
# Authorization request
# ---------------------------------------------------------------------------


@dataclass
class AuthorizeRequest:
    client: OAuthClient
    redirect_uri: str
    server: MCPServer | None
    state: str
    code_challenge: str
    resource: str


class RedirectError(OAuthError):
    """An error the client should receive at its redirect address."""


def check_authorize(params, host: str) -> AuthorizeRequest:
    """
    Validate /oauth/authorize parameters.

    OAuthError: the client or redirect address is wrong, so we must NOT redirect (show a page).
    RedirectError: safe to send back to the client's redirect address.
    """
    client = OAuthClient.objects.filter(client_id=params.get("client_id", "")).first()
    if client is None:
        raise OAuthError("invalid_client", "This app is not registered with the iTrend MCP Platform.")
    redirect_uri = params.get("redirect_uri", "")
    if redirect_uri not in client.redirect_uris:
        raise OAuthError("invalid_request", "The app sent an address it did not register.")

    if params.get("response_type") != "code":
        raise RedirectError("unsupported_response_type", "Only response_type=code is supported.")
    challenge = params.get("code_challenge", "")
    if params.get("code_challenge_method") != "S256" or not 43 <= len(challenge) <= 128:
        raise RedirectError("invalid_request", "PKCE with code_challenge_method=S256 is required.")
    resource = params.get("resource", "")
    server = server_for_resource(resource, host)
    if server is None:
        raise RedirectError("invalid_target", "Say which MCP to connect to (resource=https://<site>/mcp/<name>/).")
    return AuthorizeRequest(client, redirect_uri, server, params.get("state", ""), challenge, resource)


def issue_code(request: AuthorizeRequest, user) -> str:
    if not access.can_connect(user, request.server):
        raise RedirectError("access_denied", "You do not have Connect permission on this MCP.")
    OAuthCode.objects.filter(expires_at__lt=timezone.now() - timedelta(days=1)).delete()
    code = secrets.token_urlsafe(32)
    OAuthCode.objects.create(
        code_hash=_hash(code),
        client=request.client,
        user=user,
        mcp_server=request.server,
        redirect_uri=request.redirect_uri,
        code_challenge=request.code_challenge,
        expires_at=timezone.now() + timedelta(seconds=CODE_SECONDS),
    )
    return code


# ---------------------------------------------------------------------------
# Token endpoint
# ---------------------------------------------------------------------------


def _pkce_matches(verifier: str, challenge: str) -> bool:
    digest = hashlib.sha256((verifier or "").encode()).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return 43 <= len(verifier or "") <= 128 and hmac.compare_digest(computed, challenge)


def _check_user(user, server: MCPServer) -> None:
    if not user.is_active or not access.can_connect(user, server):
        raise OAuthError("invalid_grant", "This account can no longer use this MCP.")


def _check_resource(resource: str, server: MCPServer, host: str) -> None:
    if resource and server_for_resource(resource, host) != server:
        raise OAuthError("invalid_target", "The resource does not match the MCP this sign-in was for.")


def _token_answer(access_plain: str, refresh_plain: str) -> dict:
    return {
        "access_token": access_plain,
        "token_type": "Bearer",
        "expires_in": ACCESS_TOKEN_HOURS * 3600,
        "refresh_token": refresh_plain,
        "scope": SCOPE,
    }


@transaction.atomic
def exchange_code(client: OAuthClient, *, code: str, redirect_uri: str, verifier: str, resource: str, host: str) -> dict:
    now = timezone.now()
    entry = OAuthCode.objects.select_for_update().select_related("user", "mcp_server").filter(code_hash=_hash(code or "")).first()
    if entry is None or entry.client_id != client.pk or entry.used_at or entry.expires_at <= now:
        raise OAuthError("invalid_grant", "The code is unknown, expired or already used.")
    entry.used_at = now
    entry.save(update_fields=["used_at"])
    if redirect_uri != entry.redirect_uri:
        raise OAuthError("invalid_grant", "redirect_uri does not match the authorization request.")
    if not _pkce_matches(verifier, entry.code_challenge):
        raise OAuthError("invalid_grant", "code_verifier does not match.")
    _check_resource(resource, entry.mcp_server, host)
    _check_user(entry.user, entry.mcp_server)

    access_plain = tokens.generate_token()
    refresh_plain = REFRESH_PREFIX + secrets.token_urlsafe(32)
    token = AccessToken.objects.create(
        user=entry.user,
        mcp_server=entry.mcp_server,
        name=f"{client.client_name} (browser sign-in)"[:100],
        token_prefix=access_plain[: tokens.PREFIX_DISPLAY_LENGTH],
        token_hash=tokens.hash_token(access_plain),
        created_by=entry.user,
        expires_at=now + timedelta(hours=ACCESS_TOKEN_HOURS),
    )
    OAuthGrant.objects.create(
        access_token=token,
        client=client,
        refresh_hash=_hash(refresh_plain),
        refresh_expires_at=now + timedelta(days=REFRESH_TOKEN_DAYS),
    )
    audit.record(
        audit.Action.TOKEN_CREATED,
        user=entry.user,
        mcp_server=entry.mcp_server,
        token_id=token.pk,
        token_name=token.name,
        token_prefix=token.token_prefix,
        owner=entry.user.get_username(),
        expires_at=token.expires_at.isoformat(),
        via="browser sign-in (OAuth)",
        client=client.client_name,
    )
    return _token_answer(access_plain, refresh_plain)


@transaction.atomic
def refresh(client: OAuthClient, *, refresh_token: str, resource: str, host: str) -> dict:
    now = timezone.now()
    grant = (
        OAuthGrant.objects.select_for_update()
        .select_related("access_token__user", "access_token__mcp_server")
        .filter(refresh_hash=_hash(refresh_token or ""))
        .first()
    )
    if grant is None or grant.client_id != client.pk or grant.refresh_expires_at <= now:
        raise OAuthError("invalid_grant", "The refresh token is unknown or expired.")
    token = grant.access_token
    if token.revoked_at:
        raise OAuthError("invalid_grant", "This connection was revoked on the platform.")
    _check_resource(resource, token.mcp_server, host)
    _check_user(token.user, token.mcp_server)

    # Rotate both: the old access and refresh tokens stop working now.
    access_plain = tokens.generate_token()
    refresh_plain = REFRESH_PREFIX + secrets.token_urlsafe(32)
    token.token_hash = tokens.hash_token(access_plain)
    token.token_prefix = access_plain[: tokens.PREFIX_DISPLAY_LENGTH]
    token.expires_at = now + timedelta(hours=ACCESS_TOKEN_HOURS)
    token.save(update_fields=["token_hash", "token_prefix", "expires_at"])
    grant.refresh_hash = _hash(refresh_plain)
    grant.refresh_expires_at = now + timedelta(days=REFRESH_TOKEN_DAYS)
    grant.save(update_fields=["refresh_hash", "refresh_expires_at"])
    return _token_answer(access_plain, refresh_plain)
