"""The real Streamable HTTP endpoint: authentication, host checks and real clients."""

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from .conftest import VALID_TOKEN

MCP_HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
}


def post(url, *, token=None, host=None):
    headers = dict(MCP_HEADERS)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if host:
        headers["Host"] = host
    return httpx2.post(url, headers=headers, json=INITIALIZE, timeout=10)


async def _call(url, token, mode="auto"):
    http = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
    async with http, Client(streamable_http_client(url, http_client=http), mode=mode) as client:
        tools = await client.list_tools()
        result = await client.call_tool("run_query", {"sql": "SELECT 1 AS n"})
    return tools, result


def test_health_needs_no_token(live_server):
    response = httpx2.get(f"{live_server}/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_reports_database_outage(live_server, fake_db):
    fake_db.available = False
    assert httpx2.get(f"{live_server}/health").status_code == 503


def test_request_without_token_is_rejected(live_server, fake_db):
    response = post(f"{live_server}/mcp")
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")
    assert fake_db.queries == []


def test_request_with_wrong_token_is_rejected(live_server):
    assert post(f"{live_server}/mcp", token="wrong").status_code == 401
    assert post(f"{live_server}/mcp", token=VALID_TOKEN + "x").status_code == 401


def test_token_in_query_string_is_not_accepted(live_server):
    response = httpx2.post(f"{live_server}/mcp?access_token={VALID_TOKEN}", headers=MCP_HEADERS, json=INITIALIZE)
    assert response.status_code == 401


def test_unexpected_host_header_is_rejected(live_server):
    """DNS-rebinding protection: only expected Host headers are served."""
    assert post(f"{live_server}/mcp", token=VALID_TOKEN, host="evil.example.com").status_code in (400, 403, 421)


def test_initialize_streams_and_creates_session(live_server):
    """Streamable HTTP: the reply is an SSE stream and carries an Mcp-Session-Id header."""
    headers = {**MCP_HEADERS, "Authorization": f"Bearer {VALID_TOKEN}"}
    with httpx2.stream("POST", f"{live_server}/mcp", headers=headers, json=INITIALIZE, timeout=10) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers.get("mcp-session-id")
        body = "".join(response.iter_text())
    assert '"serverInfo"' in body


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_official_client_end_to_end(live_server, mode):
    """Both current and older (initialize-handshake) MCP clients can connect and call tools."""
    tools, result = await _call(f"{live_server}/mcp", VALID_TOKEN, mode=mode)
    assert {tool.name for tool in tools.tools} == {"list_tables", "describe_table", "run_query"}
    assert result.structured_content["rows"] == [{"n": 1}]


def test_token_rejected_by_platform_is_refused(live_server, platform, fake_db):
    """Revoked, expired or no-longer-permitted tokens: the platform says inactive, the MCP answers 401."""
    platform.tokens.clear()
    assert post(f"{live_server}/mcp", token=VALID_TOKEN).status_code == 401
    assert fake_db.queries == []


def test_token_for_another_mcp_is_refused(live_server, platform):
    platform.tokens[VALID_TOKEN]["mcp"] = "other-mcp"
    assert post(f"{live_server}/mcp", token=VALID_TOKEN).status_code == 401


def test_platform_unreachable_fails_closed(live_server, platform):
    platform.down = True
    assert post(f"{live_server}/mcp", token=VALID_TOKEN).status_code == 401


def test_every_request_is_checked_with_the_platform(live_server, platform):
    post(f"{live_server}/mcp", token=VALID_TOKEN)
    post(f"{live_server}/mcp", token=VALID_TOKEN)
    assert platform.calls == 2  # caching is switched off in these tests
