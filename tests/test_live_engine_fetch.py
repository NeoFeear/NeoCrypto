import time
from decimal import Decimal

from live_engine import _fetch_latest_closed_kline
from market_data.provider import MarketDataProvider
from market_data.types import Kline


class RecordingProvider(MarketDataProvider):
    """Records the exact (start_ms, end_ms, limit) it was called with and returns
    two canned klines built relative to a fixed "now" the test controls."""

    def __init__(self, klines: list[Kline]):
        self._klines = klines
        self.calls: list[tuple[int, int, int]] = []

    def get_klines(self, symbol, interval, start_ms, end_ms, limit=1000):
        self.calls.append((start_ms, end_ms, limit))
        return self._klines

    def get_book_ticker(self, symbol):
        raise NotImplementedError

    def get_ticker_24h(self, symbol):
        raise NotImplementedError


def _kline(close_time_ms: int) -> Kline:
    return Kline(
        open_time_ms=close_time_ms - 299_999, open=Decimal("100"), high=Decimal("100"),
        low=Decimal("100"), close=Decimal("100"), volume=Decimal("1"), close_time_ms=close_time_ms,
    )


def test_fetch_latest_closed_kline_uses_real_window_and_keeps_only_closed_candle():
    now_ms = int(time.time() * 1000)
    closed_kline = _kline(now_ms - 5 * 60_000)   # safely in the past: closed
    forming_kline = _kline(now_ms + 5 * 60_000)  # safely in the future: still forming
    provider = RecordingProvider([closed_kline, forming_kline])

    result = _fetch_latest_closed_kline(provider, "BTCUSDT", "5m")

    assert len(provider.calls) == 1
    start_ms, end_ms, limit = provider.calls[0]
    assert start_ms > 0 and end_ms > 0
    assert limit == 2
    assert result == [closed_kline]
