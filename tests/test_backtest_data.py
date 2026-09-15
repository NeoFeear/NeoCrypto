from decimal import Decimal

from backtest import download_backtest_klines, select_backtest_symbols
from config import LiquidityConfig
from market_data.provider import MarketDataProvider
from market_data.types import BookTicker, Kline, Ticker24h


class FakeProvider(MarketDataProvider):
    def __init__(self, tickers: dict, books: dict, klines_by_symbol: dict | None = None):
        self._tickers = tickers
        self._books = books
        self._klines_by_symbol = klines_by_symbol or {}
        self.kline_calls: list[tuple] = []
        self._call_count_by_symbol: dict[str, int] = {}

    def get_klines(self, symbol, interval, start_ms, end_ms, limit=1000):
        self.kline_calls.append((symbol, interval, start_ms, end_ms, limit))
        # Return klines on first call, empty on subsequent calls (simulates provider behavior where
        # all data is returned in one batch, typical for test data)
        self._call_count_by_symbol[symbol] = self._call_count_by_symbol.get(symbol, 0) + 1
        if self._call_count_by_symbol[symbol] == 1:
            return self._klines_by_symbol.get(symbol, [])
        return []

    def get_book_ticker(self, symbol):
        return self._books[symbol]

    def get_ticker_24h(self, symbol):
        return self._tickers[symbol]


def _liquidity_config() -> LiquidityConfig:
    return LiquidityConfig(min_quote_volume_24h=Decimal("50000000"), max_spread_bps=Decimal("10"))


def test_select_backtest_symbols_partitions_pass_and_fail():
    provider = FakeProvider(
        tickers={
            "BTCUSDT": Ticker24h(symbol="BTCUSDT", quote_volume=Decimal("60000000")),
            "ETHUSDT": Ticker24h(symbol="ETHUSDT", quote_volume=Decimal("1000")),
            "SOLUSDT": Ticker24h(symbol="SOLUSDT", quote_volume=Decimal("60000000")),
        },
        books={
            "BTCUSDT": BookTicker(symbol="BTCUSDT", bid_price=Decimal("100"), ask_price=Decimal("100.05")),
            "ETHUSDT": BookTicker(symbol="ETHUSDT", bid_price=Decimal("100"), ask_price=Decimal("100.05")),
            "SOLUSDT": BookTicker(symbol="SOLUSDT", bid_price=Decimal("100"), ask_price=Decimal("101")),
        },
    )

    passing, excluded = select_backtest_symbols(
        provider, ["BTCUSDT", "ETHUSDT", "SOLUSDT"], _liquidity_config()
    )

    assert passing == ["BTCUSDT"]
    assert excluded == [("ETHUSDT", "volume insuffisant"), ("SOLUSDT", "spread trop large")]


def test_download_backtest_klines_computes_start_end_from_lookback_days():
    now_ms = 1_800_000_000_000
    start_ms = now_ms - 90 * 86_400_000
    # Create a kline at the start of the window to test proper time range computation
    fake_kline = Kline(open_time_ms=start_ms, open=Decimal("1"), high=Decimal("1"),
                        low=Decimal("1"), close=Decimal("1"), volume=Decimal("1"),
                        close_time_ms=start_ms + 3_599_999)
    provider = FakeProvider(tickers={}, books={}, klines_by_symbol={"BTCUSDT": [fake_kline]})

    result = download_backtest_klines(provider, "BTCUSDT", "1h", lookback_days=90, now_ms=now_ms)

    assert result == [fake_kline]
    # First call should request the correct start_ms and end_ms
    assert len(provider.kline_calls) >= 1
    symbol, interval, call_start_ms, end_ms_call, limit = provider.kline_calls[0]
    assert symbol == "BTCUSDT"
    assert interval == "1h"
    assert end_ms_call == now_ms
    assert call_start_ms == start_ms


def test_download_backtest_klines_warns_on_short_coverage(caplog):
    import logging
    # 90 days of 1h candles = 2160 expected; provider returns far fewer (a
    # truncated/short window, e.g. Kraken's OHLC candle cap) with no error.
    short_run = [
        Kline(open_time_ms=i * 3_600_000, open=Decimal("1"), high=Decimal("1"),
              low=Decimal("1"), close=Decimal("1"), volume=Decimal("1"),
              close_time_ms=i * 3_600_000 + 3_599_999)
        for i in range(500)
    ]
    provider = FakeProvider(tickers={}, books={}, klines_by_symbol={"BTCUSDT": short_run})

    with caplog.at_level(logging.WARNING):
        result = download_backtest_klines(provider, "BTCUSDT", "1h", lookback_days=90, now_ms=1_800_000_000_000)

    assert len(result) == 500
    assert any("couverture" in record.getMessage() for record in caplog.records)


def test_download_backtest_klines_no_warning_on_full_coverage(caplog):
    import logging
    full_run = [
        Kline(open_time_ms=i * 3_600_000, open=Decimal("1"), high=Decimal("1"),
              low=Decimal("1"), close=Decimal("1"), volume=Decimal("1"),
              close_time_ms=i * 3_600_000 + 3_599_999)
        for i in range(2160)
    ]
    provider = FakeProvider(tickers={}, books={}, klines_by_symbol={"BTCUSDT": full_run})

    with caplog.at_level(logging.WARNING):
        download_backtest_klines(provider, "BTCUSDT", "1h", lookback_days=90, now_ms=1_800_000_000_000)

    assert not any("couverture" in record.getMessage() for record in caplog.records)
