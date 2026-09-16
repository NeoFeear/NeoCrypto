from decimal import Decimal

import pytest

from backtest import run_strategy
from engine.fifo_engine import Side
from market_data.types import Kline


def _kline(open_time_ms: int, close: str) -> Kline:
    c = Decimal(close)
    return Kline(open_time_ms=open_time_ms, open=c, high=c, low=c, close=c,
                 volume=Decimal("1"), close_time_ms=open_time_ms + 3_599_999)


def test_run_strategy_buy_hold_produces_fresh_engine_and_snapshots():
    klines = [_kline(0, "100"), _kline(3_600_000, "110")]

    engine, snapshots = run_strategy(
        "buy_hold", klines, "BTCUSDT", params={"invest_at": "start"},
        initial_capital=Decimal("1000"), fee_pct=Decimal("0.001"), interval_ms=3_600_000,
    )

    assert engine.cash_balance == Decimal("0E-24")
    assert len(engine.trades) == 1
    assert len(snapshots) == 2


def test_run_strategy_dca_uses_interval_hours():
    klines = [_kline(i * 3_600_000, "100") for i in range(3)]

    engine, snapshots = run_strategy(
        "dca", klines, "BTCUSDT",
        params={"amount_per_buy": 50, "frequency_hours": 1, "reference_price": "close"},
        initial_capital=Decimal("1000"), fee_pct=Decimal("0.001"), interval_ms=3_600_000,
    )

    assert len(engine.trades) == 3  # frequency_hours=1, interval_ms=3_600_000 -> every candle


def test_run_strategy_grid_ignores_interval_hours():
    klines = [_kline(0, "150")]

    engine, snapshots = run_strategy(
        "grid", klines, "BTCUSDT",
        params={"lower_bound": 100, "upper_bound": 200, "n_levels": 1,
                "spacing": "arithmetic", "order_size_quote": 100},
        initial_capital=Decimal("1000"), fee_pct=Decimal("0.001"), interval_ms=3_600_000,
    )

    assert engine.trades == []  # price 150 never touches buy=100 or sell=200 on a flat OHLC kline


def test_run_strategy_unknown_type_raises():
    with pytest.raises(ValueError, match="martingale"):
        run_strategy("martingale", [], "BTCUSDT", {}, Decimal("1000"), Decimal("0.001"), 3_600_000)
