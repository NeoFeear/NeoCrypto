from decimal import Decimal

import pytest

from db.migrate import init_db
from db.repository import (
    get_engine_state,
    insert_lot,
    insert_snapshot,
    insert_trade,
    replace_lots_for_symbol,
    set_engine_state,
)
from engine.fifo_engine import Lot, Side, Trade
from models import PortfolioSnapshot


@pytest.fixture
def conn(tmp_path):
    c = init_db(str(tmp_path / "test.db"))
    yield c
    c.close()


def _trade(side=Side.BUY, realized_pnl=None) -> Trade:
    return Trade(
        id=0, timestamp=1000, symbol="BTCUSDT", side=side, price=Decimal("100"),
        quantity=Decimal("1"), fee_pct=Decimal("0.001"), fee_amount=Decimal("0.1"),
        total_cost=Decimal("100.1"), realized_pnl=realized_pnl,
        cash_balance_after=Decimal("899.9"), strategy_name="dca",
    )


def test_insert_trade_returns_new_id_and_round_trips_decimals(conn):
    trade_id = insert_trade(conn, _trade())

    row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    assert row["symbol"] == "BTCUSDT"
    assert row["side"] == "BUY"
    assert Decimal(row["price"]) == Decimal("100")
    assert Decimal(row["total_cost"]) == Decimal("100.1")
    assert row["realized_pnl"] is None


def test_insert_trade_sell_stores_realized_pnl(conn):
    trade_id = insert_trade(conn, _trade(side=Side.SELL, realized_pnl=Decimal("59.74")))

    row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    assert Decimal(row["realized_pnl"]) == Decimal("59.74")


def test_insert_lot_round_trips_decimals(conn):
    trade_id = insert_trade(conn, _trade())

    lot_id = insert_lot(
        conn, trade_id=trade_id, symbol="BTCUSDT",
        quantity_restante=Decimal("1"), prix_achat=Decimal("100"), timestamp_achat=1000,
    )

    row = conn.execute("SELECT * FROM lots WHERE id = ?", (lot_id,)).fetchone()
    assert row["trade_id_achat"] == trade_id
    assert Decimal(row["quantity_restante"]) == Decimal("1")
    assert Decimal(row["prix_achat"]) == Decimal("100")


def test_insert_snapshot_round_trips_decimals(conn):
    snapshot = PortfolioSnapshot(
        timestamp=2000, symbol="BTCUSDT", cash_balance=Decimal("899.9"),
        position_value=Decimal("110"), total_value=Decimal("1009.9"),
        unrealized_pnl=Decimal("10"), realized_pnl_cumule=Decimal("0"),
    )

    insert_snapshot(conn, snapshot)

    row = conn.execute("SELECT * FROM portfolio_snapshots").fetchone()
    assert Decimal(row["total_value"]) == Decimal("1009.9")
    assert row["timestamp"] == 2000


def test_engine_state_set_then_get_round_trips():
    conn = init_db(":memory:")
    assert get_engine_state(conn, "last_ts:BTCUSDT:dca") is None

    set_engine_state(conn, "last_ts:BTCUSDT:dca", "123456")
    assert get_engine_state(conn, "last_ts:BTCUSDT:dca") == "123456"


def test_engine_state_set_overwrites_existing_key():
    conn = init_db(":memory:")
    set_engine_state(conn, "k", "v1")
    set_engine_state(conn, "k", "v2")
    assert get_engine_state(conn, "k") == "v2"


def test_replace_lots_for_symbol_deletes_old_rows_and_inserts_current_ones(conn):
    trade_id = insert_trade(conn, _trade())
    insert_lot(conn, trade_id=trade_id, symbol="BTCUSDT",
               quantity_restante=Decimal("5"), prix_achat=Decimal("90"), timestamp_achat=500)

    current_lots = [
        Lot(id=1, trade_id_achat=trade_id, symbol="BTCUSDT",
            quantity_restante=Decimal("2"), prix_achat=Decimal("100"), timestamp_achat=1000),
        Lot(id=2, trade_id_achat=trade_id, symbol="BTCUSDT",
            quantity_restante=Decimal("3"), prix_achat=Decimal("110"), timestamp_achat=2000),
    ]

    replace_lots_for_symbol(conn, "BTCUSDT", current_lots)

    rows = conn.execute("SELECT * FROM lots WHERE symbol = ? ORDER BY timestamp_achat", ("BTCUSDT",)).fetchall()
    assert len(rows) == 2  # the stale pre-existing lot (price 90) is gone
    assert Decimal(rows[0]["prix_achat"]) == Decimal("100")
    assert Decimal(rows[0]["quantity_restante"]) == Decimal("2")
    assert Decimal(rows[1]["prix_achat"]) == Decimal("110")
    assert Decimal(rows[1]["quantity_restante"]) == Decimal("3")


def test_replace_lots_for_symbol_with_empty_list_clears_all_lots(conn):
    trade_id = insert_trade(conn, _trade())
    insert_lot(conn, trade_id=trade_id, symbol="BTCUSDT",
               quantity_restante=Decimal("1"), prix_achat=Decimal("100"), timestamp_achat=1000)

    replace_lots_for_symbol(conn, "BTCUSDT", [])

    rows = conn.execute("SELECT * FROM lots WHERE symbol = ?", ("BTCUSDT",)).fetchall()
    assert rows == []


def test_replace_lots_for_symbol_does_not_touch_other_symbols(conn):
    trade_id = insert_trade(conn, _trade())
    insert_lot(conn, trade_id=trade_id, symbol="ETHUSDT",
               quantity_restante=Decimal("1"), prix_achat=Decimal("50"), timestamp_achat=1000)

    replace_lots_for_symbol(conn, "BTCUSDT", [])

    rows = conn.execute("SELECT * FROM lots WHERE symbol = ?", ("ETHUSDT",)).fetchall()
    assert len(rows) == 1
