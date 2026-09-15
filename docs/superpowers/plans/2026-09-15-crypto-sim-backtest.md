# Crypto Paper-Trading Simulator — Backtest (Plan 2 of 5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the backtest script: a provider factory, portfolio valuation helpers on the FIFO engine, three strategy implementations (Buy & Hold, DCA, Grid trading) driven by the FIFO engine, a liquidity filter, and `backtest.py` orchestrating watchlist filtering, historical download, strategy replay, and CSV/console reporting.

**Architecture:** Strategies are pure functions `run_X(klines, engine, symbol, params) -> list[PortfolioSnapshot]` that only call `engine.buy()`/`engine.sell()` and read `Kline` data — no I/O, no provider knowledge. `backtest.py` is the only module that wires `MarketDataProvider` + strategies + `analytics.py` together; it is the composition root. `analytics.py` and `engine/fifo_engine.py` gain small, additive extensions (never modifying existing approved behavior).

**Tech Stack:** Same as Plan 1 — Python 3.13, `httpx`, `PyYAML`, `pytest`, stdlib `decimal`/`dataclasses`/`csv`.

**Spec:** `docs/superpowers/specs/2026-09-15-crypto-paper-trading-sim-design.md` — this plan implements sections 5 (watchlist/liquidity filter), 3 (strategy JSON schemas), 8 (grid clarifications D1-D3), 9 (backtest), and closes five gaps the Plan 1 final review explicitly deferred here (provider factory, engine valuation helpers, `trade_stats` export, `analytics_report.csv`, pagination-coverage awareness). Read both.

## Global Constraints

