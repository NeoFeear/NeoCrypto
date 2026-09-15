import logging
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


def test_buy_rejected_when_cash_insufficient(caplog):
    engine = FifoEngine(initial_cash=Decimal("50"), fee_pct=Decimal("0.001"))

    with caplog.at_level(logging.WARNING):
        trade = engine.buy(
            timestamp=1000, symbol="BTCUSDT", price=Decimal("100"), quantity=Decimal("1"),
            strategy_name="test",
        )
    assert any(record.levelname == "WARNING" for record in caplog.records)

    assert trade is None
    assert engine.cash_balance == Decimal("50")
    assert engine.get_lots("BTCUSDT") == []
    assert engine.trades == []


def test_buy_exact_affordability_consumes_all_cash_balance_exactly():
    """BUY where total_cost exactly equals cash_balance should SUCCEED (not reject).

    Numbers: cash=100.1, price=100, quantity=1, fee_pct=0.001
    gross = 100, fee = 0.1, total_cost = 100.1, cash_after = 0
    """
    engine = FifoEngine(initial_cash=Decimal("100.1"), fee_pct=Decimal("0.001"))

    trade = engine.buy(
        timestamp=1000, symbol="BTCUSDT", price=Decimal("100"), quantity=Decimal("1"),
        strategy_name="test",
    )

    assert trade is not None
    assert trade.side == Side.BUY
    assert trade.fee_amount == Decimal("0.1")  # 100 * 0.001
    assert trade.total_cost == Decimal("100.1")  # 100 + 0.1
    assert trade.cash_balance_after == Decimal("0")
    assert engine.cash_balance == Decimal("0")


def test_position_value_sums_remaining_lots_at_current_price():
    engine = FifoEngine(initial_cash=Decimal("10000"), fee_pct=Decimal("0.001"))
    engine.buy(timestamp=1000, symbol="BTCUSDT", price=Decimal("100"), quantity=Decimal("2"), strategy_name="t")
    engine.buy(timestamp=2000, symbol="BTCUSDT", price=Decimal("110"), quantity=Decimal("3"), strategy_name="t")

    value = engine.position_value("BTCUSDT", current_price=Decimal("120"))

    assert value == Decimal("600")  # (2+3) * 120


def test_buy_then_partial_sell_computes_realized_pnl_and_leaves_remainder():
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    engine.buy(timestamp=1000, symbol="BTCUSDT", price=Decimal("100"), quantity=Decimal("5"), strategy_name="t")

    trade = engine.sell(
        timestamp=2000, symbol="BTCUSDT", price=Decimal("120"), quantity=Decimal("2"),
        strategy_name="t",
    )

    assert trade is not None
    assert trade.side == Side.SELL
    assert trade.fee_amount == Decimal("0.24")  # 240 * 0.001
    assert trade.total_cost == Decimal("239.76")  # 240 - 0.24 (net proceeds)
    assert trade.realized_pnl == Decimal("39.76")  # (120-100)*2 - 0.24
    assert trade.cash_balance_after == Decimal("739.26")  # 499.5 + 239.76
    assert engine.cash_balance == Decimal("739.26")

    lots = engine.get_lots("BTCUSDT")
    assert len(lots) == 1
    assert lots[0].quantity_restante == Decimal("3")


def test_sell_crosses_multiple_lots_fifo_order():
    engine = FifoEngine(initial_cash=Decimal("10000"), fee_pct=Decimal("0.001"))
    engine.buy(timestamp=1000, symbol="BTCUSDT", price=Decimal("100"), quantity=Decimal("3"), strategy_name="t")
    engine.buy(timestamp=2000, symbol="BTCUSDT", price=Decimal("110"), quantity=Decimal("4"), strategy_name="t")

    trade = engine.sell(
        timestamp=3000, symbol="BTCUSDT", price=Decimal("130"), quantity=Decimal("5"),
        strategy_name="t",
    )

    # consumes lot1 fully (3 @ 100) + lot2 partially (2 @ 110)
    # gross pnl = (130-100)*3 + (130-110)*2 = 90 + 40 = 130 ; fee = 650*0.001 = 0.65
    assert trade.realized_pnl == Decimal("129.35")

    lots = engine.get_lots("BTCUSDT")
    assert len(lots) == 1
    assert lots[0].prix_achat == Decimal("110")
    assert lots[0].quantity_restante == Decimal("2")


def test_multiple_sells_across_lots_of_different_ages():
    engine = FifoEngine(initial_cash=Decimal("10000"), fee_pct=Decimal("0.001"))
    engine.buy(timestamp=1000, symbol="BTCUSDT", price=Decimal("100"), quantity=Decimal("2"), strategy_name="t")
    engine.buy(timestamp=2000, symbol="BTCUSDT", price=Decimal("105"), quantity=Decimal("2"), strategy_name="t")
    engine.buy(timestamp=3000, symbol="BTCUSDT", price=Decimal("110"), quantity=Decimal("2"), strategy_name="t")

    sell1 = engine.sell(timestamp=4000, symbol="BTCUSDT", price=Decimal("120"), quantity=Decimal("3"), strategy_name="t")
    # consumes buy1 fully (2@100) + buy2 partial (1@105)
    # gross pnl = (120-100)*2 + (120-105)*1 = 40+15 = 55 ; fee = 360*0.001 = 0.36
    assert sell1.realized_pnl == Decimal("54.64")

    sell2 = engine.sell(timestamp=5000, symbol="BTCUSDT", price=Decimal("115"), quantity=Decimal("2"), strategy_name="t")
    # consumes buy2 remainder (1@105) + buy3 partial (1@110)
    # gross pnl = (115-105)*1 + (115-110)*1 = 10+5 = 15 ; fee = 230*0.001 = 0.23
    assert sell2.realized_pnl == Decimal("14.77")

    lots = engine.get_lots("BTCUSDT")
    assert len(lots) == 1
    assert lots[0].prix_achat == Decimal("110")
    assert lots[0].quantity_restante == Decimal("1")


def test_sell_with_no_lot_available_is_rejected_in_full(caplog):
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))

    with caplog.at_level(logging.WARNING):
        trade = engine.sell(timestamp=1000, symbol="BTCUSDT", price=Decimal("100"), quantity=Decimal("1"), strategy_name="t")
    assert any(record.levelname == "WARNING" for record in caplog.records)

    assert trade is None
    assert engine.cash_balance == Decimal("1000")
    assert engine.trades == []


def test_sell_exceeding_available_quantity_is_rejected_in_full_not_partial(caplog):
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    engine.buy(timestamp=1000, symbol="BTCUSDT", price=Decimal("100"), quantity=Decimal("1"), strategy_name="t")
    cash_after_buy = engine.cash_balance

    with caplog.at_level(logging.WARNING):
        trade = engine.sell(timestamp=2000, symbol="BTCUSDT", price=Decimal("110"), quantity=Decimal("5"), strategy_name="t")
    assert any(record.levelname == "WARNING" for record in caplog.records)

    assert trade is None
    assert engine.cash_balance == cash_after_buy
    lots = engine.get_lots("BTCUSDT")
    assert len(lots) == 1
    assert lots[0].quantity_restante == Decimal("1")
