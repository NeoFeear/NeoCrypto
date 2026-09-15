from decimal import Decimal

import pytest

from engine.strategies.grid import GridLevel, build_grid_levels


def test_build_grid_levels_arithmetic_spacing():
    levels = build_grid_levels(lower_bound=100, upper_bound=400, n_levels=3, spacing="arithmetic")

    assert len(levels) == 3
    # boundaries: 100, 200, 300, 400 (step 100)
    assert [lvl.buy_price for lvl in levels] == [Decimal("100"), Decimal("200"), Decimal("300")]
    assert [lvl.sell_price for lvl in levels] == [Decimal("200"), Decimal("300"), Decimal("400")]
    assert all(lvl.state == "EMPTY" and lvl.filled_quantity is None for lvl in levels)


def test_build_grid_levels_geometric_spacing():
    levels = build_grid_levels(lower_bound=100, upper_bound=400, n_levels=2, spacing="geometric")

    assert len(levels) == 2
    # ratio = sqrt(400/100) = 2 ; boundaries: 100, 200, 400
    assert levels[0].buy_price == Decimal("100")
    assert levels[0].sell_price == Decimal("200")
    assert levels[1].buy_price == Decimal("200")
    assert levels[1].sell_price == Decimal("400")


def test_build_grid_levels_unknown_spacing_raises():
    with pytest.raises(ValueError, match="triangular"):
        build_grid_levels(lower_bound=100, upper_bound=200, n_levels=1, spacing="triangular")


def test_build_grid_levels_accepts_int_and_float_params_without_float_leak():
    # JSON-loaded params can be plain int/float — must not leak float into Decimal math
    levels = build_grid_levels(lower_bound=25000, upper_bound=35000, n_levels=10, spacing="arithmetic")
    assert isinstance(levels[0].buy_price, Decimal)
    assert levels[0].buy_price == Decimal("25000")
    assert levels[-1].sell_price == Decimal("35000")


def test_build_grid_levels_geometric_endpoints_are_exact_even_with_irrational_ratio():
    levels = build_grid_levels(lower_bound=3, upper_bound=10, n_levels=5, spacing="geometric")
    assert levels[0].buy_price == Decimal("3")
    assert levels[-1].sell_price == Decimal("10")


from engine.fifo_engine import FifoEngine
from engine.strategies.grid import run_grid


def _ohlc_kline(open_time_ms: int, low: str, high: str, close: str) -> "Kline":
    from market_data.types import Kline
    return Kline(
        open_time_ms=open_time_ms, open=Decimal(close), high=Decimal(high),
        low=Decimal(low), close=Decimal(close), volume=Decimal("1"),
        close_time_ms=open_time_ms + 3_599_999,
    )


def test_grid_level_rearms_across_multiple_oscillations():
    # single level: lower=100, upper=200, n_levels=1 -> buy=100, sell=200
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    klines = [
        _ohlc_kline(0, "90", "110", "100"),              # dips to 100 -> BUY
        _ohlc_kline(3_600_000, "150", "250", "200"),      # rises to 200 -> SELL
        _ohlc_kline(7_200_000, "90", "110", "100"),       # dips to 100 again -> BUY (re-arm, D1)
    ]

    run_grid(klines, engine, "BTCUSDT",
              params={"lower_bound": 100, "upper_bound": 200, "n_levels": 1,
                      "spacing": "arithmetic", "order_size_quote": 100})

    assert len(engine.trades) == 3
    assert [t.side.value for t in engine.trades] == ["BUY", "SELL", "BUY"]
    # buy: qty=100/100=1, gross=100, fee=0.1, total_cost=100.1 -> cash 1000-100.1=899.9
    assert engine.trades[0].total_cost == Decimal("100.1")
    # sell: gross=200, fee=0.2, proceeds=199.8, pnl=(200-100)*1-0.2=99.8 -> cash 899.9+199.8=1099.7
    assert engine.trades[1].realized_pnl == Decimal("99.8")
    assert engine.trades[1].total_cost == Decimal("199.8")
    # re-arm buy: identical to the first -> cash 1099.7-100.1=999.6
    assert engine.trades[2].total_cost == Decimal("100.1")
    assert engine.cash_balance == Decimal("999.6")


def test_grid_multi_level_fills_cheapest_first_rejects_rest_on_insufficient_cash():
    # 2 levels: lower=100, upper=200, n_levels=2 -> level0(100,150), level1(150,200)
    engine = FifoEngine(initial_cash=Decimal("200"), fee_pct=Decimal("0.001"))
    klines = [_ohlc_kline(0, "90", "160", "120")]  # touches both buy_price 100 and 150

    run_grid(klines, engine, "BTCUSDT",
              params={"lower_bound": 100, "upper_bound": 200, "n_levels": 2,
                      "spacing": "arithmetic", "order_size_quote": 150})

    # level0 (buy=100): qty=150/100=1.5, gross=150, fee=0.15, total_cost=150.15
    # cash after level0: 200-150.15=49.85
    # level1 (buy=150): qty=150/150=1, total_cost=150.15 > 49.85 -> rejected (D3)
    assert len(engine.trades) == 1
    assert engine.trades[0].price == Decimal("100")
    assert engine.trades[0].quantity == Decimal("1.5")
    assert engine.cash_balance == Decimal("49.85")


def test_grid_never_triggers_when_price_stays_between_levels():
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    klines = [_ohlc_kline(0, "120", "140", "130")]  # never touches 100 or 200

    run_grid(klines, engine, "BTCUSDT",
              params={"lower_bound": 100, "upper_bound": 200, "n_levels": 1,
                      "spacing": "arithmetic", "order_size_quote": 100})

    assert engine.trades == []


def test_grid_level_does_not_round_trip_within_same_candle():
    # single level: lower=100, upper=200, n_levels=1 -> buy=100, sell=200
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    klines = [
        _ohlc_kline(0, "90", "210", "150"),           # spans BOTH buy=100 and sell=200 in one candle
        _ohlc_kline(3_600_000, "190", "210", "200"),  # still touches sell=200, now allowed (separate candle)
    ]

    run_grid(klines, engine, "BTCUSDT",
              params={"lower_bound": 100, "upper_bound": 200, "n_levels": 1,
                      "spacing": "arithmetic", "order_size_quote": 100})

    # candle 1: BUY fires (qty=1 @ 100, total_cost=100.1), SELL does NOT fire even though
    # sell_price=200 is within [90,210] -- the level was just bought this same candle.
    # candle 2: SELL now fires (gross=200, fee=0.2, proceeds=199.8, pnl=(200-100)*1-0.2=99.8)
    assert len(engine.trades) == 2
    assert [t.side.value for t in engine.trades] == ["BUY", "SELL"]
    assert engine.trades[0].total_cost == Decimal("100.1")
    assert engine.trades[1].realized_pnl == Decimal("99.8")
