import pytest

from mysql_mcp.sql_guard import UnsafeQueryError, check_read_only

ALLOWED = [
    "SELECT * FROM customers",
    "select id, name from customers where city = 'Chennai';",
    "WITH t AS (SELECT 1 AS n) SELECT n FROM t",
    "SHOW TABLES",
    "DESCRIBE customers",
    "EXPLAIN SELECT * FROM orders",
    "SELECT name FROM customers ORDER BY name DESC LIMIT 5",
    "SELECT 'DROP TABLE x; -- not really' AS text",  # dangerous words inside a string are harmless
    "SELECT `select`, 'it''s' FROM t",
]

BLOCKED = [
    ("", "empty"),
    ("   ", "empty"),
    ("DELETE FROM orders", "read-only"),
    ("UPDATE customers SET name = 'x'", "read-only"),
    ("INSERT INTO t VALUES (1)", "read-only"),
    ("DROP TABLE customers", "read-only"),
    ("SELECT 1; DROP TABLE customers", "one statement"),
    ("SELECT 1; SELECT 2", "one statement"),
    ("SELECT * FROM customers INTO OUTFILE '/tmp/x'", "INTO"),
    ("SELECT LOAD_FILE('/etc/passwd')", "LOAD_FILE"),
    ("SELECT SLEEP(100)", "SLEEP"),
    ("SELECT BENCHMARK(1000000, MD5('x'))", "BENCHMARK"),
    ("SELECT * FROM orders FOR UPDATE", "FOR"),
    ("SELECT 1 /*! ; DROP TABLE customers */", "Comments"),
    ("SELECT 1 -- comment", "Comments"),
    ("SELECT 1 # comment", "Comments"),
    ("SELECT 'unterminated", "unterminated"),
    ("SET @a = 1", "read-only"),
    ("CALL do_something()", "read-only"),
    ("SELECT " + "1," * 10_000 + "1", "longer"),
]


@pytest.mark.parametrize("sql", ALLOWED)
def test_allowed(sql):
    assert check_read_only(sql)


@pytest.mark.parametrize(("sql", "reason"), BLOCKED)
def test_blocked(sql, reason):
    with pytest.raises(UnsafeQueryError, match=reason):
        check_read_only(sql)


def test_trailing_semicolon_is_removed():
    assert check_read_only("SELECT 1;") == "SELECT 1"
