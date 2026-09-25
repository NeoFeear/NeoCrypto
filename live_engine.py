import logging
import signal
import sqlite3
import threading
import time
from datetime import datetime
from decimal import Decimal
from typing import Callable
from zoneinfo import ZoneInfo

import httpx

from config import LivePair, load_config
from db.migrate import init_db
from db.repository import get_engine_state, insert_snapshot, insert_trade, replace_lots_for_symbol, set_engine_state
from logutil import RepeatFilter
from discord_notifier import (
    DiscordWebhooks, PairDailySummary, load_discord_webhooks, send_alert, send_log,
    send_portfolio_daily_summary, send_transaction, start_background_delivery, stop_background_delivery,
)
from engine.fifo_engine import FifoEngine, Lot, Side, Trade
from engine.strategies import base as strategy_base
from engine.strategies.buy_hold import BuyHoldState, buy_hold_state_from_json, buy_hold_state_to_json
from engine.strategies.buy_hold import step as buy_hold_step
from engine.strategies.dca import DcaState, dca_state_from_json, dca_state_to_json
from engine.strategies.dca import step as dca_step
from engine.strategies.grid import GridState, build_grid_state, grid_state_from_json, grid_state_to_json
from engine.strategies.grid import step_grid_live
from housekeeping import DAY_MS, aggregate_old_snapshots
from market_data.factory import build_provider
from market_data.provider import INTERVAL_MS, MarketDataProvider
from market_data.types import Kline

logger = logging.getLogger(__name__)


def _handle_shutdown_signal(signum, frame) -> None:
    """SIGTERM (a `systemctl stop`) and SIGINT (Ctrl+C) both request the same
    graceful shutdown. With several pair-worker threads running concurrently
    (one per configured live.pairs entry), a raised exception only ever
    reaches the thread that happened to receive the signal -- in CPython
    that is always the main thread, never the workers -- so it cannot stop
    them. Setting a shared threading.Event instead lets every worker's
    run_polling_loop notice it on its own next cycle boundary and exit its
    while loop cleanly, no exception required."""
    _stop_event.set()


_stop_event = threading.Event()


def install_signal_handlers() -> None:
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    signal.signal(signal.SIGINT, _handle_shutdown_signal)


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


def _load_state(
    conn: sqlite3.Connection, symbol: str, strategy_type: str, params: dict, current_price: Decimal | None = None,
):
    key = f"strategy_state:{symbol}:{strategy_type}"
    raw = get_engine_state(conn, key)
    if strategy_type == "grid":
        # current_price is only ever consulted when params has no explicit
        # lower_bound/upper_bound (the auto-band config) -- see
        # build_grid_state. Existing fixed-bound configs never need it.
        return grid_state_from_json(raw) if raw is not None else build_grid_state(params, current_price)
    loader, factory = _STATE_LOADERS[strategy_type]
    return loader(raw) if raw is not None else factory()


def _save_state(conn: sqlite3.Connection, symbol: str, strategy_type: str, state) -> None:
    key = f"strategy_state:{symbol}:{strategy_type}"
    # commit=False: this is only ever called from inside run_cycle, which commits
    # once at the very end so a cycle is atomic.
    set_engine_state(conn, key, _STATE_DUMPERS[strategy_type](state), commit=False)


def _last_processed_key(symbol: str, strategy_type: str) -> str:
    return f"last_ts:{symbol}:{strategy_type}"


