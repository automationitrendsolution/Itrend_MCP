"""Tool behaviour, using the official MCP client connected in-process."""

import pytest
from mcp import Client

from mysql_mcp.server import build_server

pytestmark = pytest.mark.anyio


@pytest.fixture
def server(settings, fake_db):
    return build_server(settings, fake_db)


async def test_lists_three_read_only_tools(server):
    async with Client(server) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert set(tools) == {"list_tables", "describe_table", "run_query"}
    for tool in tools.values():
        assert tool.annotations.read_only_hint is True
        assert tool.output_schema is not None


async def test_run_query_returns_structured_rows(server, fake_db):
    async with Client(server) as client:
        result = await client.call_tool("run_query", {"sql": "SELECT 1 AS n;", "max_rows": 5})
    assert not result.is_error
    assert result.structured_content == {"columns": ["n"], "rows": [{"n": 1}], "row_count": 1, "truncated": False}
    assert fake_db.queries[-1] == ("SELECT 1 AS n", None, 5)


async def test_unsafe_query_never_reaches_database(server, fake_db):
    async with Client(server) as client:
        result = await client.call_tool("run_query", {"sql": "DELETE FROM orders"})
    assert result.is_error
    assert "read-only" in result.content[0].text
    assert fake_db.queries == []


async def test_describe_table_uses_parameters(server, fake_db):
    async with Client(server) as client:
        result = await client.call_tool("describe_table", {"table": "customers"})
    assert result.structured_content["table"] == "customers"
    assert fake_db.queries[-1][1] == ("customers",)


async def test_describe_table_rejects_injection(server, fake_db):
    async with Client(server) as client:
        result = await client.call_tool("describe_table", {"table": "x' OR '1'='1"})
    assert result.is_error
    assert fake_db.queries == []


async def test_describe_unknown_table(server):
    async with Client(server) as client:
        result = await client.call_tool("describe_table", {"table": "nope"})
    assert result.is_error
    assert "does not exist" in result.content[0].text


async def test_database_outage_gives_friendly_error(server, fake_db):
    fake_db.available = False
    async with Client(server) as client:
        result = await client.call_tool("list_tables", {})
    assert result.is_error
    assert "not available" in result.content[0].text


async def test_max_rows_must_be_positive(server):
    async with Client(server) as client:
        result = await client.call_tool("run_query", {"sql": "SELECT 1", "max_rows": 0})
    assert result.is_error
