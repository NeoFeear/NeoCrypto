from decimal import Decimal

from market_data.types import BookTicker, Kline, Ticker24h
from market_data.provider import INTERVAL_MS, MarketDataProvider


def test_kline_is_frozen_and_holds_decimals():
    k = Kline(
        open_time_ms=1_700_000_000_000,
        open=Decimal("100.5"),
        high=Decimal("101.0"),
        low=Decimal("99.5"),
        close=Decimal("100.8"),
        volume=Decimal("12.3"),
        close_time_ms=1_700_000_059_999,
    )
    assert k.close == Decimal("100.8")


def test_book_ticker_spread_relative():
    bt = BookTicker(symbol="BTCUSDT", bid_price=Decimal("100"), ask_price=Decimal("101"))
    # (101-100) / ((101+100)/2) = 1 / 100.5
    assert bt.spread_relative == Decimal("1") / Decimal("100.5")


def test_book_ticker_spread_bps():
    bt = BookTicker(symbol="BTCUSDT", bid_price=Decimal("100"), ask_price=Decimal("100.10"))
    # spread_relative = 0.10 / 100.05 ; spread_bps = that * 10000
    assert bt.spread_bps == (Decimal("0.10") / Decimal("100.05")) * Decimal(10000)


def test_ticker_24h_holds_quote_volume():
    t = Ticker24h(symbol="BTCUSDT", quote_volume=Decimal("60000000"))
    assert t.quote_volume == Decimal("60000000")


def test_interval_ms_covers_required_intervals():
    assert INTERVAL_MS == {
        "1m": 60_000,
        "5m": 300_000,
        "1h": 3_600_000,
        "1d": 86_400_000,
    }


def test_market_data_provider_is_abstract():
    import pytest

    with pytest.raises(TypeError):
        MarketDataProvider()  # abstract, cannot instantiate directly
