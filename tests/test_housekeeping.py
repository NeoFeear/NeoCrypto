from decimal import Decimal

import pytest

from db.migrate import init_db
from db.repository import insert_snapshot
from housekeeping import aggregate_old_snapshots
from models import PortfolioSnapshot

HOUR_MS = 3_600_000
DAY_MS = 86_400_000


@pytest.fixture
def conn():
    c = init_db(":memory:")
    yield c
    c.close()


def _snap(ts: int, total_value: str) -> PortfolioSnapshot:
    v = Decimal(total_value)
    return PortfolioSnapshot(
        timestamp=ts, symbol="BTCUSDT", cash_balance=v, position_value=Decimal("0"),
        total_value=v, unrealized_pnl=Decimal("0"), realized_pnl_cumule=Decimal("0"),
    )


def test_aggregates_old_snapshots_into_hourly_average(conn):
    now_ms = 40 * DAY_MS  # "today" is day 40
    old_bucket_start = 0  # hour 0 of day 0, well past the 30-day window
    # 3 five-minute snapshots inside the same hour bucket, all older than 30 days
    insert_snapshot(conn, _snap(old_bucket_start, "1000"))
    insert_snapshot(conn, _snap(old_bucket_start + 5 * 60_000, "1010"))
    insert_snapshot(conn, _snap(old_bucket_start + 10 * 60_000, "1020"))
    # 1 snapshot inside the 30-day retention window -- must NOT be touched
    recent_ts = now_ms - 1 * DAY_MS
    insert_snapshot(conn, _snap(recent_ts, "2000"))

    replaced = aggregate_old_snapshots(conn, now_ms=now_ms, retention_days=30)

    rows = conn.execute("SELECT * FROM portfolio_snapshots ORDER BY timestamp").fetchall()
    assert replaced == 3
    assert len(rows) == 2  # 3 old rows collapsed into 1, plus the 1 recent row untouched
    assert rows[0]["timestamp"] == old_bucket_start
    # average of 1000, 1010, 1020 = 1010 exactly
    assert Decimal(rows[0]["total_value"]) == Decimal("1010")
    assert Decimal(rows[1]["total_value"]) == Decimal("2000")
    assert rows[1]["timestamp"] == recent_ts


def test_aggregate_is_idempotent_second_run_does_nothing(conn):
    now_ms = 40 * DAY_MS
    insert_snapshot(conn, _snap(0, "1000"))
    insert_snapshot(conn, _snap(5 * 60_000, "1010"))

    first = aggregate_old_snapshots(conn, now_ms=now_ms, retention_days=30)
    second = aggregate_old_snapshots(conn, now_ms=now_ms, retention_days=30)

    assert first == 2
    assert second == 0  # already a single aggregated row per bucket, nothing left to collapse


def test_aggregate_no_old_snapshots_returns_zero(conn):
    now_ms = 40 * DAY_MS
    insert_snapshot(conn, _snap(now_ms - 1_000, "1000"))

    replaced = aggregate_old_snapshots(conn, now_ms=now_ms, retention_days=30)

    assert replaced == 0
    assert len(conn.execute("SELECT * FROM portfolio_snapshots").fetchall()) == 1
