import sqlite3
from decimal import Decimal

HOUR_MS = 3_600_000
DAY_MS = 86_400_000

_DECIMAL_COLUMNS = ["cash_balance", "position_value", "total_value", "unrealized_pnl", "realized_pnl_cumule"]


def aggregate_old_snapshots(conn: sqlite3.Connection, now_ms: int, retention_days: int) -> int:
    cutoff = now_ms - retention_days * DAY_MS
    rows = conn.execute(
        "SELECT * FROM portfolio_snapshots WHERE timestamp < ? ORDER BY symbol, timestamp",
        (cutoff,),
    ).fetchall()

    buckets: dict[tuple[str, int], list[sqlite3.Row]] = {}
    for row in rows:
        bucket_start = row["timestamp"] - (row["timestamp"] % HOUR_MS)
        buckets.setdefault((row["symbol"], bucket_start), []).append(row)

    replaced = 0
    for (symbol, bucket_start), bucket_rows in buckets.items():
        if len(bucket_rows) <= 1:
            continue  # already a single row for this bucket -- nothing to collapse
        ids = [r["id"] for r in bucket_rows]
        averages = {
            col: sum((Decimal(r[col]) for r in bucket_rows), Decimal("0")) / Decimal(len(bucket_rows))
            for col in _DECIMAL_COLUMNS
        }
        conn.execute(f"DELETE FROM portfolio_snapshots WHERE id IN ({','.join('?' * len(ids))})", ids)
        conn.execute(
            """
            INSERT INTO portfolio_snapshots
                (timestamp, symbol, cash_balance, position_value, total_value, unrealized_pnl, realized_pnl_cumule)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bucket_start, symbol, str(averages["cash_balance"]), str(averages["position_value"]),
                str(averages["total_value"]), str(averages["unrealized_pnl"]), str(averages["realized_pnl_cumule"]),
            ),
        )
        replaced += len(bucket_rows)

    conn.commit()
    return replaced
