"""CSV tables -> SQLite (§B5.4), plus a read-only copy for the agent and the MCP sqlite server."""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE teams (
    team_id TEXT PRIMARY KEY, name TEXT NOT NULL, domain TEXT NOT NULL,
    manager_emp_id TEXT REFERENCES employees(emp_id));
CREATE TABLE employees (
    emp_id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL,
    team_id TEXT NOT NULL REFERENCES teams(team_id), role TEXT, location TEXT);
CREATE TABLE systems (
    system_id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, tier INTEGER NOT NULL,
    owner_team_id TEXT NOT NULL REFERENCES teams(team_id), status TEXT);
CREATE TABLE products (
    product_id TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT, price REAL,
    system_id TEXT NOT NULL REFERENCES systems(system_id));
CREATE TABLE tickets (
    ticket_id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES products(product_id),
    priority TEXT NOT NULL, status TEXT NOT NULL, opened_at TEXT NOT NULL, closed_at TEXT,
    assignee_emp_id TEXT REFERENCES employees(emp_id));
CREATE TABLE transactions (
    txn_id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES products(product_id),
    amount REAL NOT NULL, currency TEXT NOT NULL, region TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE documents_meta (
    doc_id TEXT PRIMARY KEY, title TEXT NOT NULL, doc_type TEXT NOT NULL, domain TEXT NOT NULL,
    owner_team_id TEXT REFERENCES teams(team_id), version TEXT, effective_date TEXT,
    sensitivity TEXT NOT NULL);
"""
TABLES = ["teams", "employees", "systems", "products", "tickets", "transactions", "documents_meta"]
INTEGER_COLS = {("systems", "tier")}
REAL_COLS = {("products", "price"), ("transactions", "amount")}


def _value(table: str, col: str, v: str):
    if v == "":
        return None
    if (table, col) in INTEGER_COLS:
        return int(v)
    if (table, col) in REAL_COLS:
        return float(v)
    return v


def build_sqlite(tables_dir: Path, db_path: Path) -> dict[str, int]:
    """(Re)create db_path from the CSVs and return row counts. Also writes <name>_ro.db next to it."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(".tmp.db")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    try:
        con.executescript(SCHEMA)
        counts = {}
        for table in TABLES:
            with open(tables_dir / f"{table}.csv", newline="") as f:
                rows = list(csv.DictReader(f))
            cols = list(rows[0])
            con.executemany(
                f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                [tuple(_value(table, c, r[c]) for c in cols) for r in rows])
            counts[table] = len(rows)
        con.commit()
        problems = con.execute("PRAGMA foreign_key_check").fetchall()
        if problems:
            raise ValueError(f"foreign key violations: {problems[:5]}")
    finally:
        con.close()
    tmp.replace(db_path)

    ro_path = db_path.with_name(f"{db_path.stem}_ro{db_path.suffix}")
    ro_path.unlink(missing_ok=True)
    src, dst = sqlite3.connect(db_path), sqlite3.connect(ro_path)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    return counts


def connect_ro(db_path: Path) -> sqlite3.Connection:
    """Read-only connection (URI mode=ro), as the agent's sql_query tool will use."""
    return sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)


if __name__ == "__main__":
    from app.config import get_settings

    db = Path(get_settings().sqlite_path)
    print(build_sqlite(Path("data/raw/tables"), db))
