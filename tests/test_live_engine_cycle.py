from decimal import Decimal

import pytest

from db.migrate import init_db
from db.repository import get_engine_state, insert_trade, replace_lots_for_symbol
from engine.fifo_engine import FifoEngine, Lot
from live_engine import reconstruct_engine_from_db, run_cycle
from market_data.provider import MarketDataProvider
from market_data.types import BookTicker, Kline, Ticker24h


class FakeProvider(MarketDataProvider):
    def __init__(self, klines: list[Kline]):
        self._klines = klines
        self._index = 0

    def get_klines(self, symbol, interval, start_ms, end_ms, limit=1000):
        if self._index >= len(self._klines):
            return []
        k = self._klines[self._index]
        self._index += 1
        return [k]

    def get_book_ticker(self, symbol):
        raise NotImplementedError

    def get_ticker_24h(self, symbol):
        raise NotImplementedError


def _kline(open_time_ms: int, close: str) -> Kline:
    c = Decimal(close)
    return Kline(open_time_ms=open_time_ms, open=c, high=c, low=c, close=c,
                 volume=Decimal("1"), close_time_ms=open_time_ms + 299_999)


@pytest.fixture
def conn():
    c = init_db(":memory:")
    yield c
    c.close()


def test_run_cycle_executes_dca_buy_and_persists_trade_and_snapshot(conn):
    provider = FakeProvider([_kline(0, "100")])
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    params = {"amount_per_buy": 50, "frequency_hours": 24, "reference_price": "close"}

    run_cycle(conn, provider, engine, "BTCUSDT", "dca", params, poll_interval="5m", trade_id_map={})

    assert len(engine.trades) == 1
    trades_in_db = conn.execute("SELECT * FROM trades").fetchall()
    assert len(trades_in_db) == 1
    assert Decimal(trades_in_db[0]["total_cost"]) == Decimal("50.05")
    snapshots_in_db = conn.execute("SELECT * FROM portfolio_snapshots").fetchall()
    assert len(snapshots_in_db) == 1


def test_run_cycle_skips_already_processed_candle(conn):
    kline = _kline(0, "100")
    provider = FakeProvider([kline, kline])  # same candle served twice
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    params = {"amount_per_buy": 50, "frequency_hours": 24, "reference_price": "close"}
    trade_id_map: dict[int, int] = {}

    run_cycle(conn, provider, engine, "BTCUSDT", "dca", params, poll_interval="5m", trade_id_map=trade_id_map)
    run_cycle(conn, provider, engine, "BTCUSDT", "dca", params, poll_interval="5m", trade_id_map=trade_id_map)

    # Second cycle must not re-process the same candle (no duplicate BUY, no duplicate snapshot)
    assert len(engine.trades) == 1
    assert len(conn.execute("SELECT * FROM trades").fetchall()) == 1
    assert len(conn.execute("SELECT * FROM portfolio_snapshots").fetchall()) == 1


def test_run_cycle_persists_and_restores_state_across_fresh_engine_instances(conn):
    provider = FakeProvider([_kline(0, "100"), _kline(300_000, "100")])
    params = {"amount_per_buy": 50, "frequency_hours": 24, "reference_price": "close"}

    engine1 = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    run_cycle(conn, provider, engine1, "BTCUSDT", "dca", params, poll_interval="5m", trade_id_map={})

    # Simulate a restart: fresh engine and a fresh (empty) trade_id_map, exactly
    # as a real restart would have (trade_id_map is never persisted -- see Task 6's
    # interface notes). This test isolates STRATEGY-state persistence (last_buy_ms
    # survives via engine_state) from full portfolio reconstruction, which is
    # `reconstruct_engine_from_db`'s job and is tested separately, below.
    engine2 = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    run_cycle(conn, provider, engine2, "BTCUSDT", "dca", params, poll_interval="5m", trade_id_map={})

    assert len(engine2.trades) == 0  # too soon since the persisted last_buy_ms (frequency_hours=24)


