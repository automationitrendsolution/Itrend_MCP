"""PlatformTokenVerifier: asks the platform about each token, caches briefly, fails closed."""

import httpx2
import pytest

from mysql_mcp.auth import READ_SCOPE, PlatformTokenVerifier

from .conftest import INTROSPECTION_KEY, VALID_TOKEN

pytestmark = pytest.mark.anyio


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def test_valid_token(settings, platform):
    access = await platform.verifier(settings).verify_token(VALID_TOKEN)
    assert access is not None
    assert access.scopes == [READ_SCOPE]
    assert access.subject == "alice"
    assert access.client_id == "itmcp-token-1"
    assert access.resource == settings.public_url
    assert access.expires_at == platform.tokens[VALID_TOKEN]["expires_at"]


async def test_sends_key_slug_and_token(settings):
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        seen["body"] = request.content
        return httpx2.Response(200, json={"active": False})

    verifier = PlatformTokenVerifier(
        platform_url="http://platform.test/", introspection_key=INTROSPECTION_KEY, mcp_slug="mysql",
        resource_url=settings.public_url, transport=httpx2.MockTransport(handler),
    )
    assert await verifier.verify_token(VALID_TOKEN) is None
    assert seen["auth"] == f"Bearer {INTROSPECTION_KEY}"
    assert b'"mcp":"mysql"' in seen["body"].replace(b" ", b"")


@pytest.mark.parametrize("token", ["", "wrong", "Bearer itmcp_x", "itmcp_" + "x" * 300])
async def test_malformed_tokens_never_reach_the_platform(settings, platform, token):
    assert await platform.verifier(settings).verify_token(token) is None
    assert platform.calls == 0


async def test_unknown_token(settings, platform):
    assert await platform.verifier(settings).verify_token("itmcp_" + "u" * 43) is None


async def test_answer_for_another_mcp_is_ignored(settings, platform):
    platform.tokens[VALID_TOKEN]["mcp"] = "other"
    assert await platform.verifier(settings).verify_token(VALID_TOKEN) is None


async def test_wrong_introspection_key_fails_closed(settings, platform):
    verifier = PlatformTokenVerifier(
        platform_url=settings.platform_url, introspection_key="wrong" * 10, mcp_slug="mysql",
        resource_url=settings.public_url, transport=httpx2.MockTransport(platform.handler),
    )
    assert await verifier.verify_token(VALID_TOKEN) is None


async def test_platform_down_fails_closed(settings, platform):
    platform.down = True
    assert await platform.verifier(settings).verify_token(VALID_TOKEN) is None


async def test_unexpected_answer_fails_closed(settings):
    verifier = PlatformTokenVerifier(
        platform_url=settings.platform_url, introspection_key=INTROSPECTION_KEY, mcp_slug="mysql",
        resource_url=settings.public_url,
        transport=httpx2.MockTransport(lambda request: httpx2.Response(200, json={"active": True, "mcp": "mysql"})),
    )
    assert await verifier.verify_token(VALID_TOKEN) is None


async def test_positive_answers_are_cached_briefly(settings, platform):
    clock = Clock()
    verifier = platform.verifier(settings, cache_seconds=30, clock=clock)
    for _ in range(3):
        assert await verifier.verify_token(VALID_TOKEN)
    assert platform.calls == 1

    platform.tokens.clear()  # e.g. revoked on the platform
    clock.now += 29
    assert await verifier.verify_token(VALID_TOKEN)  # still cached
    clock.now += 2
    assert await verifier.verify_token(VALID_TOKEN) is None  # cache expired, platform says no
    assert platform.calls == 2


async def test_negative_answers_are_not_cached(settings, platform):
    verifier = platform.verifier(settings, cache_seconds=30)
    saved = dict(platform.tokens)
    platform.tokens.clear()
    assert await verifier.verify_token(VALID_TOKEN) is None
    platform.tokens.update(saved)
    assert await verifier.verify_token(VALID_TOKEN)
