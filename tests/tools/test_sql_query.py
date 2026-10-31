"""`sql_query` (§B9.3, §B17): SELECT only, one statement, table allow-list, read-only file, row cap, masking.
"""
import sqlite3
from pathlib import Path

import pytest

from app.tools.sql_query import MAX_ROWS, check_sql, make_sql_query, render, run_select

RO_DB = Path("data/acme_ro.db")
pytestmark = pytest.mark.skipif(not RO_DB.exists(), reason="run `make ingest` first")


@pytest.fixture(scope="module")
def tool():
    return make_sql_query(RO_DB)


async def call(tool, sql):
    msg = await tool.ainvoke({"name": "sql_query", "args": {"sql": sql}, "id": "1", "type": "tool_call"})
    return msg.content, msg.artifact


async def test_drop_table_is_rejected(tool):
    content, artifact = await call(tool, "DROP TABLE tickets")
    assert content == "error: query rejected: only SELECT queries are allowed, got DROP" and artifact == []


@pytest.mark.parametrize(("sql", "reason"), [
    ("SELECT 1; DROP TABLE tickets", "exactly one statement"),
    ("DELETE FROM tickets", "got DELETE"),
    ("UPDATE teams SET name = 'x'", "got UPDATE"),
    ("INSERT INTO teams VALUES ('T', 'x', 'y', NULL)", "got INSERT"),
    ("PRAGMA table_info(tickets)", "got PRAGMA"),
    ("ATTACH DATABASE 'data/acme.db' AS rw", "got ATTACH"),
    ("WITH x AS (DELETE FROM tickets RETURNING *) SELECT * FROM x", "DELETE found"),
    ("SELECT * FROM sqlite_master", "not allowed: sqlite_master"),
    ("SELECT load_extension('evil')", "function(s) not allowed: load_extension"),
    ("SELEC count(*) FROM tickets", "could not parse SQL"),
])
def test_unsafe_statements_are_rejected(sql, reason):
    assert reason in (check_sql(sql) or "")


def test_ctes_unions_and_joins_are_allowed():
    sev1 = "WITH s AS (SELECT * FROM tickets WHERE priority = 'SEV1') SELECT count(*) FROM s"
    assert check_sql(sev1) is None
    assert check_sql("SELECT name FROM teams UNION SELECT name FROM systems") is None


def test_the_file_is_read_only_even_without_the_parser_check():
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        run_select(RO_DB, "DELETE FROM tickets")


def test_slow_queries_are_interrupted():
    with pytest.raises(sqlite3.OperationalError, match="interrupted"):
        run_select(RO_DB, "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) "
                          "SELECT count(*) FROM c", timeout=0.2)


async def test_select_returns_a_citable_result(tool):
    content, artifact = await call(tool, "SELECT count(*) AS n FROM tickets WHERE priority = 'SEV1'")
    assert content.endswith("n\n17\n(1 row)")
    [chunk] = artifact
    assert chunk.source_type == "sql" and chunk.text == "n\n17\n(1 row)"
    assert chunk.metadata["query"] == "SELECT count(*) AS n FROM tickets WHERE priority = 'SEV1'"


async def test_rows_are_capped(tool):
    content, artifact = await call(tool, "SELECT txn_id FROM transactions")
    assert artifact[0].metadata == {**artifact[0].metadata, "rows": MAX_ROWS, "truncated": True}
    assert content.endswith(f"({MAX_ROWS} rows, truncated to {MAX_ROWS})")


async def test_emails_and_phones_are_masked(tool):
    content, _ = await call(tool, "SELECT name, email, email AS contact FROM employees LIMIT 3")
    assert "@" not in content and content.count("<EMAIL>") == 6
    assert render(["note"], [("call 415-555-0134 now",)], False).splitlines()[1] == "call <PHONE> now"


def test_description_has_the_schema_and_the_data_snapshot(tool):
    assert "tickets(ticket_id, product_id -> products, priority: SEV1|SEV2|SEV3|SEV4" in tool.description
    assert "snapshot taken on 2026-01-01" in tool.description


async def test_sql_errors_carry_the_schema(tool):
    content, artifact = await call(tool, "SELECT system_id FROM tickets")
    assert content.startswith("error: SQLite: no such column: system_id. Fix the query")
    assert "tickets and transactions have NO system_id" in content and artifact == []