def test_run_cycle_grid_persists_level_state_across_calls(conn):
    # This must be a scenario where correctly-persisted state produces a DIFFERENT
    # outcome than a buggy implementation that silently rebuilds a fresh state every
    # call (fresh state always has prev_price=None, which can never trigger a cross,
    # so a bug here always shows up as "0 trades" rather than a crash).
    kline_above = _kline(0, "150")       # first tick: no prior price, just records prev_price=150
    kline_dip = _kline(300_000, "90")    # second tick: needs prev_price=150 (persisted) to detect
                                          # the down-cross through buy_price=100; a state-reset bug
                                          # would see prev_price=None here and produce 0 trades instead.
    provider = FakeProvider([kline_above, kline_dip])
    params = {"lower_bound": 100, "upper_bound": 200, "n_levels": 1,
              "spacing": "arithmetic", "order_size_quote": 100}
    trade_id_map: dict[int, int] = {}

    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    run_cycle(conn, provider, engine, "BTCUSDT", "grid", params, poll_interval="5m", trade_id_map=trade_id_map)
    run_cycle(conn, provider, engine, "BTCUSDT", "grid", params, poll_interval="5m", trade_id_map=trade_id_map)

    assert len(engine.trades) == 1
    assert engine.trades[0].side.value == "BUY"
    assert engine.trades[0].total_cost == Decimal("100.1")


def test_run_cycle_resyncs_lots_table_on_sell_not_just_buy(conn):
    # This is the scenario the old "insert_lot only on BUY" design could never
    # handle at all: a SELL must remove the row from `lots`, not just add nothing.
    provider = FakeProvider([_kline(0, "150"), _kline(300_000, "90"), _kline(600_000, "250")])
    params = {"lower_bound": 100, "upper_bound": 200, "n_levels": 1,
              "spacing": "arithmetic", "order_size_quote": 100}
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    trade_id_map: dict[int, int] = {}

    run_cycle(conn, provider, engine, "BTCUSDT", "grid", params, poll_interval="5m", trade_id_map=trade_id_map)  # records prev=150
    run_cycle(conn, provider, engine, "BTCUSDT", "grid", params, poll_interval="5m", trade_id_map=trade_id_map)  # BUY at 100
    lots_after_buy = conn.execute("SELECT * FROM lots WHERE symbol = 'BTCUSDT'").fetchall()
    assert len(lots_after_buy) == 1
    assert Decimal(lots_after_buy[0]["prix_achat"]) == Decimal("100")
    # The FK now correctly points at a real trades.id, not FifoEngine's own
    # internal trade-id counter -- verify the translation actually happened.
    real_trade_id = conn.execute("SELECT id FROM trades WHERE side = 'BUY'").fetchone()["id"]
    assert lots_after_buy[0]["trade_id_achat"] == real_trade_id

    run_cycle(conn, provider, engine, "BTCUSDT", "grid", params, poll_interval="5m", trade_id_map=trade_id_map)  # SELL at 200

    assert len(engine.trades) == 2
    assert [t.side.value for t in engine.trades] == ["BUY", "SELL"]
    lots_after_sell = conn.execute("SELECT * FROM lots WHERE symbol = 'BTCUSDT'").fetchall()
    assert lots_after_sell == []  # fully consumed -- resync correctly removed the row
    trades_in_db = conn.execute("SELECT * FROM trades").fetchall()
    assert len(trades_in_db) == 2


def test_reconstruct_engine_from_db_rebuilds_cash_and_open_lots(conn):
    provider = FakeProvider([_kline(0, "150"), _kline(300_000, "90")])
    params = {"lower_bound": 100, "upper_bound": 200, "n_levels": 1,
              "spacing": "arithmetic", "order_size_quote": 100}
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    trade_id_map: dict[int, int] = {}
    run_cycle(conn, provider, engine, "BTCUSDT", "grid", params, poll_interval="5m", trade_id_map=trade_id_map)
    run_cycle(conn, provider, engine, "BTCUSDT", "grid", params, poll_interval="5m", trade_id_map=trade_id_map)  # BUY at 100, cash -> 899.9

    restored = reconstruct_engine_from_db(conn, "BTCUSDT", initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))

    assert restored.cash_balance == Decimal("899.9")
    restored_lots = restored.get_lots("BTCUSDT")
    assert len(restored_lots) == 1
    assert restored_lots[0].prix_achat == Decimal("100")
    assert restored_lots[0].quantity_restante == Decimal("1")


def test_reconstruct_engine_from_db_with_no_history_uses_initial_cash(conn):
    restored = reconstruct_engine_from_db(conn, "BTCUSDT", initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))

    assert restored.cash_balance == Decimal("1000")
    assert restored.get_lots("BTCUSDT") == []