def _fetch_latest_closed_kline(provider: MarketDataProvider, symbol: str, poll_interval: str) -> list[Kline]:
    """Spec line 217 wants the most recent available kline. Passing (0, 0) as the
    window returns nothing from either real provider (Binance treats it as the
    literal epoch window; Kraken's pagination guard drops everything before
    end_ms). Fetch the last two candles over a real window and keep only the one
    that has actually closed, so live trading uses a complete candle rather than
    the still-forming one."""
    interval_ms = INTERVAL_MS[poll_interval]
    now_ms = int(time.time() * 1000)
    klines = provider.get_klines(symbol, poll_interval, now_ms - 2 * interval_ms, now_ms, limit=2)
    closed = [k for k in klines if k.close_time_ms < now_ms]
    return closed[-1:]


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
) -> list[Trade]:
    klines = fetch_fn() if fetch_fn is not None else _fetch_latest_closed_kline(provider, symbol, poll_interval)
    if not klines:
        logger.debug("Aucune bougie recue pour %s, cycle ignore.", symbol)
        return []
    k = klines[-1]

    last_ts_raw = get_engine_state(conn, _last_processed_key(symbol, strategy_type))
    if last_ts_raw is not None and int(last_ts_raw) >= k.open_time_ms:
        logger.debug("Bougie deja traitee pour %s/%s (ts=%s), cycle ignore.", symbol, strategy_type, k.open_time_ms)
        return []

    trades_before = len(engine.trades)

    state = _load_state(conn, symbol, strategy_type, params, current_price=k.close)
    if strategy_type == "buy_hold":
        buy_hold_step(state, k, engine, symbol, params)
    elif strategy_type == "dca":
        dca_step(state, k, engine, symbol, params)
    elif strategy_type == "grid":
        step_grid_live(state, k.close, k.open_time_ms, engine, symbol, params, high=k.high, low=k.low)
    else:
        raise ValueError(f"strategie inconnue: {strategy_type}")

    new_trades = engine.trades[trades_before:]
    for trade in new_trades:
        db_trade_id = insert_trade(conn, trade, commit=False)
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
    replace_lots_for_symbol(conn, symbol, translated_lots, commit=False)

    snapshot = strategy_base.build_snapshot(engine, symbol, k.close, k.open_time_ms)
    insert_snapshot(conn, snapshot, commit=False)

    _save_state(conn, symbol, strategy_type, state)
    set_engine_state(conn, _last_processed_key(symbol, strategy_type), str(k.open_time_ms), commit=False)

    # One cycle is atomic: everything above runs with commit=False, so a crash
    # before this point leaves nothing durably committed (no trade recorded
    # without its matching "processed" marker), and a crash never replays a
    # signal on the next cycle.
    conn.commit()
    # Only report trades as "happened" after the commit above succeeds --
    # returning new_trades before this point (or reporting them if commit()
    # were to raise) would let the caller announce a trade to Discord that
    # never actually became durable.
    return new_trades


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

    # Also rebuild engine.trades, oldest-first (matching how FifoEngine.buy()/sell()
    # append to self.trades) -- without this, FifoEngine.realized_pnl_cumule()
    # silently reports 0 after every restart since it sums over self.trades, which
    # would otherwise be empty, corrupting the permanent portfolio_snapshots record
    # with a discontinuity on every restart.
    trade_rows = conn.execute(
        "SELECT * FROM trades WHERE symbol = ? ORDER BY id", (symbol,)
    ).fetchall()
    engine.trades = [
        Trade(
            id=row["id"], timestamp=row["timestamp"], symbol=row["symbol"],
            side=Side(row["side"]), price=Decimal(row["price"]), quantity=Decimal(row["quantity"]),
            fee_pct=Decimal(row["fee_pct"]), fee_amount=Decimal(row["fee_amount"]),
            total_cost=Decimal(row["total_cost"]),
            realized_pnl=Decimal(row["realized_pnl"]) if row["realized_pnl"] is not None else None,
            cash_balance_after=Decimal(row["cash_balance_after"]), strategy_name=row["strategy_name"],
        )
        for row in trade_rows
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
    max_attempts = len(_RETRY_BACKOFF_SECONDS) + 1
    attempts = 0
    last_error: Exception | None = None
    while attempts < max_attempts:
        try:
            return _fetch_latest_closed_kline(provider, symbol, poll_interval)
        except httpx.HTTPError as e:
            last_error = e
            attempts += 1
            if attempts < max_attempts:
                time.sleep(_RETRY_BACKOFF_SECONDS[attempts - 1])
    on_critical_failure(f"Echec API repete pour {symbol} apres 3 tentatives: {last_error}")
    return []


def _check_drawdown_alert(
    conn: sqlite3.Connection, symbol: str, strategy_type: str, total_value: Decimal,
    threshold_pct: Decimal, webhook_url: str,
) -> None:
    peak_key = f"portfolio_peak_value:{symbol}:{strategy_type}"
    active_key = f"drawdown_alert_active:{symbol}:{strategy_type}"

    peak_raw = get_engine_state(conn, peak_key)
    peak = Decimal(peak_raw) if peak_raw is not None else total_value
    if total_value > peak:
        peak = total_value
    set_engine_state(conn, peak_key, str(peak), commit=False)

    if peak <= 0:
        conn.commit()
        return
    drawdown_pct = (peak - total_value) / peak * Decimal(100)
    was_active = get_engine_state(conn, active_key) == "1"

    should_alert = drawdown_pct > threshold_pct and not was_active
    if should_alert:
        set_engine_state(conn, active_key, "1", commit=False)
    elif drawdown_pct <= threshold_pct and was_active:
        set_engine_state(conn, active_key, "0", commit=False)
    # Commit the state BEFORE sending -- send_alert cannot report success/
    # failure back to us, so there is nothing to gain by waiting until after
    # the HTTP call, and everything to lose: a crash or a later exception in
    # this same cycle (e.g. _check_global_daily_summary raising) used to roll back
    # this flag via the caller's deferred commit, even though the alert had
    # already been delivered -- causing it to re-fire every subsequent
    # cycle. Committing first means a failure after this point can only
    # produce a missed alert, never a duplicate one.
    conn.commit()

    if should_alert:
        send_alert(webhook_url, "drawdown", f"Drawdown de {drawdown_pct:.2f}% pour {symbol}", severity="warning")


_PARIS_TZ = ZoneInfo("Europe/Paris")
_DAILY_SUMMARY_HOUR = 8


def _check_global_daily_summary(
    conn: sqlite3.Connection, pairs: list[LivePair], webhook_url: str,
    now_ms: int, capital_per_pair: Decimal,
) -> None:
    """Consolidated replacement for the old per-pair _check_daily_summary:
    Florian asked for ONE Discord message covering the whole portfolio
    (global evolution + every currency detailed) instead of 1 separate
    message per live.pairs entry. Only the "leader" pair's worker thread
    calls this (see run_pair_worker/run_polling_loop's summary_pairs param)
    so exactly one thread ever evaluates it per cycle, even with several
    pair-worker threads polling concurrently -- reusing the same date/
    message-id engine_state pattern as before, just under a single global
    key instead of one key per (symbol, strategy_type)."""
    now_paris = datetime.fromtimestamp(now_ms / 1000, tz=_PARIS_TZ)
    if now_paris.hour < _DAILY_SUMMARY_HOUR:
        return
    today_str = now_paris.date().isoformat()

    date_key = "discord_daily_summary_date:global"
    message_id_key = "discord_daily_summary_message_id:global"

    sent_date = get_engine_state(conn, date_key)
    existing_message_id = get_engine_state(conn, message_id_key) if sent_date == today_str else None

    cutoff_24h_ms = now_ms - DAY_MS
    rows: list[PairDailySummary] = []
    rows_with_24h_history: list[tuple[Decimal, Decimal]] = []  # (value_now, value_24h_ago)
    for pair in pairs:
        latest_snapshot = conn.execute(
            "SELECT * FROM portfolio_snapshots WHERE symbol = ? ORDER BY id DESC LIMIT 1", (pair.symbol,)
        ).fetchone()
        if latest_snapshot is None:
            continue

        total_value = Decimal(latest_snapshot["total_value"])
        return_pct_since_start = (
            (total_value - capital_per_pair) / capital_per_pair * Decimal(100) if capital_per_pair > 0 else Decimal(0)
        )

        snapshot_24h_ago = conn.execute(
            "SELECT total_value FROM portfolio_snapshots WHERE symbol = ? AND timestamp <= ? "
            "ORDER BY timestamp DESC LIMIT 1",
            (pair.symbol, cutoff_24h_ms),
        ).fetchone()
        return_pct_24h = None
        if snapshot_24h_ago is not None:
            value_24h_ago = Decimal(snapshot_24h_ago["total_value"])
            if value_24h_ago > 0:
                return_pct_24h = (total_value - value_24h_ago) / value_24h_ago * Decimal(100)
                rows_with_24h_history.append((total_value, value_24h_ago))

        rows.append(PairDailySummary(
            symbol=pair.symbol, total_value=total_value,
            return_pct_since_start=return_pct_since_start, return_pct_24h=return_pct_24h,
        ))

    if not rows:
        return  # no pair has any snapshot history yet -- nothing to summarize

    portfolio_total_value = sum((row.total_value for row in rows), Decimal(0))
    total_capital = capital_per_pair * Decimal(len(pairs))
    return_pct = (
        (portfolio_total_value - total_capital) / total_capital * Decimal(100) if total_capital > 0 else Decimal(0)
    )

    return_pct_24h = None
    if rows_with_24h_history:
        # Only pairs with their own 24h-old snapshot contribute to either side of
        # this ratio -- mixing in a pair's current value without its matching
        # past value would skew the global 24h delta, not just omit that pair.
        current_sum = sum((now for now, _ in rows_with_24h_history), Decimal(0))
        past_sum = sum((past for _, past in rows_with_24h_history), Decimal(0))
        if past_sum > 0:
            return_pct_24h = (current_sum - past_sum) / past_sum * Decimal(100)

    new_message_id = send_portfolio_daily_summary(
        webhook_url, pairs=rows, total_value=portfolio_total_value, total_capital=total_capital,
        return_pct=return_pct, return_pct_24h=return_pct_24h, existing_message_id=existing_message_id,
    )
    if new_message_id is not None:
        # Commit immediately, not deferred to the caller: send_portfolio_daily_summary's
        # HTTP call already happened above, so a crash or exception between
        # here and the caller's own later commit would leave a real Discord
        # message with no durable record of it -- causing the next cycle to
        # POST a second message instead of correctly PATCHing this one,
        # violating the spec's "jamais duplique" requirement. Committing
        # here shrinks that window to just these two set_engine_state calls.
        set_engine_state(conn, date_key, today_str, commit=False)
        set_engine_state(conn, message_id_key, new_message_id, commit=False)
        conn.commit()


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
    initial_cash: Decimal,
    discord_webhooks: DiscordWebhooks,
    drawdown_threshold_pct: Decimal,
    max_cycles: int | None = None,
    retention_days: int | None = None,
    stop_event: threading.Event | None = None,
    summary_pairs: list[LivePair] | None = None,
) -> None:
    # Owned here, for the lifetime of this process: see Task 6's interface notes
    # on why trade_id_map is never persisted to the DB.
    trade_id_map: dict[int, int] = {}
    cycles = 0
    last_housekeeping_day: int | None = None
    while (max_cycles is None or cycles < max_cycles) and (stop_event is None or not stop_event.is_set()):
        try:
            committed_trades = run_cycle(
                conn, provider, engine, symbol, strategy_type, params, poll_interval, trade_id_map,
                fetch_fn=lambda: fetch_with_retry(provider, symbol, poll_interval, on_critical_failure),
            )
            for trade in committed_trades:
                send_transaction(discord_webhooks.transactions, trade, initial_cash=initial_cash)

            latest_snapshot = conn.execute(
                "SELECT total_value FROM portfolio_snapshots WHERE symbol = ? ORDER BY id DESC LIMIT 1",
                (symbol,),
            ).fetchone()
            if latest_snapshot is not None:
                _check_drawdown_alert(
                    conn, symbol, strategy_type, Decimal(latest_snapshot["total_value"]),
                    drawdown_threshold_pct, discord_webhooks.alerts,
                )
                if summary_pairs is not None:
                    # Only the "leader" pair-worker thread is ever given
                    # summary_pairs (see run_pair_worker) -- capital_per_pair
                    # is the same for every pair (total_capital split
                    # equally), so this thread's own initial_cash doubles as
                    # that shared per-pair figure.
                    _check_global_daily_summary(
                        conn, summary_pairs, discord_webhooks.daily_summary,
                        int(time.time() * 1000), initial_cash,
                    )
                conn.commit()

            if retention_days is not None:
                now_ms = int(time.time() * 1000)
                today = now_ms // DAY_MS
                if today != last_housekeeping_day:
                    aggregate_old_snapshots(conn, now_ms=now_ms, retention_days=retention_days)
                    last_housekeeping_day = today
        except Exception as e:
            logger.exception("Cycle en echec pour %s/%s, cycle ignore.", symbol, strategy_type)
            # Discard whatever this cycle (or a failed housekeeping pass) left
            # pending and uncommitted -- without this, those writes would sit in
            # the open transaction and get silently made durable by whatever the
            # NEXT conn.commit() happens to be, defeating run_cycle's atomicity
            # guarantee and letting a signal be replayed after a partial failure.
            conn.rollback()
            # conn.rollback() only undoes the DB side. run_cycle mutates `engine`
            # in memory (trades/lots/cash_balance) BEFORE persisting anything, and
            # nothing undoes that -- so without this, a rolled-back trade stays
            # "real" in the in-process engine for the rest of the run. Since
            # build_snapshot() reads off this same in-memory engine, that phantom
            # trade's PnL would leak into a LATER, successful cycle's durable
            # snapshot even though the trades table never durably recorded it --
            # the exact C2 symptom (durable history disagreeing with a fresh
            # reconstruction), reintroduced through this narrower window. Treat a
            # rolled-back cycle like a mini-restart and re-derive engine from the
            # DB, the same known-good mechanism C2/Task 6 already established.
            # Unconditional (initial_cash is required, not optional): an opt-in
            # guard here would silently drop this protection for any future
            # caller that forgot to pass it -- exactly the class of silent gap
            # this whole fix wave exists to close.
            engine = reconstruct_engine_from_db(conn, symbol, initial_cash, engine.fee_pct)
            # Any entries pointed at engine-internal trade ids from the now-
            # discarded engine object; the reconstructed engine's own
            # _next_trade_id/_next_lot_id counters are freshly reseeded past
            # MAX(id) by reconstruct_engine_from_db itself, so no future lookup
            # could ever hit a stale entry anyway -- cleared regardless for
            # clarity.
            trade_id_map.clear()
            on_critical_failure(f"Exception non geree pendant le cycle pour {symbol}: {e}")

        cycles += 1
        if max_cycles is None or cycles < max_cycles:
            if stop_event is not None:
                # Interruptible wait: returns True (and stops looping) the
                # instant a shutdown is requested, instead of blocking the
                # full poll_interval_seconds like a plain sleep would.
                if stop_event.wait(poll_interval_seconds):
                    break
            else:
                time.sleep(poll_interval_seconds)


