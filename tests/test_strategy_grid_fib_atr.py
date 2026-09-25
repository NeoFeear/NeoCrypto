# Additive grid behavior: Fibonacci-retracement level spacing, and
# ATR-derived (volatility-adaptive) re-range band width. Existing
# tests/test_strategy_grid.py and test_strategy_grid_auto.py prove the
# arithmetic/geometric spacing and static band_pct paths are untouched.
from decimal import Decimal

import indicators
from engine.fifo_engine import FifoEngine
from engine.strategies.grid import build_grid_levels, build_grid_state, step_grid_live


def test_build_grid_levels_fibonacci_spacing_uses_standard_retracement_ratios():
    levels = build_grid_levels(lower_bound=100, upper_bound=200, n_levels=10, spacing="fibonacci")

    # n_levels is ignored -- always the 6 fixed Fibonacci segments.
    assert len(levels) == 6
    assert levels[0].buy_price == Decimal("100")
    assert levels[0].sell_price == Decimal("123.6")
    assert levels[1].sell_price == Decimal("138.2")
    assert levels[2].sell_price == Decimal("150.0")
    assert levels[3].sell_price == Decimal("161.8")
    assert levels[4].sell_price == Decimal("178.6")
    assert levels[5].sell_price == Decimal("200")


def test_step_grid_live_reranges_with_atr_derived_band_once_warmed_up():
    engine = FifoEngine(initial_cash=Decimal("10000"), fee_pct=Decimal("0.001"))
    params = {
        "n_levels": 1, "spacing": "arithmetic", "order_size_quote": 100,
        "band_pct": 10,  # static fallback for the very first grid (no ATR history yet)
        "rebuild_breakout_pct": 15,
        "atr_period": 2, "atr_band_multiplier": 2,
    }
    state = build_grid_state(params, current_price=Decimal("100"))  # seeded via band_pct: [90, 110]
    assert state.lower_bound == Decimal("90")
    assert state.upper_bound == Decimal("110")

    bars = [
        # (price, high, low)
        (Decimal("100"), Decimal("105"), Decimal("95")),
        (Decimal("100"), Decimal("105"), Decimal("95")),
        (Decimal("89"), Decimal("105"), Decimal("95")),   # crosses down through buy_price=90 -> BUY
        (Decimal("130"), Decimal("135"), Decimal("125")),  # breakout past 110 * 1.15 = 126.5
    ]
    for i, (price, high, low) in enumerate(bars):
        step_grid_live(state, price, i, engine, "BTCUSDT", params, high=high, low=low)

    assert len(engine.trades) == 2  # BUY, then normal SELL at the level's own sell_price (110)
    assert engine.trades[1].strategy_name == "grid"

    # The re-range band comes from the *same* rolling ATR window step_grid_live
    # maintains internally (last atr_period+1=3 bars: index 1, 2, 3 above --
    # index 0 fell out of the cap). Recomputed independently here via the same
    # shared indicators.atr(), proving grid.py wires the real window/multiplier/
    # price through correctly rather than e.g. always falling back to band_pct.
    expected_atr = indicators.atr(
        [b[1] for b in bars[1:]], [b[2] for b in bars[1:]], [b[0] for b in bars[1:]], 2,
    )
    assert expected_atr is not None
    expected_band_pct = (expected_atr / Decimal("130")) * Decimal(100) * Decimal(2)
    expected_lower = Decimal("130") * (Decimal(1) - expected_band_pct / Decimal(100))
    expected_upper = Decimal("130") * (Decimal(1) + expected_band_pct / Decimal(100))
    assert state.lower_bound == expected_lower
    assert state.upper_bound == expected_upper
    # Sanity: a real breakout of this size genuinely does widen ATR-derived
    # band past what the static 10% fallback would have given.
    static_upper = Decimal("130") * Decimal("1.10")
    assert state.upper_bound > static_upper


def test_step_grid_live_falls_back_to_static_band_when_atr_not_warmed_up():
    # atr_band_multiplier configured but only 1 bar of history -- not enough
    # for atr_period=2 (needs 3) -- must still fall back to band_pct cleanly,
    # never raise or silently skip the re-range.
    engine = FifoEngine(initial_cash=Decimal("10000"), fee_pct=Decimal("0.001"))
    params = {
        "n_levels": 1, "spacing": "arithmetic", "order_size_quote": 100,
        "band_pct": 10, "rebuild_breakout_pct": 15, "atr_period": 2, "atr_band_multiplier": 2,
    }
    state = build_grid_state(params, current_price=Decimal("100"))

    step_grid_live(state, Decimal("100"), 0, engine, "BTCUSDT", params)
    step_grid_live(state, Decimal("130"), 1, engine, "BTCUSDT", params)  # breakout, ATR not warmed up

    assert state.lower_bound == Decimal("130") * Decimal("0.9")
    assert state.upper_bound == Decimal("130") * Decimal("1.1")


def test_grid_state_json_round_trip_preserves_atr_history():
    from engine.strategies.grid import grid_state_from_json, grid_state_to_json

    state = build_grid_state(
        {"band_pct": 10, "n_levels": 1, "spacing": "arithmetic", "order_size_quote": 100},
        current_price=Decimal("100"),
    )
    state.recent_highs = [Decimal("105"), Decimal("108")]
    state.recent_lows = [Decimal("95"), Decimal("98")]
    state.recent_closes = [Decimal("100"), Decimal("103")]

    restored = grid_state_from_json(grid_state_to_json(state))

    assert restored.recent_highs == [Decimal("105"), Decimal("108")]
    assert restored.recent_lows == [Decimal("95"), Decimal("98")]
    assert restored.recent_closes == [Decimal("100"), Decimal("103")]
