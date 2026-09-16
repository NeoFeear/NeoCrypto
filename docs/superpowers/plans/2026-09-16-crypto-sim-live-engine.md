# Crypto Paper-Trading Simulator — Live Engine (Plan 3 of 6) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce SQLite persistence (WAL mode), refactor the three strategies from Plan 2's "whole-klines-list" pure functions into explicit state objects with a `step()` function each (so a live engine can call them once per poll and resume across restarts), and build `live_engine.py`: polling, incident recovery, retry/backoff, and snapshot-retention housekeeping.

**Architecture:** Each strategy gets a small `XState` dataclass + `step(state, price_data, engine, symbol, params)` that mutates state and calls `engine.buy()`/`engine.sell()`. Plan 2's `run_X(klines, ...)` functions become thin folds over `step()` for buy_hold and dca (identical behavior, verified by regression tests) — grid keeps its existing backtest `run_grid`/`build_grid_levels`/`GridLevel` untouched (already reviewed, already correct for D2's backtest side) and gains a *separate* `GridState`/`step_grid_live()` for the live D2 side, since the spec defines backtest and live crossing detection as two different algorithms sharing the same level/slot model. State is (de)serialized to/from JSON for persistence in `engine_state`. `db/` is a new package: `schema.sql` + `migrate.py` (WAL init) + `repository.py` (typed read/write functions — no raw SQL outside this package).

**Tech Stack:** Same as Plans 1-2, plus stdlib `sqlite3` and `json`.

**Spec:** `docs/superpowers/specs/2026-09-15-crypto-paper-trading-sim-design.md` — implements section 3 (DB schema), section 5 (moteur live), and the D2 live-side crossing rule from section 8 that Plans 1-2 only implemented for backtest. Read both.

## Global Constraints

