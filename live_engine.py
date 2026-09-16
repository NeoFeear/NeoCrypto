import logging
import sqlite3
import time
from decimal import Decimal
from typing import Callable

import httpx

from config import load_config
from db.migrate import init_db
from db.repository import get_engine_state, insert_snapshot, insert_trade, replace_lots_for_symbol, set_engine_state
from engine.fifo_engine import FifoEngine, Lot
from engine.strategies import base as strategy_base
from engine.strategies.buy_hold import BuyHoldState, buy_hold_state_from_json, buy_hold_state_to_json
from engine.strategies.buy_hold import step as buy_hold_step
from engine.strategies.dca import DcaState, dca_state_from_json, dca_state_to_json
from engine.strategies.dca import step as dca_step
from engine.strategies.grid import GridState, build_grid_state, grid_state_from_json, grid_state_to_json
from engine.strategies.grid import step_grid_live
from market_data.factory import build_provider
from market_data.provider import MarketDataProvider
from market_data.types import Kline

logger = logging.getLogger(__name__)

_STATE_LOADERS = {
    "buy_hold": (buy_hold_state_from_json, lambda: BuyHoldState()),
    "dca": (dca_state_from_json, lambda: DcaState()),
    "grid": None,  # grid needs params to build a fresh state -- handled specially below
}
_STATE_DUMPERS = {
    "buy_hold": buy_hold_state_to_json,
    "dca": dca_state_to_json,
    "grid": grid_state_to_json,
}


def _load_state(conn: sqlite3.Connection, symbol: str, strategy_type: str, params: dict):
    key = f"strategy_state:{symbol}:{strategy_type}"
    raw = get_engine_state(conn, key)
    if strategy_type == "grid":
        return grid_state_from_json(raw) if raw is not None else build_grid_state(params)
    loader, factory = _STATE_LOADERS[strategy_type]
    return loader(raw) if raw is not None else factory()


def _save_state(conn: sqlite3.Connection, symbol: str, strategy_type: str, state) -> None:
    key = f"strategy_state:{symbol}:{strategy_type}"
    set_engine_state(conn, key, _STATE_DUMPERS[strategy_type](state))


def _last_processed_key(symbol: str, strategy_type: str) -> str:
    return f"last_ts:{symbol}:{strategy_type}"


def run_cycle(
    conn: sqlite3.Connection,
    provider: MarketDataProvider,
    engine: FifoEngine,
    symbol: str,
    strategy_type: str,
    params: dict,
    poll_interval: str,
    trade_id_map: dict[int, int],
    fetch_fn=None,
) -> None:
    klines = fetch_fn() if fetch_fn is not None else provider.get_klines(symbol, poll_interval, 0, 0, limit=1)
    if not klines:
        logger.debug("Aucune bougie recue pour %s, cycle ignore.", symbol)
        return
    k = klines[-1]

    last_ts_raw = get_engine_state(conn, _last_processed_key(symbol, strategy_type))
    if last_ts_raw is not None and int(last_ts_raw) >= k.open_time_ms:
        logger.debug("Bougie deja traitee pour %s/%s (ts=%s), cycle ignore.", symbol, strategy_type, k.open_time_ms)
        return

    trades_before = len(engine.trades)

    state = _load_state(conn, symbol, strategy_type, params)
    if strategy_type == "buy_hold":
        buy_hold_step(state, k, engine, symbol, params)
    elif strategy_type == "dca":
        dca_step(state, k, engine, symbol, params)
    elif strategy_type == "grid":
        step_grid_live(state, k.close, k.open_time_ms, engine, symbol, params)
    else:
        raise ValueError(f"strategie inconnue: {strategy_type}")

    for trade in engine.trades[trades_before:]:
        db_trade_id = insert_trade(conn, trade)
        trade_id_map[trade.id] = db_trade_id

    # Resync lots from the engine's own state rather than tracking "which lot is
    # new" per trade: a BUY creates a lot, a SELL partially or fully consumes one,
    # and a single grid cycle can do several BUYs at once (D3) -- diffing
    # individual Lot objects across multiple same-cycle inserts is fragile and
    # gets the trade/lot association wrong. engine.get_lots() is the single
    # source of truth; just mirror it.
    #
    # `Lot.trade_id_achat` is FifoEngine's own private in-memory trade counter,
    # not the DB's real trades.id -- translate every lot's trade_id_achat through
    # trade_id_map before persisting. A lot reconstructed from the DB already
    # carries a real DB id in trade_id_achat and was never entered into
    # trade_id_map this process, so `.get(x, x)` correctly leaves it untouched.
    translated_lots = [
        Lot(
            id=lot.id, symbol=lot.symbol, quantity_restante=lot.quantity_restante,
            prix_achat=lot.prix_achat, timestamp_achat=lot.timestamp_achat,
            trade_id_achat=trade_id_map.get(lot.trade_id_achat, lot.trade_id_achat),
        )
        for lot in engine.get_lots(symbol)
    ]
    replace_lots_for_symbol(conn, symbol, translated_lots)

    snapshot = strategy_base.build_snapshot(engine, symbol, k.close, k.open_time_ms)
    insert_snapshot(conn, snapshot)

    _save_state(conn, symbol, strategy_type, state)
    set_engine_state(conn, _last_processed_key(symbol, strategy_type), str(k.open_time_ms))


