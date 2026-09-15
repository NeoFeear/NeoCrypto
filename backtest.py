import logging
from decimal import Decimal

from market_data.pagination import fetch_klines_paginated
from market_data.provider import INTERVAL_MS, MarketDataProvider
from market_data.types import Kline
from config import LiquidityConfig
from liquidity import passes_liquidity_filter

logger = logging.getLogger(__name__)

# Below this fraction of the expected candle count, log a coverage warning
# rather than silently trusting a truncated window (Plan 1 final review).
MIN_COVERAGE_RATIO = Decimal("0.95")


def select_backtest_symbols(
    provider: MarketDataProvider, watchlist: list[str], liquidity_cfg: LiquidityConfig
) -> tuple[list[str], list[tuple[str, str]]]:
    passing: list[str] = []
    excluded: list[tuple[str, str]] = []
    for symbol in watchlist:
        ticker = provider.get_ticker_24h(symbol)
        book = provider.get_book_ticker(symbol)
        ok, reason = passes_liquidity_filter(ticker, book, liquidity_cfg)
        if ok:
            passing.append(symbol)
        else:
            excluded.append((symbol, reason))
    return passing, excluded


def download_backtest_klines(
    provider: MarketDataProvider, symbol: str, interval: str, lookback_days: int, now_ms: int
) -> list[Kline]:
    interval_ms = INTERVAL_MS[interval]
    end_ms = now_ms
    start_ms = end_ms - lookback_days * 86_400_000
    klines = fetch_klines_paginated(provider, symbol, interval, start_ms, end_ms)

    expected_count = (end_ms - start_ms) // interval_ms
    if expected_count > 0 and Decimal(len(klines)) / Decimal(expected_count) < MIN_COVERAGE_RATIO:
        logger.warning(
            "couverture insuffisante pour %s: %d/%d bougies attendues (fenetre possiblement tronquee)",
            symbol, len(klines), expected_count,
        )
    return klines
