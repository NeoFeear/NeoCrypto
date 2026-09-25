import csv
from decimal import Decimal
from pathlib import Path

import backtest
from config import (
    BacktestConfig,
    Config,
    DashboardConfig,
    DiscordConfig,
    FeesConfig,
    LiquidityConfig,
    LiveConfig,
    LivePair,
    SnapshotsConfig,
)
from market_data.provider import MarketDataProvider
from market_data.types import BookTicker, Kline, Ticker24h


class FakeProvider(MarketDataProvider):
    """Mirrors tests/test_backtest_data.py's FakeProvider: returns the preset
    klines on the first get_klines call for a symbol, empty afterwards (so
    fetch_klines_paginated's pagination loop terminates after one real batch)."""

    def __init__(self, tickers: dict, books: dict, klines_by_symbol: dict):
        self._tickers = tickers
        self._books = books
        self._klines_by_symbol = klines_by_symbol
        self._call_count_by_symbol: dict[str, int] = {}

    def get_klines(self, symbol, interval, start_ms, end_ms, limit=1000):
        self._call_count_by_symbol[symbol] = self._call_count_by_symbol.get(symbol, 0) + 1
        if self._call_count_by_symbol[symbol] == 1:
            return self._klines_by_symbol.get(symbol, [])
        return []

    def get_book_ticker(self, symbol):
        return self._books[symbol]

    def get_ticker_24h(self, symbol):
        return self._tickers[symbol]


def _oscillating_klines(count: int) -> list[Kline]:
    """Real (non-flat) price movement: a triangle wave cycling between 40 and
    160 so the per-symbol grid band (Fix 1) spans a wide range and the extreme
    candle's own low/high guarantees at least one grid level touch."""
    cycle = [100, 130, 160, 130, 100, 70, 40, 70]
    klines = []
    for i in range(count):
        price = Decimal(cycle[i % len(cycle)])
        open_time_ms = i * 3_600_000
        klines.append(
            Kline(
                open_time_ms=open_time_ms,
                open=price,
                high=price + Decimal(5),
                low=price - Decimal(5),
                close=price,
                volume=Decimal("1000"),
                close_time_ms=open_time_ms + 3_599_999,
            )
        )
    return klines


def _fake_config() -> Config:
    return Config(
        data_source="binance",
        db_path="crypto_sim.db",
        watchlist=["BTCUSDT"],
        liquidity=LiquidityConfig(
            min_quote_volume_24h=Decimal("50000000"), max_spread_bps=Decimal("10")
        ),
        backtest=BacktestConfig(
            initial_capital=Decimal("1000"), interval="1h", lookback_days=2,
        ),
        fees=FeesConfig(default_fee_pct=Decimal("0.001")),
        live=LiveConfig(
            poll_interval_seconds=300, poll_kline_interval="5m",
            pairs=[LivePair(symbol="BTCUSDT", strategy="dca")], total_capital=Decimal("1000"),
        ),
        snapshots=SnapshotsConfig(retention_detail_days=30),
        discord=DiscordConfig(alert_drawdown_threshold_pct=Decimal("10")),
        dashboard=DashboardConfig(port=8303),
        strategy_defaults={
            "buy_hold": {"invest_at": "start"},
            "dca": {"amount_per_buy": 50, "frequency_hours": 24, "reference_price": "close"},
            "grid": {
                "lower_bound": 25000, "upper_bound": 35000, "n_levels": 5,
                "spacing": "arithmetic", "order_size_quote": 50,
            },
        },
    )


def test_main_end_to_end_wires_pipeline_with_grid_bounds_and_alpha_column(monkeypatch, tmp_path):
    cfg = _fake_config()
    provider = FakeProvider(
        tickers={"BTCUSDT": Ticker24h(symbol="BTCUSDT", quote_volume=Decimal("100000000"))},
        books={"BTCUSDT": BookTicker(symbol="BTCUSDT", bid_price=Decimal("100"), ask_price=Decimal("100.05"))},
        klines_by_symbol={"BTCUSDT": _oscillating_klines(48)},
    )

    monkeypatch.setattr(backtest, "load_config", lambda: cfg)
    monkeypatch.setattr(backtest, "build_provider", lambda data_source: provider)
    monkeypatch.chdir(tmp_path)

    backtest.main()

    raw_path = Path(tmp_path) / "backtest_report.csv"
    analytics_path = Path(tmp_path) / "analytics_report.csv"
    assert raw_path.exists() and raw_path.stat().st_size > 0
    assert analytics_path.exists() and analytics_path.stat().st_size > 0

    with open(raw_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    assert "alpha_vs_buy_hold_pct" in rows[0].keys()  # Fix 3

    grid_rows = [r for r in rows if r["strategy"] == "grid" and r["symbol"] == "BTCUSDT"]
    assert len(grid_rows) == 1
    assert int(grid_rows[0]["nb_trades"]) > 0  # Fix 1: per-symbol grid bounds actually trade
