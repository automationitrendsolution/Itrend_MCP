"""
First line of defence: reject any SQL that is not a single read-only statement.

This is defence in depth. The real guarantees come from the database:
- the MCP connects as a MySQL user that only has SELECT privileges, and
- every query runs inside a READ ONLY transaction with a time limit (see db.py).
"""

import re

ALLOWED_FIRST_KEYWORDS = frozenset({"SELECT", "WITH", "SHOW", "DESCRIBE", "DESC", "EXPLAIN"})

# Words that are never needed for reading data. Checked outside string literals.
BLOCKED_KEYWORDS = frozenset(
    {
        "INSERT", "UPDATE", "DELETE", "REPLACE", "MERGE", "UPSERT",
        "CREATE", "ALTER", "DROP", "TRUNCATE", "RENAME",
        "GRANT", "REVOKE", "SET", "LOCK", "UNLOCK", "CALL", "DO", "HANDLER", "LOAD",
        "INTO",      # SELECT ... INTO OUTFILE / DUMPFILE writes files on the server
        "OUTFILE", "DUMPFILE",
        "LOAD_FILE", # reads files from the server
        "SLEEP", "BENCHMARK", "GET_LOCK",
        "FOR",       # SELECT ... FOR UPDATE / FOR SHARE takes locks
        "SHARE",
    }
)

MAX_SQL_LENGTH = 20_000

_STRING_LITERAL = re.compile(r"""'(?:[^'\\]|\\.|'')*'|"(?:[^"\\]|\\.|"")*"|`(?:[^`]|``)*`""", re.DOTALL)
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class UnsafeQueryError(ValueError):
    """The SQL was refused before reaching the database."""


def check_read_only(sql: str) -> str:
    """Return the statement (without a trailing semicolon) if it is a single read-only query."""
    if not sql or not sql.strip():
        raise UnsafeQueryError("The query is empty.")
    if len(sql) > MAX_SQL_LENGTH:
        raise UnsafeQueryError(f"The query is longer than {MAX_SQL_LENGTH} characters.")

    # Remove string literals first, so text inside quotes cannot hide or fake anything.
    without_strings = _STRING_LITERAL.sub("''", sql)
    if any(quote in without_strings.replace("''", "") for quote in ("'", '"', "`")):
        raise UnsafeQueryError("The query has an unterminated quote.")

    # Comments can hide statements, and /*! ... */ is executed by MySQL. Refuse them all.
    if "/*" in without_strings or "--" in without_strings or "#" in without_strings:
        raise UnsafeQueryError("Comments are not allowed in queries.")

    statement = sql.strip()
    body = without_strings.strip()
    if body.endswith(";"):
        body = body[:-1].rstrip()
        statement = statement[:-1].rstrip()
    if ";" in body:
        raise UnsafeQueryError("Only one statement is allowed.")

    words = [word.upper() for word in _WORD.findall(body)]
    if not words or words[0] not in ALLOWED_FIRST_KEYWORDS:
        allowed = ", ".join(sorted(ALLOWED_FIRST_KEYWORDS))
        raise UnsafeQueryError(f"Only read-only queries are allowed ({allowed}).")

    blocked = sorted(BLOCKED_KEYWORDS.intersection(words))
    if blocked:
        raise UnsafeQueryError(f"These keywords are not allowed: {', '.join(blocked)}.")

    return statement


# ---------------------------------------------------------------------------
# Staying inside one database
# ---------------------------------------------------------------------------

SYSTEM_SCHEMAS = frozenset({"information_schema", "mysql", "performance_schema", "sys"})
# Text in '...' or "..." (backquoted names are kept: they can be database names).
_TEXT_LITERAL = re.compile(r"'(?:[^'\\]|\\.|'')*'|" + r'"(?:[^"\\]|\\.|"")*"', re.DOTALL)
_IDENT = r"(?:`(?:[^`]|``)+`|[A-Za-z0-9_$]+)"
# name . name  or  name . *   -> the first name may be a database
_QUALIFIER = re.compile(rf"({_IDENT})\s*\.\s*(?:{_IDENT}|\*)")
_SHOW_SOURCE = re.compile(rf"\b(?:FROM|IN)\s+({_IDENT})", re.IGNORECASE)


def _name(identifier: str) -> str:
    if identifier.startswith("`"):
        identifier = identifier[1:-1].replace("``", "`")
    return identifier.lower()


def check_own_database(sql: str, own: str, others: set[str]) -> None:
    """
    Refuse a query that reaches into another database (e.g. SELECT * FROM sales.orders).

    `others` are the names of databases the MySQL login can see besides its own. Needed when
    several databases share one MySQL login; with one login per database MySQL refuses anyway.
    A table alias that happens to equal a database name is refused too: rename the alias.
    """
    text = _TEXT_LITERAL.sub("''", sql)
    own = own.lower()
    forbidden = ({name.lower() for name in others} | SYSTEM_SCHEMAS) - {own}
    names = {_name(match) for match in _QUALIFIER.findall(text)}
    if _WORD.match(text.strip()) and text.strip().split()[0].upper() == "SHOW":
        names |= {_name(match) for match in _SHOW_SOURCE.findall(text)}
        if any(name != own for name in names):
            raise UnsafeQueryError("SHOW can only look at this database.")
    blocked = sorted(names & forbidden)
    if blocked:
        raise UnsafeQueryError(
            f"This query reaches into another database ({', '.join(blocked)}). "
            "Only the chosen database can be used; pick another with the database argument if you have access to it."
        )
