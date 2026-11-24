"""Several databases, and each person only using the ones switched on for them."""

import pytest
from mcp import Client

from mysql_mcp.config import ConfigError, DatabaseEntry, DatabaseSettings, Settings, load_databases
from mysql_mcp.server import build_server

from .conftest import INTROSPECTION_KEY, FakeDatabase

pytestmark = pytest.mark.anyio


def entry(key, database=None):
    return DatabaseEntry(key, key.title(), DatabaseSettings("localhost", 3306, "u", "p", database or f"{key}db"))


@pytest.fixture
def settings():
    return Settings(databases=(entry("powerbi"), entry("sales")), introspection_key=INTROSPECTION_KEY)


@pytest.fixture
def dbs():
    return {"powerbi": FakeDatabase(), "sales": FakeDatabase()}


def server_for(settings, dbs, allowed):
    return build_server(settings, dbs, allowed=lambda: allowed)


async def test_list_databases_shows_only_yours(settings, dbs):
    async with Client(server_for(settings, dbs, ["sales"])) as client:
        result = await client.call_tool("list_databases", {})
    assert result.structured_content == {"databases": [{"name": "sales", "label": "Sales"}]}


async def test_star_means_all(settings, dbs):
    async with Client(server_for(settings, dbs, ["*"])) as client:
        result = await client.call_tool("list_databases", {})
    assert [d["name"] for d in result.structured_content["databases"]] == ["powerbi", "sales"]


async def test_single_allowed_database_is_used_by_default(settings, dbs):
    async with Client(server_for(settings, dbs, ["sales"])) as client:
        result = await client.call_tool("run_query", {"sql": "SELECT 1"})
    assert not result.is_error
    assert dbs["sales"].queries and not dbs["powerbi"].queries


async def test_must_choose_when_several(settings, dbs):
    async with Client(server_for(settings, dbs, ["*"])) as client:
        unclear = await client.call_tool("run_query", {"sql": "SELECT 1"})
        chosen = await client.call_tool("run_query", {"sql": "SELECT 1", "database": "powerbi"})
    assert unclear.is_error and "Choose a database" in unclear.content[0].text
    assert not chosen.is_error and dbs["powerbi"].queries


async def test_database_not_switched_on_is_refused(settings, dbs):
    async with Client(server_for(settings, dbs, ["sales"])) as client:
        for tool, args in (("run_query", {"sql": "SELECT 1"}), ("list_tables", {}), ("describe_table", {"table": "t"})):
            result = await client.call_tool(tool, {**args, "database": "powerbi"})
            assert result.is_error and "not available to you" in result.content[0].text
    assert not dbs["powerbi"].queries


async def test_no_databases_switched_on(settings, dbs):
    async with Client(server_for(settings, dbs, [])) as client:
        result = await client.call_tool("list_tables", {})
        listing = await client.call_tool("list_databases", {})
    assert result.is_error and "not been given any database" in result.content[0].text
    assert listing.structured_content == {"databases": []}


async def test_query_cannot_reach_another_database(settings, dbs):
    async with Client(server_for(settings, dbs, ["sales"])) as client:
        result = await client.call_tool("run_query", {"sql": "SELECT * FROM powerbidb.orders"})
    assert result.is_error and "another database" in result.content[0].text
    assert not dbs["sales"].queries


def test_config_several_databases(monkeypatch):
    for name, value in {
        "MYSQL_DATABASES": "powerbi,sales", "MYSQL_USER": "shared", "MYSQL_PASSWORD": "pw",
        "MYSQL_POWERBI_DATABASE": "powerbidb", "MYSQL_POWERBI_LABEL": "Power BI",
        "MYSQL_SALES_USER": "sales_ro", "MYSQL_SALES_PASSWORD": "pw2", "MYSQL_SALES_HOST": "10.0.0.5",
    }.items():
        monkeypatch.setenv(name, value)
    powerbi, sales = load_databases()
    assert (powerbi.key, powerbi.label, powerbi.settings.database, powerbi.settings.user) == ("powerbi", "Power BI", "powerbidb", "shared")
    assert (sales.settings.database, sales.settings.user, sales.settings.host) == ("sales", "sales_ro", "10.0.0.5")


def test_config_rejects_bad_names(monkeypatch):
    monkeypatch.setenv("MYSQL_DATABASES", "Power BI")
    monkeypatch.setenv("MYSQL_USER", "u")
    monkeypatch.setenv("MYSQL_PASSWORD", "p")
    with pytest.raises(ConfigError):
        load_databases()


def test_config_one_database_as_before(monkeypatch):
    monkeypatch.delenv("MYSQL_DATABASES", raising=False)
    for name, value in {"MYSQL_DATABASE": "mcp_demo", "MYSQL_USER": "u", "MYSQL_PASSWORD": "p"}.items():
        monkeypatch.setenv(name, value)
    (only,) = load_databases()
    assert (only.key, only.settings.database) == ("mcp_demo", "mcp_demo")
