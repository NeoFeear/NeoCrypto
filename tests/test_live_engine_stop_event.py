import threading
from decimal import Decimal

from db.migrate import init_db
from discord_notifier import DiscordWebhooks
from engine.fifo_engine import FifoEngine
from live_engine import run_polling_loop
from market_data.provider import MarketDataProvider
from market_data.types import Kline


class InfiniteProvider(MarketDataProvider):
    """Always serves a new (later) closed candle -- without max_cycles or a
    stop_event, run_polling_loop would never return."""

    def __init__(self):
        self._n = 0

    def get_klines(self, symbol, interval, start_ms, end_ms, limit=1000):
        self._n += 1
        c = Decimal("100")
        return [Kline(open_time_ms=self._n * 300_000, open=c, high=c, low=c, close=c,
                       volume=Decimal("1"), close_time_ms=self._n * 300_000 + 299_999)]

    def get_book_ticker(self, symbol):
        raise NotImplementedError

    def get_ticker_24h(self, symbol):
        raise NotImplementedError


def test_run_polling_loop_stops_promptly_once_stop_event_is_set(monkeypatch):
    conn = init_db(":memory:")
    stop_event = threading.Event()
    waits = []
    provider = InfiniteProvider()
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))

    # Set the event after the first cycle's wait call so the loop runs
    # exactly once more before returning.
    def fake_wait_then_stop(timeout):
        waits.append(timeout)
        stop_event.set()
        return True

    monkeypatch.setattr(stop_event, "wait", fake_wait_then_stop)

    run_polling_loop(
        conn, provider, engine, "BTCUSDT", "buy_hold", {"invest_at": "start"},
        poll_interval="5m", poll_interval_seconds=300,
        on_critical_failure=lambda msg: None, initial_cash=Decimal("1000"),
        discord_webhooks=DiscordWebhooks(daily_summary="", transactions="", alerts="", logs=""),
        drawdown_threshold_pct=Decimal("10"), stop_event=stop_event,
    )

    # buy_hold only ever buys once regardless of how many cycles ran.
    assert len(engine.trades) == 1
    assert len(waits) == 1
    conn.close()


def test_run_polling_loop_never_enters_loop_body_if_stop_event_already_set():
    conn = init_db(":memory:")
    stop_event = threading.Event()
    stop_event.set()
    provider = InfiniteProvider()
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))

    run_polling_loop(
        conn, provider, engine, "BTCUSDT", "buy_hold", {"invest_at": "start"},
        poll_interval="5m", poll_interval_seconds=300,
        on_critical_failure=lambda msg: None, initial_cash=Decimal("1000"),
        discord_webhooks=DiscordWebhooks(daily_summary="", transactions="", alerts="", logs=""),
        drawdown_threshold_pct=Decimal("10"), stop_event=stop_event,
    )

    assert engine.trades == []
    conn.close()
