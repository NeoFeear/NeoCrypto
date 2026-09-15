from decimal import Decimal

from market_data.pagination import fetch_klines_paginated
from market_data.provider import MarketDataProvider
from market_data.types import BookTicker, Kline, Ticker24h


def _kline(open_time_ms: int) -> Kline:
    return Kline(
        open_time_ms=open_time_ms,
        open=Decimal("1"),
        high=Decimal("1"),
        low=Decimal("1"),
        close=Decimal("1"),
        volume=Decimal("1"),
        close_time_ms=open_time_ms + 3_599_999,
    )


class FakeProvider(MarketDataProvider):
    """Returns pre-scripted pages, one per call, regardless of requested range."""

    def __init__(self, pages: list[list[Kline]]):
        self._pages = pages
        self.calls: list[tuple] = []

    def get_klines(self, symbol, interval, start_ms, end_ms, limit=1000):
        self.calls.append((symbol, interval, start_ms, end_ms, limit))
        if not self._pages:
            return []
        return self._pages.pop(0)

    def get_book_ticker(self, symbol):
        raise NotImplementedError

    def get_ticker_24h(self, symbol):
        raise NotImplementedError


def test_paginates_until_batch_is_empty(monkeypatch):
    sleeps = []
    monkeypatch.setattr("market_data.pagination.time.sleep", lambda s: sleeps.append(s))

    interval_ms = 3_600_000
    page1 = [_kline(0), _kline(interval_ms)]
    page2 = [_kline(interval_ms * 2)]
    provider = FakeProvider(pages=[page1, page2, []])

    result = fetch_klines_paginated(
        provider, "BTCUSDT", "1h", start_ms=0, end_ms=interval_ms * 10, sleep_seconds=0
    )

    assert [k.open_time_ms for k in result] == [0, interval_ms, interval_ms * 2]
    # 3rd call gets the empty page and stops the loop before sleeping
    assert len(provider.calls) == 3
    assert sleeps == [0, 0]


def test_cursor_advances_from_last_kline_plus_interval(monkeypatch):
    monkeypatch.setattr("market_data.pagination.time.sleep", lambda s: None)

    interval_ms = 3_600_000
    page1 = [_kline(0), _kline(interval_ms)]
    provider = FakeProvider(pages=[page1, []])

    fetch_klines_paginated(provider, "BTCUSDT", "1h", start_ms=0, end_ms=interval_ms * 10)

    # second call's start_ms must be last kline's open_time + interval_ms
    assert provider.calls[1][2] == interval_ms * 2


def test_stops_once_cursor_reaches_end_ms(monkeypatch):
    monkeypatch.setattr("market_data.pagination.time.sleep", lambda s: None)

    interval_ms = 3_600_000
    page1 = [_kline(0)]
    provider = FakeProvider(pages=[page1])

    result = fetch_klines_paginated(provider, "BTCUSDT", "1h", start_ms=0, end_ms=interval_ms)

    assert [k.open_time_ms for k in result] == [0]
    assert len(provider.calls) == 1
