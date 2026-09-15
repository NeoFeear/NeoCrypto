from decimal import Decimal

from config import LiquidityConfig
from liquidity import passes_liquidity_filter
from market_data.types import BookTicker, Ticker24h


def _config(min_volume="50000000", max_spread_bps="10") -> LiquidityConfig:
    return LiquidityConfig(
        min_quote_volume_24h=Decimal(min_volume),
        max_spread_bps=Decimal(max_spread_bps),
    )


def test_passes_when_volume_high_and_spread_tight():
    ticker = Ticker24h(symbol="BTCUSDT", quote_volume=Decimal("60000000"))
    book = BookTicker(symbol="BTCUSDT", bid_price=Decimal("100"), ask_price=Decimal("100.05"))
    ok, reason = passes_liquidity_filter(ticker, book, _config())
    assert ok is True
    assert reason is None


def test_rejects_when_volume_at_or_below_threshold():
    ticker = Ticker24h(symbol="BTCUSDT", quote_volume=Decimal("50000000"))
    book = BookTicker(symbol="BTCUSDT", bid_price=Decimal("100"), ask_price=Decimal("100.05"))
    ok, reason = passes_liquidity_filter(ticker, book, _config())
    assert ok is False
    assert reason == "volume insuffisant"


def test_rejects_when_spread_at_or_above_threshold():
    ticker = Ticker24h(symbol="BTCUSDT", quote_volume=Decimal("60000000"))
    # bid=100, ask=101 -> spread_relative = 1/100.5 -> spread_bps = 10000/100.5 = ~99.5 bps, well over 10
    book = BookTicker(symbol="BTCUSDT", bid_price=Decimal("100"), ask_price=Decimal("101"))
    ok, reason = passes_liquidity_filter(ticker, book, _config())
    assert ok is False
    assert reason == "spread trop large"


def test_volume_check_takes_priority_when_both_fail():
    ticker = Ticker24h(symbol="BTCUSDT", quote_volume=Decimal("1000"))
    book = BookTicker(symbol="BTCUSDT", bid_price=Decimal("100"), ask_price=Decimal("101"))
    ok, reason = passes_liquidity_filter(ticker, book, _config())
    assert ok is False
    assert reason == "volume insuffisant"
