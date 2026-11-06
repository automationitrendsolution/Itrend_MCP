"""
Bearer-token check for incoming MCP requests.

Clients send a personal (or service-account) token issued by the iTrend MCP
Platform. This server does not know any tokens itself: it asks the platform
(POST /api/v1/tokens/introspect/) whether the token may be used on this MCP
right now. The platform checks expiry, revocation, the owner's account and
their permission on this MCP, and records the connection in its activity log.

Positive answers are remembered for a few seconds (MCP_TOKEN_CACHE_SECONDS)
so a busy client does not cause one platform call per message. Revoking a
token therefore takes effect within that time. If the platform cannot be
reached, every request is refused ("fail closed").
"""

import hashlib
import logging
import time
from collections.abc import Callable

import httpx2
from mcp.server.auth.provider import AccessToken

logger = logging.getLogger(__name__)

READ_SCOPE = "mysql:read"
TOKEN_PREFIX = "itmcp_"
MAX_TOKEN_LENGTH = 200
MAX_CACHE_ENTRIES = 1000


class PlatformTokenVerifier:
    def __init__(
        self,
        *,
        platform_url: str,
        introspection_key: str,
        mcp_slug: str,
        resource_url: str,
        cache_seconds: float = 30,
        timeout: float = 5,
        transport: httpx2.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = platform_url.rstrip("/") + "/api/v1/tokens/introspect/"
        self._headers = {"Authorization": f"Bearer {introspection_key}"}
        self._slug = mcp_slug
        self._resource_url = resource_url
        self._cache_seconds = cache_seconds
        self._timeout = timeout
        self._transport = transport
        self._clock = clock
        # sha256(token) -> (valid until, AccessToken). Plain tokens are never used as keys.
        self._cache: dict[str, tuple[float, AccessToken]] = {}

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token.startswith(TOKEN_PREFIX) or len(token) > MAX_TOKEN_LENGTH:
            return None

        key = hashlib.sha256(token.encode()).hexdigest()
        cached = self._cache.get(key)
        if cached and cached[0] > self._clock():
            return cached[1]

        answer = await self._ask_platform(token)
        if answer is None:
            self._cache.pop(key, None)
            return None

        access = AccessToken(
            token=token,
            client_id=f"itmcp-token-{answer['token_id']}",
            subject=str(answer.get("username", "")),
            scopes=[READ_SCOPE],
            expires_at=int(answer["expires_at"]),
            # The platform confirmed this token was issued for this MCP, so it is
            # bound to this server's URL (checked again by the SDK).
            resource=self._resource_url,
        )
        if self._cache_seconds > 0:
            if len(self._cache) >= MAX_CACHE_ENTRIES:
                self._cache.clear()
            self._cache[key] = (self._clock() + self._cache_seconds, access)
        return access

    async def _ask_platform(self, token: str) -> dict | None:
        try:
            async with httpx2.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                response = await client.post(self._url, json={"token": token, "mcp": self._slug}, headers=self._headers)
        except httpx2.HTTPError as exc:
            logger.error("Cannot reach the platform to check a token (%s). Refusing the request.", exc.__class__.__name__)
            return None

        if response.status_code != 200:
            logger.error("Platform token check failed with HTTP %s. Is MCP_INTROSPECTION_KEY correct?", response.status_code)
            return None
        try:
            body = response.json()
        except ValueError:
            logger.error("Platform token check returned invalid JSON.")
            return None
        if body.get("active") is not True or body.get("mcp") != self._slug:
            return None
        if not isinstance(body.get("token_id"), int) or not isinstance(body.get("expires_at"), int):
            logger.error("Platform token check returned an unexpected answer.")
            return None
        return body
