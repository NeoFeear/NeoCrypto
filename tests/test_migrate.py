import sqlite3
from pathlib import Path

from db.migrate import init_db


def test_init_db_creates_all_tables_and_enables_wal(tmp_path: Path):
    db_path = tmp_path / "test.db"

    conn = init_db(str(db_path))

    cursor = conn.execute("PRAGMA journal_mode")
    assert cursor.fetchone()[0].lower() == "wal"

    tables = {row["name"] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert {"trades", "lots", "portfolio_snapshots", "strategy_configs", "engine_state"} <= tables
    conn.close()


def test_init_db_is_idempotent(tmp_path: Path):
    db_path = tmp_path / "test.db"
    init_db(str(db_path)).close()
    conn = init_db(str(db_path))  # must not raise on re-init
    conn.execute("INSERT INTO engine_state (key, value) VALUES ('k', 'v')")
    conn.commit()
    conn.close()
