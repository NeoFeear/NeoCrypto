# Additive "smart DCA" behavior: RSI-based dip-buy sizing / overbought skip,
# ATR-based (volatility-adaptive) take-profit/stop-loss/trailing-stop, and
# the original fixed-pct trailing stop. All gated behind params keys the
# original DCA schema never set -- tests/test_strategy_dca.py proves these
# are all no-ops when absent, and that the fixed take_profit_pct/
# stop_loss_pct path is unchanged.
from decimal import Decimal

from engine.fifo_engine import FifoEngine
from engine.strategies.dca import DcaState, step
from market_data.types import Kline


def _kline(open_time_ms: int, close: str, high: str | None = None, low: str | None = None) -> Kline:
    c = Decimal(close)
    h = Decimal(high) if high is not None else c
    l = Decimal(low) if low is not None else c
    return Kline(
        open_time_ms=open_time_ms, open=c, high=h, low=l, close=c,
        volume=Decimal("1"), close_time_ms=open_time_ms + 3_599_999,
    )


HOUR = 3_600_000


def test_smart_dca_skips_scheduled_buy_when_rsi_overbought():
    engine = FifoEngine(initial_cash=Decimal("10000"), fee_pct=Decimal("0.001"))
    state = DcaState()
    params = {
        "amount_per_buy": 50, "frequency_hours": 1, "reference_price": "close",
        "rsi_period": 3, "rsi_overbought": 70,
    }

    # 4 consecutive +1 closes -> RSI(3) = 100 (all gains, no losses) once warmed up.
    step(state, _kline(0 * HOUR, "100"), engine, "BTCUSDT", params)
    step(state, _kline(1 * HOUR, "101"), engine, "BTCUSDT", params)
    step(state, _kline(2 * HOUR, "102"), engine, "BTCUSDT", params)
    assert len(engine.trades) == 3  # not enough history yet for RSI(3) -- buys normally
    # 4th close: RSI now computable (100/101/102/103, all gains) = 100 >= 70 -> skip
    step(state, _kline(3 * HOUR, "103"), engine, "BTCUSDT", params)

    assert len(engine.trades) == 3
    assert state.last_buy_ms == 3 * HOUR  # schedule slot still consumed on a skip


def test_smart_dca_buys_bigger_on_rsi_oversold():
    engine = FifoEngine(initial_cash=Decimal("10000"), fee_pct=Decimal("0.001"))
    state = DcaState()
    params = {
        "amount_per_buy": 50, "frequency_hours": 1, "reference_price": "close",
        "rsi_period": 3, "rsi_oversold": 30, "dip_buy_multiplier": 2,
    }

    # 4 consecutive -1 closes -> RSI(3) = 0 (all losses) once warmed up -> oversold.
    step(state, _kline(0 * HOUR, "100"), engine, "BTCUSDT", params)
    step(state, _kline(1 * HOUR, "99"), engine, "BTCUSDT", params)
    step(state, _kline(2 * HOUR, "98"), engine, "BTCUSDT", params)
    step(state, _kline(3 * HOUR, "97"), engine, "BTCUSDT", params)  # RSI(3)=0 <= 30 -> double buy

    assert len(engine.trades) == 4
    dip_trade = engine.trades[3]
    expected_quantity = Decimal("100") / Decimal("97")  # amount_per_buy * multiplier / price
    assert dip_trade.quantity == expected_quantity


def test_smart_dca_atr_take_profit_scales_with_volatility():
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    state = DcaState()
    params = {
        "amount_per_buy": 50, "frequency_hours": 100, "reference_price": "close",
        "atr_period": 2, "atr_take_profit_mult": 2,
    }

    # ATR needs atr_period+1=3 bars. Constant 10-point high-low range with no
    # gaps -> ATR settles at exactly 10 once warmed up.
    step(state, _kline(0 * HOUR, "100", high="105", low="95"), engine, "BTCUSDT", params)  # BUY @ 100
    step(state, _kline(1 * HOUR, "100", high="105", low="95"), engine, "BTCUSDT", params)
    # ATR now computable = 10. Take-profit trigger = 100 + 2*10 = 120.
    step(state, _kline(2 * HOUR, "119", high="119", low="119"), engine, "BTCUSDT", params)
    assert all(t.side.value == "BUY" for t in engine.trades)  # 119 < 120, no exit yet

    step(state, _kline(3 * HOUR, "121", high="121", low="121"), engine, "BTCUSDT", params)

    assert [t.side.value for t in engine.trades] == ["BUY", "SELL"]
    sell = engine.trades[1]
    assert sell.strategy_name == "dca_take_profit"
    assert sell.price == Decimal("121")


