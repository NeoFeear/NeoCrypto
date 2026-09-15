from decimal import Decimal

from engine.fifo_engine import FifoEngine, Side


def test_buy_creates_a_lot_and_deducts_cash_with_fee():
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))

    trade = engine.buy(
        timestamp=1000, symbol="BTCUSDT", price=Decimal("100"), quantity=Decimal("5"),
        strategy_name="test",
    )

    assert trade is not None
    assert trade.side == Side.BUY
    assert trade.fee_amount == Decimal("0.5")  # 500 * 0.001
    assert trade.total_cost == Decimal("500.5")  # 500 + 0.5
    assert trade.realized_pnl is None
    assert trade.cash_balance_after == Decimal("499.5")
    assert engine.cash_balance == Decimal("499.5")

    lots = engine.get_lots("BTCUSDT")
    assert len(lots) == 1
    assert lots[0].quantity_restante == Decimal("5")
    assert lots[0].prix_achat == Decimal("100")
    assert lots[0].timestamp_achat == 1000


def test_buy_rejected_when_cash_insufficient():
    engine = FifoEngine(initial_cash=Decimal("50"), fee_pct=Decimal("0.001"))

    trade = engine.buy(
        timestamp=1000, symbol="BTCUSDT", price=Decimal("100"), quantity=Decimal("1"),
        strategy_name="test",
    )

    assert trade is None
    assert engine.cash_balance == Decimal("50")
    assert engine.get_lots("BTCUSDT") == []
    assert engine.trades == []


def test_position_value_sums_remaining_lots_at_current_price():
    engine = FifoEngine(initial_cash=Decimal("10000"), fee_pct=Decimal("0.001"))
    engine.buy(timestamp=1000, symbol="BTCUSDT", price=Decimal("100"), quantity=Decimal("2"), strategy_name="t")
    engine.buy(timestamp=2000, symbol="BTCUSDT", price=Decimal("110"), quantity=Decimal("3"), strategy_name="t")

    value = engine.position_value("BTCUSDT", current_price=Decimal("120"))

    assert value == Decimal("600")  # (2+3) * 120
