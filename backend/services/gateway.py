"""
The MCP gateway: one front door for every MCP.

    MCP client ──POST /mcp/<slug>/  (Authorization: Bearer itmcp_…)──▶ website
        1. find the MCP by its slug
        2. check the token (tokens.introspect: exists, not revoked/expired,
           issued for this MCP, owner active and has Connect) – logged
        3. forward the request to the MCP's own address (127.0.0.1:<port>)
        4. stream the answer back (JSON or Server-Sent Events)

Clients only ever see https://<platform>/mcp/<slug>/. The MCP itself listens
on 127.0.0.1 only, so on a server it cannot be reached without the gateway.

Only a fixed list of headers is passed on in each direction. Headers that
claim an identity (X-ITrend-…) are always set by the gateway, never copied
from the client.
"""

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from backend.models import MCPServer

from . import tokens

logger = logging.getLogger(__name__)

# Request headers an MCP needs. Everything else (cookies, Host, proxies…) is dropped.
FORWARDED_REQUEST_HEADERS = (
    "accept",
    "authorization",  # the MCP may check the token again (defence in depth)
    "content-type",
    "last-event-id",
    "mcp-protocol-version",
    "mcp-session-id",
    "user-agent",
)
# Response headers the client needs.
FORWARDED_RESPONSE_HEADERS = (
    "cache-control",
    "content-encoding",
    "content-type",
    "mcp-protocol-version",
    "mcp-session-id",
    "www-authenticate",
)
ALLOWED_METHODS = {"GET", "POST", "DELETE"}

# Connecting must be quick (the MCP is on this machine). Reading may be slow:
# a tool can run for minutes, and an event stream stays open while idle.
TIMEOUT = httpx.Timeout(connect=5.0, read=600.0, write=30.0, pool=10.0)
_client: httpx.Client | None = None


def get_client() -> httpx.Client:
    """One shared connection pool for all forwarded requests (tests replace it)."""
    global _client
    if _client is None:
        _client = httpx.Client(timeout=TIMEOUT, follow_redirects=False, trust_env=False)
    return _client


def set_client(client: httpx.Client | None) -> None:
    global _client
    _client = client


class GatewayError(Exception):
    """A refusal the client should see, with its HTTP status."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@dataclass
class Upstream:
    status: int
    headers: dict[str, str]
    body: Iterator[bytes]


def find_server(slug: str) -> MCPServer:
    server = MCPServer.objects.filter(slug=slug).first()
    if server is None or server.is_external or not server.internal_endpoint:
        # Same answer for "does not exist" and "not served here": nothing to learn by guessing.
        raise GatewayError(404, "not_found", "There is no MCP at this address.")
    return server


def bearer_token(authorization: str) -> str:
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        raise GatewayError(401, "missing_token", "Send your access token: Authorization: Bearer itmcp_…")
    return value.strip()


def authorize(server: MCPServer, authorization: str) -> tokens.Introspection:
    result = tokens.introspect(bearer_token(authorization), server.slug)
    if not result.active:
        # Never say *why* (expired, revoked, …); the reason is in the activity log.
        raise GatewayError(401, "invalid_token", "This token cannot be used for this MCP.")
    return result


def upstream_url(server: MCPServer, rest: str, query: str) -> str:
    base = server.internal_endpoint.rstrip("/")
    parts = urlsplit(base)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise GatewayError(502, "bad_configuration", "This MCP's address is not configured correctly.")
    url = base + ("/" + rest.lstrip("/") if rest.strip("/") else "")
    return f"{url}?{query}" if query else url


def forward(
    server: MCPServer,
    *,
    method: str,
    rest: str,
    query: str,
    headers: dict[str, str],
    body: bytes,
    identity: tokens.Introspection,
    scheme: str = "http",
) -> Upstream:
    if method not in ALLOWED_METHODS:
        raise GatewayError(405, "method_not_allowed", "MCP clients use GET, POST or DELETE.")

    outgoing = {name: headers[name] for name in FORWARDED_REQUEST_HEADERS if headers.get(name)}
    outgoing["x-itrend-user"] = identity.token.user.get_username()
    outgoing["x-itrend-permission"] = identity.level or ""
    outgoing["x-forwarded-proto"] = scheme

    client = get_client()
    request = client.build_request(method, upstream_url(server, rest, query), headers=outgoing, content=body or None)
    try:
        response = client.send(request, stream=True)
    except httpx.ConnectError:
        raise GatewayError(503, "mcp_not_running", f"{server.name} is not running right now. Ask an administrator to start it.") from None
    except httpx.TimeoutException:
        raise GatewayError(504, "mcp_timeout", f"{server.name} did not answer in time.") from None
    except httpx.HTTPError as exc:
        logger.warning("Gateway could not reach %s: %s", server.slug, exc)
        raise GatewayError(502, "mcp_unreachable", f"{server.name} could not be reached.") from None

    def body_chunks() -> Iterator[bytes]:
        try:
            # iter_raw: pass bytes on exactly as received (no decompression, no buffering).
            yield from response.iter_raw()
        except httpx.HTTPError as exc:
            logger.info("Stream from %s ended: %s", server.slug, exc)
        finally:
            response.close()

    returned = {name: response.headers[name] for name in FORWARDED_RESPONSE_HEADERS if name in response.headers}
    return Upstream(status=response.status_code, headers=returned, body=body_chunks())