def run_pair_worker(
    cfg, pair: LivePair, discord_webhooks: DiscordWebhooks, stop_event: threading.Event,
) -> None:
    """One (symbol, strategy) pair's full live-trading lifecycle, run in its
    own thread with its own provider and its own DB connection (SQLite
    connections are not shared across threads). Each pair's portfolio is
    fully independent -- see reconstruct_engine_from_db, which reconstructs
    cash/lots by filtering the shared trades/lots tables on this pair's own
    symbol -- so pairs never interfere with each other's capital."""
    provider = build_provider(cfg.data_source)
    conn = init_db(cfg.db_path)
    symbol = pair.symbol
    strategy_type = pair.strategy
    params = cfg.strategy_defaults[strategy_type]
    # live.total_capital split equally across every configured pair -- e.g.
    # 1000 total / 8 pairs = 125 each -- NOT cfg.backtest.initial_capital
    # (which stays the full amount per symbol, on purpose, for backtest.py's
    # own comparative analysis; see LiveConfig.capital_per_pair).
    initial_cash = cfg.live.capital_per_pair

    # Rebuilds cash_balance + open lots from the DB (Task 6) so a restart
    # genuinely resumes the portfolio, not just each strategy's own state.
    engine = reconstruct_engine_from_db(
        conn, symbol, initial_cash=initial_cash, fee_pct=cfg.fees.default_fee_pct
    )

    def on_critical_failure(message: str) -> None:
        logger.critical(message)
        send_alert(discord_webhooks.alerts, "api_error", message, severity="critical")

    logger.info("Demarrage du moteur live: %s / %s", symbol, strategy_type)

    # Exactly one pair-worker thread (the first one configured, e.g. BTCUSDT/dca)
    # is the "leader" that checks/sends the consolidated daily summary covering
    # every pair -- picking one designated thread rather than having every
    # worker independently check is what keeps this to 1 Discord message a day
    # instead of racing len(cfg.live.pairs) threads into duplicate posts.
    is_summary_leader = pair == cfg.live.pairs[0]

    try:
        run_polling_loop(
            conn, provider, engine, symbol, strategy_type, params,
            poll_interval=cfg.live.poll_kline_interval,
            poll_interval_seconds=cfg.live.poll_interval_seconds,
            on_critical_failure=on_critical_failure,
            retention_days=cfg.snapshots.retention_detail_days,
            initial_cash=initial_cash,
            discord_webhooks=discord_webhooks,
            drawdown_threshold_pct=cfg.discord.alert_drawdown_threshold_pct,
            stop_event=stop_event,
            summary_pairs=cfg.live.pairs if is_summary_leader else None,
        )
    except Exception as e:
        # run_cycle's own try/except inside the polling loop already absorbs
        # per-cycle failures -- reaching here means something broke outside
        # that (e.g. a bug in run_polling_loop itself), genuinely unexpected
        # for this one pair. Logged and alerted, but never re-raised: one
        # pair's worker thread dying must not take down the others.
        logger.exception("Arret inattendu du moteur live pour %s/%s.", symbol, strategy_type)
        send_alert(
            discord_webhooks.alerts, "service_down",
            f"Le moteur live pour {symbol}/{strategy_type} s'est arrete de facon inattendue: {e}",
            severity="critical",
        )
    finally:
        logger.info("Moteur live arrete: %s / %s", symbol, strategy_type)
        conn.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx logs every request URL at INFO -- that wrote the secret Discord webhook
    # URLs into journald on every message, plus 1 line per pair per poll.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # A DCA pair out of cash re-logs the same "BUY rejete" every poll; keep one
    # line per symbol per 6h (the masked count is reported on the next one).
    logging.getLogger("engine.fifo_engine").addFilter(RepeatFilter(window_s=6 * 3600))
    install_signal_handlers()
    cfg = load_config()
    discord_webhooks = load_discord_webhooks()
    # Every Discord message from here on is delivered by one background thread:
    # no pair-worker thread ever waits on Discord (rate limits, DNS at boot).
    start_background_delivery()

    pairs_label = ", ".join(f"{p.symbol}/{p.strategy}" for p in cfg.live.pairs)
    logger.info("Demarrage du moteur live sur %d paire(s): %s", len(cfg.live.pairs), pairs_label)
    # One start message for the whole engine rather than one per pair thread:
    # N simultaneous posts is exactly the burst that got the logs webhook
    # rate-limited (Retry-After ~33 min) on every restart.
    send_log(discord_webhooks.logs, f"Moteur live demarre sur {len(cfg.live.pairs)} paire(s) : {pairs_label}.", level="INFO")

    threads = [
        threading.Thread(
            target=run_pair_worker, args=(cfg, pair, discord_webhooks, _stop_event),
            name=f"{pair.symbol}-{pair.strategy}",
        )
        for pair in cfg.live.pairs
    ]
    for t in threads:
        t.start()

    try:
        # install_signal_handlers routes both SIGTERM and SIGINT to
        # _stop_event instead of raising, so a plain t.join() per thread
        # (rather than a busy-poll) is enough: each worker's run_polling_loop
        # notices _stop_event on its own and returns, which unblocks join().
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        # Defensive fallback only -- normally unreachable, since SIGINT is
        # handled above and never raises here. Covers the unlikely case of a
        # signal handler failing to install.
        logger.info("Arret demande.")
        _stop_event.set()
        for t in threads:
            t.join()
    finally:
        send_log(discord_webhooks.logs, f"Moteur live arrete ({len(cfg.live.pairs)} paire(s)).", level="INFO")
        # Bounded well under systemd's TimeoutStopSec=30 so the unit stops cleanly.
        stop_background_delivery(timeout=10)


if __name__ == "__main__":
    main()