- All price/quantity/PnL/cash values are `decimal.Decimal`, never `float` — including values round-tripped through JSON (`engine_state` blobs) and SQLite (stored as `TEXT`, reconstructed via `Decimal(text)`, never `REAL`).
- `PRAGMA journal_mode = WAL;` set at DB init, not per-connection ad hoc.
- A BUY/SELL rejection (from Plan 1's `FifoEngine`) is never silently lost — every trade attempt this plan makes must still go through the engine's existing cash/lot checks unchanged.
- No new hardcoded thresholds — retry timing (1s, 4s), the 3-failure alert threshold, and the 30-day retention window are the exact values the spec names (not user-configurable in this plan; they're spec-mandated constants, not tunable amounts).
- Resume-from-restart must never replay an already-processed signal or skip one — this is the entire point of `engine_state`'s persisted "last processed timestamp".
- This plan does NOT touch Discord — that's Plan 4. Nothing here imports `discord_notifier` (which doesn't exist yet); alert/notification hooks in this plan are plain callback parameters the live engine calls, wired to Discord in Plan 4.

---

## Task 1: SQLite schema + migration (WAL mode)

**Files:**
- Create: `db/__init__.py`
- Create: `db/schema.sql`
- Create: `db/migrate.py`
- Test: `tests/test_migrate.py`

**Interfaces:**
- Produces: `init_db(path: str) -> sqlite3.Connection` — applies `schema.sql`, sets `PRAGMA journal_mode=WAL`, returns an open connection with `row_factory = sqlite3.Row`.

- [ ] **Step 1: Create `db/__init__.py`** (empty file)

- [ ] **Step 2: Write `db/schema.sql`**

```sql
-- db/schema.sql
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('BUY', 'SELL')),
    price TEXT NOT NULL,
    quantity TEXT NOT NULL,
    fee_pct TEXT NOT NULL,
    fee_amount TEXT NOT NULL,
    total_cost TEXT NOT NULL,
    realized_pnl TEXT,
    cash_balance_after TEXT NOT NULL,
    strategy_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id_achat INTEGER NOT NULL REFERENCES trades(id),
    symbol TEXT NOT NULL,
    quantity_restante TEXT NOT NULL,
    prix_achat TEXT NOT NULL,
    timestamp_achat INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    cash_balance TEXT NOT NULL,
    position_value TEXT NOT NULL,
    total_value TEXT NOT NULL,
    unrealized_pnl TEXT NOT NULL,
    realized_pnl_cumule TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    type TEXT NOT NULL CHECK(type IN ('dca', 'grid', 'buy_hold')),
    symbol TEXT NOT NULL,
    params TEXT NOT NULL,
    capital_initial TEXT NOT NULL,
    actif INTEGER NOT NULL DEFAULT 1,
    date_creation INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS engine_state (
    key TEXT PRIMARY KEY,
    value TEXT
);
```

- [ ] **Step 3: Write the failing test**

```python
# tests/test_migrate.py
import sqlite3
from pathlib import Path

from db.migrate import init_db


def test_init_db_creates_all_tables_and_enables_wal(tmp_path: Path):
    db_path = tmp_path / "test.db"

    conn = init_db(str(db_path))

    cursor = conn.execute("PRAGMA journal_mode")
    assert cursor.fetchone()[0].lower() == "wal"

    tables = {row["name"] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert {"trades", "lots", "portfolio_snapshots", "strategy_configs", "engine_state"} <= tables
    conn.close()


def test_init_db_is_idempotent(tmp_path: Path):
    db_path = tmp_path / "test.db"
    init_db(str(db_path)).close()
    conn = init_db(str(db_path))  # must not raise on re-init
    conn.execute("INSERT INTO engine_state (key, value) VALUES ('k', 'v')")
    conn.commit()
    conn.close()
```

- [ ] **Step 4: Run test to verify it fails**

Run: `pytest tests/test_migrate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'db.migrate'`

- [ ] **Step 5: Write `db/migrate.py`**

```python
# db/migrate.py
import sqlite3
from pathlib import Path

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def init_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
    return conn
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_migrate.py -v`
Expected: PASS (2 passed)

- [ ] **Step 7: Commit**

```bash
git add db/__init__.py db/schema.sql db/migrate.py tests/test_migrate.py
git commit -m "feat: add SQLite schema and WAL-mode migration"
```

---

## Task 2: DB repository — persist trades, lots, snapshots, engine_state

**Files:**
- Create: `db/repository.py`
- Test: `tests/test_repository.py`

**Interfaces:**
- Consumes: `sqlite3.Connection` (Task 1), `engine.fifo_engine.Trade` (Plan 1), `models.PortfolioSnapshot` (Plan 1)
- Produces: `insert_trade(conn, trade: Trade) -> int` (returns the new trade's DB id), `insert_lot(conn, trade_id: int, symbol: str, quantity_restante: Decimal, prix_achat: Decimal, timestamp_achat: int) -> int`, `replace_lots_for_symbol(conn, symbol: str, lots: list[Lot]) -> None`, `insert_snapshot(conn, snapshot: PortfolioSnapshot) -> None`, `get_engine_state(conn, key: str) -> str | None`, `set_engine_state(conn, key: str, value: str) -> None`

This is the ONLY module allowed to write raw SQL for these tables — the live engine calls these functions, never `conn.execute(...)` directly for trades/lots/snapshots/engine_state.

`replace_lots_for_symbol` is the mechanism the live engine actually uses to keep `lots` in sync after each cycle: it deletes every existing row for that symbol and re-inserts one row per lot currently in `engine.get_lots(symbol)`. This is deliberately a full resync, not an incremental patch — a BUY can create a lot, a SELL can partially consume or fully remove one, and a grid cycle can do several BUYs at once (D3's simultaneous fills); trying to track "which lot is new" per individual trade is fragile (Lot objects have no natural identity to diff against across multiple same-cycle inserts) and gets the association wrong when more than one BUY lands in the same cycle. Resyncing from `engine.get_lots()` — the FIFO engine's own single source of truth — after every cycle sidesteps all of that. `insert_lot` stays as a low-level primitive (used directly by `replace_lots_for_symbol` and useful on its own in tests), but the live engine (Task 6) never calls it directly.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_repository.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_repository.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'db.repository'`

- [ ] **Step 3: Write `db/repository.py`**

```python
# db/repository.py
import sqlite3
from decimal import Decimal

from engine.fifo_engine import Lot, Trade
from models import PortfolioSnapshot


def insert_trade(conn: sqlite3.Connection, trade: Trade) -> int:
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


def insert_snapshot(conn: sqlite3.Connection, snapshot: PortfolioSnapshot) -> None:
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
    conn.commit()


def replace_lots_for_symbol(conn: sqlite3.Connection, symbol: str, lots: list[Lot]) -> None:
    conn.execute("DELETE FROM lots WHERE symbol = ?", (symbol,))
    for lot in lots:
        conn.execute(
            """
            INSERT INTO lots (trade_id_achat, symbol, quantity_restante, prix_achat, timestamp_achat)
            VALUES (?, ?, ?, ?, ?)
            """,
            (lot.trade_id_achat, symbol, str(lot.quantity_restante), str(lot.prix_achat), lot.timestamp_achat),
        )
    conn.commit()


def get_engine_state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM engine_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row is not None else None


def set_engine_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO engine_state (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_repository.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add db/repository.py tests/test_repository.py
git commit -m "feat: add DB repository for trades, lots, snapshots, engine_state"
```

---

## Task 3: Buy & Hold — state object + step()

**Files:**
- Modify: `engine/strategies/buy_hold.py`
- Modify: `tests/test_strategy_buy_hold.py`

**Interfaces:**
- Produces: `BuyHoldState` (dataclass: `invested: bool = False`), `step(state: BuyHoldState, k: Kline, engine: FifoEngine, symbol: str, params: dict) -> None`, `buy_hold_state_to_json(state: BuyHoldState) -> str`, `buy_hold_state_from_json(s: str) -> BuyHoldState`. `run_buy_hold` keeps its EXACT existing signature and behavior (regression-tested), now implemented as a thin fold over `step()`.

- [ ] **Step 1: Append the failing tests**

```python
# tests/test_strategy_buy_hold.py (append)
from engine.strategies.buy_hold import (
    BuyHoldState,
    buy_hold_state_from_json,
    buy_hold_state_to_json,
    step,
)


def test_run_buy_hold_unchanged_after_refactor():
    # Exact regression of the pre-refactor behavior/assertions
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    klines = [_kline(0, "100"), _kline(3_600_000, "110"), _kline(7_200_000, "120")]

    snapshots = run_buy_hold(klines, engine, "BTCUSDT", params={"invest_at": "start"})

    assert len(engine.trades) == 1
    assert engine.trades[0].total_cost == Decimal("1000.000000000000000000000000")
    assert engine.cash_balance == Decimal("0E-24")
    assert len(snapshots) == 3


def test_step_called_twice_only_buys_once():
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    state = BuyHoldState()

    step(state, _kline(0, "100"), engine, "BTCUSDT", {"invest_at": "start"})
    step(state, _kline(3_600_000, "110"), engine, "BTCUSDT", {"invest_at": "start"})

    assert len(engine.trades) == 1
    assert state.invested is True


def test_buy_hold_state_json_round_trip():
    state = BuyHoldState(invested=True)

    restored = buy_hold_state_from_json(buy_hold_state_to_json(state))

    assert restored.invested is True


def test_buy_hold_state_json_round_trip_default():
    restored = buy_hold_state_from_json(buy_hold_state_to_json(BuyHoldState()))
    assert restored.invested is False
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `pytest tests/test_strategy_buy_hold.py -v`
Expected: FAIL — `ImportError: cannot import name 'BuyHoldState' from 'engine.strategies.buy_hold'`; the 2 existing tests still pass.

- [ ] **Step 3: Rewrite `engine/strategies/buy_hold.py` in full**

```python
# engine/strategies/buy_hold.py
import json
import logging
from dataclasses import dataclass
from decimal import Decimal

from engine.fifo_engine import FifoEngine
from engine.strategies.base import build_snapshot
from market_data.types import Kline
from models import PortfolioSnapshot

logger = logging.getLogger(__name__)


@dataclass
class BuyHoldState:
    invested: bool = False


def step(state: BuyHoldState, k: Kline, engine: FifoEngine, symbol: str, params: dict) -> None:
    if not state.invested:
        price = k.close
        quantity = (engine.cash_balance / (Decimal(1) + engine.fee_pct)) / price
        trade = engine.buy(k.open_time_ms, symbol, price, quantity, "buy_hold")
        if trade is None:
            logger.warning("Buy & Hold: achat initial rejete pour %s (cash insuffisant?)", symbol)
        state.invested = True


def run_buy_hold(klines: list[Kline], engine: FifoEngine, symbol: str, params: dict) -> list[PortfolioSnapshot]:
    """Spec section 3: {"invest_at": "start"} — invest everything on the first
    candle, hold forever. No selling."""
    state = BuyHoldState()
    snapshots: list[PortfolioSnapshot] = []
    for k in klines:
        step(state, k, engine, symbol, params)
        snapshots.append(build_snapshot(engine, symbol, k.close, k.open_time_ms))
    return snapshots


def buy_hold_state_to_json(state: BuyHoldState) -> str:
    return json.dumps({"invested": state.invested})


def buy_hold_state_from_json(s: str) -> BuyHoldState:
    data = json.loads(s)
    return BuyHoldState(invested=data["invested"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_strategy_buy_hold.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add engine/strategies/buy_hold.py tests/test_strategy_buy_hold.py
git commit -m "refactor: extract BuyHoldState + step() so live engine can call once per poll"
```

---

## Task 4: DCA — timestamp-based state object + step() (drops the interval_ms parameter entirely)

**Files:**
- Modify: `engine/strategies/dca.py`
- Modify: `tests/test_strategy_dca.py`
- Modify: `backtest.py`
- Modify: `tests/test_backtest_replay.py`
- Modify: `tests/test_backtest_reports.py`

**Interfaces:**
- Produces: `DcaState` (dataclass: `last_buy_ms: int | None = None`), `step(state: DcaState, k: Kline, engine: FifoEngine, symbol: str, params: dict) -> None`, `dca_state_to_json`/`dca_state_from_json`. `run_dca(klines, engine, symbol, params)` — **signature changes: the `interval_hours`/`interval_ms` parameter is REMOVED entirely.**

Plan 2's DCA scheduled buys by candle *index* (`i % candles_per_buy == 0`), which needed to know the candle interval and had a `ZeroDivisionError` bug for sub-hourly intervals (found and patched in Plan 2's final review). This task replaces it with a strictly better design: schedule buys by comparing each candle's own timestamp against the last buy's timestamp (`k.open_time_ms - state.last_buy_ms >= frequency_hours * 3_600_000`). This needs no interval parameter at all (it reads timestamps directly off whatever candles it's given), generalizes correctly to live polling (irregular real-world gaps between polls), and was verified during planning to reproduce Plan 2's exact existing test outcomes for both the 1h-candle case and the 4h-candle edge case that originally triggered the ZeroDivisionError.

- [ ] **Step 1: Replace the failing tests in `tests/test_strategy_dca.py`**

Replace the entire file's contents with:

```python
# tests/test_strategy_dca.py
from decimal import Decimal

from engine.fifo_engine import FifoEngine
from engine.strategies.dca import DcaState, dca_state_from_json, dca_state_to_json, run_dca, step
from market_data.types import Kline


def _kline(open_time_ms: int, close: str) -> Kline:
    c = Decimal(close)
    return Kline(
        open_time_ms=open_time_ms, open=c, high=c, low=c, close=c,
        volume=Decimal("1"), close_time_ms=open_time_ms + 3_599_999,
    )


def test_dca_buys_every_frequency_hours_starting_at_index_zero():
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    # 25 hourly candles (index 0..24); frequency_hours=24 -> buys at index 0 and 24 only.
    klines = [_kline(i * 3_600_000, "100" if i != 24 else "125") for i in range(25)]

    snapshots = run_dca(
        klines, engine, "BTCUSDT",
        params={"amount_per_buy": 50, "frequency_hours": 24, "reference_price": "close"},
    )

    assert len(engine.trades) == 2
    trade0, trade24 = engine.trades
    assert trade0.price == Decimal("100")
    assert trade0.quantity == Decimal("0.5")
    assert trade0.total_cost == Decimal("50.05")
    assert trade24.price == Decimal("125")
    assert trade24.quantity == Decimal("0.4")
    assert trade24.total_cost == Decimal("50.05")
    assert engine.cash_balance == Decimal("1000") - Decimal("50.05") - Decimal("50.05")
    assert len(snapshots) == 25


def test_dca_never_sells():
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    klines = [_kline(0, "100"), _kline(3_600_000, "50")]

    run_dca(klines, engine, "BTCUSDT",
            params={"amount_per_buy": 50, "frequency_hours": 24, "reference_price": "close"})

    assert all(t.side.value == "BUY" for t in engine.trades)


def test_dca_rejected_buy_is_logged_not_raised():
    engine = FifoEngine(initial_cash=Decimal("60"), fee_pct=Decimal("0.001"))
    klines = [_kline(i * 3_600_000, "100") for i in range(3)]

    run_dca(klines, engine, "BTCUSDT",
            params={"amount_per_buy": 50, "frequency_hours": 1, "reference_price": "close"})

    assert len(engine.trades) == 1
    assert engine.cash_balance == Decimal("9.95")


def test_dca_sub_hourly_candles_no_longer_need_an_interval_parameter():
    # This is the scenario that used to require interval_hours and could
    # ZeroDivisionError for interval_hours < 1 (e.g. 5-minute candles).
    # The timestamp-based design has no such parameter or failure mode.
    engine = FifoEngine(initial_cash=Decimal("10000"), fee_pct=Decimal("0.001"))
    klines = [_kline(i * 14_400_000, "100") for i in range(3)]  # 4-hour candles

    run_dca(klines, engine, "BTCUSDT",
            params={"amount_per_buy": 50, "frequency_hours": 1, "reference_price": "close"})

    assert len(engine.trades) == 3  # frequency (1h) < candle spacing (4h) -> buys every candle


def test_step_schedules_by_timestamp_not_call_count():
    engine = FifoEngine(initial_cash=Decimal("10000"), fee_pct=Decimal("0.001"))
    state = DcaState()
    params = {"amount_per_buy": 50, "frequency_hours": 24, "reference_price": "close"}

    step(state, _kline(0, "100"), engine, "BTCUSDT", params)
    step(state, _kline(3_600_000, "100"), engine, "BTCUSDT", params)  # +1h, too soon
    step(state, _kline(86_400_000, "100"), engine, "BTCUSDT", params)  # +24h from last buy

    assert len(engine.trades) == 2
    assert state.last_buy_ms == 86_400_000


def test_dca_state_json_round_trip():
    restored = dca_state_from_json(dca_state_to_json(DcaState(last_buy_ms=12345)))
    assert restored.last_buy_ms == 12345


def test_dca_state_json_round_trip_never_bought():
    restored = dca_state_from_json(dca_state_to_json(DcaState()))
    assert restored.last_buy_ms is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_strategy_dca.py -v`
Expected: FAIL with `ImportError: cannot import name 'DcaState' from 'engine.strategies.dca'`

- [ ] **Step 3: Rewrite `engine/strategies/dca.py` in full**

```python
# engine/strategies/dca.py
import json
from dataclasses import dataclass
from decimal import Decimal

from engine.fifo_engine import FifoEngine
from engine.strategies.base import build_snapshot
from market_data.types import Kline
from models import PortfolioSnapshot


@dataclass
class DcaState:
    last_buy_ms: int | None = None


def step(state: DcaState, k: Kline, engine: FifoEngine, symbol: str, params: dict) -> None:
    """reference_price is always "close" (the only value the spec's schema defines)."""
    frequency_ms = int(params["frequency_hours"]) * 3_600_000
    if state.last_buy_ms is None or k.open_time_ms - state.last_buy_ms >= frequency_ms:
        amount_per_buy = Decimal(str(params["amount_per_buy"]))
        price = k.close
        quantity = amount_per_buy / price
        engine.buy(k.open_time_ms, symbol, price, quantity, "dca")
        state.last_buy_ms = k.open_time_ms


def run_dca(klines: list[Kline], engine: FifoEngine, symbol: str, params: dict) -> list[PortfolioSnapshot]:
    """Spec section 3: buy amount_per_buy (quote) every frequency_hours, starting
    immediately at the first candle. Scheduling is timestamp-based (compares each
    candle's own open_time_ms against the last buy), not candle-index-based —
    this needs no interval parameter and has no divide-by-zero failure mode for
    sub-hourly candles."""
    state = DcaState()
    snapshots: list[PortfolioSnapshot] = []
    for k in klines:
        step(state, k, engine, symbol, params)
        snapshots.append(build_snapshot(engine, symbol, k.close, k.open_time_ms))
    return snapshots


def dca_state_to_json(state: DcaState) -> str:
    return json.dumps({"last_buy_ms": state.last_buy_ms})


def dca_state_from_json(s: str) -> DcaState:
    data = json.loads(s)
    return DcaState(last_buy_ms=data["last_buy_ms"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_strategy_dca.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Update `backtest.py` to drop `interval_ms`/`interval_hours` plumbing**

In `backtest.py`:
- In `run_strategy`, remove the `interval_ms: int` parameter entirely, and change the `dca` branch from `run_dca(klines, engine, symbol, params, interval_ms)` to `run_dca(klines, engine, symbol, params)`.
- In `main()`, remove the line computing `interval_ms = INTERVAL_MS[cfg.backtest.interval]` (keep the `now_ms` truncation line, just compute `interval_ms` inline there instead if still needed for that truncation — i.e. `interval_ms = INTERVAL_MS[cfg.backtest.interval]` may still be needed for the `now_ms` truncation from Plan 2's final review; if so, keep that one assignment but remove every `run_strategy(..., interval_ms)` argument from all three call sites in `main()`, since `run_strategy` no longer takes it).

- [ ] **Step 6: Update `tests/test_backtest_replay.py` and `tests/test_backtest_reports.py`**

Remove the `interval_hours=1` (or any `interval_ms=...`) argument from every `run_strategy(...)` call in both files — `run_strategy` no longer accepts that parameter. Do not change any other assertion.

- [ ] **Step 7: Run the full suite to verify everything still passes**

Run: `pytest -v`
Expected: PASS, all tests including Plan 2's backtest tests.

- [ ] **Step 8: Commit**

```bash
git add engine/strategies/dca.py tests/test_strategy_dca.py backtest.py tests/test_backtest_replay.py tests/test_backtest_reports.py
git commit -m "refactor: DCA scheduling is timestamp-based, drops interval parameter and its ZeroDivisionError failure mode"
```

---

## Task 5: Grid — live-side state object + step_grid_live() (D2 live crossing rule)

**Files:**
- Modify: `engine/strategies/grid.py`
- Modify: `tests/test_strategy_grid.py`

**Interfaces:**
- Consumes: `engine.strategies.grid.{GridLevel, build_grid_levels}` (Plan 2, UNCHANGED)
- Produces: `GridState` (dataclass: `levels: list[GridLevel]`, `prev_price: Decimal | None = None`), `build_grid_state(params: dict) -> GridState`, `step_grid_live(state: GridState, current_price: Decimal, timestamp: int, engine: FifoEngine, symbol: str, params: dict) -> None`, `grid_state_to_json`/`grid_state_from_json`.

Plan 2's `run_grid`/`build_grid_levels`/`GridLevel` are for BACKTEST and stay completely unchanged (spec decision D2: backtest uses intrabar `[low, high]` touch on a full candle — a different algorithm from live's point-to-point comparison, not a variant of it). This task adds the LIVE side: a level triggers when price crosses through it between two consecutive polls (`prev_price` vs `current_price`), re-arms indefinitely (D1, same as backtest), and simultaneous triggers fill cheapest-first (D3, same as backtest) with the same same-poll-cycle guard backtest uses (a level bought this poll cannot also sell this same poll — same rationale as backtest's same-candle guard: a single prev→current transition doesn't prove the actual path touched both boundaries in a favorable order).

The test numbers below were chosen to exactly match Plan 2's already-verified backtest re-arm and D3 test fixtures (same fee/price/quantity setup, only the trigger mechanism changes from OHLC-touch to point-to-point-crossing) — no new arithmetic verification needed.

- [ ] **Step 1: Append the failing tests**

```python
# tests/test_strategy_grid.py (append)
from engine.strategies.grid import (
    GridState,
    build_grid_state,
    grid_state_from_json,
    grid_state_to_json,
    step_grid_live,
)


def test_step_grid_live_first_tick_never_triggers_only_records_price():
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    state = build_grid_state({"lower_bound": 100, "upper_bound": 200, "n_levels": 1,
                               "spacing": "arithmetic", "order_size_quote": 100})

    step_grid_live(state, Decimal("150"), 0, engine, "BTCUSDT",
                    {"lower_bound": 100, "upper_bound": 200, "n_levels": 1,
                     "spacing": "arithmetic", "order_size_quote": 100})

    assert engine.trades == []
    assert state.prev_price == Decimal("150")


def test_step_grid_live_rearms_across_multiple_ticks():
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    params = {"lower_bound": 100, "upper_bound": 200, "n_levels": 1,
              "spacing": "arithmetic", "order_size_quote": 100}
    state = build_grid_state(params)

    step_grid_live(state, Decimal("150"), 0, engine, "BTCUSDT", params)         # sets prev=150
    step_grid_live(state, Decimal("90"), 1, engine, "BTCUSDT", params)          # 150>100>=90 -> BUY
    step_grid_live(state, Decimal("250"), 2, engine, "BTCUSDT", params)         # 90<200<=250 -> SELL
    step_grid_live(state, Decimal("90"), 3, engine, "BTCUSDT", params)          # re-arm -> BUY

    assert len(engine.trades) == 3
    assert [t.side.value for t in engine.trades] == ["BUY", "SELL", "BUY"]
    assert engine.trades[0].total_cost == Decimal("100.1")
    assert engine.trades[1].realized_pnl == Decimal("99.8")
    assert engine.trades[2].total_cost == Decimal("100.1")
    assert engine.cash_balance == Decimal("999.6")


def test_step_grid_live_multi_level_fills_cheapest_first_rejects_rest():
    engine = FifoEngine(initial_cash=Decimal("200"), fee_pct=Decimal("0.001"))
    params = {"lower_bound": 100, "upper_bound": 200, "n_levels": 2,
              "spacing": "arithmetic", "order_size_quote": 150}
    state = build_grid_state(params)

    step_grid_live(state, Decimal("200"), 0, engine, "BTCUSDT", params)  # sets prev=200
    step_grid_live(state, Decimal("90"), 1, engine, "BTCUSDT", params)   # crosses both 150 and 100 down

    assert len(engine.trades) == 1
    assert engine.trades[0].price == Decimal("100")
    assert engine.trades[0].quantity == Decimal("1.5")
    assert engine.cash_balance == Decimal("49.85")


def test_step_grid_live_does_not_round_trip_within_same_poll():
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    params = {"lower_bound": 100, "upper_bound": 200, "n_levels": 1,
              "spacing": "arithmetic", "order_size_quote": 100}
    state = build_grid_state(params)

    step_grid_live(state, Decimal("150"), 0, engine, "BTCUSDT", params)   # sets prev=150
    step_grid_live(state, Decimal("250"), 1, engine, "BTCUSDT", params)   # crosses buy(100)? no. crosses sell(200)? level is EMPTY, no sell possible.

    # Neither buy nor sell should fire: price never touched buy_price=100 in this jump (150->250, upward)
    assert engine.trades == []


def test_grid_state_json_round_trip():
    state = build_grid_state({"lower_bound": 100, "upper_bound": 200, "n_levels": 1,
                               "spacing": "arithmetic", "order_size_quote": 100})
    state.levels[0].state = "FILLED"
    state.levels[0].filled_quantity = Decimal("1")
    state.prev_price = Decimal("90")

    restored = grid_state_from_json(grid_state_to_json(state))

    assert restored.prev_price == Decimal("90")
    assert restored.levels[0].state == "FILLED"
    assert restored.levels[0].filled_quantity == Decimal("1")
    assert restored.levels[0].buy_price == Decimal("100")
    assert restored.levels[0].sell_price == Decimal("200")


def test_grid_state_json_round_trip_fresh_state():
    state = build_grid_state({"lower_bound": 100, "upper_bound": 200, "n_levels": 1,
                               "spacing": "arithmetic", "order_size_quote": 100})
    restored = grid_state_from_json(grid_state_to_json(state))
    assert restored.prev_price is None
    assert restored.levels[0].state == "EMPTY"
    assert restored.levels[0].filled_quantity is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_strategy_grid.py -v`
Expected: FAIL with `ImportError: cannot import name 'GridState' from 'engine.strategies.grid'`; all pre-existing grid tests still pass.

- [ ] **Step 3: Append to `engine/strategies/grid.py`**

Add to the top of the file, alongside the existing imports:
```python
import json
```

Append at the end of the file:

```python
@dataclass
class GridState:
    levels: list[GridLevel]
    prev_price: Decimal | None = None


def build_grid_state(params: dict) -> GridState:
    levels = build_grid_levels(
        params["lower_bound"], params["upper_bound"], int(params["n_levels"]), params["spacing"]
    )
    return GridState(levels=levels)


def step_grid_live(
    state: GridState, current_price: Decimal, timestamp: int, engine: FifoEngine, symbol: str, params: dict
) -> None:
    """Spec section 8, D2 live side: a level triggers when price crosses through
    it between two consecutive polls, not via an intrabar OHLC touch (there is
    no OHLC in live polling — just point samples). D1 (re-arm) and D3
    (cheapest-first fill) apply identically to the backtest side. The first-ever
    call for a fresh state has no prior price to compare against, so it can only
    record the current price, never trigger — this is unavoidable with a
    point-to-point crossing detector."""
    order_size_quote = Decimal(str(params["order_size_quote"]))

    if state.prev_price is not None:
        just_filled = []
        buy_candidates = sorted(
            (lvl for lvl in state.levels if lvl.state == "EMPTY" and state.prev_price > lvl.buy_price >= current_price),
            key=lambda lvl: lvl.buy_price,
        )
        for lvl in buy_candidates:
            quantity = order_size_quote / lvl.buy_price
            trade = engine.buy(timestamp, symbol, lvl.buy_price, quantity, "grid")
            if trade is not None:
                lvl.state = "FILLED"
                lvl.filled_quantity = quantity
                just_filled.append(lvl)

        for lvl in state.levels:
            if lvl not in just_filled and lvl.state == "FILLED" and state.prev_price < lvl.sell_price <= current_price:
                trade = engine.sell(timestamp, symbol, lvl.sell_price, lvl.filled_quantity, "grid")
                if trade is not None:
                    lvl.state = "EMPTY"
                    lvl.filled_quantity = None

    state.prev_price = current_price


def grid_state_to_json(state: GridState) -> str:
    return json.dumps({
        "levels": [
            {
                "buy_price": str(lvl.buy_price),
                "sell_price": str(lvl.sell_price),
                "state": lvl.state,
                "filled_quantity": str(lvl.filled_quantity) if lvl.filled_quantity is not None else None,
            }
            for lvl in state.levels
        ],
        "prev_price": str(state.prev_price) if state.prev_price is not None else None,
    })


def grid_state_from_json(s: str) -> GridState:
    data = json.loads(s)
    levels = [
        GridLevel(
            buy_price=Decimal(lvl["buy_price"]),
            sell_price=Decimal(lvl["sell_price"]),
            state=lvl["state"],
            filled_quantity=Decimal(lvl["filled_quantity"]) if lvl["filled_quantity"] is not None else None,
        )
        for lvl in data["levels"]
    ]
    prev_price = Decimal(data["prev_price"]) if data["prev_price"] is not None else None
    return GridState(levels=levels, prev_price=prev_price)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_strategy_grid.py -v`
Expected: PASS (all Plan 2 grid tests + 6 new ones)

- [ ] **Step 5: Commit**

```bash
git add engine/strategies/grid.py tests/test_strategy_grid.py
git commit -m "feat: add grid live-side state and step_grid_live() (D2 poll-to-poll crossing)"
```

---

## Task 6: Live engine — single cycle (fetch, step, persist, advance state)

**Files:**
- Create: `live_engine.py`
- Test: `tests/test_live_engine_cycle.py`

**Interfaces:**
- Consumes: `db.repository.{insert_trade, insert_snapshot, replace_lots_for_symbol, get_engine_state, set_engine_state}` (Task 2), `engine.strategies.{buy_hold, dca, grid}` state/step/JSON functions (Tasks 3-5), `engine.fifo_engine.FifoEngine` (Plan 1), `market_data.provider.MarketDataProvider` (Plan 1)
- Produces: `run_cycle(conn: sqlite3.Connection, provider: MarketDataProvider, engine: FifoEngine, symbol: str, strategy_type: str, params: dict, poll_interval: str, trade_id_map: dict[int, int]) -> None`, `reconstruct_engine_from_db(conn: sqlite3.Connection, symbol: str, initial_cash: Decimal, fee_pct: Decimal) -> FifoEngine`

One cycle: fetch the latest kline, load (or initialize) this `(symbol, strategy_type)`'s persisted state and last-processed timestamp from `engine_state`, skip if this candle was already processed (resume safety — never replay a signal), call the right strategy's `step()`, persist any new trade(s)/lot(s) the engine recorded this cycle plus a snapshot, then save the updated state and timestamp back to `engine_state`.

**Why `trade_id_map` exists (a bug caught during Task 2's review, fixed here before it ever shipped):** `FifoEngine.Lot.trade_id_achat` is the engine's own private in-memory trade counter (`self._next_trade_id`, starts at 1 per engine instance) — it is NOT the SQLite-assigned `trades.id` that `insert_trade` returns. Persisting a `Lot` straight from `engine.get_lots()` into `lots.trade_id_achat` would silently write the wrong foreign key. `trade_id_map` is a plain `dict[int, int]` (engine trade id → real DB trade id) that the CALLER (Task 8's polling loop) creates once, empty, at process start and passes into every `run_cycle` call for the lifetime of that process — `run_cycle` records a new entry every time it inserts a trade, and uses the map to translate `lot.trade_id_achat` before calling `replace_lots_for_symbol`. It is deliberately never persisted to the DB or to `engine_state`: a lot reconstructed from the DB (via `reconstruct_engine_from_db`, below) already carries a real DB id in `trade_id_achat`, so translation is only ever needed for trades created since the current process started — exactly what a fresh, empty, in-memory dict gives you for free, with no stale-mapping risk across restarts.

**Why `reconstruct_engine_from_db` exists:** the spec requires state to "survive a reboot" — a live engine that starts every restart with a fresh, empty `FifoEngine` (zero lots, cash reset to the configured initial capital) isn't actually resuming, even though its strategy-level state (DCA's last buy time, grid's level states) does correctly resume via `engine_state`. `reconstruct_engine_from_db` rebuilds `cash_balance` from the most recent trade's `cash_balance_after` for that symbol (or leaves the configured initial capital untouched if there are no trades yet) and rebuilds `engine.lots` directly from the `lots` table — which, thanks to `replace_lots_for_symbol`'s per-cycle resync, is always an accurate mirror of the engine's last known open positions.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_live_engine_cycle.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_live_engine_cycle.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'live_engine'`

- [ ] **Step 3: Write `live_engine.py`**

```python
# live_engine.py
import logging
import sqlite3
from decimal import Decimal

from db.repository import get_engine_state, insert_snapshot, insert_trade, replace_lots_for_symbol, set_engine_state
from engine.fifo_engine import FifoEngine, Lot
from engine.strategies import base as strategy_base
from engine.strategies.buy_hold import BuyHoldState, buy_hold_state_from_json, buy_hold_state_to_json
from engine.strategies.buy_hold import step as buy_hold_step
from engine.strategies.dca import DcaState, dca_state_from_json, dca_state_to_json
from engine.strategies.dca import step as dca_step
from engine.strategies.grid import GridState, build_grid_state, grid_state_from_json, grid_state_to_json
from engine.strategies.grid import step_grid_live
from market_data.provider import MarketDataProvider

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
) -> None:
    klines = provider.get_klines(symbol, poll_interval, 0, 0, limit=1)
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
    return engine
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_live_engine_cycle.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add live_engine.py tests/test_live_engine_cycle.py
git commit -m "feat: add live engine single-cycle function (fetch, step, persist, resume-safe)"
```

---

## Task 7: Live engine — retry/backoff on provider failure

**Files:**
- Modify: `live_engine.py`
- Test: `tests/test_live_engine_retry.py`

**Interfaces:**
- Produces: `fetch_with_retry(provider: MarketDataProvider, symbol: str, poll_interval: str, on_critical_failure: Callable[[str], None]) -> list[Kline]` — 1 retry with backoff (1s, then 4s) on failure; after 3 consecutive failures, calls `on_critical_failure(message)` and returns `[]` (never raises out of this function, so the polling loop never crashes on a bad cycle).

Spec section 5: "en cas d'echec de l'appel a la source de donnees — 1 retry avec backoff exponentiel (1s, puis 4s); apres 3 echecs consecutifs, declenche send_alert(...) et passe le cycle sans crasher le process." `on_critical_failure` is a plain callback parameter here — Plan 4 wires it to `discord_notifier.send_alert`, this plan doesn't know Discord exists.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_live_engine_retry.py
from decimal import Decimal

import httpx

from live_engine import fetch_with_retry
from market_data.provider import MarketDataProvider
from market_data.types import Kline


class FlakyProvider(MarketDataProvider):
    def __init__(self, fail_count: int):
        self.fail_count = fail_count
        self.calls = 0

    def get_klines(self, symbol, interval, start_ms, end_ms, limit=1000):
        self.calls += 1
        if self.calls <= self.fail_count:
            raise httpx.HTTPError("boom")
        return [Kline(open_time_ms=0, open=Decimal("1"), high=Decimal("1"),
                       low=Decimal("1"), close=Decimal("1"), volume=Decimal("1"),
                       close_time_ms=299_999)]

    def get_book_ticker(self, symbol):
        raise NotImplementedError

    def get_ticker_24h(self, symbol):
        raise NotImplementedError


def test_fetch_with_retry_succeeds_after_one_failure(monkeypatch):
    sleeps = []
    monkeypatch.setattr("live_engine.time.sleep", lambda s: sleeps.append(s))
    provider = FlakyProvider(fail_count=1)
    alerts = []

    result = fetch_with_retry(provider, "BTCUSDT", "5m", on_critical_failure=alerts.append)

    assert len(result) == 1
    assert sleeps == [1]  # one retry, 1s backoff
    assert alerts == []


def test_fetch_with_retry_gives_up_after_three_failures_and_alerts(monkeypatch):
    sleeps = []
    monkeypatch.setattr("live_engine.time.sleep", lambda s: sleeps.append(s))
    provider = FlakyProvider(fail_count=10)  # always fails
    alerts = []

    result = fetch_with_retry(provider, "BTCUSDT", "5m", on_critical_failure=alerts.append)

    assert result == []
    assert len(alerts) == 1
    assert "BTCUSDT" in alerts[0]
    assert provider.calls == 3  # exactly 3 attempts, no more


def test_fetch_with_retry_no_alert_when_never_fails(monkeypatch):
    monkeypatch.setattr("live_engine.time.sleep", lambda s: None)
    provider = FlakyProvider(fail_count=0)
    alerts = []

    result = fetch_with_retry(provider, "BTCUSDT", "5m", on_critical_failure=alerts.append)

    assert len(result) == 1
    assert alerts == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_live_engine_retry.py -v`
Expected: FAIL with `ImportError: cannot import name 'fetch_with_retry' from 'live_engine'`

- [ ] **Step 3: Add to `live_engine.py`**

Add to the top of the file, alongside the existing imports:
```python
import time
from typing import Callable

import httpx

from market_data.types import Kline
```

Append at the end of the file:

```python
_RETRY_BACKOFF_SECONDS = [1, 4]


def fetch_with_retry(
    provider: MarketDataProvider, symbol: str, poll_interval: str,
    on_critical_failure: Callable[[str], None],
) -> list["Kline"]:
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_live_engine_retry.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add live_engine.py tests/test_live_engine_retry.py
git commit -m "feat: add retry/backoff (1s, 4s) with critical-failure callback after 3 attempts"
```

---

## Task 8: Live engine — polling loop with graceful resume

**Files:**
- Modify: `live_engine.py`
- Test: `tests/test_live_engine_loop.py`

**Interfaces:**
- Produces: `run_polling_loop(conn, provider, engine, symbol, strategy_type, params, poll_interval, poll_interval_seconds, on_critical_failure, max_cycles=None) -> None` — sleeps `poll_interval_seconds` between cycles, calls `fetch_with_retry` + `run_cycle`'s logic each time, runs forever unless `max_cycles` is given (test-only escape hatch).

This wires `fetch_with_retry` (Task 7) and the resume/persist logic (Task 6) into an actual loop. `run_cycle` already re-fetches via `provider.get_klines` directly; to route through retry, this task changes `run_cycle` to accept `fetch_fn: Callable[[], list[Kline]]` as an optional injection point defaulting to a direct provider call, and `run_polling_loop` passes `lambda: fetch_with_retry(...)` — read Task 6's existing `run_cycle` carefully before changing it, since Task 6's own tests must keep passing unmodified.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_live_engine_loop.py
from decimal import Decimal

import pytest

from db.migrate import init_db
from engine.fifo_engine import FifoEngine
from live_engine import run_polling_loop
from market_data.provider import MarketDataProvider
from market_data.types import Kline


class SequenceProvider(MarketDataProvider):
    def __init__(self, klines: list[Kline]):
        self._klines = klines
        self._index = 0

    def get_klines(self, symbol, interval, start_ms, end_ms, limit=1000):
        if self._index >= len(self._klines):
            return [self._klines[-1]]  # keep serving the last one (simulates "no new candle yet")
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


def test_run_polling_loop_stops_after_max_cycles_and_processes_new_candles(conn, monkeypatch):
    monkeypatch.setattr("live_engine.time.sleep", lambda s: None)
    provider = SequenceProvider([_kline(0, "100"), _kline(300_000, "110"), _kline(600_000, "120")])
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    params = {"invest_at": "start"}

    run_polling_loop(
        conn, provider, engine, "BTCUSDT", "buy_hold", params,
        poll_interval="5m", poll_interval_seconds=300,
        on_critical_failure=lambda msg: None, max_cycles=3,
    )

    # buy_hold only ever buys once, on the first genuinely-new candle it sees
    assert len(engine.trades) == 1
    assert len(conn.execute("SELECT * FROM portfolio_snapshots").fetchall()) == 3


def test_run_polling_loop_sleeps_between_cycles(conn, monkeypatch):
    sleeps = []
    monkeypatch.setattr("live_engine.time.sleep", lambda s: sleeps.append(s))
    provider = SequenceProvider([_kline(0, "100"), _kline(300_000, "100")])
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))

    run_polling_loop(
        conn, provider, engine, "BTCUSDT", "buy_hold", {"invest_at": "start"},
        poll_interval="5m", poll_interval_seconds=300,
        on_critical_failure=lambda msg: None, max_cycles=2,
    )

    # No sleep after the final cycle (mirrors Plan 1's pagination convention of no
    # trailing sleep once there's nothing left to do) -- with max_cycles=2 that's
    # exactly 1 sleep, between cycle 1 and cycle 2.
    assert sleeps == [300]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_live_engine_loop.py -v`
Expected: FAIL with `ImportError: cannot import name 'run_polling_loop' from 'live_engine'`

- [ ] **Step 3: Refactor `run_cycle` and add `run_polling_loop` in `live_engine.py`**

Change `run_cycle`'s signature (add `fetch_fn` as an optional keyword parameter AFTER the existing required `trade_id_map` parameter from Task 6 — do not reorder or remove `trade_id_map`) and its first two lines (replace the existing `klines = provider.get_klines(...)` line) to accept an injectable fetch function:

```python
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
    # ... rest of the function body is UNCHANGED from Task 6 (still uses trade_id_map exactly as before)
```

Append at the end of the file:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_live_engine_loop.py -v` then `pytest -v` (full suite, confirming Task 6's tests still pass with the `run_cycle` signature change — they call it without `fetch_fn`, which defaults to `None` and falls back to the direct provider call, identical to before)
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add live_engine.py tests/test_live_engine_loop.py
git commit -m "feat: add live engine polling loop wiring retry/backoff into each cycle"
```

---

## Task 9: Snapshot retention housekeeping

**Files:**
- Create: `housekeeping.py`
- Test: `tests/test_housekeeping.py`

**Interfaces:**
- Consumes: `sqlite3.Connection` (Task 1)
- Produces: `aggregate_old_snapshots(conn: sqlite3.Connection, now_ms: int, retention_days: int) -> int` (returns the number of detail rows replaced) — for each `symbol`, groups `portfolio_snapshots` rows older than `now_ms - retention_days*86_400_000` into one-hour buckets, replacing each bucket's rows with a single row holding the AVERAGE of each Decimal column (computed in `Decimal`, not `float`), timestamped at the bucket's start.

Spec section 5: "garder le detail 5 min sur une fenetre glissante de 30 jours; au-dela, une tache de housekeeping quotidienne agrege en un point horaire (moyenne)."

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_housekeeping.py
from decimal import Decimal

import pytest

from db.migrate import init_db
from db.repository import insert_snapshot
from housekeeping import aggregate_old_snapshots
from models import PortfolioSnapshot

HOUR_MS = 3_600_000
DAY_MS = 86_400_000


@pytest.fixture
def conn():
    c = init_db(":memory:")
    yield c
    c.close()


def _snap(ts: int, total_value: str) -> PortfolioSnapshot:
    v = Decimal(total_value)
    return PortfolioSnapshot(
        timestamp=ts, symbol="BTCUSDT", cash_balance=v, position_value=Decimal("0"),
        total_value=v, unrealized_pnl=Decimal("0"), realized_pnl_cumule=Decimal("0"),
    )


def test_aggregates_old_snapshots_into_hourly_average(conn):
    now_ms = 40 * DAY_MS  # "today" is day 40
    old_bucket_start = 0  # hour 0 of day 0, well past the 30-day window
    # 3 five-minute snapshots inside the same hour bucket, all older than 30 days
    insert_snapshot(conn, _snap(old_bucket_start, "1000"))
    insert_snapshot(conn, _snap(old_bucket_start + 5 * 60_000, "1010"))
    insert_snapshot(conn, _snap(old_bucket_start + 10 * 60_000, "1020"))
    # 1 snapshot inside the 30-day retention window -- must NOT be touched
    recent_ts = now_ms - 1 * DAY_MS
    insert_snapshot(conn, _snap(recent_ts, "2000"))

    replaced = aggregate_old_snapshots(conn, now_ms=now_ms, retention_days=30)

    rows = conn.execute("SELECT * FROM portfolio_snapshots ORDER BY timestamp").fetchall()
    assert replaced == 3
    assert len(rows) == 2  # 3 old rows collapsed into 1, plus the 1 recent row untouched
    assert rows[0]["timestamp"] == old_bucket_start
    # average of 1000, 1010, 1020 = 1010 exactly
    assert Decimal(rows[0]["total_value"]) == Decimal("1010")
    assert Decimal(rows[1]["total_value"]) == Decimal("2000")
    assert rows[1]["timestamp"] == recent_ts


def test_aggregate_is_idempotent_second_run_does_nothing(conn):
    now_ms = 40 * DAY_MS
    insert_snapshot(conn, _snap(0, "1000"))
    insert_snapshot(conn, _snap(5 * 60_000, "1010"))

    first = aggregate_old_snapshots(conn, now_ms=now_ms, retention_days=30)
    second = aggregate_old_snapshots(conn, now_ms=now_ms, retention_days=30)

    assert first == 2
    assert second == 0  # already a single aggregated row per bucket, nothing left to collapse


def test_aggregate_no_old_snapshots_returns_zero(conn):
    now_ms = 40 * DAY_MS
    insert_snapshot(conn, _snap(now_ms - 1_000, "1000"))

    replaced = aggregate_old_snapshots(conn, now_ms=now_ms, retention_days=30)

    assert replaced == 0
    assert len(conn.execute("SELECT * FROM portfolio_snapshots").fetchall()) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_housekeeping.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'housekeeping'`

- [ ] **Step 3: Write `housekeeping.py`**

```python
# housekeeping.py
import sqlite3
from decimal import Decimal

HOUR_MS = 3_600_000
DAY_MS = 86_400_000

_DECIMAL_COLUMNS = ["cash_balance", "position_value", "total_value", "unrealized_pnl", "realized_pnl_cumule"]


def aggregate_old_snapshots(conn: sqlite3.Connection, now_ms: int, retention_days: int) -> int:
    cutoff = now_ms - retention_days * DAY_MS
    rows = conn.execute(
        "SELECT * FROM portfolio_snapshots WHERE timestamp < ? ORDER BY symbol, timestamp",
        (cutoff,),
    ).fetchall()

    buckets: dict[tuple[str, int], list[sqlite3.Row]] = {}
    for row in rows:
        bucket_start = row["timestamp"] - (row["timestamp"] % HOUR_MS)
        buckets.setdefault((row["symbol"], bucket_start), []).append(row)

    replaced = 0
    for (symbol, bucket_start), bucket_rows in buckets.items():
        if len(bucket_rows) <= 1:
            continue  # already a single row for this bucket -- nothing to collapse
        ids = [r["id"] for r in bucket_rows]
        averages = {
            col: sum((Decimal(r[col]) for r in bucket_rows), Decimal("0")) / Decimal(len(bucket_rows))
            for col in _DECIMAL_COLUMNS
        }
        conn.execute(f"DELETE FROM portfolio_snapshots WHERE id IN ({','.join('?' * len(ids))})", ids)
        conn.execute(
            """
            INSERT INTO portfolio_snapshots
                (timestamp, symbol, cash_balance, position_value, total_value, unrealized_pnl, realized_pnl_cumule)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bucket_start, symbol, str(averages["cash_balance"]), str(averages["position_value"]),
                str(averages["total_value"]), str(averages["unrealized_pnl"]), str(averages["realized_pnl_cumule"]),
            ),
        )
        replaced += len(bucket_rows)

    conn.commit()
    return replaced
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_housekeeping.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add housekeeping.py tests/test_housekeeping.py
git commit -m "feat: add snapshot retention housekeeping (30-day window, hourly aggregation)"
```

---

## Task 10: `live_engine.py` main() CLI + README update

**Files:**
- Modify: `live_engine.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: everything from Tasks 1-9, `config.load_config` (Plan 1), `market_data.factory.build_provider` (Plan 2)
- Produces: `main() -> None` (CLI entry point, not covered by the automated suite — makes real network calls and runs forever, same precedent as `backtest.py`'s `main()`)

`main()` reads `strategy_configs` (this plan's schema has the table, but nothing populates it yet — that's the dashboard's job in Plan 5). For now, `main()` reads its single active strategy from `config.yaml`'s existing `strategy_defaults` (Plan 2) plus a new top-level `live.active_symbol`/`live.active_strategy` pair — read `config.py`/`config.yaml` first to add these two keys consistently with the existing `LiveConfig` dataclass pattern.

- [ ] **Step 1: Add `active_symbol`/`active_strategy` to `LiveConfig` in `config.py` and `config.yaml`**

In `config.yaml`, add two keys under the existing `live:` section:
```yaml
live:
  poll_interval_seconds: 300
  poll_kline_interval: 5m
  active_symbol: BTCUSDT
  active_strategy: dca
```

In `config.py`'s `LiveConfig` dataclass, add `active_symbol: str` and `active_strategy: str` fields, and in `load_config()`'s `LiveConfig(...)` construction, add `active_symbol=raw["live"]["active_symbol"]` and `active_strategy=raw["live"]["active_strategy"]`.

Update `tests/test_config.py`'s sample YAML and assertions to include these two new keys (add them to the `live:` block in the test's YAML string, and assert `cfg.live.active_symbol == "BTCUSDT"` / `cfg.live.active_strategy == "dca"`).

- [ ] **Step 2: Append `main()` to `live_engine.py`**

Add to the top of the file, alongside the existing imports:
```python
from config import load_config
from db.migrate import init_db
from market_data.factory import build_provider
```

Append at the end of the file:

```python
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
```

- [ ] **Step 3: Update `README.md`**

Replace the "État actuel" section:

```markdown
## État actuel

Plan 1/6 (fondation) : `MarketDataProvider` (Binance + repli Kraken), moteur FIFO, `analytics.py` — termine.
Plan 2/6 (backtest) : filtre de liquidite, 3 strategies, `backtest.py` (telechargement, rejeu, exports CSV) — termine.
Plan 3/6 (moteur live) : persistance SQLite (WAL), strategies pilotables au poll (`step()`), `live_engine.py`
(polling, reprise sur incident, retry/backoff, housekeeping snapshots) — termine.

Lancer le backtest : `python backtest.py`
Lancer le moteur live : `python live_engine.py` (tourne indefiniment, Ctrl+C pour arreter)

Pas encore de notifications Discord, de dashboard ni de deploiement — voir les plans suivants dans
`docs/superpowers/plans/`.
```

- [ ] **Step 4: Run the full test suite**

Run: `pytest -v`
Expected: all tests from Plans 1-2 plus all tests from Tasks 1-9 of this plan pass — 0 failures. `main()` and `run_polling_loop`'s infinite-loop behavior are intentionally not covered by automated tests (same precedent as `backtest.py`'s `main()` — real network calls, and an infinite loop without `max_cycles`).

- [ ] **Step 5: Commit**

```bash
git add config.py config.yaml tests/test_config.py live_engine.py README.md
git commit -m "feat: add live_engine.py main() CLI, active symbol/strategy config"
```

---

## Task 11: Full suite verification

**Files:**
- (no new files — verification checkpoint)

**Interfaces:**
- Consumes: everything built in Tasks 1-10
- Produces: nothing new.

- [ ] **Step 1: Run the full test suite**

Run: `pytest -v`
Expected: all tests pass — 0 failures.
