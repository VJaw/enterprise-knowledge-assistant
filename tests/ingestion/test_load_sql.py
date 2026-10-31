"""CSV -> SQLite: row counts, foreign keys, and a read-only copy that rejects writes."""
import sqlite3
from pathlib import Path

import pytest

from ingestion.load_sql import build_sqlite, connect_ro

TABLES = Path("data/raw/tables")
pytestmark = pytest.mark.skipif(not (TABLES / "tickets.csv").exists(), reason="run `make data` first")


def test_build_sqlite_counts_and_read_only_copy(tmp_path):
    db = tmp_path / "acme.db"
    counts = build_sqlite(TABLES, db)
    assert counts["tickets"] == 300 and counts["employees"] == 40 and counts["documents_meta"] == 34

    ro = connect_ro(tmp_path / "acme_ro.db")
    assert ro.execute("SELECT count(*) FROM transactions").fetchone()[0] == 1000
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("DELETE FROM tickets")
    ro.close()


def test_every_system_has_an_owner_team_with_a_manager(tmp_path):
    db = tmp_path / "acme.db"
    build_sqlite(TABLES, db)
    con = sqlite3.connect(db)
    rows = con.execute("""SELECT s.name, t.name, e.name FROM systems s
                          JOIN teams t ON t.team_id = s.owner_team_id
                          JOIN employees e ON e.emp_id = t.manager_emp_id""").fetchall()
    con.close()
    assert len(rows) == 15
