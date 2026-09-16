import sqlite3
from decimal import Decimal

from engine.fifo_engine import Lot, Trade
from models import PortfolioSnapshot


def insert_trade(conn: sqlite3.Connection, trade: Trade, commit: bool = True) -> int:
    cursor = conn.execute(
        """
        INSERT INTO trades
            (timestamp, symbol, side, price, quantity, fee_pct, fee_amount,
             total_cost, realized_pnl, cash_balance_after, strategy_name)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            trade.timestamp, trade.symbol, trade.side.value, str(trade.price),
            str(trade.quantity), str(trade.fee_pct), str(trade.fee_amount),
            str(trade.total_cost),
            str(trade.realized_pnl) if trade.realized_pnl is not None else None,
            str(trade.cash_balance_after), trade.strategy_name,
        ),
    )
    if commit:
        conn.commit()
    return cursor.lastrowid


def insert_lot(
    conn: sqlite3.Connection, trade_id: int, symbol: str,
    quantity_restante: Decimal, prix_achat: Decimal, timestamp_achat: int,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO lots (trade_id_achat, symbol, quantity_restante, prix_achat, timestamp_achat)
        VALUES (?, ?, ?, ?, ?)
        """,
        (trade_id, symbol, str(quantity_restante), str(prix_achat), timestamp_achat),
    )
    conn.commit()
    return cursor.lastrowid


def insert_snapshot(conn: sqlite3.Connection, snapshot: PortfolioSnapshot, commit: bool = True) -> None:
    conn.execute(
        """
        INSERT INTO portfolio_snapshots
            (timestamp, symbol, cash_balance, position_value, total_value,
             unrealized_pnl, realized_pnl_cumule)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot.timestamp, snapshot.symbol, str(snapshot.cash_balance),
            str(snapshot.position_value), str(snapshot.total_value),
            str(snapshot.unrealized_pnl), str(snapshot.realized_pnl_cumule),
        ),
    )
    if commit:
        conn.commit()


def replace_lots_for_symbol(conn: sqlite3.Connection, symbol: str, lots: list[Lot], commit: bool = True) -> None:
    conn.execute("DELETE FROM lots WHERE symbol = ?", (symbol,))
    for lot in lots:
        conn.execute(
            """
            INSERT INTO lots (trade_id_achat, symbol, quantity_restante, prix_achat, timestamp_achat)
            VALUES (?, ?, ?, ?, ?)
            """,
            (lot.trade_id_achat, symbol, str(lot.quantity_restante), str(lot.prix_achat), lot.timestamp_achat),
        )
    if commit:
        conn.commit()


def get_engine_state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM engine_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row is not None else None


def set_engine_state(conn: sqlite3.Connection, key: str, value: str, commit: bool = True) -> None:
    conn.execute(
        "INSERT INTO engine_state (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    if commit:
        conn.commit()
