# Additive grid behavior: auto-derived bounds (band_pct around current price,
# no hardcoded lower_bound/upper_bound -- works for any symbol regardless of
# its price scale) and automatic re-ranging on a breakout. Existing
# tests/test_strategy_grid.py proves the fixed-bound path is untouched.
from decimal import Decimal

from engine.fifo_engine import FifoEngine
from engine.strategies.grid import build_grid_levels_auto, build_grid_state, step_grid_live


def test_build_grid_levels_auto_derives_symmetric_band_around_current_price():
    levels = build_grid_levels_auto(Decimal("100"), band_pct=10, n_levels=2, spacing="arithmetic")

    assert levels[0].buy_price == Decimal("90")
    assert levels[-1].sell_price == Decimal("110")


def test_build_grid_levels_auto_scales_to_small_price_symbols():
    # Same band_pct, wildly different price scale (e.g. an altcoin priced in
    # cents) -- no hardcoded $ bound leaks in.
    levels = build_grid_levels_auto(Decimal("0.50"), band_pct=10, n_levels=1, spacing="arithmetic")

    assert levels[0].buy_price == Decimal("0.45")
    assert levels[0].sell_price == Decimal("0.55")


def test_build_grid_state_without_explicit_bounds_requires_current_price():
    import pytest
    with pytest.raises(ValueError):
        build_grid_state({"band_pct": 10, "n_levels": 1, "spacing": "arithmetic", "order_size_quote": 100})


def test_build_grid_state_auto_band_records_bounds_on_state():
    state = build_grid_state(
        {"band_pct": 10, "n_levels": 1, "spacing": "arithmetic", "order_size_quote": 100},
        current_price=Decimal("100"),
    )
    assert state.lower_bound == Decimal("90")
    assert state.upper_bound == Decimal("110")
    assert state.levels[0].buy_price == Decimal("90")
    assert state.levels[0].sell_price == Decimal("110")


def test_step_grid_live_reranges_upward_on_breakout_after_normal_sell_clears_the_level():
    # A level's own sell_price never exceeds the band's upper_bound, so an
    # upward breakout has, by construction, already sold every filled level
    # through the ordinary crossing check before the breakout re-range logic
    # ever runs -- there is nothing left to force-liquidate.
    engine = FifoEngine(initial_cash=Decimal("10000"), fee_pct=Decimal("0.001"))
    params = {
        "n_levels": 1, "spacing": "arithmetic", "order_size_quote": 100,
        "band_pct": 10, "rebuild_breakout_pct": 15,
    }
    state = build_grid_state(params, current_price=Decimal("100"))  # bounds [90, 110]

    step_grid_live(state, Decimal("100"), 0, engine, "BTCUSDT", params)  # sets prev=100
    step_grid_live(state, Decimal("89"), 1, engine, "BTCUSDT", params)   # crosses down through 90 -> BUY
    assert len(engine.trades) == 1
    assert engine.trades[0].side.value == "BUY"

    # Single big jump: crosses the level's own sell_price (110) on the way to
    # breaking out past 110 * 1.15 = 126.5. The normal per-tick crossing check
    # sells it at its own sell_price (110), then the breakout re-range rebuilds
    # the (now fully empty) grid around the new price.
    step_grid_live(state, Decimal("130"), 2, engine, "BTCUSDT", params)

    assert len(engine.trades) == 2
    sell = engine.trades[1]
    assert sell.side.value == "SELL"
    assert sell.strategy_name == "grid"
    assert sell.price == Decimal("110")
    # New grid re-centered on 130 with the same 10% band.
    assert state.lower_bound == Decimal("117.0")
    assert state.upper_bound == Decimal("143.0")
    assert all(lvl.state == "EMPTY" for lvl in state.levels)


def test_step_grid_live_reranges_downward_without_liquidating_filled_levels():
    engine = FifoEngine(initial_cash=Decimal("10000"), fee_pct=Decimal("0.001"))
    params = {
        "n_levels": 1, "spacing": "arithmetic", "order_size_quote": 100,
        "band_pct": 10, "rebuild_breakout_pct": 15,
    }
    state = build_grid_state(params, current_price=Decimal("100"))  # bounds [90, 110]

    step_grid_live(state, Decimal("100"), 0, engine, "BTCUSDT", params)  # sets prev=100
    step_grid_live(state, Decimal("89"), 1, engine, "BTCUSDT", params)   # crosses down through 90 -> BUY
    assert len(engine.trades) == 1
    filled_level = next(lvl for lvl in state.levels if lvl.state == "FILLED")
    original_sell_price = filled_level.sell_price

    # Breakout downward: 90 * (1 - 15%) = 76.5 -- 70 is past it.
    step_grid_live(state, Decimal("70"), 2, engine, "BTCUSDT", params)

    # No forced sell at a loss -- still exactly 1 trade (the earlier BUY).
    assert len(engine.trades) == 1
    still_filled = next(lvl for lvl in state.levels if lvl.state == "FILLED")
    assert still_filled.sell_price == original_sell_price
    # New EMPTY levels were added, re-centered around 70.
    assert state.lower_bound == Decimal("63.0")
    assert state.upper_bound == Decimal("77.0")
    empty_levels = [lvl for lvl in state.levels if lvl.state == "EMPTY"]
    assert len(empty_levels) == 1
    assert empty_levels[0].buy_price == Decimal("63.0")


def test_step_grid_live_no_rerange_when_breakout_pct_not_configured():
    # rebuild_breakout_pct absent -- behaves exactly like the original,
    # non-adaptive grid even far outside the initial bounds.
    engine = FifoEngine(initial_cash=Decimal("10000"), fee_pct=Decimal("0.001"))
    params = {"n_levels": 1, "spacing": "arithmetic", "order_size_quote": 100, "band_pct": 10}
    state = build_grid_state(params, current_price=Decimal("100"))

    step_grid_live(state, Decimal("100"), 0, engine, "BTCUSDT", params)
    step_grid_live(state, Decimal("500"), 1, engine, "BTCUSDT", params)  # way past upper bound

    assert state.lower_bound == Decimal("90")
    assert state.upper_bound == Decimal("110")
    assert engine.trades == []  # EMPTY level, no crossing of buy_price -> nothing to liquidate either


def test_grid_state_json_round_trip_preserves_bounds():
    from engine.strategies.grid import grid_state_from_json, grid_state_to_json

    state = build_grid_state(
        {"band_pct": 10, "n_levels": 1, "spacing": "arithmetic", "order_size_quote": 100},
        current_price=Decimal("100"),
    )
    restored = grid_state_from_json(grid_state_to_json(state))

    assert restored.lower_bound == Decimal("90")
    assert restored.upper_bound == Decimal("110")
