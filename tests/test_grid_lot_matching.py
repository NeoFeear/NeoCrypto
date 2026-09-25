"""Review "Revue NeoCrypto" (2026-09-25), points CRITIQUE and MODERE.

CRITIQUE: a grid sell was matched FIFO to the oldest lot, not to the lot its
own level bought, so a winning 33000 -> 34000 round trip was recorded as a
loss (-0.0139 $) and win rate / profit factor / expectancy were wrong for
every grid. Each level now sells the lot it bought (same buy price and
quantity); DCA keeps plain FIFO.

MODERE: live compared 5m closes only (missing every wick a resting limit
order would have caught) while backtest used each candle's low/high. With
fill_model: range, live applies the backtest rule to each closed 5m candle.
"""
from decimal import Decimal

from engine.fifo_engine import FifoEngine
from engine.strategies.grid import GridState, build_grid_levels, run_grid, step_grid_live
from market_data.types import Kline

FEE = Decimal("0.001")


def _k(t, low, high, close=None):
    low, high = Decimal(low), Decimal(high)
    close = Decimal(close) if close is not None else high
    return Kline(open_time_ms=t, open=close, high=high, low=low, close=close, volume=Decimal(1), close_time_ms=t + 1)


def test_review_case_backtest_grid_records_the_winning_cycle_as_a_gain():
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=FEE)
    params = {"lower_bound": 30000, "upper_bound": 35000, "n_levels": 5, "spacing": "arithmetic", "order_size_quote": 100}
    run_grid([_k(1, "34000", "34000"), _k(2, "33000", "33000"), _k(3, "34000", "34000")], engine, "BTCUSDT", params)

    sells = [t for t in engine.trades if t.side.value == "SELL"]
    assert len(sells) == 1
    # (34000 - 33000) * 100/33000 - sell fee = +2.93 $ (the buy fee is charged to cash,
    # not to realized PnL, by FifoEngine's convention) -- instead of -0.0139 $
    assert Decimal("2.9") < sells[0].realized_pnl < Decimal("2.95")
    # the lot left open is the 34000 level's own lot, with its own quantity
    [lot] = engine.get_lots("BTCUSDT")
    assert lot.prix_achat == Decimal("34000")
    assert lot.quantity_restante == Decimal(100) / Decimal("34000")


def test_sell_targeting_a_missing_lot_falls_back_to_fifo():
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=FEE)
    engine.buy(1, "BTCUSDT", Decimal("34000"), Decimal("0.001"), "grid")
    engine.buy(2, "BTCUSDT", Decimal("33000"), Decimal("0.001"), "grid")
    # no lot bought at 32000 (e.g. a level restored from before this fix) -> FIFO
    trade = engine.sell(3, "BTCUSDT", Decimal("34000"), Decimal("0.001"), "grid", lot_price=Decimal("32000"))
    assert trade is not None
    [lot] = engine.get_lots("BTCUSDT")
    assert lot.prix_achat == Decimal("33000")  # oldest (34000) consumed first


def test_plain_sell_is_still_fifo():
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=FEE)
    engine.buy(1, "ETHUSDT", Decimal("2000"), Decimal("0.01"), "dca")
    engine.buy(2, "ETHUSDT", Decimal("1800"), Decimal("0.01"), "dca")
    engine.sell(3, "ETHUSDT", Decimal("1900"), Decimal("0.01"), "dca")
    [lot] = engine.get_lots("ETHUSDT")
    assert lot.prix_achat == Decimal("1800")


def _live_state(price):
    return GridState(levels=build_grid_levels(30000, 35000, 5, "arithmetic"), prev_price=Decimal(price),
                     lower_bound=Decimal(30000), upper_bound=Decimal(35000))


def test_live_range_model_catches_a_wick_that_close_to_close_misses():
    params = {"order_size_quote": 100, "fill_model": "range"}
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=FEE)
    state = _live_state("33500")
    # the 5m candle wicks down to 32900 but closes back at 33400
    step_grid_live(state, Decimal("33400"), 10, engine, "BTCUSDT", params, high=Decimal("33500"), low=Decimal("32900"))
    assert [lvl.buy_price for lvl in state.levels if lvl.state == "FILLED"] == [Decimal("33000")]

    cross = FifoEngine(initial_cash=Decimal("1000"), fee_pct=FEE)
    state2 = _live_state("33500")
    step_grid_live(state2, Decimal("33400"), 10, cross, "BTCUSDT", {"order_size_quote": 100},
                   high=Decimal("33500"), low=Decimal("32900"))
    assert not any(lvl.state == "FILLED" for lvl in state2.levels)


def test_live_range_model_never_round_trips_inside_one_candle():
    params = {"order_size_quote": 100, "fill_model": "range"}
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=FEE)
    state = _live_state("33500")
    # one wide candle spans the 33000 buy and its 34000 sell
    step_grid_live(state, Decimal("33500"), 10, engine, "BTCUSDT", params, high=Decimal("34100"), low=Decimal("32900"))
    assert not [t for t in engine.trades if t.side.value == "SELL"]
    # the next candle reaching 34000 sells it, matched to its own lot, at a gain
    step_grid_live(state, Decimal("34000"), 20, engine, "BTCUSDT", params, high=Decimal("34050"), low=Decimal("33700"))
    [sell] = [t for t in engine.trades if t.side.value == "SELL"]
    assert sell.price == Decimal("34000") and sell.realized_pnl > 0


def test_live_range_model_does_not_fill_a_brand_new_grid_on_the_candle_it_was_built_from():
    params = {"order_size_quote": 100, "fill_model": "range"}
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=FEE)
    state = GridState(levels=build_grid_levels(30000, 35000, 5, "arithmetic"), prev_price=None,
                      lower_bound=Decimal(30000), upper_bound=Decimal(35000))
    step_grid_live(state, Decimal("33500"), 10, engine, "BTCUSDT", params, high=Decimal("34100"), low=Decimal("32900"))
    assert engine.trades == []
