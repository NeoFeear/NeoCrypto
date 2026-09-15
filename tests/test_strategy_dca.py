from decimal import Decimal

from engine.fifo_engine import FifoEngine
from engine.strategies.dca import run_dca
from market_data.types import Kline


def _kline(open_time_ms: int, close: str) -> Kline:
    c = Decimal(close)
    return Kline(
        open_time_ms=open_time_ms, open=c, high=c, low=c, close=c,
        volume=Decimal("1"), close_time_ms=open_time_ms + 3_599_999,
    )


def test_dca_buys_every_frequency_hours_starting_at_index_zero():
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    # 25 hourly candles (index 0..24) with frequency_hours=24, interval_hours=1
    # -> buys trigger at index 0 and index 24 only.
    klines = [_kline(i * 3_600_000, "100" if i != 24 else "125") for i in range(25)]

    snapshots = run_dca(
        klines, engine, "BTCUSDT",
        params={"amount_per_buy": 50, "frequency_hours": 24, "reference_price": "close"},
        interval_hours=1,
    )

    assert len(engine.trades) == 2
    trade0, trade24 = engine.trades

    # buy0: price=100, qty=50/100=0.5, gross=50, fee=0.05, total_cost=50.05
    assert trade0.price == Decimal("100")
    assert trade0.quantity == Decimal("0.5")
    assert trade0.total_cost == Decimal("50.05")

    # buy24: price=125, qty=50/125=0.4, gross=50, fee=0.05, total_cost=50.05
    assert trade24.price == Decimal("125")
    assert trade24.quantity == Decimal("0.4")
    assert trade24.total_cost == Decimal("50.05")

    assert engine.cash_balance == Decimal("1000") - Decimal("50.05") - Decimal("50.05")
    assert len(snapshots) == 25


def test_dca_never_sells():
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    klines = [_kline(0, "100"), _kline(3_600_000, "50")]

    run_dca(klines, engine, "BTCUSDT",
            params={"amount_per_buy": 50, "frequency_hours": 24, "reference_price": "close"},
            interval_hours=1)

    assert all(t.side.value == "BUY" for t in engine.trades)


def test_dca_rejected_buy_is_logged_not_raised():
    # cash runs out; later scheduled buys are silently rejected by the engine (spec: no leverage)
    engine = FifoEngine(initial_cash=Decimal("60"), fee_pct=Decimal("0.001"))
    klines = [_kline(i * 3_600_000, "100") for i in range(3)]

    run_dca(klines, engine, "BTCUSDT",
            params={"amount_per_buy": 50, "frequency_hours": 1, "reference_price": "close"},
            interval_hours=1)

    # buy0: total_cost=50.05, cash=60-50.05=9.95 ; buy1,buy2: total_cost=50.05 > 9.95, rejected
    assert len(engine.trades) == 1
    assert engine.cash_balance == Decimal("9.95")


def test_dca_frequency_shorter_than_interval_buys_every_candle_no_crash():
    engine = FifoEngine(initial_cash=Decimal("10000"), fee_pct=Decimal("0.001"))
    klines = [_kline(i * 14_400_000, "100") for i in range(3)]  # 4h candles

    run_dca(klines, engine, "BTCUSDT",
            params={"amount_per_buy": 50, "frequency_hours": 1, "reference_price": "close"},
            interval_hours=4)

    assert len(engine.trades) == 3  # clamped to 1 -> buys every candle, no crash
