from decimal import Decimal

import pytest

from db.migrate import init_db
from db.repository import insert_snapshot
from engine.fifo_engine import FifoEngine
from housekeeping import DAY_MS
from live_engine import run_polling_loop
from market_data.provider import MarketDataProvider
from market_data.types import Kline
from models import PortfolioSnapshot


class SequenceProvider(MarketDataProvider):
    def __init__(self, klines: list[Kline]):
        self._klines = klines
        self._index = 0

    def get_klines(self, symbol, interval, start_ms, end_ms, limit=1000):
        if self._index >= len(self._klines):
            return [self._klines[-1]]  # keep serving the last one (simulates "no new candle yet")
        k = self._klines[self._index]
        self._index += 1
        return [k]

    def get_book_ticker(self, symbol):
        raise NotImplementedError

    def get_ticker_24h(self, symbol):
        raise NotImplementedError


class RaiseOnceProvider(MarketDataProvider):
    """Raises a plain RuntimeError (not httpx.HTTPError, so fetch_with_retry's
    except clause never catches it) on its first call, then serves a valid
    kline on every subsequent call."""

    def __init__(self, kline: Kline):
        self._kline = kline
        self.calls = 0

    def get_klines(self, symbol, interval, start_ms, end_ms, limit=1000):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("erreur API inattendue")
        return [self._kline]

    def get_book_ticker(self, symbol):
        raise NotImplementedError

    def get_ticker_24h(self, symbol):
        raise NotImplementedError


def _kline(open_time_ms: int, close: str) -> Kline:
    c = Decimal(close)
    return Kline(open_time_ms=open_time_ms, open=c, high=c, low=c, close=c,
                 volume=Decimal("1"), close_time_ms=open_time_ms + 299_999)


@pytest.fixture
def conn():
    c = init_db(":memory:")
    yield c
    c.close()


def test_run_polling_loop_stops_after_max_cycles_and_processes_new_candles(conn, monkeypatch):
    monkeypatch.setattr("live_engine.time.sleep", lambda s: None)
    provider = SequenceProvider([_kline(0, "100"), _kline(300_000, "110"), _kline(600_000, "120")])
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    params = {"invest_at": "start"}

    run_polling_loop(
        conn, provider, engine, "BTCUSDT", "buy_hold", params,
        poll_interval="5m", poll_interval_seconds=300,
        on_critical_failure=lambda msg: None, max_cycles=3,
    )

    # buy_hold only ever buys once, on the first genuinely-new candle it sees
    assert len(engine.trades) == 1
    assert len(conn.execute("SELECT * FROM portfolio_snapshots").fetchall()) == 3


def test_run_polling_loop_sleeps_between_cycles(conn, monkeypatch):
    sleeps = []
    monkeypatch.setattr("live_engine.time.sleep", lambda s: sleeps.append(s))
    provider = SequenceProvider([_kline(0, "100"), _kline(300_000, "100")])
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))

    run_polling_loop(
        conn, provider, engine, "BTCUSDT", "buy_hold", {"invest_at": "start"},
        poll_interval="5m", poll_interval_seconds=300,
        on_critical_failure=lambda msg: None, max_cycles=2,
    )

    # No sleep after the final cycle (mirrors Plan 1's pagination convention of no
    # trailing sleep once there's nothing left to do) -- with max_cycles=2 that's
    # exactly 1 sleep, between cycle 1 and cycle 2.
    assert sleeps == [300]


def test_run_polling_loop_survives_unhandled_exception_and_continues_next_cycle(conn, monkeypatch):
    monkeypatch.setattr("live_engine.time.sleep", lambda s: None)
    provider = RaiseOnceProvider(_kline(0, "100"))
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    failures = []

    run_polling_loop(
        conn, provider, engine, "BTCUSDT", "buy_hold", {"invest_at": "start"},
        poll_interval="5m", poll_interval_seconds=300,
        on_critical_failure=failures.append, max_cycles=2,
    )

    # First cycle's unhandled RuntimeError must not propagate out of the loop.
    assert len(failures) == 1
    # Second cycle's kline is still processed normally.
    assert len(engine.trades) == 1
    assert len(conn.execute("SELECT * FROM portfolio_snapshots").fetchall()) == 1


def test_run_polling_loop_runs_daily_housekeeping_and_aggregates_old_snapshots(conn, monkeypatch):
    now_ms = 40 * DAY_MS  # "today" is day 40
    monkeypatch.setattr("live_engine.time.time", lambda: now_ms / 1000)
    monkeypatch.setattr("live_engine.time.sleep", lambda s: None)

    # Two synthetic old snapshots for a DIFFERENT symbol than the one the cycle
    # itself trades, in the same hour bucket, well past a 30-day retention window
    # relative to the mocked "now" -- this way the cycle's own BTCUSDT snapshot
    # (whatever bucket it lands in) can never make the assertion ambiguous.
    old_bucket_start = 0
    insert_snapshot(conn, PortfolioSnapshot(
        timestamp=old_bucket_start, symbol="ETHUSDT", cash_balance=Decimal("1000"),
        position_value=Decimal("0"), total_value=Decimal("1000"),
        unrealized_pnl=Decimal("0"), realized_pnl_cumule=Decimal("0"),
    ))
    insert_snapshot(conn, PortfolioSnapshot(
        timestamp=old_bucket_start + 5 * 60_000, symbol="ETHUSDT", cash_balance=Decimal("1010"),
        position_value=Decimal("0"), total_value=Decimal("1010"),
        unrealized_pnl=Decimal("0"), realized_pnl_cumule=Decimal("0"),
    ))

    # A closed candle safely in the past relative to the mocked "now" so the
    # cycle actually runs and inserts its own (unrelated, BTCUSDT) snapshot too.
    provider = SequenceProvider([_kline(now_ms - 400_000, "100")])
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))

    run_polling_loop(
        conn, provider, engine, "BTCUSDT", "buy_hold", {"invest_at": "start"},
        poll_interval="5m", poll_interval_seconds=300,
        on_critical_failure=lambda msg: None, max_cycles=1, retention_days=30,
    )

    count = conn.execute(
        "SELECT COUNT(*) AS c FROM portfolio_snapshots WHERE symbol = 'ETHUSDT' AND timestamp = ?",
        (old_bucket_start,),
    ).fetchone()["c"]
    # Without the wiring, both old ETHUSDT rows would still be separate (count == 2).
    assert count == 1
