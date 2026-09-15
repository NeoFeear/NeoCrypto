import logging
import csv
import time
from decimal import Decimal
from pathlib import Path

from market_data.pagination import fetch_klines_paginated
from market_data.provider import INTERVAL_MS, MarketDataProvider
from market_data.types import Kline
from market_data.factory import build_provider
from config import LiquidityConfig, load_config
from liquidity import passes_liquidity_filter
from engine.fifo_engine import FifoEngine, Side
from engine.strategies.buy_hold import run_buy_hold
from engine.strategies.dca import run_dca
from engine.strategies.grid import run_grid
from models import PortfolioSnapshot
import analytics

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


def build_raw_metrics_row(
    symbol: str, strategy_type: str, engine: FifoEngine, snapshots: list[PortfolioSnapshot],
    initial_capital: Decimal,
) -> dict:
    final_value = snapshots[-1].total_value if snapshots else initial_capital
    stats = analytics.trade_stats(engine.trades)
    sells = [t.realized_pnl for t in engine.trades if t.side == Side.SELL and t.realized_pnl is not None]
    wins = [p for p in sells if p > 0]
    losses = [p for p in sells if p < 0]
    max_dd, _ = analytics.max_drawdown(snapshots)
    total_fees = sum((t.fee_amount for t in engine.trades), Decimal("0"))

    return {
        "symbol": symbol,
        "strategy": strategy_type,
        "return_pct": analytics.total_return_pct(initial_capital, final_value),
        "nb_trades": len(engine.trades),
        "win_rate_pct": stats["win_rate"] * Decimal(100),
        "avg_win": stats["avg_win"],
        "avg_loss": stats["avg_loss"],
        "biggest_win": max(wins) if wins else Decimal("0"),
        "biggest_loss": min(losses) if losses else Decimal("0"),
        "max_drawdown_pct": max_dd,
        "total_fees": total_fees,
    }


def build_analytics_row(
    symbol: str, strategy_type: str, engine: FifoEngine, snapshots: list[PortfolioSnapshot],
    initial_capital: Decimal, periods_per_year: int, buy_hold_return_pct: Decimal,
) -> dict:
    final_value = snapshots[-1].total_value if snapshots else initial_capital
    days = max(1, (snapshots[-1].timestamp - snapshots[0].timestamp) // 86_400_000) if snapshots else 1
    total_return = analytics.total_return_pct(initial_capital, final_value)
    cagr = analytics.cagr_pct(initial_capital, final_value, days)
    max_dd, recovery_days = analytics.max_drawdown(snapshots)

    return {
        "symbol": symbol,
        "strategy": strategy_type,
        "total_return_pct": total_return,
        "cagr_pct": cagr,
        "sharpe_ratio": analytics.sharpe_ratio(snapshots, periods_per_year),
        "sortino_ratio": analytics.sortino_ratio(snapshots, periods_per_year),
        "calmar_ratio": analytics.calmar_ratio(cagr, max_dd),
        "max_drawdown_pct": max_dd,
        "recovery_days": recovery_days,
        "profit_factor": analytics.profit_factor(engine.trades),
        "expectancy": analytics.expectancy(engine.trades),
        "exposure_time_pct": analytics.exposure_time_pct(snapshots),
        "alpha_vs_buy_hold_pct": analytics.alpha_vs_buy_hold(total_return, buy_hold_return_pct),
    }


def write_csv(rows: list[dict], path: str) -> None:
    if not rows:
        Path(path).write_text("", encoding="utf-8")
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def print_console_table(rows: list[dict]) -> None:
    if not rows:
        print("Aucun resultat.")
        return
    headers = list(rows[0].keys())
    print(" | ".join(headers))
    for row in rows:
        print(" | ".join(str(row[h]) for h in headers))


def main() -> None:
    cfg = load_config()
    provider = build_provider(cfg.data_source)

    passing, excluded = select_backtest_symbols(provider, cfg.watchlist, cfg.liquidity)
    for symbol, reason in excluded:
        logger.warning("Symbole exclu du backtest: %s (%s)", symbol, reason)

    interval_hours = INTERVAL_MS[cfg.backtest.interval] // 3_600_000
    periods_per_year = (365 * 24 * 3_600_000) // INTERVAL_MS[cfg.backtest.interval]
    now_ms = int(time.time() * 1000)

    raw_rows: list[dict] = []
    analytics_rows: list[dict] = []

    strategy_params = {
        "buy_hold": {"invest_at": "start"},
        "dca": {"amount_per_buy": 50, "frequency_hours": 24, "reference_price": "close"},
        "grid": {"lower_bound": 25000, "upper_bound": 35000, "n_levels": 10,
                 "spacing": "geometric", "order_size_quote": 100},
    }

    for symbol in passing:
        klines = download_backtest_klines(
            provider, symbol, cfg.backtest.interval, cfg.backtest.lookback_days, now_ms
        )
        if not klines:
            logger.warning("Aucune bougie recuperee pour %s, symbole ignore.", symbol)
            continue

        buy_hold_engine, buy_hold_snapshots = run_strategy(
            "buy_hold", klines, symbol, strategy_params["buy_hold"],
            cfg.backtest.initial_capital, cfg.fees.default_fee_pct, interval_hours,
        )
        buy_hold_return = analytics.total_return_pct(
            cfg.backtest.initial_capital, buy_hold_snapshots[-1].total_value
        )

        for strategy_type in ("buy_hold", "dca", "grid"):
            if strategy_type == "buy_hold":
                engine, snapshots = buy_hold_engine, buy_hold_snapshots
            else:
                engine, snapshots = run_strategy(
                    strategy_type, klines, symbol, strategy_params[strategy_type],
                    cfg.backtest.initial_capital, cfg.fees.default_fee_pct, interval_hours,
                )

            raw_rows.append(
                build_raw_metrics_row(symbol, strategy_type, engine, snapshots, cfg.backtest.initial_capital)
            )
            analytics_rows.append(
                build_analytics_row(
                    symbol, strategy_type, engine, snapshots, cfg.backtest.initial_capital,
                    periods_per_year, buy_hold_return,
                )
            )

    write_csv(raw_rows, "backtest_report.csv")
    write_csv(analytics_rows, "analytics_report.csv")
    print_console_table(raw_rows)


if __name__ == "__main__":
    main()
