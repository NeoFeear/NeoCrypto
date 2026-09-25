import sqlite3
from decimal import Decimal

from engine.fifo_engine import Lot, Side, Trade
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


def starting_capital(conn: sqlite3.Connection, symbol: str, default: Decimal) -> Decimal:
    """The cash this symbol's live portfolio actually started with.

    reconstruct_engine_from_db continues a pair's cash from its last trade, so
    a pair keeps the capital it started with even when live.pairs later grows
    or shrinks and live.capital_per_pair changes. Returns must be measured
    against that real starting cash, derived here from the symbol's first
    trade (cash after it, plus what it cost for a BUY); `default`
    (live.capital_per_pair) only applies to a pair that has not traded yet."""
    first = conn.execute(
        "SELECT side, total_cost, cash_balance_after FROM trades WHERE symbol = ? ORDER BY id LIMIT 1",
        (symbol,),
    ).fetchone()
    if first is None:
        return default
    after, cost = Decimal(first["cash_balance_after"]), Decimal(first["total_cost"])
    return after + cost if first["side"] == Side.BUY.value else after - cost


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


def _row_to_trade(row: sqlite3.Row) -> Trade:
    return Trade(
        id=row["id"], timestamp=row["timestamp"], symbol=row["symbol"],
        side=Side(row["side"]), price=Decimal(row["price"]), quantity=Decimal(row["quantity"]),
        fee_pct=Decimal(row["fee_pct"]), fee_amount=Decimal(row["fee_amount"]),
        total_cost=Decimal(row["total_cost"]),
        realized_pnl=Decimal(row["realized_pnl"]) if row["realized_pnl"] is not None else None,
        cash_balance_after=Decimal(row["cash_balance_after"]), strategy_name=row["strategy_name"],
    )


def list_trades(conn: sqlite3.Connection, symbol: str | None = None) -> list[Trade]:
    if symbol is not None:
        rows = conn.execute("SELECT * FROM trades WHERE symbol = ? ORDER BY id DESC", (symbol,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM trades ORDER BY id DESC").fetchall()
    return [_row_to_trade(row) for row in rows]


def list_snapshots(conn: sqlite3.Connection, symbol: str) -> list[PortfolioSnapshot]:
    rows = conn.execute(
        "SELECT * FROM portfolio_snapshots WHERE symbol = ? ORDER BY timestamp ASC", (symbol,)
    ).fetchall()
    return [
        PortfolioSnapshot(
            timestamp=row["timestamp"], symbol=row["symbol"],
            cash_balance=Decimal(row["cash_balance"]), position_value=Decimal(row["position_value"]),
            total_value=Decimal(row["total_value"]), unrealized_pnl=Decimal(row["unrealized_pnl"]),
            realized_pnl_cumule=Decimal(row["realized_pnl_cumule"]),
        )
        for row in rows
    ]


def list_symbols_with_trades(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT DISTINCT symbol FROM trades ORDER BY symbol").fetchall()
    return [row["symbol"] for row in rows]
