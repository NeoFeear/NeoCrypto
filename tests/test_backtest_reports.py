from decimal import Decimal

from backtest import build_analytics_row, build_raw_metrics_row, run_strategy
from market_data.types import Kline


def _kline(open_time_ms: int, close: str) -> Kline:
    c = Decimal(close)
    return Kline(open_time_ms=open_time_ms, open=c, high=c, low=c, close=c,
                 volume=Decimal("1"), close_time_ms=open_time_ms + 3_599_999)


def _run_dca_scenario():
    # 25 hourly candles: DCA buys at 0 (price 100) and 24 (price 125), no sells.
    klines = [_kline(i * 3_600_000, "100" if i != 24 else "125") for i in range(25)]
    engine, snapshots = run_strategy(
        "dca", klines, "BTCUSDT",
        params={"amount_per_buy": 50, "frequency_hours": 24, "reference_price": "close"},
        initial_capital=Decimal("1000"), fee_pct=Decimal("0.001"), interval_ms=3_600_000,
    )
    return engine, snapshots


def test_build_raw_metrics_row_shape_and_basic_values():
    engine, snapshots = _run_dca_scenario()

    row = build_raw_metrics_row(
        "BTCUSDT", "dca", engine, snapshots, initial_capital=Decimal("1000"),
        buy_hold_return_pct=Decimal("5.00"),
    )

    assert row["symbol"] == "BTCUSDT"
    assert row["strategy"] == "dca"
    assert row["nb_trades"] == 2
    assert row["win_rate_pct"] == Decimal("0")  # no sells at all -> trade_stats reports 0
    assert row["total_fees"] == Decimal("0.05") + Decimal("0.05")
    # DCA never sells, so realized-PnL-based fields are all zero/neutral
    assert row["avg_loss"] == Decimal("0")  # no sells -> sign flip is a no-op here
    assert row["biggest_win"] == Decimal("0")
    assert row["biggest_loss"] == Decimal("0")
    # final_total_value = cash + position_value(at last close=125)
    # qty held = 0.5 + 0.4 = 0.9 ; position_value = 0.9*125 = 112.5
    # cash = 1000 - 50.05 - 50.05 = 899.90 ; total = 899.90 + 112.5 = 1012.40
    assert row["return_pct"] == (Decimal("1012.40") - Decimal("1000")) / Decimal("1000") * Decimal("100")
    assert row["alpha_vs_buy_hold_pct"] == row["return_pct"] - Decimal("5.00")


def test_build_analytics_row_shape_and_alpha():
    engine, snapshots = _run_dca_scenario()

    row = build_analytics_row(
        "BTCUSDT", "dca", engine, snapshots, initial_capital=Decimal("1000"),
        periods_per_year=8760, buy_hold_return_pct=Decimal("5.00"),
    )

    assert row["symbol"] == "BTCUSDT"
    assert row["strategy"] == "dca"
    assert "sharpe_ratio" in row
    assert "sortino_ratio" in row
    assert "calmar_ratio" in row
    assert "max_drawdown_pct" in row
    assert "recovery_days" in row
    assert "profit_factor" in row
    assert "expectancy" in row
    assert "exposure_time_pct" in row
    assert row["alpha_vs_buy_hold_pct"] == row["total_return_pct"] - Decimal("5.00")


def test_build_analytics_row_buy_hold_self_comparison_is_zero_alpha():
    engine, snapshots = run_strategy(
        "buy_hold", [_kline(0, "100"), _kline(3_600_000, "110")], "BTCUSDT",
        params={"invest_at": "start"}, initial_capital=Decimal("1000"),
        fee_pct=Decimal("0.001"), interval_ms=3_600_000,
    )
    own_return = (snapshots[-1].total_value - Decimal("1000")) / Decimal("1000") * Decimal("100")

    row = build_analytics_row(
        "BTCUSDT", "buy_hold", engine, snapshots, initial_capital=Decimal("1000"),
        periods_per_year=8760, buy_hold_return_pct=own_return,
    )

    assert row["alpha_vs_buy_hold_pct"] == Decimal("0")
