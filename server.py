import os
import sqlite3
import requests
from bs4 import BeautifulSoup
from fastmcp import FastMCP
from ddgs import DDGS
from dotenv import load_dotenv

load_dotenv()

mcp = FastMCP(
    name="DevTools",
    instructions=(
        "A read-only developer toolbox. Use fetch_markdown to pull clean text "
        "from a webpage, read_log to inspect the tail of a local log file, "
        "search_web to look up current documentation or solutions, and "
        "query_database to run a single read-only SELECT against a local "
        "SQLite file or a Postgres/Supabase connection string. "
        "query_database only accepts SELECT statements — it will reject any "
        "query that could modify or delete data."
    ),
)


@mcp.tool
def fetch_markdown(url: str) -> str:
    """Fetches a webpage and extracts the main text content, stripping away HTML."""
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        for tag in soup(["script", "style", "nav", "footer"]):
            tag.extract()

        text = soup.get_text(separator="\n\n")
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        clean = "\n".join(chunk for chunk in chunks if chunk)

        return clean[:8000]
    except Exception as e:
        return f"Error fetching {url}: {str(e)}"


@mcp.tool
def read_log(file_path: str, lines: int = 50) -> str:
    """Reads the last N lines of a local log file or text file."""
    try:
        if not os.path.exists(file_path):
            return f"Error: File not found at {file_path}"

        with open(file_path, "r", encoding="utf-8") as f:
            all_lines = f.readlines()

        return "".join(all_lines[-lines:])
    except Exception as e:
        return f"Error reading log file: {str(e)}"


@mcp.tool
def search_web(query: str, max_results: int = 3) -> str:
    """Searches the live web to fetch the latest documentation or solutions."""
    try:
        result = DDGS().text(query, max_results=max_results)
        if not result:
            return "No results found."

        output = []
        for r in result:
            output.append(f"Title: {r['title']}\nLink: {r['href']}\nSnippet: {r['body']}\n")
        return "\n".join(output)
    except Exception as e:
        return f"Search Error: {str(e)}"


_FORBIDDEN_KEYWORDS = (
    "insert", "update", "delete", "drop", "alter",
    "truncate", "grant", "revoke", "create", "attach",
)


def _is_safe_select(sql_query: str) -> bool:
    normalized = sql_query.strip().lower()

    if not normalized.startswith("select"):
        return False

    if any(word in normalized for word in _FORBIDDEN_KEYWORDS):
        return False

    if ";" in sql_query.strip().rstrip(";"):
        return False

    return True


@mcp.tool
def query_database(db_path_or_url: str, sql_query: str, limit: int = 50) -> str:
    """Runs a read-only SELECT query against a SQLite file or Postgres/Supabase URL."""
    if not _is_safe_select(sql_query):
        return "Error: Only single, read-only SELECT statements are permitted."

    try:
        if db_path_or_url.startswith(("postgres://", "postgresql://")):
            return _query_postgres(db_path_or_url, sql_query, limit)
        else:
            return _query_sqlite(db_path_or_url, sql_query, limit)
    except Exception as e:
        return f"Database Error: {str(e)}"


def _query_sqlite(db_path: str, sql_query: str, limit: int) -> str:
    if not os.path.exists(db_path):
        return f"Error: SQLite database not found at {db_path}"

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute(sql_query)
        rows = cur.fetchmany(limit)
        results = [dict(row) for row in rows]
        return str(results) if results else "Query returned no rows."
    finally:
        conn.close()


def _query_postgres(connection_string: str, sql_query: str, limit: int) -> str:
    import psycopg2

    conn = psycopg2.connect(connection_string)
    try:
        cur = conn.cursor()
        cur.execute(sql_query)
        columns = [desc[0] for desc in cur.description]
        rows = cur.fetchmany(limit)
        results = [dict(zip(columns, row)) for row in rows]
        return str(results) if results else "Query returned no rows."
    finally:
        conn.close()


if __name__ == "__main__":
    mcp.run()