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
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    klines = [_kline(0, "100"), _kline(3_600_000, "50")]

    run_dca(klines, engine, "BTCUSDT",
            params={"amount_per_buy": 50, "frequency_hours": 24, "reference_price": "close"})

    assert all(t.side.value == "BUY" for t in engine.trades)


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
