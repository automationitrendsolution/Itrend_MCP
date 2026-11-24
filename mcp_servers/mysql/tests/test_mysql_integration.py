"""
Against the real local MySQL demo database (scripts/setup_demo_db.py).
Skipped automatically when it is not set up.
"""

import pytest
from dotenv import dotenv_values

from mysql_mcp.config import PROJECT_DIR, DatabaseSettings
from mysql_mcp.db import MySQLDatabase, QueryError

MCP_ENV = dotenv_values(PROJECT_DIR / ".env")
PLATFORM_ENV = dotenv_values(PROJECT_DIR.parents[1] / ".env")


def _settings(user, password, **extra):
    return DatabaseSettings(
        host=MCP_ENV.get("MYSQL_HOST", "localhost"),
        port=int(MCP_ENV.get("MYSQL_PORT", "3306")),
        user=user,
        password=password,
        database=MCP_ENV.get("MYSQL_DATABASE", "mcp_demo"),
        **extra,
    )


@pytest.fixture
def readonly_db():
    if not MCP_ENV.get("MYSQL_PASSWORD"):
        pytest.skip("Demo database not configured (run scripts/setup_demo_db.py)")
    db = MySQLDatabase(_settings(MCP_ENV["MYSQL_USER"], MCP_ENV["MYSQL_PASSWORD"], max_rows=3))
    try:
        db.query("SELECT 1")
    except Exception as exc:
        pytest.skip(f"MySQL not reachable: {exc}")
    return db


def test_reads_demo_data(readonly_db):
    result = readonly_db.query("SELECT name, city FROM customers ORDER BY id")
    assert result.columns == ["name", "city"]
    assert result.rows[0] == {"name": "Arun Traders", "city": "Chennai"}


def test_row_limit_and_truncation(readonly_db):
    result = readonly_db.query("SELECT id FROM orders")
    assert len(result.rows) == 3
    assert result.truncated is True


def test_decimal_and_dates_are_json_safe(readonly_db):
    row = readonly_db.query("SELECT price, NOW() AS now FROM products LIMIT 1").rows[0]
    assert isinstance(row["price"], str) and isinstance(row["now"], str)


def test_readonly_user_cannot_write_even_without_the_sql_guard(readonly_db):
    with pytest.raises(QueryError):
        readonly_db.query("INSERT INTO customers (name, city, created_at) VALUES ('x', 'y', '2026-01-01')")


def test_readonly_user_cannot_read_other_databases(readonly_db):
    with pytest.raises(QueryError, match="denied"):
        readonly_db.query("SELECT COUNT(*) FROM mysql.user")


@pytest.fixture
def admin_db(readonly_db):
    if not PLATFORM_ENV.get("DB_PASSWORD"):
        pytest.skip("Platform admin credentials not available")
    return MySQLDatabase(_settings(PLATFORM_ENV["DB_USER"], PLATFORM_ENV["DB_PASSWORD"]))


def test_full_access_login_is_accepted_for_reading(admin_db):
    assert admin_db.query("SELECT COUNT(*) AS n FROM customers").rows[0]["n"] > 0


WRITES = [
    "INSERT INTO customers (name, city, created_at) VALUES ('x', 'y', '2026-01-01')",
    "UPDATE customers SET city = 'changed'",
    "DELETE FROM customers",
    "REPLACE INTO customers (id, name, city, created_at) VALUES (999999, 'x', 'y', '2026-01-01')",
    "CREATE TABLE zz_mcp_probe (id INT)",
    "CREATE TABLE zz_mcp_probe AS SELECT * FROM customers",
    "ALTER TABLE customers ADD COLUMN zz_probe INT",
    "TRUNCATE TABLE zz_mcp_probe_missing",
    "DROP TABLE zz_mcp_probe_missing",
    "RENAME TABLE customers TO zz_customers_renamed",
    "CREATE DATABASE zz_mcp_probe_db",
    "CREATE USER 'zz_probe'@'localhost'",
    "GRANT SELECT ON mcp_demo.* TO 'mcp_readonly'@'localhost'",
]


@pytest.mark.parametrize("sql", WRITES)
def test_full_access_login_still_cannot_write(admin_db, sql):
    """
    Without the SQL guard and with a full-access login, MySQL itself still refuses every change,
    because the session and the transaction are READ ONLY.
    """
    before = admin_db.query("SELECT COUNT(*) AS n, MAX(city) AS c FROM customers").rows
    with pytest.raises(QueryError):
        admin_db.query(sql)
    tables = {next(iter(r.values())) for r in admin_db.query("SHOW TABLES", max_rows=1000).rows}
    dbs = {next(iter(r.values())) for r in admin_db.query("SHOW DATABASES", max_rows=1000).rows}
    columns = {r["Field"] for r in admin_db.query("SHOW COLUMNS FROM customers").rows}
    assert tables == {"customers", "orders", "products"}
    assert "zz_mcp_probe_db" not in dbs and "zz_probe" not in columns
    assert admin_db.query("SELECT COUNT(*) AS n, MAX(city) AS c FROM customers").rows == before


def test_query_timeout(readonly_db):
    db = MySQLDatabase(_settings(MCP_ENV["MYSQL_USER"], MCP_ENV["MYSQL_PASSWORD"], query_timeout_ms=200))
    # A deliberately slow query (cartesian product), bypassing the SQL guard.
    with pytest.raises(QueryError):
        db.query(
            "SELECT COUNT(*) FROM information_schema.COLUMNS a, information_schema.COLUMNS b, information_schema.COLUMNS c"
        )
