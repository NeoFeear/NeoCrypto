from decimal import Decimal

import pytest

from db.migrate import init_db
from engine.fifo_engine import FifoEngine
from live_engine import run_polling_loop
from market_data.provider import MarketDataProvider
from market_data.types import Kline


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
