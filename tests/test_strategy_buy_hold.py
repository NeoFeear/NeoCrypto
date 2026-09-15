from decimal import Decimal

from engine.fifo_engine import FifoEngine
from engine.strategies.buy_hold import run_buy_hold
from market_data.types import Kline


def _kline(open_time_ms: int, close: str) -> Kline:
    c = Decimal(close)
    return Kline(
        open_time_ms=open_time_ms, open=c, high=c, low=c, close=c,
        volume=Decimal("1"), close_time_ms=open_time_ms + 3_599_999,
    )


def test_buy_hold_invests_all_cash_on_first_candle_only():
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    klines = [_kline(0, "100"), _kline(3_600_000, "110"), _kline(7_200_000, "120")]

    snapshots = run_buy_hold(klines, engine, "BTCUSDT", params={"invest_at": "start"})

    assert len(engine.trades) == 1
    trade = engine.trades[0]
    assert trade.price == Decimal("100")
    # qty = (1000 / 1.001) / 100 ; total_cost lands back on exactly 1000
    assert trade.total_cost == Decimal("1000.000000000000000000000000")
    assert engine.cash_balance == Decimal("0E-24")
    assert len(snapshots) == 3
    # total_value tracks the position's mark-to-market on later candles
    assert snapshots[1].total_value > snapshots[0].total_value  # price rose 100->110
    assert snapshots[2].symbol == "BTCUSDT"


def test_buy_hold_never_sells():
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    klines = [_kline(0, "100"), _kline(3_600_000, "90")]

    run_buy_hold(klines, engine, "BTCUSDT", params={"invest_at": "start"})

    assert all(t.side.value == "BUY" for t in engine.trades)
