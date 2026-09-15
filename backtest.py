import logging
from decimal import Decimal

from market_data.pagination import fetch_klines_paginated
from market_data.provider import INTERVAL_MS, MarketDataProvider
from market_data.types import Kline
from config import LiquidityConfig
from liquidity import passes_liquidity_filter
from engine.fifo_engine import FifoEngine
from engine.strategies.buy_hold import run_buy_hold
from engine.strategies.dca import run_dca
from engine.strategies.grid import run_grid
from models import PortfolioSnapshot

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


def run_strategy(
    strategy_type: str,
    klines: list[Kline],
    symbol: str,
    params: dict,
    initial_capital: Decimal,
    fee_pct: Decimal,
    interval_hours: int,
) -> tuple[FifoEngine, list[PortfolioSnapshot]]:
    engine = FifoEngine(initial_cash=initial_capital, fee_pct=fee_pct)
    if strategy_type == "buy_hold":
        snapshots = run_buy_hold(klines, engine, symbol, params)
    elif strategy_type == "dca":
        snapshots = run_dca(klines, engine, symbol, params, interval_hours)
    elif strategy_type == "grid":
        snapshots = run_grid(klines, engine, symbol, params)
    else:
        raise ValueError(f"strategie inconnue: {strategy_type}")
    return engine, snapshots
