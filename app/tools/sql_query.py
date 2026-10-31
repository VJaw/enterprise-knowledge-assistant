"""`sql_query`: read-only analytics over the SQLite copy (§B9.3, §B17).

Checks, in order: sqlglot parses exactly one statement; it is a query (SELECT / UNION / ... , CTEs allowed)
with no write, DDL, PRAGMA, ATTACH or other command anywhere in the tree; every table it reads is on the
allow-list (CTE names excepted). It then runs on `acme_ro.db` opened with `?mode=ro`, aborts after 5 s,
returns at most 200 rows, and masks email / phone values."""
from __future__ import annotations

import asyncio
import hashlib
import sqlite3
import time
from datetime import date
from pathlib import Path

import sqlglot
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field
from sqlglot import exp

from app.core.types import RetrievedChunk
from app.tools.common import EMAIL, error, guarded, mask_contacts

ALLOWED_TABLES = {"teams", "employees", "systems", "products", "tickets", "transactions", "documents_meta"}
MAX_ROWS = 200
SQL_TIMEOUT_S = 5.0
CONTACT_COLUMNS = ("email", "phone")
FORBIDDEN = (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter, exp.Command,
             exp.Pragma, exp.Attach, exp.Detach, exp.Transaction, exp.Commit, exp.Rollback, exp.Set, exp.Use,
             exp.Copy, exp.LoadData, exp.Into)
# File/extension access; Python's sqlite3 already disables extension loading, this is belt and braces.
DENIED_FUNCTIONS = {"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"}

SCHEMA = """Tables (SQLite):
- teams(team_id, name, domain, manager_emp_id -> employees)
- employees(emp_id, name, email, team_id -> teams, role, location)
- systems(system_id, name, tier, owner_team_id -> teams, status: active|deprecated)
- products(product_id, name, category, price, system_id -> systems)
- tickets(ticket_id, product_id -> products, priority: SEV1|SEV2|SEV3|SEV4, status: \
open|in_progress|resolved|closed, opened_at, closed_at, assignee_emp_id -> employees)
- transactions(txn_id, product_id -> products, amount, currency: USD|EUR|GBP|INR, region: \
NA|EMEA|APAC|LATAM, created_at)
- documents_meta(doc_id, title, doc_type, domain, owner_team_id -> teams, version, effective_date, \
sensitivity)
tickets and transactions have NO system_id: reach a system with tickets JOIN products USING (product_id) \
JOIN systems ON systems.system_id = products.system_id, and select systems.name.
Timestamps are ISO text ('2025-03-14 09:30:00'); compare them as strings."""


class SQLArgs(BaseModel):
    sql: str = Field(description="One SQLite SELECT statement")


def check_sql(sql: str) -> str | None:
    """None if the statement may run, else the reason it is rejected."""
    try:
        statements = [s for s in sqlglot.parse(sql, read="sqlite") if s is not None]
    except sqlglot.errors.ParseError as e:
        return f"could not parse SQL: {str(e).splitlines()[0]}"
    if len(statements) != 1:
        return f"exactly one statement is allowed, got {len(statements)}"
    tree = statements[0]
    if not isinstance(tree, exp.Query):
        return f"only SELECT queries are allowed, got {tree.key.upper()}"
    if bad := next(tree.find_all(*FORBIDDEN), None):
        return f"only SELECT queries are allowed ({bad.key.upper()} found)"
    funcs = {(f.name if isinstance(f, exp.Anonymous) else f.sql_name()).lower()
             for f in tree.find_all(exp.Func)}
    if denied := sorted(funcs & DENIED_FUNCTIONS):
        return f"function(s) not allowed: {', '.join(denied)}"
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    tables = {t.name.lower() for t in tree.find_all(exp.Table) if t.name} - ctes
    if denied := sorted(tables - ALLOWED_TABLES):
        return f"table(s) not allowed: {', '.join(denied)}; allowed: {', '.join(sorted(ALLOWED_TABLES))}"
    return None


def data_snapshot(db_path: Path) -> date | None:
    """The day after the newest ticket/transaction, i.e. when this data was 'pulled'."""
    try:
        con = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
        try:
            newest = con.execute("SELECT max(m) FROM (SELECT max(opened_at) AS m FROM tickets "
                                 "UNION ALL SELECT max(created_at) FROM transactions)").fetchone()[0]
        finally:
            con.close()
    except sqlite3.Error:
        return None
    d = date.fromisoformat(newest[:10])
    return date(d.year + d.month // 12, d.month % 12 + 1, 1)


def run_select(db_path: Path, sql: str,
               timeout: float = SQL_TIMEOUT_S) -> tuple[list[str], list[tuple], bool]:
    con = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    try:
        deadline = time.monotonic() + timeout
        con.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)  # non-zero aborts
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description or []]
        rows = cur.fetchmany(MAX_ROWS + 1)
    finally:
        con.close()
    return cols, rows[:MAX_ROWS], len(rows) > MAX_ROWS


def render(cols: list[str], rows: list[tuple], truncated: bool) -> str:
    masked = {i for i, c in enumerate(cols) if any(k in c.lower() for k in CONTACT_COLUMNS)}

    def cell(i: int, v) -> str:
        if v is None:
            return "NULL"
        if i in masked:
            return "<EMAIL>" if EMAIL.search(str(v)) else "<PHONE>"
        return mask_contacts(str(v))

    lines = [" | ".join(cols)] + [" | ".join(cell(i, v) for i, v in enumerate(r)) for r in rows]
    cut = f", truncated to {MAX_ROWS}" if truncated else ""
    note = f"({len(rows)} row{'s' if len(rows) != 1 else ''}{cut})"
    return "\n".join([*lines, note])


def make_sql_query(db_path: str | Path) -> StructuredTool:
    db = Path(db_path)
    snapshot = data_snapshot(db)
    when = (f"\nThe data is a snapshot taken on {snapshot.isoformat()} (nothing later exists). Resolve "
            f"relative periods such as 'last quarter' or 'this year' against that date, not today's date, "
            f"and say which date range you used." if snapshot else "")

    @guarded()
    async def sql_query(sql: str):
        if reason := check_sql(sql):
            return error(f"query rejected: {reason}")
        try:
            cols, rows, truncated = await asyncio.to_thread(run_select, db, sql)
        except sqlite3.OperationalError as e:
            if "interrupted" in str(e):
                return error(f"query exceeded {SQL_TIMEOUT_S:g} s")
            # The schema in the error lets the agent fix the query in one retry instead of exploring it
            return error(f"SQLite: {e}. Fix the query against this schema and retry.\n{SCHEMA}")
        text = render(cols, rows, truncated)
        chunk = RetrievedChunk(chunk_id=f"sql:{hashlib.sha1(sql.encode()).hexdigest()[:12]}", doc_id="sql",
                               text=text, score=1.0, source_type="sql",
                               metadata={"query": " ".join(sql.split()), "rows": len(rows),
                                         "truncated": truncated})
        return f"(sql: {chunk.metadata['query']})\n{text}", [chunk]

    description = (f"Run one read-only SQL SELECT over Acme's operational database for counts, aggregates, "
                   f"rankings and lists (tickets, transactions, employees, teams, systems, products). At "
                   f"most {MAX_ROWS} rows; email and phone values are masked.\n{SCHEMA}{when}")
    return StructuredTool.from_function(coroutine=sql_query, name="sql_query", description=description,
                                        args_schema=SQLArgs, response_format="content_and_artifact")
