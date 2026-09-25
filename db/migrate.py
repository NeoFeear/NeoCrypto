import sqlite3
from pathlib import Path

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def init_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    # Several live-engine worker threads (one per traded pair, see
    # live_engine.main) each hold their own connection to the same file.
    # WAL allows concurrent readers + one writer, but two writers landing in
    # the same instant still hit SQLITE_BUSY -- without a busy_timeout that
    # raises immediately instead of retrying briefly. 5s comfortably covers
    # one connection's short single-cycle transaction.
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
    return conn