def test_reconstruct_engine_from_db_reconstructed_lots_need_no_further_translation(conn):
    # A lot rebuilt from the DB already carries a real trades.id in trade_id_achat
    # (that's what was persisted). If a NEW cycle runs against the reconstructed
    # engine with a fresh (empty) trade_id_map and doesn't trade, the existing
    # lot's trade_id_achat must survive a resync unchanged -- proving
    # replace_lots_for_symbol's fallback (map.get(x, x)) does the right thing
    # for ids that were never in the map to begin with.
    provider = FakeProvider([_kline(0, "150"), _kline(300_000, "90")])
    params = {"lower_bound": 100, "upper_bound": 200, "n_levels": 1,
              "spacing": "arithmetic", "order_size_quote": 100}
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    trade_id_map: dict[int, int] = {}
    run_cycle(conn, provider, engine, "BTCUSDT", "grid", params, poll_interval="5m", trade_id_map=trade_id_map)
    run_cycle(conn, provider, engine, "BTCUSDT", "grid", params, poll_interval="5m", trade_id_map=trade_id_map)
    original_trade_id = conn.execute("SELECT trade_id_achat FROM lots").fetchone()["trade_id_achat"]

    restored = reconstruct_engine_from_db(conn, "BTCUSDT", initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    # A no-op-ish next cycle: price stays flat, no new trade, but run_cycle still
    # resyncs lots every time it processes a new candle.
    provider2 = FakeProvider([_kline(600_000, "150")])
    run_cycle(conn, provider2, restored, "BTCUSDT", "grid", params, poll_interval="5m", trade_id_map={})

    lots_after = conn.execute("SELECT trade_id_achat FROM lots").fetchall()
    assert len(lots_after) == 1
    assert lots_after[0]["trade_id_achat"] == original_trade_id


def test_reconstruct_engine_from_db_new_trade_after_restart_does_not_corrupt_old_lot_fk(conn):
    # Reproduce the critical bug: if a reconstructed engine's _next_trade_id isn't
    # seeded past the max existing db trade id, the first new trade after restart
    # gets an engine-internal id that collides with a pre-restart trade's real db id.
    # When the old lot (whose trade_id_achat correctly points to the old trade) gets
    # resynced, trade_id_map.get(1, 1) returns the NEW trade's db id instead of
    # leaving 1 alone, silently corrupting the old lot's foreign key.
    #
    # 2 levels: lower=100, upper=200, n_levels=2 -> level0(100,150), level1(150,200)
    params = {"lower_bound": 100, "upper_bound": 200, "n_levels": 2,
              "spacing": "arithmetic", "order_size_quote": 150}
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    trade_id_map: dict[int, int] = {}

    # Pre-restart: only level1 (buy=150) fills. Price path: 200 -> 140 crosses
    # level1's buy_price (150) downward but NOT level0's (100).
    provider = FakeProvider([_kline(0, "200"), _kline(300_000, "140")])
    run_cycle(conn, provider, engine, "BTCUSDT", "grid", params, poll_interval="5m", trade_id_map=trade_id_map)
    run_cycle(conn, provider, engine, "BTCUSDT", "grid", params, poll_interval="5m", trade_id_map=trade_id_map)

    old_lot_row = conn.execute("SELECT * FROM lots WHERE symbol = 'BTCUSDT'").fetchone()
    assert Decimal(old_lot_row["prix_achat"]) == Decimal("150")
    old_trade_id_achat = old_lot_row["trade_id_achat"]

    # Simulate a restart: fresh engine reconstructed from the DB, fresh (empty)
    # trade_id_map, fresh GridState reconstructed from persisted engine_state.
    restored = reconstruct_engine_from_db(conn, "BTCUSDT", initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    new_trade_id_map: dict[int, int] = {}

    # Post-restart: level0 (buy=100) now fills -- a genuinely new, unrelated trade.
    provider2 = FakeProvider([_kline(600_000, "90")])
    run_cycle(conn, provider2, restored, "BTCUSDT", "grid", params, poll_interval="5m", trade_id_map=new_trade_id_map)

    lots_after = conn.execute("SELECT * FROM lots WHERE symbol = 'BTCUSDT' ORDER BY prix_achat").fetchall()
    assert len(lots_after) == 2
    old_lot_after = next(l for l in lots_after if Decimal(l["prix_achat"]) == Decimal("150"))
    # The old lot's FK must be untouched -- this is what the reviewer proved breaks without the fix.
    assert old_lot_after["trade_id_achat"] == old_trade_id_achat


def test_reconstruct_engine_from_db_rebuilds_trades_so_realized_pnl_cumule_survives_restart(conn):
    # Reproduces the critical bug: reconstruct_engine_from_db rebuilt cash_balance
    # and lots but never engine.trades, so realized_pnl_cumule() (which sums over
    # self.trades where side == SELL) silently reported 0 after every restart,
    # regardless of real trading history.
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    engine.buy(0, "BTCUSDT", Decimal("100"), Decimal("1"), "dca")
    sell_trade = engine.sell(300_000, "BTCUSDT", Decimal("150"), Decimal("1"), "dca")
    assert sell_trade.realized_pnl is not None and sell_trade.realized_pnl != Decimal("0")

    # Persist both trades + lots exactly the way run_cycle does.
    trade_id_map: dict[int, int] = {}
    for trade in engine.trades:
        db_trade_id = insert_trade(conn, trade)
        trade_id_map[trade.id] = db_trade_id
    translated_lots = [
        Lot(
            id=lot.id, symbol=lot.symbol, quantity_restante=lot.quantity_restante,
            prix_achat=lot.prix_achat, timestamp_achat=lot.timestamp_achat,
            trade_id_achat=trade_id_map.get(lot.trade_id_achat, lot.trade_id_achat),
        )
        for lot in engine.get_lots("BTCUSDT")
    ]
    replace_lots_for_symbol(conn, "BTCUSDT", translated_lots)

    pre_restart_pnl = engine.realized_pnl_cumule("BTCUSDT")
    assert pre_restart_pnl != Decimal("0")

    restored = reconstruct_engine_from_db(conn, "BTCUSDT", initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))

    assert restored.realized_pnl_cumule("BTCUSDT") == pre_restart_pnl


def test_run_cycle_is_atomic_partial_failure_leaves_nothing_committed(conn, monkeypatch):
    # insert_snapshot runs after insert_trade/replace_lots_for_symbol but before
    # the final conn.commit() -- raising here simulates a crash mid-cycle.
    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    # run_cycle calls insert_snapshot via the name bound into live_engine's own
    # namespace (`from db.repository import insert_snapshot`), so patch it there.
    monkeypatch.setattr("live_engine.insert_snapshot", _boom)
    provider = FakeProvider([_kline(0, "100")])
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    params = {"amount_per_buy": 50, "frequency_hours": 24, "reference_price": "close"}

    with pytest.raises(RuntimeError):
        run_cycle(conn, provider, engine, "BTCUSDT", "dca", params, poll_interval="5m", trade_id_map={})
    conn.rollback()

    # Without the fix, insert_trade's own internal commit() would have already
    # made the trade durable, and it would survive this rollback.
    assert conn.execute("SELECT * FROM trades WHERE symbol = 'BTCUSDT'").fetchall() == []


def test_run_cycle_final_commit_actually_persists_durably_across_rollback(conn):
    # Distinguishes "committed for real" from "merely visible on this same
    # connection while a transaction is still open": every other test in this
    # file reads back through the SAME conn that ran run_cycle, so an
    # uncommitted transaction's writes would still be visible to those
    # assertions even if run_cycle's final conn.commit() were deleted entirely.
    # Calling conn.rollback() immediately after a successful run_cycle and
    # confirming the rows survive proves they were actually committed, not just
    # pending-and-visible.
    provider = FakeProvider([_kline(0, "100")])
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    params = {"amount_per_buy": 50, "frequency_hours": 24, "reference_price": "close"}

    run_cycle(conn, provider, engine, "BTCUSDT", "dca", params, poll_interval="5m", trade_id_map={})
    conn.rollback()

    assert len(conn.execute("SELECT * FROM trades WHERE symbol = 'BTCUSDT'").fetchall()) == 1
    assert len(conn.execute("SELECT * FROM portfolio_snapshots WHERE symbol = 'BTCUSDT'").fetchall()) == 1
    assert get_engine_state(conn, "last_ts:BTCUSDT:dca") is not None
