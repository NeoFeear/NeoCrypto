from decimal import Decimal

import pytest

from db.migrate import init_db
from engine.fifo_engine import FifoEngine
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
