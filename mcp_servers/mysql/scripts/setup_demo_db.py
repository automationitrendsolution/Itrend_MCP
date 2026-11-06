"""
Create a local demo database and a read-only MySQL user for the MySQL MCP.

    uv run python scripts/setup_demo_db.py

Uses the MySQL administrator account from the platform's .env (DB_USER /
DB_PASSWORD) only to create:
  - database  mcp_demo  with sample customers, products and orders
  - user      mcp_readonly@localhost  with SELECT on mcp_demo.* and nothing else
and writes this project's .env (if it does not exist yet) with a random password
and API key. Safe to run more than once.
"""

import secrets
from pathlib import Path

import pymysql
from dotenv import dotenv_values

PROJECT_DIR = Path(__file__).resolve().parents[1]
PLATFORM_ENV = PROJECT_DIR.parents[1] / ".env"
MCP_ENV = PROJECT_DIR / ".env"

DATABASE = "mcp_demo"
READONLY_USER = "mcp_readonly"

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS customers (
        id INT PRIMARY KEY AUTO_INCREMENT,
        name VARCHAR(100) NOT NULL,
        city VARCHAR(60) NOT NULL,
        created_at DATE NOT NULL
    ) COMMENT='Demo customers'""",
    """CREATE TABLE IF NOT EXISTS products (
        id INT PRIMARY KEY AUTO_INCREMENT,
        sku VARCHAR(20) NOT NULL UNIQUE,
        name VARCHAR(100) NOT NULL,
        price DECIMAL(10, 2) NOT NULL
    ) COMMENT='Demo product catalogue'""",
    """CREATE TABLE IF NOT EXISTS orders (
        id INT PRIMARY KEY AUTO_INCREMENT,
        customer_id INT NOT NULL,
        product_id INT NOT NULL,
        quantity INT NOT NULL,
        ordered_at DATETIME NOT NULL,
        FOREIGN KEY (customer_id) REFERENCES customers (id),
        FOREIGN KEY (product_id) REFERENCES products (id)
    ) COMMENT='Demo orders'""",
]

CUSTOMERS = [
    ("Arun Traders", "Chennai", "2025-01-15"),
    ("Bluewave Retail", "Bengaluru", "2025-03-02"),
    ("Coastal Foods", "Kochi", "2025-04-20"),
    ("Delta Hardware", "Hyderabad", "2025-06-11"),
    ("Evergreen Stores", "Coimbatore", "2025-08-30"),
]
PRODUCTS = [
    ("SKU-1001", "Printer paper A4 (500 sheets)", "349.00"),
    ("SKU-1002", "Ballpoint pens (box of 50)", "420.00"),
    ("SKU-1003", "USB-C cable 1m", "299.00"),
    ("SKU-1004", "Wireless mouse", "899.00"),
]
ORDERS = [
    (1, 1, 10, "2026-09-01 10:15:00"),
    (1, 2, 4, "2026-09-03 12:40:00"),
    (2, 3, 25, "2026-09-05 09:05:00"),
    (3, 4, 6, "2026-09-12 16:20:00"),
    (4, 1, 40, "2026-10-02 11:00:00"),
    (5, 2, 12, "2026-10-08 14:30:00"),
    (2, 4, 3, "2026-10-15 17:45:00"),
]


def main() -> None:
    admin = dotenv_values(PLATFORM_ENV)
    existing = dotenv_values(MCP_ENV) if MCP_ENV.exists() else {}
    password = existing.get("MYSQL_PASSWORD") or secrets.token_urlsafe(24)

    connection = pymysql.connect(
        host=admin.get("DB_HOST", "localhost"),
        port=int(admin.get("DB_PORT", "3306")),
        user=admin["DB_USER"],
        password=admin["DB_PASSWORD"],
        autocommit=True,
    )
    with connection, connection.cursor() as cursor:
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS {DATABASE} CHARACTER SET utf8mb4")
        cursor.execute(f"USE {DATABASE}")
        for statement in SCHEMA:
            cursor.execute(statement)

        cursor.execute("SELECT COUNT(*) FROM customers")
        if cursor.fetchone()[0] == 0:
            cursor.executemany("INSERT INTO customers (name, city, created_at) VALUES (%s, %s, %s)", CUSTOMERS)
            cursor.executemany("INSERT INTO products (sku, name, price) VALUES (%s, %s, %s)", PRODUCTS)
            cursor.executemany(
                "INSERT INTO orders (customer_id, product_id, quantity, ordered_at) VALUES (%s, %s, %s, %s)", ORDERS
            )

        cursor.execute(f"CREATE USER IF NOT EXISTS '{READONLY_USER}'@'localhost' IDENTIFIED BY %s", (password,))
        cursor.execute(f"ALTER USER '{READONLY_USER}'@'localhost' IDENTIFIED BY %s", (password,))
        cursor.execute(f"GRANT SELECT ON {DATABASE}.* TO '{READONLY_USER}'@'localhost'")
        cursor.execute(f"SHOW GRANTS FOR '{READONLY_USER}'@'localhost'")
        grants = [row[0] for row in cursor.fetchall()]

    if not MCP_ENV.exists():
        MCP_ENV.write_text(
            "\n".join(
                [
                    "# MySQL MCP settings. Never commit this file.",
                    "MYSQL_HOST=localhost",
                    "MYSQL_PORT=3306",
                    f"MYSQL_DATABASE={DATABASE}",
                    f"MYSQL_USER={READONLY_USER}",
                    f"MYSQL_PASSWORD={password}",
                    "",
                    "# Clients must send: Authorization: Bearer <MCP_API_KEY>",
                    f"MCP_API_KEY={secrets.token_urlsafe(32)}",
                    "MCP_HOST=127.0.0.1",
                    "MCP_PORT=8101",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        print(f"Wrote {MCP_ENV}")
    else:
        print(f"Kept existing {MCP_ENV}")

    print(f"Database '{DATABASE}' ready. Grants for {READONLY_USER}:")
    for grant in grants:
        print("  ", grant)


if __name__ == "__main__":
    main()