def test_smart_dca_atr_stop_loss_scales_with_volatility():
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    state = DcaState()
    params = {
        "amount_per_buy": 50, "frequency_hours": 100, "reference_price": "close",
        "atr_period": 2, "atr_stop_loss_mult": 2,
    }

    step(state, _kline(0 * HOUR, "100", high="105", low="95"), engine, "BTCUSDT", params)  # BUY @ 100
    step(state, _kline(1 * HOUR, "100", high="105", low="95"), engine, "BTCUSDT", params)
    # ATR now = 10. Stop-loss trigger = 100 - 2*10 = 80.
    step(state, _kline(2 * HOUR, "81", high="81", low="81"), engine, "BTCUSDT", params)
    assert all(t.side.value == "BUY" for t in engine.trades)  # 81 > 80, no exit yet

    step(state, _kline(3 * HOUR, "79", high="79", low="79"), engine, "BTCUSDT", params)

    assert [t.side.value for t in engine.trades] == ["BUY", "SELL"]
    assert engine.trades[1].strategy_name == "dca_stop_loss"


def test_smart_dca_atr_thresholds_take_priority_over_pct_when_both_configured():
    # A tight pct stop_loss_pct (2%, would trigger almost immediately) is
    # configured alongside a much looser ATR-based one -- ATR must win once
    # it has enough history, proving it isn't just an additional independent
    # check but the actual active threshold.
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    state = DcaState()
    params = {
        "amount_per_buy": 50, "frequency_hours": 100, "reference_price": "close",
        "atr_period": 2, "atr_stop_loss_mult": 5, "stop_loss_pct": 2,
    }

    step(state, _kline(0 * HOUR, "100", high="105", low="95"), engine, "BTCUSDT", params)  # BUY @ 100
    step(state, _kline(1 * HOUR, "100", high="105", low="95"), engine, "BTCUSDT", params)
    # ATR now = 10 -> ATR stop-loss trigger = 100 - 5*10 = 50, far below the 2%
    # pct trigger (98) that would otherwise have already fired.
    step(state, _kline(2 * HOUR, "96", high="96", low="96"), engine, "BTCUSDT", params)

    assert all(t.side.value == "BUY" for t in engine.trades)  # pct would've sold; ATR doesn't


def test_smart_dca_trailing_stop_pct_still_works_unchanged():
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    state = DcaState()
    params = {
        "amount_per_buy": 50, "frequency_hours": 100, "reference_price": "close",
        "trailing_stop_pct": 4,
    }

    step(state, _kline(0 * HOUR, "100"), engine, "BTCUSDT", params)   # BUY @ 100
    step(state, _kline(1 * HOUR, "130"), engine, "BTCUSDT", params)   # peak -> 130
    step(state, _kline(2 * HOUR, "140"), engine, "BTCUSDT", params)   # peak -> 140
    # 140 * (1 - 4%) = 134.4 -> 134 is a 4.29% drawdown from peak -> trailing stop fires
    step(state, _kline(3 * HOUR, "134"), engine, "BTCUSDT", params)

    assert [t.side.value for t in engine.trades] == ["BUY", "SELL"]
    sell = engine.trades[1]
    assert sell.strategy_name == "dca_trailing_stop"
    assert engine.get_lots("BTCUSDT") == []
    assert state.peak_price_since_oldest_entry is None


def test_dca_state_json_round_trip_preserves_smart_dca_fields():
    from engine.strategies.dca import dca_state_from_json, dca_state_to_json

    state = DcaState(
        last_buy_ms=999,
        recent_closes=[Decimal("100"), Decimal("105")],
        recent_highs=[Decimal("110"), Decimal("115")],
        recent_lows=[Decimal("90"), Decimal("95")],
        peak_price_since_oldest_entry=Decimal("140"),
        peak_tracks_lot_id=7,
    )
    restored = dca_state_from_json(dca_state_to_json(state))

    assert restored.last_buy_ms == 999
    assert restored.recent_closes == [Decimal("100"), Decimal("105")]
    assert restored.recent_highs == [Decimal("110"), Decimal("115")]
    assert restored.recent_lows == [Decimal("90"), Decimal("95")]
    assert restored.peak_price_since_oldest_entry == Decimal("140")
    assert restored.peak_tracks_lot_id == 7
