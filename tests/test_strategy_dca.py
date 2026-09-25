# tests/test_strategy_dca.py
from decimal import Decimal

from engine.fifo_engine import FifoEngine
from engine.strategies.dca import DcaState, dca_state_from_json, dca_state_to_json, run_dca, step
from market_data.types import Kline


def _kline(open_time_ms: int, close: str) -> Kline:
    c = Decimal(close)
    return Kline(
        open_time_ms=open_time_ms, open=c, high=c, low=c, close=c,
        volume=Decimal("1"), close_time_ms=open_time_ms + 3_599_999,
    )


def test_dca_buys_every_frequency_hours_starting_at_index_zero():
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    # 25 hourly candles (index 0..24); frequency_hours=24 -> buys at index 0 and 24 only.
    klines = [_kline(i * 3_600_000, "100" if i != 24 else "125") for i in range(25)]

    snapshots = run_dca(
        klines, engine, "BTCUSDT",
        params={"amount_per_buy": 50, "frequency_hours": 24, "reference_price": "close"},
    )

    assert len(engine.trades) == 2
    trade0, trade24 = engine.trades
    assert trade0.price == Decimal("100")
    assert trade0.quantity == Decimal("0.5")
    assert trade0.total_cost == Decimal("50.05")
    assert trade24.price == Decimal("125")
    assert trade24.quantity == Decimal("0.4")
    assert trade24.total_cost == Decimal("50.05")
    assert engine.cash_balance == Decimal("1000") - Decimal("50.05") - Decimal("50.05")
    assert len(snapshots) == 25


def test_dca_never_sells():
    # No take_profit_pct/stop_loss_pct in params -- the default, spec'd
    # DCA schema -- so pure accumulation, no exit ever fires.
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    klines = [_kline(0, "100"), _kline(3_600_000, "50")]

    run_dca(klines, engine, "BTCUSDT",
            params={"amount_per_buy": 50, "frequency_hours": 24, "reference_price": "close"})

    assert all(t.side.value == "BUY" for t in engine.trades)


def test_dca_take_profit_sells_oldest_lot():
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    # frequency_hours=24 keeps this to a single buy, so the +15% move at
    # candle 1 can only ever be the take-profit exit, never a second buy.
    klines = [_kline(0, "100"), _kline(3_600_000, "115")]

    run_dca(klines, engine, "BTCUSDT", params={
        "amount_per_buy": 50, "frequency_hours": 24, "reference_price": "close",
        "take_profit_pct": 10,
    })

    assert [t.side.value for t in engine.trades] == ["BUY", "SELL"]
    sell = engine.trades[1]
    assert sell.strategy_name == "dca_take_profit"
    assert sell.quantity == Decimal("0.5")
    assert sell.realized_pnl == Decimal("7.4425")
    assert engine.get_lots("BTCUSDT") == []


def test_dca_stop_loss_sells_oldest_lot():
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    klines = [_kline(0, "100"), _kline(3_600_000, "90")]  # -10%

    run_dca(klines, engine, "BTCUSDT", params={
        "amount_per_buy": 50, "frequency_hours": 24, "reference_price": "close",
        "stop_loss_pct": 10,
    })

    assert [t.side.value for t in engine.trades] == ["BUY", "SELL"]
    sell = engine.trades[1]
    assert sell.strategy_name == "dca_stop_loss"
    assert sell.realized_pnl == Decimal("-5.045")


def test_dca_no_exit_when_price_move_is_within_thresholds():
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    klines = [_kline(0, "100"), _kline(3_600_000, "105")]  # +5%, under both thresholds

    run_dca(klines, engine, "BTCUSDT", params={
        "amount_per_buy": 50, "frequency_hours": 24, "reference_price": "close",
        "take_profit_pct": 10, "stop_loss_pct": 10,
    })

    assert all(t.side.value == "BUY" for t in engine.trades)


def test_dca_exit_only_evaluates_oldest_lot():
    # FifoEngine.sell() always consumes oldest-first, so selling based on a
    # non-oldest lot's own cost basis would realize PnL against the wrong lot.
    # _check_exit must only ever look at lots[0].
    engine = FifoEngine(initial_cash=Decimal("10000"), fee_pct=Decimal("0.001"))
    state = DcaState()
    no_exit_params = {"amount_per_buy": 50, "frequency_hours": 1, "reference_price": "close"}
    step(state, _kline(0, "100"), engine, "BTCUSDT", no_exit_params)          # lot cost 100 (oldest)
    step(state, _kline(3_600_000, "500"), engine, "BTCUSDT", no_exit_params)  # lot cost 500 (newest)
    assert len(engine.get_lots("BTCUSDT")) == 2

    # At price=110: oldest lot (cost 100) is at +10%, under the 50% thresholds.
    # Newest lot (cost 500) is at -78%, which would blow through a 50%
    # stop-loss if it were the one evaluated. Neither exit should fire.
    step(state, _kline(7_200_000, "110"), engine, "BTCUSDT", {
        **no_exit_params, "take_profit_pct": 50, "stop_loss_pct": 50,
    })

    assert all(t.side.value == "BUY" for t in engine.trades)
    assert len(engine.get_lots("BTCUSDT")) == 3


def test_dca_rejected_buy_is_logged_not_raised():
    engine = FifoEngine(initial_cash=Decimal("60"), fee_pct=Decimal("0.001"))
    klines = [_kline(i * 3_600_000, "100") for i in range(3)]

    run_dca(klines, engine, "BTCUSDT",
            params={"amount_per_buy": 50, "frequency_hours": 1, "reference_price": "close"})

    assert len(engine.trades) == 1
    assert engine.cash_balance == Decimal("9.95")


def test_dca_sub_hourly_candles_no_longer_need_an_interval_parameter():
    # This is the scenario that used to require interval_hours and could
    # ZeroDivisionError for interval_hours < 1 (e.g. 5-minute candles).
    # The timestamp-based design has no such parameter or failure mode.
    engine = FifoEngine(initial_cash=Decimal("10000"), fee_pct=Decimal("0.001"))
    klines = [_kline(i * 14_400_000, "100") for i in range(3)]  # 4-hour candles

    run_dca(klines, engine, "BTCUSDT",
            params={"amount_per_buy": 50, "frequency_hours": 1, "reference_price": "close"})

    assert len(engine.trades) == 3  # frequency (1h) < candle spacing (4h) -> buys every candle


def test_step_schedules_by_timestamp_not_call_count():
    engine = FifoEngine(initial_cash=Decimal("10000"), fee_pct=Decimal("0.001"))
    state = DcaState()
    params = {"amount_per_buy": 50, "frequency_hours": 24, "reference_price": "close"}

    step(state, _kline(0, "100"), engine, "BTCUSDT", params)
    step(state, _kline(3_600_000, "100"), engine, "BTCUSDT", params)  # +1h, too soon
    step(state, _kline(86_400_000, "100"), engine, "BTCUSDT", params)  # +24h from last buy

    assert len(engine.trades) == 2
    assert state.last_buy_ms == 86_400_000


def test_dca_state_json_round_trip():
    restored = dca_state_from_json(dca_state_to_json(DcaState(last_buy_ms=12345)))
    assert restored.last_buy_ms == 12345


def test_dca_state_json_round_trip_never_bought():
    restored = dca_state_from_json(dca_state_to_json(DcaState()))
    assert restored.last_buy_ms is None
