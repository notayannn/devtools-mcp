"""
Notes:
- TEST SUITE IS AI PRODUCED
- Tests for query_database (SQLite) and read_log use pytest's
  built-in `tmp_path` fixture, so each test gets a fresh, throwaway
  file/directory and nothing touches your real filesystem or database.
- Tests for fetch_markdown and search_web mock out the network
  calls (requests.get / DDGS().text) so the suite runs fully offline
  and deterministically, without hitting rate limits or real sites.
- query_database's Postgres branch (_query_postgres) is NOT exercised
  here since it requires a live Postgres/Supabase connection string.
  See test_query_database_postgres_routing for what IS covered.
"""

import sqlite3

import pytest

import server
from server import (
    _is_safe_select,
    fetch_markdown,
    query_database,
    read_log,
    search_web,
)


# ---------------------------------------------------------
# Fixtures
# ---------------------------------------------------------

@pytest.fixture
def sqlite_db(tmp_path):
    """Creates a small throwaway SQLite database with a `users` table."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, status TEXT)")
    cur.executemany(
        "INSERT INTO users (name, status) VALUES (?, ?)",
        [
            ("Alice", "active"),
            ("Bob", "inactive"),
            ("Charlie", "active"),
        ],
    )
    conn.commit()
    conn.close()
    return str(db_path)


@pytest.fixture
def log_file(tmp_path):
    """Creates a throwaway log file with 10 numbered lines."""
    path = tmp_path / "test.log"
    path.write_text("\n".join(f"log line {i}" for i in range(1, 11)) + "\n")
    return str(path)


# ---------------------------------------------------------
# _is_safe_select — the SQL safety guard
# ---------------------------------------------------------

@pytest.mark.parametrize(
    "sql_query, expected",
    [
        ("SELECT * FROM users", True),
        ("select * from users;", True),
        ("  SELECT id, name FROM orders WHERE status = 'active'", True),
        ("DROP TABLE users", False),
        ("SELECT * FROM users; DROP TABLE users;", False),
        ("INSERT INTO users VALUES (1)", False),
        ("UPDATE users SET name=1", False),
        ("DELETE FROM users", False),
        ("ALTER TABLE users ADD COLUMN age INT", False),
        ("", False),
    ],
)
def test_is_safe_select(sql_query, expected):
    assert _is_safe_select(sql_query) is expected


def test_is_safe_select_false_positive_substring_match():
    """
    Known limitation: the keyword check is a substring match, not a real
    SQL parser, so it can reject harmless queries that merely *contain*
    a forbidden word inside a table/column name or string literal.
    This test documents that behavior rather than asserting it's ideal.
    """
    assert _is_safe_select("select * from updates_log") is False
    assert _is_safe_select("select * from users where name='drop the mic'") is False


# ---------------------------------------------------------
# query_database — SQLite path
# ---------------------------------------------------------

def test_query_database_sqlite_basic_select(sqlite_db):
    result = query_database(sqlite_db, "SELECT * FROM users")
    assert "Alice" in result
    assert "Bob" in result
    assert "Charlie" in result


def test_query_database_sqlite_respects_limit(sqlite_db):
    result = query_database(sqlite_db, "SELECT * FROM users", limit=1)
    assert result.count("'name'") == 1


def test_query_database_sqlite_filter(sqlite_db):
    result = query_database(sqlite_db, "SELECT name FROM users WHERE status = 'active'")
    assert "Alice" in result
    assert "Charlie" in result
    assert "Bob" not in result


def test_query_database_blocks_unsafe_query(sqlite_db):
    result = query_database(sqlite_db, "DROP TABLE users")
    assert result == "Error: Only single, read-only SELECT statements are permitted."


def test_query_database_missing_file():
    result = query_database("does_not_exist.db", "SELECT * FROM users")
    assert "not found" in result.lower()


def test_query_database_missing_table(sqlite_db):
    result = query_database(sqlite_db, "SELECT * FROM ghost_table")
    assert "Database Error" in result


def test_query_database_no_rows(sqlite_db):
    result = query_database(sqlite_db, "SELECT * FROM users WHERE status = 'archived'")
    assert result == "Query returned no rows."


def test_query_database_routes_postgres_urls_to_postgres_handler(monkeypatch, sqlite_db):
    """
    Confirms the dispatch logic in query_database sends postgres:// / postgresql://
    URLs down the Postgres path rather than trying to open them as SQLite files.
    We stub out _query_postgres itself so this test doesn't need a live database.
    """
    called_with = {}

    def fake_query_postgres(connection_string, sql_query, limit):
        called_with["args"] = (connection_string, sql_query, limit)
        return "fake postgres result"

    monkeypatch.setattr(server, "_query_postgres", fake_query_postgres)

    result = query_database("postgresql://user:pass@host:5432/db", "SELECT * FROM users", limit=5)

    assert result == "fake postgres result"
    assert called_with["args"] == ("postgresql://user:pass@host:5432/db", "SELECT * FROM users", 5)


# ---------------------------------------------------------
# read_log
# ---------------------------------------------------------

def test_read_log_returns_last_n_lines(log_file):
    result = read_log(log_file, lines=3)
    assert result.strip().splitlines() == ["log line 8", "log line 9", "log line 10"]


def test_read_log_missing_file():
    result = read_log("nope.log")
    assert result == "Error: File not found at nope.log"


def test_read_log_default_lines(log_file):
    result = read_log(log_file) 
    assert len(result.strip().splitlines()) == 10


# ---------------------------------------------------------
# fetch_markdown — network call mocked out
# ---------------------------------------------------------

def test_fetch_markdown_strips_html(monkeypatch):
    class FakeResponse:
        text = "<html><body><script>bad()</script><h1>Title</h1><p>Hello world</p></body></html>"

        def raise_for_status(self):
            pass

    def fake_get(url, headers=None, timeout=None):
        return FakeResponse()

    monkeypatch.setattr(server.requests, "get", fake_get)

    result = fetch_markdown("https://example.com")

    assert "Hello world" in result
    assert "bad()" not in result  # script contents should be stripped


def test_fetch_markdown_handles_request_error(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        raise ConnectionError("network unreachable")

    monkeypatch.setattr(server.requests, "get", fake_get)

    result = fetch_markdown("https://example.com")
    assert result.startswith("Error fetching")


# ---------------------------------------------------------
# search_web — network call mocked out
# ---------------------------------------------------------

def test_search_web_formats_results(monkeypatch):
    fake_results = [
        {"title": "Result One", "href": "https://a.com", "body": "Snippet A"},
        {"title": "Result Two", "href": "https://b.com", "body": "Snippet B"},
    ]

    class FakeDDGS:
        def text(self, query, max_results=3):
            return fake_results[:max_results]

    monkeypatch.setattr(server, "DDGS", FakeDDGS)

    result = search_web("fastmcp", max_results=2)

    assert "Result One" in result
    assert "https://a.com" in result
    assert "Snippet B" in result


def test_search_web_no_results(monkeypatch):
    class FakeDDGS:
        def text(self, query, max_results=3):
            return []

    monkeypatch.setattr(server, "DDGS", FakeDDGS)

    result = search_web("a query with no hits")
    assert result == "No results found."


def test_search_web_handles_error(monkeypatch):
    class FakeDDGS:
        def text(self, query, max_results=3):
            raise RuntimeError("rate limited")

    monkeypatch.setattr(server, "DDGS", FakeDDGS)

    result = search_web("anything")
    assert result.startswith("Search Error")