- All price/quantity/PnL/cash values are `decimal.Decimal`, never `float` — including strategy params read from JSON/YAML, which must go through `Decimal(str(...))`, never bare `Decimal(x)` (a lesson from Plan 1's final review: JSON numbers can arrive as Python `float`).
- Grid trading (spec decisions D1-D3, already ruled on 2026-09-15 — not open for re-litigation):
  - **D1:** a grid level re-arms every time price crosses it, for the whole run.
  - **D2 (backtest side):** a level is triggered if `low <= level_price <= high` on the candle (intrabar touch, using the full OHLC — this plan only implements the backtest side; Plan 3's live engine implements the poll-to-poll comparison side separately).
  - **D3:** when multiple levels trigger a BUY in the same candle and cash can't cover all of them, fill cheapest-price-first, reject the rest individually (the engine already does this naturally — reject is per-call, not batched).
- A SELL requesting more than is available is rejected in full (spec D4, already enforced by `FifoEngine.sell()` from Plan 1 — nothing in this plan may bypass it).
- The rest of the pipeline never talks to an exchange directly, only through `MarketDataProvider` — `backtest.py` obtains its provider via the new `market_data/factory.py`, never `from market_data.binance import BinanceProvider`.
- Thresholds/amounts live in `config.yaml` (already true from Plan 1) — this plan adds no new hardcoded thresholds.
- No database in this plan (matches Plan 1's precedent) — `backtest.py` is a standalone script producing CSV + console output; SQLite persistence is Plan 3's concern (live engine).

---

## Task 1: Provider factory

**Files:**
- Create: `market_data/factory.py`
- Test: `tests/test_factory.py`

**Interfaces:**
- Consumes: `market_data.provider.MarketDataProvider`, `market_data.binance.BinanceProvider`, `market_data.kraken.KrakenProvider`
- Produces: `build_provider(data_source: str) -> MarketDataProvider`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_factory.py
import pytest

from market_data.binance import BinanceProvider
from market_data.factory import build_provider
from market_data.kraken import KrakenProvider


def test_build_provider_binance():
    provider = build_provider("binance")
    assert isinstance(provider, BinanceProvider)


def test_build_provider_kraken():
    provider = build_provider("kraken")
    assert isinstance(provider, KrakenProvider)


def test_build_provider_unknown_raises_value_error():
    with pytest.raises(ValueError, match="stellar"):
        build_provider("stellar")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_factory.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'market_data.factory'`

- [ ] **Step 3: Write `market_data/factory.py`**

```python
# market_data/factory.py
from market_data.binance import BinanceProvider
from market_data.kraken import KrakenProvider
from market_data.provider import MarketDataProvider


def build_provider(data_source: str) -> MarketDataProvider:
    """The only place that maps config.yaml's data_source string to a concrete
    provider. Callers (backtest.py, the live engine) must go through this —
    never import BinanceProvider/KrakenProvider directly (spec section 4)."""
    if data_source == "binance":
        return BinanceProvider()
    if data_source == "kraken":
        return KrakenProvider()
    raise ValueError(f"data_source inconnu: {data_source}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_factory.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add market_data/factory.py tests/test_factory.py
git commit -m "feat: add MarketDataProvider factory (data_source string -> instance)"
```

---

## Task 2: FIFO engine valuation helpers

**Files:**
- Modify: `engine/fifo_engine.py`
- Modify: `tests/test_fifo_engine.py`

**Interfaces:**
- Consumes: `engine.fifo_engine.FifoEngine` (Plan 1)
- Produces: `FifoEngine.unrealized_pnl(symbol: str, current_price: Decimal) -> Decimal`, `FifoEngine.realized_pnl_cumule(symbol: str | None = None) -> Decimal`

These close a gap the Plan 1 final review flagged: without them, the backtest, live engine, and dashboard would each hand-roll portfolio valuation independently (three chances to diverge).

- [ ] **Step 1: Append the failing tests**

```python
# tests/test_fifo_engine.py (append)

def test_unrealized_pnl_sums_open_lots_at_current_price():
    engine = FifoEngine(initial_cash=Decimal("10000"), fee_pct=Decimal("0.001"))
    engine.buy(timestamp=1000, symbol="BTCUSDT", price=Decimal("100"), quantity=Decimal("2"), strategy_name="t")
    engine.buy(timestamp=2000, symbol="BTCUSDT", price=Decimal("110"), quantity=Decimal("3"), strategy_name="t")

    unrealized = engine.unrealized_pnl("BTCUSDT", current_price=Decimal("120"))

    # (120-100)*2 + (120-110)*3 = 40 + 30 = 70
    assert unrealized == Decimal("70")


def test_unrealized_pnl_is_zero_with_no_open_lots():
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    assert engine.unrealized_pnl("BTCUSDT", current_price=Decimal("100")) == Decimal("0")


def test_realized_pnl_cumule_accumulates_across_sells():
    engine = FifoEngine(initial_cash=Decimal("10000"), fee_pct=Decimal("0.001"))
    engine.buy(timestamp=1000, symbol="BTCUSDT", price=Decimal("100"), quantity=Decimal("2"), strategy_name="t")
    engine.buy(timestamp=2000, symbol="BTCUSDT", price=Decimal("110"), quantity=Decimal("3"), strategy_name="t")

    assert engine.realized_pnl_cumule("BTCUSDT") == Decimal("0")

    engine.sell(timestamp=3000, symbol="BTCUSDT", price=Decimal("130"), quantity=Decimal("2"), strategy_name="t")
    # (130-100)*2 - fee(260*0.001=0.26) = 60 - 0.26 = 59.74
    assert engine.realized_pnl_cumule("BTCUSDT") == Decimal("59.74")

    engine.sell(timestamp=4000, symbol="BTCUSDT", price=Decimal("140"), quantity=Decimal("1"), strategy_name="t")
    # (140-110)*1 - fee(140*0.001=0.14) = 30 - 0.14 = 29.86
    # cumulative: 59.74 + 29.86 = 89.60
    assert engine.realized_pnl_cumule("BTCUSDT") == Decimal("89.60")


def test_realized_pnl_cumule_with_no_symbol_filter_sums_everything():
    engine = FifoEngine(initial_cash=Decimal("10000"), fee_pct=Decimal("0.001"))
    engine.buy(timestamp=1000, symbol="BTCUSDT", price=Decimal("100"), quantity=Decimal("1"), strategy_name="t")
    engine.sell(timestamp=2000, symbol="BTCUSDT", price=Decimal("110"), quantity=Decimal("1"), strategy_name="t")

    assert engine.realized_pnl_cumule() == engine.realized_pnl_cumule("BTCUSDT")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_fifo_engine.py -v`
Expected: FAIL — `AttributeError: 'FifoEngine' object has no attribute 'unrealized_pnl'` (and `realized_pnl_cumule`) on the 4 new tests; all Plan 1 tests still pass.

- [ ] **Step 3: Add the two methods to `FifoEngine`** (in `engine/fifo_engine.py`, after `position_value`)

```python
# engine/fifo_engine.py (add methods to FifoEngine, after position_value())

    def unrealized_pnl(self, symbol: str, current_price: Decimal) -> Decimal:
        return sum(
            ((current_price - lot.prix_achat) * lot.quantity_restante for lot in self.get_lots(symbol)),
            Decimal("0"),
        )

    def realized_pnl_cumule(self, symbol: str | None = None) -> Decimal:
        sells = [
            t for t in self.trades
            if t.side == Side.SELL and t.realized_pnl is not None and (symbol is None or t.symbol == symbol)
        ]
        return sum((t.realized_pnl for t in sells), Decimal("0"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_fifo_engine.py -v`
Expected: PASS (12 passed)

- [ ] **Step 5: Commit**

```bash
git add engine/fifo_engine.py tests/test_fifo_engine.py
git commit -m "feat: add FifoEngine.unrealized_pnl and realized_pnl_cumule valuation helpers"
```

---

## Task 3: analytics.py — trade_stats() and expectancy() refactor

**Files:**
- Modify: `analytics.py`
- Modify: `tests/test_analytics.py`

**Interfaces:**
- Consumes: `engine.fifo_engine.{Side, Trade}` (Plan 1)
- Produces: `trade_stats(trades: list[Trade]) -> dict[str, Decimal]` with keys `win_rate`, `loss_rate`, `avg_win`, `avg_loss`, `win_count`, `loss_count`, `total_sells`. `expectancy()` keeps its exact existing signature and return value — it now computes via `trade_stats()` internally instead of duplicating the win/loss split (this also resolves the Plan 1 final review's "duplicated sell-trade predicate 3x" minor finding).

This closes a Plan 1 final-review gap: the backtest report needs win_rate/avg_win/avg_loss as their own columns (spec §9), not just folded into a single `expectancy` number.

- [ ] **Step 1: Append the failing tests**

```python
# tests/test_analytics.py (append)

def test_trade_stats_matches_hand_computed_values():
    trades = [_trade(Decimal("50")), _trade(Decimal("-20")), _trade(Decimal("80")),
              _trade(Decimal("-30")), _trade(Decimal("10"))]

    stats = analytics.trade_stats(trades)

    assert stats["win_rate"] == Decimal("0.6")
    assert stats["loss_rate"] == Decimal("0.4")
    assert stats["avg_win"].quantize(Decimal("0.0001")) == Decimal("46.6667")
    assert stats["avg_loss"] == Decimal("25")
    assert stats["win_count"] == Decimal("3")
    assert stats["loss_count"] == Decimal("2")
    assert stats["total_sells"] == Decimal("5")


def test_trade_stats_empty_when_no_sells():
    stats = analytics.trade_stats([_trade(None, side=Side.BUY)])
    assert stats["win_rate"] == Decimal("0")
    assert stats["loss_rate"] == Decimal("0")
    assert stats["avg_win"] == Decimal("0")
    assert stats["avg_loss"] == Decimal("0")
    assert stats["total_sells"] == Decimal("0")


def test_expectancy_still_matches_after_refactor():
    trades = [_trade(Decimal("50")), _trade(Decimal("-20")), _trade(Decimal("80")),
              _trade(Decimal("-30")), _trade(Decimal("10"))]
    # unchanged from Plan 1: 0.6*(140/3) - 0.4*25 = 28 - 10 = 18
    assert analytics.expectancy(trades).quantize(Decimal("0.0001")) == Decimal("18.0000")
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `pytest tests/test_analytics.py -v`
Expected: FAIL with `AttributeError: module 'analytics' has no attribute 'trade_stats'` on the 2 new `trade_stats` tests; `test_expectancy_still_matches_after_refactor` currently PASSES already (expectancy is unchanged) — that's expected, it's a regression guard for the refactor in Step 3, not a red step.

- [ ] **Step 3: Add `trade_stats()` and refactor `expectancy()` in `analytics.py`**

Replace the existing `expectancy` function and add `trade_stats` right before it:

```python
# analytics.py (replace the existing `def expectancy(...)` function with these two)

def trade_stats(trades: list[Trade]) -> dict[str, Decimal]:
    sells = [t for t in trades if t.side == Side.SELL and t.realized_pnl is not None]
    wins = [t.realized_pnl for t in sells if t.realized_pnl > 0]
    losses = [-t.realized_pnl for t in sells if t.realized_pnl < 0]
    total = Decimal(len(sells))
    if total == 0:
        return {
            "win_rate": Decimal("0"), "loss_rate": Decimal("0"),
            "avg_win": Decimal("0"), "avg_loss": Decimal("0"),
            "win_count": Decimal("0"), "loss_count": Decimal("0"),
            "total_sells": Decimal("0"),
        }
    win_rate = Decimal(len(wins)) / total
    loss_rate = Decimal(len(losses)) / total
    avg_win = sum(wins, Decimal("0")) / Decimal(len(wins)) if wins else Decimal("0")
    avg_loss = sum(losses, Decimal("0")) / Decimal(len(losses)) if losses else Decimal("0")
    return {
        "win_rate": win_rate, "loss_rate": loss_rate,
        "avg_win": avg_win, "avg_loss": avg_loss,
        "win_count": Decimal(len(wins)), "loss_count": Decimal(len(losses)),
        "total_sells": total,
    }


def expectancy(trades: list[Trade]) -> Decimal:
    stats = trade_stats(trades)
    return stats["win_rate"] * stats["avg_win"] - stats["loss_rate"] * stats["avg_loss"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_analytics.py -v`
Expected: PASS (all analytics tests, including the 3 new ones)

- [ ] **Step 5: Commit**

```bash
git add analytics.py tests/test_analytics.py
git commit -m "feat: add analytics.trade_stats(), refactor expectancy() to use it"
```

---

## Task 4: Liquidity filter

**Files:**
- Create: `liquidity.py`
- Test: `tests/test_liquidity.py`

**Interfaces:**
- Consumes: `market_data.types.{Ticker24h, BookTicker}` (Plan 1), `config.LiquidityConfig` (Plan 1)
- Produces: `passes_liquidity_filter(ticker_24h: Ticker24h, book_ticker: BookTicker, config: LiquidityConfig) -> tuple[bool, str | None]`

Implements spec §5's exact two-filter rule: volume 24h (quote) must be strictly greater than the threshold, spread (in bps — using `BookTicker.spread_bps` from Plan 1's final-review fix, not `spread_relative`) must be strictly less than the threshold.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_liquidity.py
from decimal import Decimal

from config import LiquidityConfig
from liquidity import passes_liquidity_filter
from market_data.types import BookTicker, Ticker24h


def _config(min_volume="50000000", max_spread_bps="10") -> LiquidityConfig:
    return LiquidityConfig(
        min_quote_volume_24h=Decimal(min_volume),
        max_spread_bps=Decimal(max_spread_bps),
    )


def test_passes_when_volume_high_and_spread_tight():
    ticker = Ticker24h(symbol="BTCUSDT", quote_volume=Decimal("60000000"))
    book = BookTicker(symbol="BTCUSDT", bid_price=Decimal("100"), ask_price=Decimal("100.05"))
    ok, reason = passes_liquidity_filter(ticker, book, _config())
    assert ok is True
    assert reason is None


def test_rejects_when_volume_at_or_below_threshold():
    ticker = Ticker24h(symbol="BTCUSDT", quote_volume=Decimal("50000000"))
    book = BookTicker(symbol="BTCUSDT", bid_price=Decimal("100"), ask_price=Decimal("100.05"))
    ok, reason = passes_liquidity_filter(ticker, book, _config())
    assert ok is False
    assert reason == "volume insuffisant"


def test_rejects_when_spread_at_or_above_threshold():
    ticker = Ticker24h(symbol="BTCUSDT", quote_volume=Decimal("60000000"))
    # bid=100, ask=101 -> spread_relative = 1/100.5 -> spread_bps = 10000/100.5 = ~99.5 bps, well over 10
    book = BookTicker(symbol="BTCUSDT", bid_price=Decimal("100"), ask_price=Decimal("101"))
    ok, reason = passes_liquidity_filter(ticker, book, _config())
    assert ok is False
    assert reason == "spread trop large"


def test_volume_check_takes_priority_when_both_fail():
    ticker = Ticker24h(symbol="BTCUSDT", quote_volume=Decimal("1000"))
    book = BookTicker(symbol="BTCUSDT", bid_price=Decimal("100"), ask_price=Decimal("101"))
    ok, reason = passes_liquidity_filter(ticker, book, _config())
    assert ok is False
    assert reason == "volume insuffisant"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_liquidity.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'liquidity'`

- [ ] **Step 3: Write `liquidity.py`**

```python
# liquidity.py
from config import LiquidityConfig
from market_data.types import BookTicker, Ticker24h


def passes_liquidity_filter(
    ticker_24h: Ticker24h, book_ticker: BookTicker, config: LiquidityConfig
) -> tuple[bool, str | None]:
    """Spec section 5: volume 24h (quote) must be strictly > threshold, spread
    (bps) must be strictly < threshold. Volume is checked first — if both fail,
    the reported reason is 'volume insuffisant'."""
    if ticker_24h.quote_volume <= config.min_quote_volume_24h:
        return False, "volume insuffisant"
    if book_ticker.spread_bps >= config.max_spread_bps:
        return False, "spread trop large"
    return True, None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_liquidity.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add liquidity.py tests/test_liquidity.py
git commit -m "feat: add liquidity filter (spec section 5 volume/spread thresholds)"
```

---

## Task 5: Strategy package scaffolding + Buy & Hold

**Files:**
- Create: `engine/strategies/__init__.py`
- Create: `engine/strategies/base.py`
- Create: `engine/strategies/buy_hold.py`
- Test: `tests/test_strategy_buy_hold.py`

**Interfaces:**
- Consumes: `engine.fifo_engine.FifoEngine` (Plan 1, plus Task 2's `unrealized_pnl`/`realized_pnl_cumule`), `models.PortfolioSnapshot` (Plan 1), `market_data.types.Kline` (Plan 1)
- Produces:
  - `engine.strategies.base.build_snapshot(engine: FifoEngine, symbol: str, current_price: Decimal, timestamp: int) -> PortfolioSnapshot` — shared by all three strategies (Tasks 5-8)
  - `engine.strategies.buy_hold.run_buy_hold(klines: list[Kline], engine: FifoEngine, symbol: str, params: dict) -> list[PortfolioSnapshot]`

Buy & Hold (spec §3 params: `{"invest_at": "start"}`) invests the engine's entire starting cash in a single BUY on the first kline's close, then holds — no further trades. The quantity is computed so the BUY's `total_cost` (gross + fee) exactly consumes `cash_balance` (see the hand-verified arithmetic in Step 1's test: dividing by `(1 + fee_pct)` before dividing by price cancels the fee exactly).

- [ ] **Step 1: Create `engine/strategies/__init__.py`** (empty file)

- [ ] **Step 2: Write the failing test**

```python
# tests/test_strategy_buy_hold.py
from decimal import Decimal

from engine.fifo_engine import FifoEngine
from engine.strategies.buy_hold import run_buy_hold
from market_data.types import Kline


def _kline(open_time_ms: int, close: str) -> Kline:
    c = Decimal(close)
    return Kline(
        open_time_ms=open_time_ms, open=c, high=c, low=c, close=c,
        volume=Decimal("1"), close_time_ms=open_time_ms + 3_599_999,
    )


def test_buy_hold_invests_all_cash_on_first_candle_only():
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    klines = [_kline(0, "100"), _kline(3_600_000, "110"), _kline(7_200_000, "120")]

    snapshots = run_buy_hold(klines, engine, "BTCUSDT", params={"invest_at": "start"})

    assert len(engine.trades) == 1
    trade = engine.trades[0]
    assert trade.price == Decimal("100")
    # qty = (1000 / 1.001) / 100 ; total_cost lands back on exactly 1000
    assert trade.total_cost == Decimal("1000.000000000000000000000000")
    assert engine.cash_balance == Decimal("0E-24")
    assert len(snapshots) == 3
    # total_value tracks the position's mark-to-market on later candles
    assert snapshots[1].total_value > snapshots[0].total_value  # price rose 100->110
    assert snapshots[2].symbol == "BTCUSDT"


def test_buy_hold_never_sells():
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    klines = [_kline(0, "100"), _kline(3_600_000, "90")]

    run_buy_hold(klines, engine, "BTCUSDT", params={"invest_at": "start"})

    assert all(t.side.value == "BUY" for t in engine.trades)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_strategy_buy_hold.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'engine.strategies.base'`

- [ ] **Step 4: Write `engine/strategies/base.py`**

```python
# engine/strategies/base.py
from decimal import Decimal

from engine.fifo_engine import FifoEngine
from models import PortfolioSnapshot


def build_snapshot(engine: FifoEngine, symbol: str, current_price: Decimal, timestamp: int) -> PortfolioSnapshot:
    position_value = engine.position_value(symbol, current_price)
    return PortfolioSnapshot(
        timestamp=timestamp,
        symbol=symbol,
        cash_balance=engine.cash_balance,
        position_value=position_value,
        total_value=engine.cash_balance + position_value,
        unrealized_pnl=engine.unrealized_pnl(symbol, current_price),
        realized_pnl_cumule=engine.realized_pnl_cumule(symbol),
    )
```

- [ ] **Step 5: Write `engine/strategies/buy_hold.py`**

```python
# engine/strategies/buy_hold.py
from decimal import Decimal

from engine.fifo_engine import FifoEngine
from engine.strategies.base import build_snapshot
from market_data.types import Kline
from models import PortfolioSnapshot


def run_buy_hold(klines: list[Kline], engine: FifoEngine, symbol: str, params: dict) -> list[PortfolioSnapshot]:
    """Spec section 3: {"invest_at": "start"} — invest everything on the first
    candle, hold forever. No selling."""
    snapshots: list[PortfolioSnapshot] = []
    invested = False
    for k in klines:
        if not invested:
            price = k.close
            quantity = (engine.cash_balance / (Decimal(1) + engine.fee_pct)) / price
            engine.buy(k.open_time_ms, symbol, price, quantity, "buy_hold")
            invested = True
        snapshots.append(build_snapshot(engine, symbol, k.close, k.open_time_ms))
    return snapshots
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_strategy_buy_hold.py -v`
Expected: PASS (2 passed)

- [ ] **Step 7: Commit**

```bash
git add engine/strategies/__init__.py engine/strategies/base.py engine/strategies/buy_hold.py tests/test_strategy_buy_hold.py
git commit -m "feat: add strategy snapshot helper and Buy & Hold strategy"
```

---

## Task 6: DCA strategy

**Files:**
- Create: `engine/strategies/dca.py`
- Test: `tests/test_strategy_dca.py`

**Interfaces:**
- Consumes: `engine.strategies.base.build_snapshot` (Task 5), `engine.fifo_engine.FifoEngine`, `market_data.types.Kline`
- Produces: `run_dca(klines: list[Kline], engine: FifoEngine, symbol: str, params: dict, interval_hours: int) -> list[PortfolioSnapshot]`

Spec §3 params: `{"amount_per_buy": 50, "frequency_hours": 24, "reference_price": "close"}`. `interval_hours` is caller-supplied (derived from the kline interval by `backtest.py`, e.g. 1 for `"1h"`) so this module never needs to know about `INTERVAL_MS`. Buys happen every `frequency_hours / interval_hours` candles, starting at index 0 (immediate first buy, matching Buy & Hold's day-0 investment). `reference_price` is always `"close"` per the spec's schema (the only value it defines) — this implementation uses `k.close` unconditionally.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_strategy_dca.py
from decimal import Decimal

from engine.fifo_engine import FifoEngine
from engine.strategies.dca import run_dca
from market_data.types import Kline


def _kline(open_time_ms: int, close: str) -> Kline:
    c = Decimal(close)
    return Kline(
        open_time_ms=open_time_ms, open=c, high=c, low=c, close=c,
        volume=Decimal("1"), close_time_ms=open_time_ms + 3_599_999,
    )


def test_dca_buys_every_frequency_hours_starting_at_index_zero():
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    # 25 hourly candles (index 0..24) with frequency_hours=24, interval_hours=1
    # -> buys trigger at index 0 and index 24 only.
    klines = [_kline(i * 3_600_000, "100" if i != 24 else "125") for i in range(25)]

    snapshots = run_dca(
        klines, engine, "BTCUSDT",
        params={"amount_per_buy": 50, "frequency_hours": 24, "reference_price": "close"},
        interval_hours=1,
    )

    assert len(engine.trades) == 2
    trade0, trade24 = engine.trades

    # buy0: price=100, qty=50/100=0.5, gross=50, fee=0.05, total_cost=50.05
    assert trade0.price == Decimal("100")
    assert trade0.quantity == Decimal("0.5")
    assert trade0.total_cost == Decimal("50.05")

    # buy24: price=125, qty=50/125=0.4, gross=50, fee=0.05, total_cost=50.05
    assert trade24.price == Decimal("125")
    assert trade24.quantity == Decimal("0.4")
    assert trade24.total_cost == Decimal("50.05")

    assert engine.cash_balance == Decimal("1000") - Decimal("50.05") - Decimal("50.05")
    assert len(snapshots) == 25


def test_dca_never_sells():
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    klines = [_kline(0, "100"), _kline(3_600_000, "50")]

    run_dca(klines, engine, "BTCUSDT",
            params={"amount_per_buy": 50, "frequency_hours": 24, "reference_price": "close"},
            interval_hours=1)

    assert all(t.side.value == "BUY" for t in engine.trades)


def test_dca_rejected_buy_is_logged_not_raised():
    # cash runs out; later scheduled buys are silently rejected by the engine (spec: no leverage)
    engine = FifoEngine(initial_cash=Decimal("60"), fee_pct=Decimal("0.001"))
    klines = [_kline(i * 3_600_000, "100") for i in range(3)]

    run_dca(klines, engine, "BTCUSDT",
            params={"amount_per_buy": 50, "frequency_hours": 1, "reference_price": "close"},
            interval_hours=1)

    # buy0: total_cost=50.05, cash=60-50.05=9.95 ; buy1,buy2: total_cost=50.05 > 9.95, rejected
    assert len(engine.trades) == 1
    assert engine.cash_balance == Decimal("9.95")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_strategy_dca.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'engine.strategies.dca'`

- [ ] **Step 3: Write `engine/strategies/dca.py`**

```python
# engine/strategies/dca.py
from decimal import Decimal

from engine.fifo_engine import FifoEngine
from engine.strategies.base import build_snapshot
from market_data.types import Kline
from models import PortfolioSnapshot


def run_dca(
    klines: list[Kline], engine: FifoEngine, symbol: str, params: dict, interval_hours: int
) -> list[PortfolioSnapshot]:
    """Spec section 3: buy amount_per_buy (quote) every frequency_hours, starting
    immediately at the first candle. reference_price is always "close" (the
    only value the spec's schema defines)."""
    amount_per_buy = Decimal(str(params["amount_per_buy"]))
    frequency_hours = int(params["frequency_hours"])
    candles_per_buy = frequency_hours // interval_hours

    snapshots: list[PortfolioSnapshot] = []
    for i, k in enumerate(klines):
        if i % candles_per_buy == 0:
            price = k.close
            quantity = amount_per_buy / price
            engine.buy(k.open_time_ms, symbol, price, quantity, "dca")
        snapshots.append(build_snapshot(engine, symbol, k.close, k.open_time_ms))
    return snapshots
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_strategy_dca.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add engine/strategies/dca.py tests/test_strategy_dca.py
git commit -m "feat: add DCA strategy"
```

---

## Task 7: Grid strategy — level construction (arithmetic/geometric spacing)

**Files:**
- Create: `engine/strategies/grid.py`
- Test: `tests/test_strategy_grid.py`

**Interfaces:**
- Consumes: nothing beyond stdlib `decimal`
- Produces: `GridLevel` (mutable dataclass: `buy_price: Decimal, sell_price: Decimal, state: str = "EMPTY", filled_quantity: Decimal | None = None`), `build_grid_levels(lower_bound, upper_bound, n_levels: int, spacing: str) -> list[GridLevel]`

A grid with `n_levels` levels has `n_levels + 1` price boundaries from `lower_bound` to `upper_bound`. Level `i`'s buy price is boundary `i`, its sell price is boundary `i+1` — classic grid semantics (buy low, sell one rung up). `spacing: "geometric"` spaces boundaries proportionally (equal % gaps); `"arithmetic"` spaces them by equal absolute amounts.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_strategy_grid.py
from decimal import Decimal

import pytest

from engine.strategies.grid import GridLevel, build_grid_levels


def test_build_grid_levels_arithmetic_spacing():
    levels = build_grid_levels(lower_bound=100, upper_bound=400, n_levels=3, spacing="arithmetic")

    assert len(levels) == 3
    # boundaries: 100, 200, 300, 400 (step 100)
    assert [lvl.buy_price for lvl in levels] == [Decimal("100"), Decimal("200"), Decimal("300")]
    assert [lvl.sell_price for lvl in levels] == [Decimal("200"), Decimal("300"), Decimal("400")]
    assert all(lvl.state == "EMPTY" and lvl.filled_quantity is None for lvl in levels)


def test_build_grid_levels_geometric_spacing():
    levels = build_grid_levels(lower_bound=100, upper_bound=400, n_levels=2, spacing="geometric")

    assert len(levels) == 2
    # ratio = sqrt(400/100) = 2 ; boundaries: 100, 200, 400
    assert levels[0].buy_price == Decimal("100")
    assert levels[0].sell_price == Decimal("200")
    assert levels[1].buy_price == Decimal("200")
    assert levels[1].sell_price == Decimal("400")


def test_build_grid_levels_unknown_spacing_raises():
    with pytest.raises(ValueError, match="triangular"):
        build_grid_levels(lower_bound=100, upper_bound=200, n_levels=1, spacing="triangular")


def test_build_grid_levels_accepts_int_and_float_params_without_float_leak():
    # JSON-loaded params can be plain int/float — must not leak float into Decimal math
    levels = build_grid_levels(lower_bound=25000, upper_bound=35000, n_levels=10, spacing="arithmetic")
    assert isinstance(levels[0].buy_price, Decimal)
    assert levels[0].buy_price == Decimal("25000")
    assert levels[-1].sell_price == Decimal("35000")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_strategy_grid.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'engine.strategies.grid'`

- [ ] **Step 3: Write `engine/strategies/grid.py` (level construction only)**

```python
# engine/strategies/grid.py
from dataclasses import dataclass
from decimal import Decimal


@dataclass
class GridLevel:
    buy_price: Decimal
    sell_price: Decimal
    state: str = "EMPTY"
    filled_quantity: Decimal | None = None


def build_grid_levels(lower_bound, upper_bound, n_levels: int, spacing: str) -> list[GridLevel]:
    lower = Decimal(str(lower_bound))
    upper = Decimal(str(upper_bound))
    n = int(n_levels)

    if spacing == "arithmetic":
        step = (upper - lower) / Decimal(n)
        boundaries = [lower + step * i for i in range(n + 1)]
    elif spacing == "geometric":
        ratio = upper / lower
        boundaries = [lower * (ratio ** (Decimal(i) / Decimal(n))) for i in range(n + 1)]
    else:
        raise ValueError(f"spacing inconnu: {spacing}")

    return [GridLevel(buy_price=boundaries[i], sell_price=boundaries[i + 1]) for i in range(n)]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_strategy_grid.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add engine/strategies/grid.py tests/test_strategy_grid.py
git commit -m "feat: add grid level construction (arithmetic/geometric spacing)"
```

---

## Task 8: Grid strategy — stepping logic (D1 re-arm, D2 intrabar, D3 cheapest-first)

**Files:**
- Modify: `engine/strategies/grid.py`
- Modify: `tests/test_strategy_grid.py`

**Interfaces:**
- Consumes: `engine.strategies.grid.{GridLevel, build_grid_levels}` (Task 7), `engine.strategies.base.build_snapshot` (Task 5), `engine.fifo_engine.FifoEngine`, `market_data.types.Kline`
- Produces: `run_grid(klines: list[Kline], engine: FifoEngine, symbol: str, params: dict) -> list[PortfolioSnapshot]`

Every candle: any `EMPTY` level whose `buy_price` falls within `[low, high]` triggers a BUY (D2), processed cheapest-`buy_price`-first so a cash shortfall rejects the priciest levels first, not an arbitrary one (D3 — the engine's own `cash_balance` check does the actual rejecting; this just controls the order levels are *attempted* in). Any `FILLED` level whose `sell_price` falls within `[low, high]` triggers a SELL of exactly the quantity bought at that level, and the level returns to `EMPTY`, ready to trigger again on a future dip (D1).

- [ ] **Step 1: Append the failing tests**

```python
# tests/test_strategy_grid.py (append)
from engine.fifo_engine import FifoEngine
from engine.strategies.grid import run_grid


def _ohlc_kline(open_time_ms: int, low: str, high: str, close: str) -> "Kline":
    from market_data.types import Kline
    return Kline(
        open_time_ms=open_time_ms, open=Decimal(close), high=Decimal(high),
        low=Decimal(low), close=Decimal(close), volume=Decimal("1"),
        close_time_ms=open_time_ms + 3_599_999,
    )


def test_grid_level_rearms_across_multiple_oscillations():
    # single level: lower=100, upper=200, n_levels=1 -> buy=100, sell=200
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    klines = [
        _ohlc_kline(0, "90", "110", "100"),              # dips to 100 -> BUY
        _ohlc_kline(3_600_000, "150", "250", "200"),      # rises to 200 -> SELL
        _ohlc_kline(7_200_000, "90", "110", "100"),       # dips to 100 again -> BUY (re-arm, D1)
    ]

    run_grid(klines, engine, "BTCUSDT",
              params={"lower_bound": 100, "upper_bound": 200, "n_levels": 1,
                      "spacing": "arithmetic", "order_size_quote": 100})

    assert len(engine.trades) == 3
    assert [t.side.value for t in engine.trades] == ["BUY", "SELL", "BUY"]
    # buy: qty=100/100=1, gross=100, fee=0.1, total_cost=100.1 -> cash 1000-100.1=899.9
    assert engine.trades[0].total_cost == Decimal("100.1")
    # sell: gross=200, fee=0.2, proceeds=199.8, pnl=(200-100)*1-0.2=99.8 -> cash 899.9+199.8=1099.7
    assert engine.trades[1].realized_pnl == Decimal("99.8")
    assert engine.trades[1].total_cost == Decimal("199.8")
    # re-arm buy: identical to the first -> cash 1099.7-100.1=999.6
    assert engine.trades[2].total_cost == Decimal("100.1")
    assert engine.cash_balance == Decimal("999.6")


def test_grid_multi_level_fills_cheapest_first_rejects_rest_on_insufficient_cash():
    # 2 levels: lower=100, upper=200, n_levels=2 -> level0(100,150), level1(150,200)
    engine = FifoEngine(initial_cash=Decimal("200"), fee_pct=Decimal("0.001"))
    klines = [_ohlc_kline(0, "90", "160", "120")]  # touches both buy_price 100 and 150

    run_grid(klines, engine, "BTCUSDT",
              params={"lower_bound": 100, "upper_bound": 200, "n_levels": 2,
                      "spacing": "arithmetic", "order_size_quote": 150})

    # level0 (buy=100): qty=150/100=1.5, gross=150, fee=0.15, total_cost=150.15
    # cash after level0: 200-150.15=49.85
    # level1 (buy=150): qty=150/150=1, total_cost=150.15 > 49.85 -> rejected (D3)
    assert len(engine.trades) == 1
    assert engine.trades[0].price == Decimal("100")
    assert engine.trades[0].quantity == Decimal("1.5")
    assert engine.cash_balance == Decimal("49.85")


def test_grid_never_triggers_when_price_stays_between_levels():
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    klines = [_ohlc_kline(0, "120", "140", "130")]  # never touches 100 or 200

    run_grid(klines, engine, "BTCUSDT",
              params={"lower_bound": 100, "upper_bound": 200, "n_levels": 1,
                      "spacing": "arithmetic", "order_size_quote": 100})

    assert engine.trades == []
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `pytest tests/test_strategy_grid.py -v`
Expected: FAIL — `AttributeError: module 'engine.strategies.grid' has no attribute 'run_grid'` on the 3 new tests; the 4 Task 7 tests still pass.

- [ ] **Step 3: Append `run_grid` to `engine/strategies/grid.py`**

```python
# engine/strategies/grid.py (add imports at top, then append run_grid at the end)

# Add to the top of the file, alongside the existing imports:
from engine.fifo_engine import FifoEngine
from engine.strategies.base import build_snapshot
from market_data.types import Kline
from models import PortfolioSnapshot


# Append at the end of the file:

def run_grid(klines: list[Kline], engine: FifoEngine, symbol: str, params: dict) -> list[PortfolioSnapshot]:
    """Spec section 8 (decisions D1-D3). Levels re-arm indefinitely (D1); a
    level triggers if the candle's [low, high] touches its price (D2,
    backtest side); simultaneous BUY triggers are attempted cheapest-price-
    first so a cash shortfall rejects the priciest ones (D3)."""
    levels = build_grid_levels(
        params["lower_bound"], params["upper_bound"], int(params["n_levels"]), params["spacing"]
    )
    order_size_quote = Decimal(str(params["order_size_quote"]))

    snapshots: list[PortfolioSnapshot] = []
    for k in klines:
        buy_candidates = sorted(
            (lvl for lvl in levels if lvl.state == "EMPTY" and lvl.buy_price >= k.low and lvl.buy_price <= k.high),
            key=lambda lvl: lvl.buy_price,
        )
        for lvl in buy_candidates:
            quantity = order_size_quote / lvl.buy_price
            trade = engine.buy(k.open_time_ms, symbol, lvl.buy_price, quantity, "grid")
            if trade is not None:
                lvl.state = "FILLED"
                lvl.filled_quantity = quantity

        for lvl in levels:
            if lvl.state == "FILLED" and lvl.sell_price >= k.low and lvl.sell_price <= k.high:
                trade = engine.sell(k.open_time_ms, symbol, lvl.sell_price, lvl.filled_quantity, "grid")
                if trade is not None:
                    lvl.state = "EMPTY"
                    lvl.filled_quantity = None

        snapshots.append(build_snapshot(engine, symbol, k.close, k.open_time_ms))
    return snapshots
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_strategy_grid.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add engine/strategies/grid.py tests/test_strategy_grid.py
git commit -m "feat: add grid trading stepping logic (D1 re-arm, D2 intrabar touch, D3 cheapest-first)"
```

---

## Task 9: backtest.py — symbol selection and historical download

**Files:**
- Create: `backtest.py`
- Test: `tests/test_backtest_data.py`

**Interfaces:**
- Consumes: `liquidity.passes_liquidity_filter` (Task 4), `market_data.pagination.fetch_klines_paginated` (Plan 1), `market_data.provider.{MarketDataProvider, INTERVAL_MS}` (Plan 1), `config.LiquidityConfig` (Plan 1)
- Produces: `select_backtest_symbols(provider: MarketDataProvider, watchlist: list[str], liquidity_cfg: LiquidityConfig) -> tuple[list[str], list[tuple[str, str]]]` (passing symbols, `[(symbol, reason)]` excluded), `download_backtest_klines(provider: MarketDataProvider, symbol: str, interval: str, lookback_days: int, now_ms: int) -> list[Kline]`

This closes the Plan 1 final review's deferred pagination-coverage gap: `fetch_klines_paginated` (Plan 1) has no way to tell "the exchange has no more data" from "the exchange silently served a shorter window than requested" (a real risk — Kraken's `/0/public/OHLC` caps how far back a single pair's candle history reaches). Rather than raising and aborting the whole backtest over one under-covered symbol, `download_backtest_klines` logs a WARNING when coverage falls short and returns what it has — the caller decides whether a partial window is still useful.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backtest_data.py
from decimal import Decimal

from backtest import download_backtest_klines, select_backtest_symbols
from config import LiquidityConfig
from market_data.provider import MarketDataProvider
from market_data.types import BookTicker, Kline, Ticker24h


class FakeProvider(MarketDataProvider):
    def __init__(self, tickers: dict, books: dict, klines_by_symbol: dict | None = None):
        self._tickers = tickers
        self._books = books
        self._klines_by_symbol = klines_by_symbol or {}
        self.kline_calls: list[tuple] = []

    def get_klines(self, symbol, interval, start_ms, end_ms, limit=1000):
        self.kline_calls.append((symbol, interval, start_ms, end_ms, limit))
        return self._klines_by_symbol.get(symbol, [])

    def get_book_ticker(self, symbol):
        return self._books[symbol]

    def get_ticker_24h(self, symbol):
        return self._tickers[symbol]


def _liquidity_config() -> LiquidityConfig:
    return LiquidityConfig(min_quote_volume_24h=Decimal("50000000"), max_spread_bps=Decimal("10"))


def test_select_backtest_symbols_partitions_pass_and_fail():
    provider = FakeProvider(
        tickers={
            "BTCUSDT": Ticker24h(symbol="BTCUSDT", quote_volume=Decimal("60000000")),
            "ETHUSDT": Ticker24h(symbol="ETHUSDT", quote_volume=Decimal("1000")),
            "SOLUSDT": Ticker24h(symbol="SOLUSDT", quote_volume=Decimal("60000000")),
        },
        books={
            "BTCUSDT": BookTicker(symbol="BTCUSDT", bid_price=Decimal("100"), ask_price=Decimal("100.05")),
            "ETHUSDT": BookTicker(symbol="ETHUSDT", bid_price=Decimal("100"), ask_price=Decimal("100.05")),
            "SOLUSDT": BookTicker(symbol="SOLUSDT", bid_price=Decimal("100"), ask_price=Decimal("101")),
        },
    )

    passing, excluded = select_backtest_symbols(
        provider, ["BTCUSDT", "ETHUSDT", "SOLUSDT"], _liquidity_config()
    )

    assert passing == ["BTCUSDT"]
    assert excluded == [("ETHUSDT", "volume insuffisant"), ("SOLUSDT", "spread trop large")]


def test_download_backtest_klines_computes_start_end_from_lookback_days():
    fake_kline = Kline(open_time_ms=0, open=Decimal("1"), high=Decimal("1"),
                        low=Decimal("1"), close=Decimal("1"), volume=Decimal("1"),
                        close_time_ms=3_599_999)
    provider = FakeProvider(tickers={}, books={}, klines_by_symbol={"BTCUSDT": [fake_kline]})
    now_ms = 1_800_000_000_000

    result = download_backtest_klines(provider, "BTCUSDT", "1h", lookback_days=90, now_ms=now_ms)

    assert result == [fake_kline]
    assert len(provider.kline_calls) == 1
    symbol, interval, start_ms, end_ms, limit = provider.kline_calls[0]
    assert symbol == "BTCUSDT"
    assert interval == "1h"
    assert end_ms == now_ms
    assert start_ms == now_ms - 90 * 86_400_000


def test_download_backtest_klines_warns_on_short_coverage(caplog):
    import logging
    # 90 days of 1h candles = 2160 expected; provider returns far fewer (a
    # truncated/short window, e.g. Kraken's OHLC candle cap) with no error.
    short_run = [
        Kline(open_time_ms=i * 3_600_000, open=Decimal("1"), high=Decimal("1"),
              low=Decimal("1"), close=Decimal("1"), volume=Decimal("1"),
              close_time_ms=i * 3_600_000 + 3_599_999)
        for i in range(500)
    ]
    provider = FakeProvider(tickers={}, books={}, klines_by_symbol={"BTCUSDT": short_run})

    with caplog.at_level(logging.WARNING):
        result = download_backtest_klines(provider, "BTCUSDT", "1h", lookback_days=90, now_ms=1_800_000_000_000)

    assert len(result) == 500
    assert any("couverture" in record.getMessage() for record in caplog.records)


def test_download_backtest_klines_no_warning_on_full_coverage(caplog):
    import logging
    full_run = [
        Kline(open_time_ms=i * 3_600_000, open=Decimal("1"), high=Decimal("1"),
              low=Decimal("1"), close=Decimal("1"), volume=Decimal("1"),
              close_time_ms=i * 3_600_000 + 3_599_999)
        for i in range(2160)
    ]
    provider = FakeProvider(tickers={}, books={}, klines_by_symbol={"BTCUSDT": full_run})

    with caplog.at_level(logging.WARNING):
        download_backtest_klines(provider, "BTCUSDT", "1h", lookback_days=90, now_ms=1_800_000_000_000)

    assert not any("couverture" in record.getMessage() for record in caplog.records)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_backtest_data.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backtest'`

- [ ] **Step 3: Write `backtest.py` (data acquisition functions only)**

```python
# backtest.py
import logging
from decimal import Decimal

from market_data.pagination import fetch_klines_paginated
from market_data.provider import INTERVAL_MS, MarketDataProvider
from market_data.types import Kline
from config import LiquidityConfig
from liquidity import passes_liquidity_filter

logger = logging.getLogger(__name__)

# Below this fraction of the expected candle count, log a coverage warning
# rather than silently trusting a truncated window (Plan 1 final review).
MIN_COVERAGE_RATIO = Decimal("0.95")


def select_backtest_symbols(
    provider: MarketDataProvider, watchlist: list[str], liquidity_cfg: LiquidityConfig
) -> tuple[list[str], list[tuple[str, str]]]:
    passing: list[str] = []
    excluded: list[tuple[str, str]] = []
    for symbol in watchlist:
        ticker = provider.get_ticker_24h(symbol)
        book = provider.get_book_ticker(symbol)
        ok, reason = passes_liquidity_filter(ticker, book, liquidity_cfg)
        if ok:
            passing.append(symbol)
        else:
            excluded.append((symbol, reason))
    return passing, excluded


def download_backtest_klines(
    provider: MarketDataProvider, symbol: str, interval: str, lookback_days: int, now_ms: int
) -> list[Kline]:
    interval_ms = INTERVAL_MS[interval]
    end_ms = now_ms
    start_ms = end_ms - lookback_days * 86_400_000
    klines = fetch_klines_paginated(provider, symbol, interval, start_ms, end_ms)

    expected_count = (end_ms - start_ms) // interval_ms
    if expected_count > 0 and Decimal(len(klines)) / Decimal(expected_count) < MIN_COVERAGE_RATIO:
        logger.warning(
            "couverture insuffisante pour %s: %d/%d bougies attendues (fenetre possiblement tronquee)",
            symbol, len(klines), expected_count,
        )
    return klines
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_backtest_data.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add backtest.py tests/test_backtest_data.py
git commit -m "feat: add backtest symbol selection and historical kline download"
```

---

## Task 10: backtest.py — strategy replay orchestration

**Files:**
- Modify: `backtest.py`
- Test: `tests/test_backtest_replay.py`

**Interfaces:**
- Consumes: `engine.fifo_engine.FifoEngine` (Plan 1), `engine.strategies.{buy_hold.run_buy_hold, dca.run_dca, grid.run_grid}` (Tasks 5, 6, 8)
- Produces: `run_strategy(strategy_type: str, klines: list[Kline], symbol: str, params: dict, initial_capital: Decimal, fee_pct: Decimal, interval_hours: int) -> tuple[FifoEngine, list[PortfolioSnapshot]]`

This is the single place that maps a `strategy_type` string (`"buy_hold" | "dca" | "grid"`) to its runner function, giving each replay a fresh `FifoEngine`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backtest_replay.py
from decimal import Decimal

import pytest

from backtest import run_strategy
from engine.fifo_engine import Side
from market_data.types import Kline


def _kline(open_time_ms: int, close: str) -> Kline:
    c = Decimal(close)
    return Kline(open_time_ms=open_time_ms, open=c, high=c, low=c, close=c,
                 volume=Decimal("1"), close_time_ms=open_time_ms + 3_599_999)


def test_run_strategy_buy_hold_produces_fresh_engine_and_snapshots():
    klines = [_kline(0, "100"), _kline(3_600_000, "110")]

    engine, snapshots = run_strategy(
        "buy_hold", klines, "BTCUSDT", params={"invest_at": "start"},
        initial_capital=Decimal("1000"), fee_pct=Decimal("0.001"), interval_hours=1,
    )

    assert engine.cash_balance == Decimal("0E-24")
    assert len(engine.trades) == 1
    assert len(snapshots) == 2


def test_run_strategy_dca_uses_interval_hours():
    klines = [_kline(i * 3_600_000, "100") for i in range(3)]

    engine, snapshots = run_strategy(
        "dca", klines, "BTCUSDT",
        params={"amount_per_buy": 50, "frequency_hours": 1, "reference_price": "close"},
        initial_capital=Decimal("1000"), fee_pct=Decimal("0.001"), interval_hours=1,
    )

    assert len(engine.trades) == 3  # frequency_hours=1, interval_hours=1 -> every candle


def test_run_strategy_grid_ignores_interval_hours():
    klines = [_kline(0, "150")]

    engine, snapshots = run_strategy(
        "grid", klines, "BTCUSDT",
        params={"lower_bound": 100, "upper_bound": 200, "n_levels": 1,
                "spacing": "arithmetic", "order_size_quote": 100},
        initial_capital=Decimal("1000"), fee_pct=Decimal("0.001"), interval_hours=1,
    )

    assert engine.trades == []  # price 150 never touches buy=100 or sell=200 on a flat OHLC kline


def test_run_strategy_unknown_type_raises():
    with pytest.raises(ValueError, match="martingale"):
        run_strategy("martingale", [], "BTCUSDT", {}, Decimal("1000"), Decimal("0.001"), 1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_backtest_replay.py -v`
Expected: FAIL with `ImportError: cannot import name 'run_strategy' from 'backtest'`

- [ ] **Step 3: Append to `backtest.py`**

```python
# backtest.py (add imports at top, then append at the end)

# Add to the top of the file, alongside the existing imports (Decimal is
# already imported from Task 9 — do not add it again):
from engine.fifo_engine import FifoEngine
from engine.strategies.buy_hold import run_buy_hold
from engine.strategies.dca import run_dca
from engine.strategies.grid import run_grid
from models import PortfolioSnapshot


# Append at the end of the file:

def run_strategy(
    strategy_type: str,
    klines: list[Kline],
    symbol: str,
    params: dict,
    initial_capital: Decimal,
    fee_pct: Decimal,
    interval_hours: int,
) -> tuple[FifoEngine, list[PortfolioSnapshot]]:
    engine = FifoEngine(initial_cash=initial_capital, fee_pct=fee_pct)
    if strategy_type == "buy_hold":
        snapshots = run_buy_hold(klines, engine, symbol, params)
    elif strategy_type == "dca":
        snapshots = run_dca(klines, engine, symbol, params, interval_hours)
    elif strategy_type == "grid":
        snapshots = run_grid(klines, engine, symbol, params)
    else:
        raise ValueError(f"strategie inconnue: {strategy_type}")
    return engine, snapshots
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_backtest_replay.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add backtest.py tests/test_backtest_replay.py
git commit -m "feat: add strategy replay orchestration to backtest.py"
```

---

## Task 11: backtest.py — report row builders (raw metrics + trader analytics)

**Files:**
- Modify: `backtest.py`
- Test: `tests/test_backtest_reports.py`

**Interfaces:**
- Consumes: `analytics.{total_return_pct, cagr_pct, max_drawdown, trade_stats, sharpe_ratio, sortino_ratio, calmar_ratio, alpha_vs_buy_hold, exposure_time_pct}` (Plan 1 + Task 3), `engine.fifo_engine.{FifoEngine, Side}` (Plan 1)
- Produces: `build_raw_metrics_row(symbol: str, strategy_type: str, engine: FifoEngine, snapshots: list[PortfolioSnapshot], initial_capital: Decimal) -> dict`, `build_analytics_row(symbol: str, strategy_type: str, engine: FifoEngine, snapshots: list[PortfolioSnapshot], initial_capital: Decimal, periods_per_year: int, buy_hold_return_pct: Decimal) -> dict`

`build_raw_metrics_row` implements spec §4's comparative table columns (return %, trade count, win rate, avg win/loss, biggest win/loss, max drawdown, total fees). `build_analytics_row` implements spec §4bis's full metric set (§10 of the design spec), including alpha vs Buy & Hold — the caller passes in Buy & Hold's own return % once per symbol so every strategy's row can compare against it (Buy & Hold's own row gets `alpha_vs_buy_hold_pct == 0` by construction, since it's compared to itself).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backtest_reports.py
from decimal import Decimal

from backtest import build_analytics_row, build_raw_metrics_row, run_strategy
from market_data.types import Kline


def _kline(open_time_ms: int, close: str) -> Kline:
    c = Decimal(close)
    return Kline(open_time_ms=open_time_ms, open=c, high=c, low=c, close=c,
                 volume=Decimal("1"), close_time_ms=open_time_ms + 3_599_999)


def _run_dca_scenario():
    # 25 hourly candles: DCA buys at 0 (price 100) and 24 (price 125), no sells.
    klines = [_kline(i * 3_600_000, "100" if i != 24 else "125") for i in range(25)]
    engine, snapshots = run_strategy(
        "dca", klines, "BTCUSDT",
        params={"amount_per_buy": 50, "frequency_hours": 24, "reference_price": "close"},
        initial_capital=Decimal("1000"), fee_pct=Decimal("0.001"), interval_hours=1,
    )
    return engine, snapshots


def test_build_raw_metrics_row_shape_and_basic_values():
    engine, snapshots = _run_dca_scenario()

    row = build_raw_metrics_row("BTCUSDT", "dca", engine, snapshots, initial_capital=Decimal("1000"))

    assert row["symbol"] == "BTCUSDT"
    assert row["strategy"] == "dca"
    assert row["nb_trades"] == 2
    assert row["win_rate_pct"] == Decimal("0")  # no sells at all -> trade_stats reports 0
    assert row["total_fees"] == Decimal("0.05") + Decimal("0.05")
    # DCA never sells, so realized-PnL-based fields are all zero/neutral
    assert row["biggest_win"] == Decimal("0")
    assert row["biggest_loss"] == Decimal("0")
    # final_total_value = cash + position_value(at last close=125)
    # qty held = 0.5 + 0.4 = 0.9 ; position_value = 0.9*125 = 112.5
    # cash = 1000 - 50.05 - 50.05 = 899.90 ; total = 899.90 + 112.5 = 1012.40
    assert row["return_pct"] == (Decimal("1012.40") - Decimal("1000")) / Decimal("1000") * Decimal("100")


def test_build_analytics_row_shape_and_alpha():
    engine, snapshots = _run_dca_scenario()

    row = build_analytics_row(
        "BTCUSDT", "dca", engine, snapshots, initial_capital=Decimal("1000"),
        periods_per_year=8760, buy_hold_return_pct=Decimal("5.00"),
    )

    assert row["symbol"] == "BTCUSDT"
    assert row["strategy"] == "dca"
    assert "sharpe_ratio" in row
    assert "sortino_ratio" in row
    assert "calmar_ratio" in row
    assert "max_drawdown_pct" in row
    assert "recovery_days" in row
    assert "profit_factor" in row
    assert "expectancy" in row
    assert "exposure_time_pct" in row
    assert row["alpha_vs_buy_hold_pct"] == row["total_return_pct"] - Decimal("5.00")


def test_build_analytics_row_buy_hold_self_comparison_is_zero_alpha():
    engine, snapshots = run_strategy(
        "buy_hold", [_kline(0, "100"), _kline(3_600_000, "110")], "BTCUSDT",
        params={"invest_at": "start"}, initial_capital=Decimal("1000"),
        fee_pct=Decimal("0.001"), interval_hours=1,
    )
    own_return = (snapshots[-1].total_value - Decimal("1000")) / Decimal("1000") * Decimal("100")

    row = build_analytics_row(
        "BTCUSDT", "buy_hold", engine, snapshots, initial_capital=Decimal("1000"),
        periods_per_year=8760, buy_hold_return_pct=own_return,
    )

    assert row["alpha_vs_buy_hold_pct"] == Decimal("0")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_backtest_reports.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_raw_metrics_row' from 'backtest'`

- [ ] **Step 3: Append to `backtest.py`**

```python
# backtest.py (add imports at top, then append at the end)

# Add to the top of the file, alongside the existing imports:
import analytics
from engine.fifo_engine import Side


# Append at the end of the file:

def build_raw_metrics_row(
    symbol: str, strategy_type: str, engine: FifoEngine, snapshots: list[PortfolioSnapshot],
    initial_capital: Decimal,
) -> dict:
    final_value = snapshots[-1].total_value if snapshots else initial_capital
    stats = analytics.trade_stats(engine.trades)
    sells = [t.realized_pnl for t in engine.trades if t.side == Side.SELL and t.realized_pnl is not None]
    wins = [p for p in sells if p > 0]
    losses = [p for p in sells if p < 0]
    max_dd, _ = analytics.max_drawdown(snapshots)
    total_fees = sum((t.fee_amount for t in engine.trades), Decimal("0"))

    return {
        "symbol": symbol,
        "strategy": strategy_type,
        "return_pct": analytics.total_return_pct(initial_capital, final_value),
        "nb_trades": len(engine.trades),
        "win_rate_pct": stats["win_rate"] * Decimal(100),
        "avg_win": stats["avg_win"],
        "avg_loss": stats["avg_loss"],
        "biggest_win": max(wins) if wins else Decimal("0"),
        "biggest_loss": min(losses) if losses else Decimal("0"),
        "max_drawdown_pct": max_dd,
        "total_fees": total_fees,
    }


def build_analytics_row(
    symbol: str, strategy_type: str, engine: FifoEngine, snapshots: list[PortfolioSnapshot],
    initial_capital: Decimal, periods_per_year: int, buy_hold_return_pct: Decimal,
) -> dict:
    final_value = snapshots[-1].total_value if snapshots else initial_capital
    days = max(1, (snapshots[-1].timestamp - snapshots[0].timestamp) // 86_400_000) if snapshots else 1
    total_return = analytics.total_return_pct(initial_capital, final_value)
    cagr = analytics.cagr_pct(initial_capital, final_value, days)
    max_dd, recovery_days = analytics.max_drawdown(snapshots)

    return {
        "symbol": symbol,
        "strategy": strategy_type,
        "total_return_pct": total_return,
        "cagr_pct": cagr,
        "sharpe_ratio": analytics.sharpe_ratio(snapshots, periods_per_year),
        "sortino_ratio": analytics.sortino_ratio(snapshots, periods_per_year),
        "calmar_ratio": analytics.calmar_ratio(cagr, max_dd),
        "max_drawdown_pct": max_dd,
        "recovery_days": recovery_days,
        "profit_factor": analytics.profit_factor(engine.trades),
        "expectancy": analytics.expectancy(engine.trades),
        "exposure_time_pct": analytics.exposure_time_pct(snapshots),
        "alpha_vs_buy_hold_pct": analytics.alpha_vs_buy_hold(total_return, buy_hold_return_pct),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_backtest_reports.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add backtest.py tests/test_backtest_reports.py
git commit -m "feat: add backtest raw-metrics and trader-analytics report row builders"
```

---

## Task 12: backtest.py — CSV export, console table, and main() CLI

**Files:**
- Modify: `backtest.py`
- Test: `tests/test_backtest_export.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: everything from Tasks 1-11
- Produces: `write_csv(rows: list[dict], path: str) -> None`, `print_console_table(rows: list[dict]) -> None`, `main() -> None` (CLI entry point)

`main()` wires the whole pipeline: load config → build provider via the factory → filter watchlist by liquidity → for each passing symbol, download klines once, run Buy & Hold first (its return is needed for every other strategy's alpha), then DCA and Grid → build both report rows for every (symbol, strategy) pair → write `backtest_report.csv` and `analytics_report.csv` → print a console summary. Excluded symbols are logged with their reason (spec §5).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backtest_export.py
import csv
from decimal import Decimal
from pathlib import Path

from backtest import write_csv


def test_write_csv_round_trips_rows(tmp_path: Path):
    rows = [
        {"symbol": "BTCUSDT", "strategy": "dca", "return_pct": Decimal("12.34"), "nb_trades": 2},
        {"symbol": "BTCUSDT", "strategy": "grid", "return_pct": Decimal("-5.00"), "nb_trades": 10},
    ]
    out_path = tmp_path / "report.csv"

    write_csv(rows, str(out_path))

    with open(out_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        read_rows = list(reader)

    assert len(read_rows) == 2
    assert read_rows[0]["symbol"] == "BTCUSDT"
    assert read_rows[0]["strategy"] == "dca"
    assert read_rows[0]["return_pct"] == "12.34"
    assert read_rows[1]["nb_trades"] == "10"


def test_write_csv_empty_rows_does_not_crash(tmp_path: Path):
    out_path = tmp_path / "empty.csv"
    write_csv([], str(out_path))
    assert out_path.exists()
    assert out_path.read_text(encoding="utf-8") == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_backtest_export.py -v`
Expected: FAIL with `ImportError: cannot import name 'write_csv' from 'backtest'`

- [ ] **Step 3: Append to `backtest.py`**

```python
# backtest.py (add imports at top, then append at the end)

# Add to the top of the file, alongside the existing imports (logging is
# already imported and `logger` already defined from Task 9 — do not
# redeclare either):
import csv
import time
from pathlib import Path

from config import load_config
from market_data.factory import build_provider


# Append at the end of the file:

def write_csv(rows: list[dict], path: str) -> None:
    if not rows:
        Path(path).write_text("", encoding="utf-8")
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def print_console_table(rows: list[dict]) -> None:
    if not rows:
        print("Aucun resultat.")
        return
    headers = list(rows[0].keys())
    print(" | ".join(headers))
    for row in rows:
        print(" | ".join(str(row[h]) for h in headers))


def main() -> None:
    cfg = load_config()
    provider = build_provider(cfg.data_source)

    passing, excluded = select_backtest_symbols(provider, cfg.watchlist, cfg.liquidity)
    for symbol, reason in excluded:
        logger.warning("Symbole exclu du backtest: %s (%s)", symbol, reason)

    interval_hours = INTERVAL_MS[cfg.backtest.interval] // 3_600_000
    periods_per_year = (365 * 24 * 3_600_000) // INTERVAL_MS[cfg.backtest.interval]
    now_ms = int(time.time() * 1000)

    raw_rows: list[dict] = []
    analytics_rows: list[dict] = []

    strategy_params = {
        "buy_hold": {"invest_at": "start"},
        "dca": {"amount_per_buy": 50, "frequency_hours": 24, "reference_price": "close"},
        "grid": {"lower_bound": 25000, "upper_bound": 35000, "n_levels": 10,
                 "spacing": "geometric", "order_size_quote": 100},
    }

    for symbol in passing:
        klines = download_backtest_klines(
            provider, symbol, cfg.backtest.interval, cfg.backtest.lookback_days, now_ms
        )
        if not klines:
            logger.warning("Aucune bougie recuperee pour %s, symbole ignore.", symbol)
            continue

        buy_hold_engine, buy_hold_snapshots = run_strategy(
            "buy_hold", klines, symbol, strategy_params["buy_hold"],
            cfg.backtest.initial_capital, cfg.fees.default_fee_pct, interval_hours,
        )
        buy_hold_return = analytics.total_return_pct(
            cfg.backtest.initial_capital, buy_hold_snapshots[-1].total_value
        )

        for strategy_type in ("buy_hold", "dca", "grid"):
            if strategy_type == "buy_hold":
                engine, snapshots = buy_hold_engine, buy_hold_snapshots
            else:
                engine, snapshots = run_strategy(
                    strategy_type, klines, symbol, strategy_params[strategy_type],
                    cfg.backtest.initial_capital, cfg.fees.default_fee_pct, interval_hours,
                )

            raw_rows.append(
                build_raw_metrics_row(symbol, strategy_type, engine, snapshots, cfg.backtest.initial_capital)
            )
            analytics_rows.append(
                build_analytics_row(
                    symbol, strategy_type, engine, snapshots, cfg.backtest.initial_capital,
                    periods_per_year, buy_hold_return,
                )
            )

    write_csv(raw_rows, "backtest_report.csv")
    write_csv(analytics_rows, "analytics_report.csv")
    print_console_table(raw_rows)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_backtest_export.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Update `README.md`**

Replace the "État actuel" section:

```markdown
## État actuel

Plan 1/5 (fondation) : `MarketDataProvider` (Binance + repli Kraken), moteur FIFO, `analytics.py` — termine.
Plan 2/5 (backtest) : filtre de liquidite, 3 strategies (Buy & Hold, DCA, Grid), `backtest.py` (telechargement,
rejeu, exports CSV `backtest_report.csv` + `analytics_report.csv`) — termine.

Lancer le backtest : `python backtest.py` (necessite `pip install -r requirements.txt`).

Pas encore de moteur live, de dashboard ni de deploiement — voir les plans suivants dans
`docs/superpowers/plans/`.
```

- [ ] **Step 6: Commit**

```bash
git add backtest.py tests/test_backtest_export.py README.md
git commit -m "feat: add CSV export, console table, and main() CLI to backtest.py"
```

---

## Task 13: Full suite verification

**Files:**
- (no new files — verification checkpoint)

**Interfaces:**
- Consumes: everything built in Tasks 1-12
- Produces: nothing new.

- [ ] **Step 1: Run the full test suite**

Run: `pytest -v`
Expected: all tests from Plan 1 (50) plus all tests from Tasks 1-12 of this plan pass — 0 failures.

- [ ] **Step 2: Smoke-test `backtest.py` end to end is NOT required in this task**

`backtest.py`'s `main()` makes real network calls to `data-api.binance.vision` — this is intentionally not part of the automated test suite (Plan 1's precedent: no real network calls in tests). If you want to manually verify it end-to-end, run `python backtest.py` and confirm `backtest_report.csv`/`analytics_report.csv` appear with sensible values — but this is a manual sanity check, not a required step for this task's completion, and must not be added to the automated suite.