def reconstruct_engine_from_db(
    conn: sqlite3.Connection, symbol: str, initial_cash: Decimal, fee_pct: Decimal
) -> FifoEngine:
    """Rebuild cash_balance and open lots from the DB so a restart genuinely
    resumes the portfolio, not just each strategy's own state. Safe to call on
    a symbol with no history yet (returns a fresh engine at initial_cash)."""
    engine = FifoEngine(initial_cash=initial_cash, fee_pct=fee_pct)

    last_trade = conn.execute(
        "SELECT cash_balance_after FROM trades WHERE symbol = ? ORDER BY id DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    if last_trade is not None:
        engine.cash_balance = Decimal(last_trade["cash_balance_after"])

    lot_rows = conn.execute(
        "SELECT * FROM lots WHERE symbol = ? ORDER BY timestamp_achat", (symbol,)
    ).fetchall()
    engine.lots = [
        Lot(
            id=row["id"], trade_id_achat=row["trade_id_achat"], symbol=row["symbol"],
            quantity_restante=Decimal(row["quantity_restante"]),
            prix_achat=Decimal(row["prix_achat"]), timestamp_achat=row["timestamp_achat"],
        )
        for row in lot_rows
    ]

    # Seed the internal trade and lot id counters past the highest ids already in
    # use in the DB. Without this, a post-restart engine's first new trade will get
    # internal id=1, which collides with any pre-restart trade that also has id=1,
    # causing trade_id_map.get(1, ...) to incorrectly translate old lots' fks to the
    # new trade's real db id, silently corrupting open positions across restarts.
    max_trade_id_row = conn.execute("SELECT MAX(id) AS max_id FROM trades").fetchone()
    engine._next_trade_id = (max_trade_id_row["max_id"] or 0) + 1

    max_lot_id_row = conn.execute("SELECT MAX(id) AS max_id FROM lots").fetchone()
    engine._next_lot_id = (max_lot_id_row["max_id"] or 0) + 1

    return engine


_RETRY_BACKOFF_SECONDS = [1, 4]


def fetch_with_retry(
    provider: MarketDataProvider, symbol: str, poll_interval: str,
    on_critical_failure: Callable[[str], None],
) -> list[Kline]:
    """Spec section 5: 1 retry with exponential backoff (1s, then 4s); after 3
    consecutive failures, calls on_critical_failure and returns [] rather than
    raising -- a bad cycle must never crash the polling loop."""
    attempts = 0
    last_error: Exception | None = None
    while attempts < 3:
        try:
            return provider.get_klines(symbol, poll_interval, 0, 0, limit=1)
        except httpx.HTTPError as e:
            last_error = e
            attempts += 1
            if attempts < 3:
                time.sleep(_RETRY_BACKOFF_SECONDS[attempts - 1])
    on_critical_failure(f"Echec API repete pour {symbol} apres 3 tentatives: {last_error}")
    return []


def run_polling_loop(
    conn: sqlite3.Connection,
    provider: MarketDataProvider,
    engine: FifoEngine,
    symbol: str,
    strategy_type: str,
    params: dict,
    poll_interval: str,
    poll_interval_seconds: int,
    on_critical_failure: Callable[[str], None],
    max_cycles: int | None = None,
) -> None:
    # Owned here, for the lifetime of this process: see Task 6's interface notes
    # on why trade_id_map is never persisted to the DB.
    trade_id_map: dict[int, int] = {}
    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        run_cycle(
            conn, provider, engine, symbol, strategy_type, params, poll_interval, trade_id_map,
            fetch_fn=lambda: fetch_with_retry(provider, symbol, poll_interval, on_critical_failure),
        )
        cycles += 1
        if max_cycles is None or cycles < max_cycles:
            time.sleep(poll_interval_seconds)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    provider = build_provider(cfg.data_source)
    conn = init_db("crypto_sim.db")

    symbol = cfg.live.active_symbol
    strategy_type = cfg.live.active_strategy
    params = cfg.strategy_defaults[strategy_type]

    # Rebuilds cash_balance + open lots from the DB (Task 6) so a restart
    # genuinely resumes the portfolio, not just each strategy's own state.
    engine = reconstruct_engine_from_db(
        conn, symbol, initial_cash=cfg.backtest.initial_capital, fee_pct=cfg.fees.default_fee_pct
    )

    def on_critical_failure(message: str) -> None:
        logger.critical(message)

    logger.info("Demarrage du moteur live: %s / %s", symbol, strategy_type)
    run_polling_loop(
        conn, provider, engine, symbol, strategy_type, params,
        poll_interval=cfg.live.poll_kline_interval,
        poll_interval_seconds=cfg.live.poll_interval_seconds,
        on_critical_failure=on_critical_failure,
    )


if __name__ == "__main__":
    main()
