# Crypto Paper-Trading Simulator — Foundation (Plan 1 of 5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the shared foundation of the crypto paper-trading simulator: an interchangeable market-data provider (Binance default, Kraken fallback), a Decimal-exact FIFO trading engine, and a pure-function `analytics.py` module — all fully unit-tested, no database, no network calls in tests.

**Architecture:** Three independent, composable Python packages/modules with no cross-imports except one direction (`analytics.py` reads `engine.fifo_engine.Trade` and `models.PortfolioSnapshot`; nothing imports `analytics.py`). `market_data/` and `engine/` never import each other. All HTTP goes through `httpx.Client`, injected so tests use `httpx.MockTransport` — no real network calls in the test suite.

**Tech Stack:** Python 3.13, `httpx` (HTTP client, sync), `PyYAML` (config), `pytest`, stdlib `decimal`/`dataclasses`/`enum`. No database yet — this plan is a pure-Python library layer; SQLite persistence is wired up by the backtest and live-engine plans that follow.

**Spec:** `docs/superpowers/specs/2026-09-15-crypto-paper-trading-sim-design.md` — this plan implements sections 4, 6, 7, 10 and the four clarification decisions (D1–D4) of that spec. Read both; the spec carries the "why," this plan carries the "how."

## Global Constraints

- All price/quantity/PnL/cash values are `decimal.Decimal`, never `float`, anywhere in this codebase (spec §2).
- A `BUY` is rejected (logged WARNING, never executed) if `cash_balance < total_cost` — no negative cash, no implicit leverage (spec §2, §7).
- A `SELL` requesting more quantity than is available across a symbol's lots is rejected **in full** — no partial fill (spec decision D4).
- Thresholds/amounts live in `config.yaml`, never hardcoded (spec §2). Only keys a task actually consumes are added to `config.yaml` in that task — no speculative keys (YAGNI).
- The rest of the pipeline never talks to an exchange directly, only through `MarketDataProvider` (spec §4).
- `fee_pct` is a single simulated value from `config.yaml` (`fees.default_fee_pct`, default `0.001` = 0.1%), applied uniformly to every trade (spec §6).
- Repo root: `E:\Sites\crypto-sim`. No git remote configured — commit locally only.

---

## Task 1: Project scaffolding + config loader

**Files:**
- Create: `requirements.txt`
- Create: `pytest.ini`
- Create: `.gitignore`
- Create: `config.yaml`
- Create: `config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing (first task)
- Produces: `config.load_config(path: str | Path = "config.yaml") -> Config`, where `Config` is a frozen dataclass with fields `data_source: str`, `watchlist: list[str]`, `liquidity: LiquidityConfig` (`min_quote_volume_24h: Decimal`, `max_spread_bps: Decimal`), `backtest: BacktestConfig` (`initial_capital: Decimal`, `interval: str`, `lookback_days: int`), `fees: FeesConfig` (`default_fee_pct: Decimal`), `live: LiveConfig` (`poll_interval_seconds: int`, `poll_kline_interval: str`), `snapshots: SnapshotsConfig` (`retention_detail_days: int`).

- [ ] **Step 1: Create `requirements.txt`**

```
httpx>=0.27
PyYAML>=6.0
pytest>=8.0
```

- [ ] **Step 2: Create `pytest.ini`**

```ini
[pytest]
pythonpath = .
```

- [ ] **Step 3: Create `.gitignore`**

```
__pycache__/
*.pyc
.venv/
venv/
.env
*.db
*.db-wal
*.db-shm
data/
```

- [ ] **Step 4: Create `config.yaml`**

```yaml
data_source: binance

watchlist:
  - BTCUSDT
  - ETHUSDT
  - SOLUSDT
  - BNBUSDT

liquidity:
  min_quote_volume_24h: 50000000
  max_spread_bps: 10

backtest:
  initial_capital: 1000
  interval: 1h
  lookback_days: 90

fees:
  default_fee_pct: 0.001

live:
  poll_interval_seconds: 300
  poll_kline_interval: 5m

snapshots:
  retention_detail_days: 30
```

- [ ] **Step 5: Write the failing test**

```python
# tests/test_config.py
from decimal import Decimal
from pathlib import Path

from config import load_config


def test_load_config_parses_defaults(tmp_path: Path):
    sample = tmp_path / "config.yaml"
    sample.write_text(
        """
data_source: binance
watchlist:
  - BTCUSDT
  - ETHUSDT
liquidity:
  min_quote_volume_24h: 50000000
  max_spread_bps: 10
backtest:
  initial_capital: 1000
  interval: 1h
  lookback_days: 90
fees:
  default_fee_pct: 0.001
live:
  poll_interval_seconds: 300
  poll_kline_interval: 5m
snapshots:
  retention_detail_days: 30
""",
        encoding="utf-8",
    )

    cfg = load_config(sample)

    assert cfg.data_source == "binance"
    assert cfg.watchlist == ["BTCUSDT", "ETHUSDT"]
    assert cfg.liquidity.min_quote_volume_24h == Decimal("50000000")
    assert cfg.liquidity.max_spread_bps == Decimal("10")
    assert cfg.backtest.initial_capital == Decimal("1000")
    assert cfg.backtest.interval == "1h"
    assert cfg.backtest.lookback_days == 90
    assert cfg.fees.default_fee_pct == Decimal("0.001")
    assert cfg.live.poll_interval_seconds == 300
    assert cfg.live.poll_kline_interval == "5m"
    assert cfg.snapshots.retention_detail_days == 30
```

- [ ] **Step 6: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'config'` (or import error — `config.py` doesn't exist yet).

- [ ] **Step 7: Write `config.py`**

```python
# config.py
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml


@dataclass(frozen=True)
class LiquidityConfig:
    min_quote_volume_24h: Decimal
    max_spread_bps: Decimal


@dataclass(frozen=True)
class BacktestConfig:
    initial_capital: Decimal
    interval: str
    lookback_days: int


@dataclass(frozen=True)
class FeesConfig:
    default_fee_pct: Decimal


@dataclass(frozen=True)
class LiveConfig:
    poll_interval_seconds: int
    poll_kline_interval: str


@dataclass(frozen=True)
class SnapshotsConfig:
    retention_detail_days: int


@dataclass(frozen=True)
class Config:
    data_source: str
    watchlist: list[str]
    liquidity: LiquidityConfig
    backtest: BacktestConfig
    fees: FeesConfig
    live: LiveConfig
    snapshots: SnapshotsConfig


def load_config(path: str | Path = "config.yaml") -> Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return Config(
        data_source=raw["data_source"],
        watchlist=list(raw["watchlist"]),
        liquidity=LiquidityConfig(
            min_quote_volume_24h=Decimal(str(raw["liquidity"]["min_quote_volume_24h"])),
            max_spread_bps=Decimal(str(raw["liquidity"]["max_spread_bps"])),
        ),
        backtest=BacktestConfig(
            initial_capital=Decimal(str(raw["backtest"]["initial_capital"])),
            interval=raw["backtest"]["interval"],
            lookback_days=int(raw["backtest"]["lookback_days"]),
        ),
        fees=FeesConfig(default_fee_pct=Decimal(str(raw["fees"]["default_fee_pct"]))),
        live=LiveConfig(
            poll_interval_seconds=int(raw["live"]["poll_interval_seconds"]),
            poll_kline_interval=raw["live"]["poll_kline_interval"],
        ),
        snapshots=SnapshotsConfig(
            retention_detail_days=int(raw["snapshots"]["retention_detail_days"])
        ),
    )
```

- [ ] **Step 8: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add requirements.txt pytest.ini .gitignore config.yaml config.py tests/test_config.py
git commit -m "feat: add project scaffolding and typed config loader"
```

---

## Task 2: Market data types + provider interface

**Files:**
- Create: `market_data/__init__.py`
- Create: `market_data/types.py`
- Create: `market_data/provider.py`
- Test: `tests/test_market_data_types.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `Kline(open_time_ms: int, open: Decimal, high: Decimal, low: Decimal, close: Decimal, volume: Decimal, close_time_ms: int)` — frozen dataclass
  - `BookTicker(symbol: str, bid_price: Decimal, ask_price: Decimal)` — frozen dataclass with property `spread_relative -> Decimal`
  - `Ticker24h(symbol: str, quote_volume: Decimal)` — frozen dataclass
  - `INTERVAL_MS: dict[str, int]` = `{"1m": 60_000, "5m": 300_000, "1h": 3_600_000, "1d": 86_400_000}`
  - `MarketDataProvider` — ABC with abstract methods `get_klines(symbol, interval, start_ms, end_ms, limit=1000) -> list[Kline]`, `get_book_ticker(symbol) -> BookTicker`, `get_ticker_24h(symbol) -> Ticker24h`

- [ ] **Step 1: Create `market_data/__init__.py`** (empty file, makes `market_data` a package)

- [ ] **Step 2: Write the failing test**

```python
# tests/test_market_data_types.py
from decimal import Decimal

from market_data.types import BookTicker, Kline, Ticker24h
from market_data.provider import INTERVAL_MS, MarketDataProvider


def test_kline_is_frozen_and_holds_decimals():
    k = Kline(
        open_time_ms=1_700_000_000_000,
        open=Decimal("100.5"),
        high=Decimal("101.0"),
        low=Decimal("99.5"),
        close=Decimal("100.8"),
        volume=Decimal("12.3"),
        close_time_ms=1_700_000_059_999,
    )
    assert k.close == Decimal("100.8")


def test_book_ticker_spread_relative():
    bt = BookTicker(symbol="BTCUSDT", bid_price=Decimal("100"), ask_price=Decimal("101"))
    # (101-100) / ((101+100)/2) = 1 / 100.5
    assert bt.spread_relative == Decimal("1") / Decimal("100.5")


def test_ticker_24h_holds_quote_volume():
    t = Ticker24h(symbol="BTCUSDT", quote_volume=Decimal("60000000"))
    assert t.quote_volume == Decimal("60000000")


def test_interval_ms_covers_required_intervals():
    assert INTERVAL_MS == {
        "1m": 60_000,
        "5m": 300_000,
        "1h": 3_600_000,
        "1d": 86_400_000,
    }


def test_market_data_provider_is_abstract():
    import pytest

    with pytest.raises(TypeError):
        MarketDataProvider()  # abstract, cannot instantiate directly
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_market_data_types.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'market_data.types'`

- [ ] **Step 4: Write `market_data/types.py`**

```python
# market_data/types.py
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Kline:
    open_time_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    close_time_ms: int


@dataclass(frozen=True)
class BookTicker:
    symbol: str
    bid_price: Decimal
    ask_price: Decimal

    @property
    def spread_relative(self) -> Decimal:
        mid = (self.bid_price + self.ask_price) / Decimal(2)
        return (self.ask_price - self.bid_price) / mid


@dataclass(frozen=True)
class Ticker24h:
    symbol: str
    quote_volume: Decimal
```

- [ ] **Step 5: Write `market_data/provider.py`**

```python
# market_data/provider.py
from abc import ABC, abstractmethod

from market_data.types import BookTicker, Kline, Ticker24h

INTERVAL_MS: dict[str, int] = {
    "1m": 60_000,
    "5m": 300_000,
    "1h": 3_600_000,
    "1d": 86_400_000,
}


class MarketDataProvider(ABC):
    """Normalizes klines/ticker/spread across exchanges. Never call an exchange
    SDK/endpoint directly outside a MarketDataProvider implementation."""

    @abstractmethod
    def get_klines(
        self, symbol: str, interval: str, start_ms: int, end_ms: int, limit: int = 1000
    ) -> list[Kline]:
        raise NotImplementedError

    @abstractmethod
    def get_book_ticker(self, symbol: str) -> BookTicker:
        raise NotImplementedError

    @abstractmethod
    def get_ticker_24h(self, symbol: str) -> Ticker24h:
        raise NotImplementedError
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_market_data_types.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add market_data/__init__.py market_data/types.py market_data/provider.py tests/test_market_data_types.py
git commit -m "feat: add market data types and MarketDataProvider interface"
```

---

## Task 3: Binance provider (default implementation)

**Files:**
- Create: `market_data/binance.py`
- Test: `tests/test_binance_provider.py`

**Interfaces:**
- Consumes: `market_data.types.{Kline,BookTicker,Ticker24h}`, `market_data.provider.MarketDataProvider`
- Produces: `BinanceProvider(client: httpx.Client | None = None, base_url: str = "https://data-api.binance.vision")` implementing `MarketDataProvider`. Constructor accepts an injected `httpx.Client` for testing.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_binance_provider.py
from decimal import Decimal

import httpx
import pytest

from market_data.binance import BinanceProvider


def _client_with(handler) -> httpx.Client:
    transport = httpx.MockTransport(handler)
    return httpx.Client(transport=transport, base_url="https://data-api.binance.vision")


def test_get_klines_parses_binance_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v3/klines"
        assert request.url.params["symbol"] == "BTCUSDT"
        assert request.url.params["interval"] == "1h"
        assert request.url.params["startTime"] == "1000"
        assert request.url.params["endTime"] == "2000"
        assert request.url.params["limit"] == "1000"
        return httpx.Response(
            200,
            json=[
                [1000, "100.0", "101.5", "99.0", "100.8", "12.3", 3599999, "0", 0, "0", "0", "0"]
            ],
        )

    provider = BinanceProvider(client=_client_with(handler))
    klines = provider.get_klines("BTCUSDT", "1h", 1000, 2000)

    assert len(klines) == 1
    k = klines[0]
    assert k.open_time_ms == 1000
    assert k.open == Decimal("100.0")
    assert k.high == Decimal("101.5")
    assert k.low == Decimal("99.0")
    assert k.close == Decimal("100.8")
    assert k.volume == Decimal("12.3")
    assert k.close_time_ms == 3599999


def test_get_book_ticker_parses_binance_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v3/ticker/bookTicker"
        assert request.url.params["symbol"] == "BTCUSDT"
        return httpx.Response(
            200,
            json={"symbol": "BTCUSDT", "bidPrice": "100.00", "bidQty": "1", "askPrice": "100.10", "askQty": "1"},
        )

    provider = BinanceProvider(client=_client_with(handler))
    bt = provider.get_book_ticker("BTCUSDT")

    assert bt.symbol == "BTCUSDT"
    assert bt.bid_price == Decimal("100.00")
    assert bt.ask_price == Decimal("100.10")


def test_get_ticker_24h_parses_binance_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v3/ticker/24hr"
        assert request.url.params["symbol"] == "BTCUSDT"
        return httpx.Response(200, json={"symbol": "BTCUSDT", "quoteVolume": "60000000.50"})

    provider = BinanceProvider(client=_client_with(handler))
    t = provider.get_ticker_24h("BTCUSDT")

    assert t.symbol == "BTCUSDT"
    assert t.quote_volume == Decimal("60000000.50")


def test_get_klines_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(451, json={"code": -1, "msg": "restricted location"})

    provider = BinanceProvider(client=_client_with(handler))
    with pytest.raises(httpx.HTTPStatusError):
        provider.get_klines("BTCUSDT", "1h", 1000, 2000)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_binance_provider.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'market_data.binance'`

- [ ] **Step 3: Write `market_data/binance.py`**

```python
# market_data/binance.py
from decimal import Decimal

import httpx

from market_data.provider import MarketDataProvider
from market_data.types import BookTicker, Kline, Ticker24h

DEFAULT_BASE_URL = "https://data-api.binance.vision"


class BinanceProvider(MarketDataProvider):
    """Default MarketDataProvider — data-api.binance.vision, public endpoints, no API key."""

    def __init__(self, client: httpx.Client | None = None, base_url: str = DEFAULT_BASE_URL):
        self._client = client or httpx.Client(base_url=base_url, timeout=10.0)

    def get_klines(
        self, symbol: str, interval: str, start_ms: int, end_ms: int, limit: int = 1000
    ) -> list[Kline]:
        resp = self._client.get(
            "/api/v3/klines",
            params={
                "symbol": symbol,
                "interval": interval,
                "startTime": start_ms,
                "endTime": end_ms,
                "limit": limit,
            },
        )
        resp.raise_for_status()
        rows = resp.json()
        return [
            Kline(
                open_time_ms=row[0],
                open=Decimal(row[1]),
                high=Decimal(row[2]),
                low=Decimal(row[3]),
                close=Decimal(row[4]),
                volume=Decimal(row[5]),
                close_time_ms=row[6],
            )
            for row in rows
        ]

    def get_book_ticker(self, symbol: str) -> BookTicker:
        resp = self._client.get("/api/v3/ticker/bookTicker", params={"symbol": symbol})
        resp.raise_for_status()
        data = resp.json()
        return BookTicker(
            symbol=data["symbol"],
            bid_price=Decimal(data["bidPrice"]),
            ask_price=Decimal(data["askPrice"]),
        )

    def get_ticker_24h(self, symbol: str) -> Ticker24h:
        resp = self._client.get("/api/v3/ticker/24hr", params={"symbol": symbol})
        resp.raise_for_status()
        data = resp.json()
        return Ticker24h(symbol=data["symbol"], quote_volume=Decimal(data["quoteVolume"]))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_binance_provider.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add market_data/binance.py tests/test_binance_provider.py
git commit -m "feat: add BinanceProvider (default MarketDataProvider implementation)"
```

---

## Task 4: Kraken provider (documented fallback)

**Files:**
- Create: `market_data/kraken.py`
- Test: `tests/test_kraken_provider.py`

**Interfaces:**
- Consumes: `market_data.types.{Kline,BookTicker,Ticker24h}`, `market_data.provider.MarketDataProvider`
- Produces: `KrakenProvider(client: httpx.Client | None = None, base_url: str = "https://api.kraken.com")` implementing `MarketDataProvider`. Only supports symbols in its internal `SYMBOL_MAP` (`BTCUSDT`, `ETHUSDT`, `SOLUSDT` — Kraken has no `BNBUSDT`); raises `ValueError` for unmapped symbols.

Kraken's `Ticker` endpoint has no quote-volume field (only base-currency 24h volume in `v[1]`), so `get_ticker_24h` approximates `quote_volume = base_volume_24h * last_price` — documented in a code comment since it's not an exact match to Binance's `quoteVolume`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_kraken_provider.py
from decimal import Decimal

import httpx
import pytest

from market_data.kraken import KrakenProvider


def _client_with(handler) -> httpx.Client:
    transport = httpx.MockTransport(handler)
    return httpx.Client(transport=transport, base_url="https://api.kraken.com")


def test_get_klines_parses_kraken_ohlc_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/0/public/OHLC"
        assert request.url.params["pair"] == "XBTUSDT"
        assert request.url.params["interval"] == "60"
        return httpx.Response(
            200,
            json={
                "error": [],
                "result": {
                    "XBTUSDT": [
                        [1000, "100.0", "101.5", "99.0", "100.8", "100.4", "12.3", 5]
                    ],
                    "last": 1000,
                },
            },
        )

    provider = KrakenProvider(client=_client_with(handler))
    klines = provider.get_klines("BTCUSDT", "1h", 0, 2_000_000)

    assert len(klines) == 1
    k = klines[0]
    assert k.open_time_ms == 1_000_000
    assert k.open == Decimal("100.0")
    assert k.high == Decimal("101.5")
    assert k.low == Decimal("99.0")
    assert k.close == Decimal("100.8")
    assert k.volume == Decimal("12.3")


def test_get_klines_stops_at_end_ms():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "error": [],
                "result": {
                    "XBTUSDT": [
                        [1000, "100.0", "101.0", "99.0", "100.5", "100.2", "1", 1],
                        [4000, "100.5", "102.0", "100.0", "101.0", "100.7", "1", 1],
                    ],
                    "last": 4000,
                },
            },
        )

    provider = KrakenProvider(client=_client_with(handler))
    klines = provider.get_klines("BTCUSDT", "1h", 0, 2_000_000)

    assert len(klines) == 1
    assert klines[0].open_time_ms == 1_000_000


def test_get_book_ticker_parses_kraken_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/0/public/Ticker"
        assert request.url.params["pair"] == "XBTUSDT"
        return httpx.Response(
            200,
            json={
                "error": [],
                "result": {"XBTUSDT": {"a": ["100.10", "1", "1"], "b": ["100.00", "1", "1"], "c": ["100.05", "1"], "v": ["10", "200"]}},
            },
        )

    provider = KrakenProvider(client=_client_with(handler))
    bt = provider.get_book_ticker("BTCUSDT")

    assert bt.symbol == "BTCUSDT"
    assert bt.bid_price == Decimal("100.00")
    assert bt.ask_price == Decimal("100.10")


def test_get_ticker_24h_approximates_quote_volume():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "error": [],
                "result": {"XBTUSDT": {"a": ["100.10", "1", "1"], "b": ["100.00", "1", "1"], "c": ["100.00", "1"], "v": ["10", "200"]}},
            },
        )

    provider = KrakenProvider(client=_client_with(handler))
    t = provider.get_ticker_24h("BTCUSDT")

    # base 24h volume (200) * last price (100.00)
    assert t.quote_volume == Decimal("20000.00")


def test_unmapped_symbol_raises_value_error():
    provider = KrakenProvider(client=_client_with(lambda r: httpx.Response(200, json={})))
    with pytest.raises(ValueError, match="BNBUSDT"):
        provider.get_klines("BNBUSDT", "1h", 0, 1000)


def test_kraken_error_payload_raises_runtime_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": ["EQuery:Unknown asset pair"], "result": {}})

    provider = KrakenProvider(client=_client_with(handler))
    with pytest.raises(RuntimeError, match="EQuery"):
        provider.get_klines("BTCUSDT", "1h", 0, 1000)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_kraken_provider.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'market_data.kraken'`

- [ ] **Step 3: Write `market_data/kraken.py`**

```python
# market_data/kraken.py
from decimal import Decimal

import httpx

from market_data.provider import MarketDataProvider
from market_data.types import BookTicker, Kline, Ticker24h

DEFAULT_BASE_URL = "https://api.kraken.com"

# Kraken pair codes for the watchlist that has a Kraken equivalent.
# Kraken has no BNBUSDT pair (BNB is a Binance-ecosystem token) — callers get ValueError.
SYMBOL_MAP: dict[str, str] = {
    "BTCUSDT": "XBTUSDT",
    "ETHUSDT": "ETHUSDT",
    "SOLUSDT": "SOLUSDT",
}

KRAKEN_INTERVAL_MINUTES: dict[str, int] = {"1m": 1, "5m": 5, "1h": 60, "1d": 1440}


class KrakenProvider(MarketDataProvider):
    """Documented fallback MarketDataProvider — api.kraken.com public endpoints, no API key.
    Kraken holds a valid CASP license in France; use this if data-api.binance.vision
    ever becomes unreachable (see spec section 4)."""

    def __init__(self, client: httpx.Client | None = None, base_url: str = DEFAULT_BASE_URL):
        self._client = client or httpx.Client(base_url=base_url, timeout=10.0)

    def _pair(self, symbol: str) -> str:
        if symbol not in SYMBOL_MAP:
            raise ValueError(f"Kraken repli: symbole non mappe: {symbol}")
        return SYMBOL_MAP[symbol]

    def _get_result(self, path: str, params: dict) -> dict:
        resp = self._client.get(path, params=params)
        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            raise RuntimeError(f"Kraken API error: {data['error']}")
        return data["result"]

    def get_klines(
        self, symbol: str, interval: str, start_ms: int, end_ms: int, limit: int = 1000
    ) -> list[Kline]:
        pair = self._pair(symbol)
        interval_minutes = KRAKEN_INTERVAL_MINUTES[interval]
        result = self._get_result(
            "/0/public/OHLC",
            {"pair": pair, "interval": interval_minutes, "since": start_ms // 1000},
        )
        rows = result[pair]
        klines: list[Kline] = []
        for row in rows:
            open_time_ms = int(row[0]) * 1000
            if open_time_ms > end_ms:
                break
            klines.append(
                Kline(
                    open_time_ms=open_time_ms,
                    open=Decimal(str(row[1])),
                    high=Decimal(str(row[2])),
                    low=Decimal(str(row[3])),
                    close=Decimal(str(row[4])),
                    volume=Decimal(str(row[6])),
                    close_time_ms=open_time_ms + interval_minutes * 60_000 - 1,
                )
            )
            if len(klines) >= limit:
                break
        return klines

    def get_book_ticker(self, symbol: str) -> BookTicker:
        pair = self._pair(symbol)
        result = self._get_result("/0/public/Ticker", {"pair": pair})
        t = result[pair]
        return BookTicker(symbol=symbol, bid_price=Decimal(t["b"][0]), ask_price=Decimal(t["a"][0]))

    def get_ticker_24h(self, symbol: str) -> Ticker24h:
        pair = self._pair(symbol)
        result = self._get_result("/0/public/Ticker", {"pair": pair})
        t = result[pair]
        # Kraken's Ticker has no quote-volume field, only base-currency 24h volume (v[1]).
        # Approximate quote volume as base_volume_24h * last trade price.
        last_price = Decimal(t["c"][0])
        base_volume_24h = Decimal(t["v"][1])
        return Ticker24h(symbol=symbol, quote_volume=base_volume_24h * last_price)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_kraken_provider.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add market_data/kraken.py tests/test_kraken_provider.py
git commit -m "feat: add KrakenProvider (documented fallback MarketDataProvider)"
```

---

## Task 5: Paginated kline download helper

**Files:**
- Create: `market_data/pagination.py`
- Test: `tests/test_pagination.py`

**Interfaces:**
- Consumes: `market_data.provider.{MarketDataProvider, INTERVAL_MS}`, `market_data.types.Kline`
- Produces: `fetch_klines_paginated(provider: MarketDataProvider, symbol: str, interval: str, start_ms: int, end_ms: int, limit: int = 1000, sleep_seconds: float = 0.2) -> list[Kline]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pagination.py
from decimal import Decimal

from market_data.pagination import fetch_klines_paginated
from market_data.provider import MarketDataProvider
from market_data.types import BookTicker, Kline, Ticker24h


def _kline(open_time_ms: int) -> Kline:
    return Kline(
        open_time_ms=open_time_ms,
        open=Decimal("1"),
        high=Decimal("1"),
        low=Decimal("1"),
        close=Decimal("1"),
        volume=Decimal("1"),
        close_time_ms=open_time_ms + 3_599_999,
    )


class FakeProvider(MarketDataProvider):
    """Returns pre-scripted pages, one per call, regardless of requested range."""

    def __init__(self, pages: list[list[Kline]]):
        self._pages = pages
        self.calls: list[tuple] = []

    def get_klines(self, symbol, interval, start_ms, end_ms, limit=1000):
        self.calls.append((symbol, interval, start_ms, end_ms, limit))
        if not self._pages:
            return []
        return self._pages.pop(0)

    def get_book_ticker(self, symbol):
        raise NotImplementedError

    def get_ticker_24h(self, symbol):
        raise NotImplementedError


def test_paginates_until_batch_is_empty(monkeypatch):
    sleeps = []
    monkeypatch.setattr("market_data.pagination.time.sleep", lambda s: sleeps.append(s))

    interval_ms = 3_600_000
    page1 = [_kline(0), _kline(interval_ms)]
    page2 = [_kline(interval_ms * 2)]
    provider = FakeProvider(pages=[page1, page2, []])

    result = fetch_klines_paginated(
        provider, "BTCUSDT", "1h", start_ms=0, end_ms=interval_ms * 10, sleep_seconds=0
    )

    assert [k.open_time_ms for k in result] == [0, interval_ms, interval_ms * 2]
    # 3rd call gets the empty page and stops the loop before sleeping
    assert len(provider.calls) == 3
    assert sleeps == [0, 0]


def test_cursor_advances_from_last_kline_plus_interval(monkeypatch):
    monkeypatch.setattr("market_data.pagination.time.sleep", lambda s: None)

    interval_ms = 3_600_000
    page1 = [_kline(0), _kline(interval_ms)]
    provider = FakeProvider(pages=[page1, []])

    fetch_klines_paginated(provider, "BTCUSDT", "1h", start_ms=0, end_ms=interval_ms * 10)

    # second call's start_ms must be last kline's open_time + interval_ms
    assert provider.calls[1][2] == interval_ms * 2


def test_stops_once_cursor_reaches_end_ms(monkeypatch):
    monkeypatch.setattr("market_data.pagination.time.sleep", lambda s: None)

    interval_ms = 3_600_000
    page1 = [_kline(0)]
    provider = FakeProvider(pages=[page1])

    result = fetch_klines_paginated(provider, "BTCUSDT", "1h", start_ms=0, end_ms=interval_ms)

    assert [k.open_time_ms for k in result] == [0]
    assert len(provider.calls) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pagination.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'market_data.pagination'`

- [ ] **Step 3: Write `market_data/pagination.py`**

```python
# market_data/pagination.py
import time

from market_data.provider import INTERVAL_MS, MarketDataProvider
from market_data.types import Kline


def fetch_klines_paginated(
    provider: MarketDataProvider,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    limit: int = 1000,
    sleep_seconds: float = 0.2,
) -> list[Kline]:
    """Loops startTime/endTime to download more klines than a single call's limit allows.
    Sleeps between calls to respect the provider's rate limit (spec section 1)."""
    out: list[Kline] = []
    cursor = start_ms
    interval_ms = INTERVAL_MS[interval]
    while cursor < end_ms:
        batch = provider.get_klines(symbol, interval, cursor, end_ms, limit=limit)
        if not batch:
            break
        out.extend(batch)
        cursor = batch[-1].open_time_ms + interval_ms
        time.sleep(sleep_seconds)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_pagination.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add market_data/pagination.py tests/test_pagination.py
git commit -m "feat: add paginated kline download helper"
```

---

## Task 6: FIFO engine — BUY path

**Files:**
- Create: `engine/__init__.py`
- Create: `engine/fifo_engine.py`
- Test: `tests/test_fifo_engine.py`

**Interfaces:**
- Consumes: nothing (pure stdlib)
- Produces:
  - `Side` — `str, Enum` with `BUY`, `SELL`
  - `Lot(id: int, trade_id_achat: int, symbol: str, quantity_restante: Decimal, prix_achat: Decimal, timestamp_achat: int)`
  - `Trade(id: int, timestamp: int, symbol: str, side: Side, price: Decimal, quantity: Decimal, fee_pct: Decimal, fee_amount: Decimal, total_cost: Decimal, realized_pnl: Decimal | None, cash_balance_after: Decimal, strategy_name: str)`
  - `FifoEngine(initial_cash: Decimal, fee_pct: Decimal)` with `.cash_balance: Decimal`, `.lots: list[Lot]`, `.trades: list[Trade]`, `.buy(timestamp, symbol, price, quantity, strategy_name) -> Trade | None`, `.get_lots(symbol) -> list[Lot]`, `.position_value(symbol, current_price) -> Decimal`

`total_cost` semantics (needed by later tasks and by the spec's explicit `cash_balance >= total_cost` check): for a BUY it is the full cash outflow, `price*quantity + fee_amount`. Task 7 defines it symmetrically for SELL as the net cash inflow, `price*quantity - fee_amount`.

- [ ] **Step 1: Create `engine/__init__.py`** (empty file)

- [ ] **Step 2: Write the failing tests (BUY-side only for this task)**

```python
# tests/test_fifo_engine.py
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


def test_buy_rejected_when_cash_insufficient():
    engine = FifoEngine(initial_cash=Decimal("50"), fee_pct=Decimal("0.001"))

    trade = engine.buy(
        timestamp=1000, symbol="BTCUSDT", price=Decimal("100"), quantity=Decimal("1"),
        strategy_name="test",
    )

    assert trade is None
    assert engine.cash_balance == Decimal("50")
    assert engine.get_lots("BTCUSDT") == []
    assert engine.trades == []


def test_position_value_sums_remaining_lots_at_current_price():
    engine = FifoEngine(initial_cash=Decimal("10000"), fee_pct=Decimal("0.001"))
    engine.buy(timestamp=1000, symbol="BTCUSDT", price=Decimal("100"), quantity=Decimal("2"), strategy_name="t")
    engine.buy(timestamp=2000, symbol="BTCUSDT", price=Decimal("110"), quantity=Decimal("3"), strategy_name="t")

    value = engine.position_value("BTCUSDT", current_price=Decimal("120"))

    assert value == Decimal("600")  # (2+3) * 120
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_fifo_engine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'engine.fifo_engine'`

- [ ] **Step 4: Write `engine/fifo_engine.py` (BUY path)**

```python
# engine/fifo_engine.py
import logging
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

logger = logging.getLogger(__name__)


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class Lot:
    id: int
    trade_id_achat: int
    symbol: str
    quantity_restante: Decimal
    prix_achat: Decimal
    timestamp_achat: int


@dataclass
class Trade:
    id: int
    timestamp: int
    symbol: str
    side: Side
    price: Decimal
    quantity: Decimal
    fee_pct: Decimal
    fee_amount: Decimal
    total_cost: Decimal
    realized_pnl: Decimal | None
    cash_balance_after: Decimal
    strategy_name: str


class FifoEngine:
    """Decimal-exact FIFO paper-trading engine. No cash may go negative, no
    implicit leverage: a BUY that costs more than cash_balance is rejected,
    never executed."""

    def __init__(self, initial_cash: Decimal, fee_pct: Decimal):
        self.cash_balance = initial_cash
        self.fee_pct = fee_pct
        self.lots: list[Lot] = []
        self.trades: list[Trade] = []
        self._next_trade_id = 1
        self._next_lot_id = 1

    def get_lots(self, symbol: str) -> list[Lot]:
        return [lot for lot in self.lots if lot.symbol == symbol]

    def position_value(self, symbol: str, current_price: Decimal) -> Decimal:
        total_qty = sum((lot.quantity_restante for lot in self.get_lots(symbol)), Decimal("0"))
        return total_qty * current_price

    def buy(
        self, timestamp: int, symbol: str, price: Decimal, quantity: Decimal, strategy_name: str
    ) -> Trade | None:
        gross = price * quantity
        fee_amount = gross * self.fee_pct
        total_cost = gross + fee_amount

        if total_cost > self.cash_balance:
            logger.warning(
                "BUY rejete: cash insuffisant symbol=%s cash_balance=%s total_cost=%s",
                symbol, self.cash_balance, total_cost,
            )
            return None

        self.cash_balance -= total_cost
        trade = Trade(
            id=self._next_trade_id,
            timestamp=timestamp,
            symbol=symbol,
            side=Side.BUY,
            price=price,
            quantity=quantity,
            fee_pct=self.fee_pct,
            fee_amount=fee_amount,
            total_cost=total_cost,
            realized_pnl=None,
            cash_balance_after=self.cash_balance,
            strategy_name=strategy_name,
        )
        self._next_trade_id += 1
        self.trades.append(trade)

        self.lots.append(
            Lot(
                id=self._next_lot_id,
                trade_id_achat=trade.id,
                symbol=symbol,
                quantity_restante=quantity,
                prix_achat=price,
                timestamp_achat=timestamp,
            )
        )
        self._next_lot_id += 1
        return trade
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_fifo_engine.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add engine/__init__.py engine/fifo_engine.py tests/test_fifo_engine.py
git commit -m "feat: add FIFO engine BUY path with insufficient-cash rejection"
```

---

## Task 7: FIFO engine — SELL path (completes the required test matrix)

**Files:**
- Modify: `engine/fifo_engine.py`
- Modify: `tests/test_fifo_engine.py`

**Interfaces:**
- Consumes: `engine.fifo_engine.{Side, Lot, Trade, FifoEngine}` from Task 6
- Produces: `FifoEngine.sell(timestamp, symbol, price, quantity, strategy_name) -> Trade | None`

This task adds the four remaining scenarios the spec requires (spec §7): single-buy-then-partial-sell, a sell that crosses multiple lots, multiple sells across lots of different ages, and a sell with no lot available (rejected in full per decision D4).

- [ ] **Step 1: Append the failing tests**

```python
# tests/test_fifo_engine.py (append)

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


def test_sell_with_no_lot_available_is_rejected_in_full():
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))

    trade = engine.sell(timestamp=1000, symbol="BTCUSDT", price=Decimal("100"), quantity=Decimal("1"), strategy_name="t")

    assert trade is None
    assert engine.cash_balance == Decimal("1000")
    assert engine.trades == []


def test_sell_exceeding_available_quantity_is_rejected_in_full_not_partial():
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    engine.buy(timestamp=1000, symbol="BTCUSDT", price=Decimal("100"), quantity=Decimal("1"), strategy_name="t")
    cash_after_buy = engine.cash_balance

    trade = engine.sell(timestamp=2000, symbol="BTCUSDT", price=Decimal("110"), quantity=Decimal("5"), strategy_name="t")

    assert trade is None
    assert engine.cash_balance == cash_after_buy
    lots = engine.get_lots("BTCUSDT")
    assert len(lots) == 1
    assert lots[0].quantity_restante == Decimal("1")
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `pytest tests/test_fifo_engine.py -v`
Expected: FAIL — `AttributeError: 'FifoEngine' object has no attribute 'sell'` on the 5 new tests; the 3 existing BUY tests still pass.

- [ ] **Step 3: Add `sell()` to `engine/fifo_engine.py`**

```python
# engine/fifo_engine.py (add method to FifoEngine, after buy())

    def sell(
        self, timestamp: int, symbol: str, price: Decimal, quantity: Decimal, strategy_name: str
    ) -> Trade | None:
        symbol_lots = self.get_lots(symbol)
        available = sum((lot.quantity_restante for lot in symbol_lots), Decimal("0"))

        if quantity > available:
            logger.warning(
                "SELL rejete: quantite demandee superieure au disponible "
                "symbol=%s quantite_demandee=%s disponible=%s",
                symbol, quantity, available,
            )
            return None

        remaining = quantity
        realized_pnl = Decimal("0")
        for lot in symbol_lots:
            if remaining <= 0:
                break
            take = min(lot.quantity_restante, remaining)
            realized_pnl += (price - lot.prix_achat) * take
            lot.quantity_restante -= take
            remaining -= take

        self.lots = [lot for lot in self.lots if lot.quantity_restante > 0]

        gross = price * quantity
        fee_amount = gross * self.fee_pct
        realized_pnl -= fee_amount
        total_cost = gross - fee_amount  # net proceeds credited to cash

        self.cash_balance += total_cost
        trade = Trade(
            id=self._next_trade_id,
            timestamp=timestamp,
            symbol=symbol,
            side=Side.SELL,
            price=price,
            quantity=quantity,
            fee_pct=self.fee_pct,
            fee_amount=fee_amount,
            total_cost=total_cost,
            realized_pnl=realized_pnl,
            cash_balance_after=self.cash_balance,
            strategy_name=strategy_name,
        )
        self._next_trade_id += 1
        self.trades.append(trade)
        return trade
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_fifo_engine.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add engine/fifo_engine.py tests/test_fifo_engine.py
git commit -m "feat: add FIFO engine SELL path with full-reject-on-oversell (decision D4)"
```

---

## Task 8: `models.py` + `analytics.py` part 1 (return, drawdown, profit factor, expectancy, exposure)

**Files:**
- Create: `models.py`
- Create: `analytics.py`
- Test: `tests/test_analytics.py`

**Interfaces:**
- Consumes: `engine.fifo_engine.{Trade, Side}`
- Produces:
  - `PortfolioSnapshot(timestamp: int, symbol: str, cash_balance: Decimal, position_value: Decimal, total_value: Decimal, unrealized_pnl: Decimal, realized_pnl_cumule: Decimal)` (in `models.py`)
  - In `analytics.py`: `total_return_pct(initial_capital, final_total_value) -> Decimal`, `cagr_pct(initial_capital, final_total_value, days) -> Decimal`, `drawdown_curve(snapshots) -> list[tuple[int, Decimal]]`, `max_drawdown(snapshots) -> tuple[Decimal, int | None]`, `profit_factor(trades) -> Decimal`, `expectancy(trades) -> Decimal`, `exposure_time_pct(snapshots) -> Decimal`

- [ ] **Step 1: Write `models.py`**

```python
# models.py
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class PortfolioSnapshot:
    timestamp: int
    symbol: str
    cash_balance: Decimal
    position_value: Decimal
    total_value: Decimal
    unrealized_pnl: Decimal
    realized_pnl_cumule: Decimal
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_analytics.py
from datetime import datetime, timezone
from decimal import Decimal

import analytics
from engine.fifo_engine import Side, Trade
from models import PortfolioSnapshot

DAY_MS = 86_400_000
T0 = 1_700_000_000_000

# 10 daily snapshots: a rise to a peak, a drawdown, then a recovery past the old peak.
_TOTAL_VALUES = [1000, 1050, 1100, 900, 950, 1000, 1080, 1150, 1200, 1250]
_POSITION_VALUES = [800, 850, 900, 0, 0, 800, 880, 950, 1000, 1050]


def _snapshots() -> list[PortfolioSnapshot]:
    return [
        PortfolioSnapshot(
            timestamp=T0 + i * DAY_MS,
            symbol="BTCUSDT",
            cash_balance=Decimal(total) - Decimal(pos),
            position_value=Decimal(pos),
            total_value=Decimal(total),
            unrealized_pnl=Decimal("0"),
            realized_pnl_cumule=Decimal("0"),
        )
        for i, (total, pos) in enumerate(zip(_TOTAL_VALUES, _POSITION_VALUES))
    ]


def _trade(realized_pnl: Decimal, side: Side = Side.SELL, trade_id: int = 1) -> Trade:
    return Trade(
        id=trade_id, timestamp=1000, symbol="BTCUSDT", side=side,
        price=Decimal("100"), quantity=Decimal("1"), fee_pct=Decimal("0.001"),
        fee_amount=Decimal("0.1"), total_cost=Decimal("100"),
        realized_pnl=realized_pnl if side == Side.SELL else None,
        cash_balance_after=Decimal("1000"), strategy_name="t",
    )


def test_total_return_pct():
    assert analytics.total_return_pct(Decimal("1000"), Decimal("1250")) == Decimal("25.00")


def test_cagr_pct_matches_hand_computed_value():
    result = analytics.cagr_pct(Decimal("1000"), Decimal("1250"), days=9)
    assert result.quantize(Decimal("0.0001")) == Decimal("851507.3801")


def test_drawdown_curve_tracks_running_peak():
    curve = analytics.drawdown_curve(_snapshots())
    dd_values = [round(dd, 4) for _, dd in curve]
    assert dd_values[0] == Decimal("0")
    assert dd_values[2] == Decimal("0")  # new peak at index 2 (1100)
    assert dd_values[3].quantize(Decimal("0.0001")) == Decimal("18.1818")  # trough at index 3
    assert dd_values[7] == Decimal("0")  # recovered past old peak at index 7 (1150)


def test_max_drawdown_reports_magnitude_and_recovery_days():
    max_dd, recovery_days = analytics.max_drawdown(_snapshots())
    assert max_dd.quantize(Decimal("0.0001")) == Decimal("18.1818")
    assert recovery_days == 5


def test_max_drawdown_with_no_decline_is_zero_with_zero_recovery():
    flat = [
        PortfolioSnapshot(
            timestamp=T0 + i * DAY_MS, symbol="BTCUSDT",
            cash_balance=Decimal("1000"), position_value=Decimal("0"),
            total_value=Decimal("1000"), unrealized_pnl=Decimal("0"),
            realized_pnl_cumule=Decimal("0"),
        )
        for i in range(3)
    ]
    max_dd, recovery_days = analytics.max_drawdown(flat)
    assert max_dd == Decimal("0")
    assert recovery_days == 0


def test_profit_factor_normal_case():
    trades = [_trade(Decimal("50")), _trade(Decimal("-20")), _trade(Decimal("80")),
              _trade(Decimal("-30")), _trade(Decimal("10"))]
    # gains = 50+80+10 = 140 ; losses = 20+30 = 50 ; 140/50 = 2.8
    assert analytics.profit_factor(trades) == Decimal("2.8")


def test_profit_factor_no_losses_is_infinite():
    trades = [_trade(Decimal("50")), _trade(Decimal("10"))]
    assert analytics.profit_factor(trades) == Decimal("Infinity")


def test_profit_factor_no_sell_trades_is_zero():
    trades = [_trade(None, side=Side.BUY)]
    assert analytics.profit_factor(trades) == Decimal("0")


def test_expectancy_matches_hand_computed_value():
    trades = [_trade(Decimal("50")), _trade(Decimal("-20")), _trade(Decimal("80")),
              _trade(Decimal("-30")), _trade(Decimal("10"))]
    # win_rate=3/5=0.6 avg_win=140/3 ; loss_rate=0.4 avg_loss=50/2=25
    # expectancy = 0.6*(140/3) - 0.4*25 = 28 - 10 = 18
    assert analytics.expectancy(trades).quantize(Decimal("0.0001")) == Decimal("18.0000")


def test_exposure_time_pct():
    # 8 of 10 snapshots have position_value > 0 (indices 3,4 are flat cash)
    assert analytics.exposure_time_pct(_snapshots()) == Decimal("80.00")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_analytics.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'analytics'`

- [ ] **Step 4: Write `analytics.py` (part 1)**

```python
# analytics.py
from decimal import Decimal

from engine.fifo_engine import Side, Trade
from models import PortfolioSnapshot

DAY_MS = 86_400_000


def total_return_pct(initial_capital: Decimal, final_total_value: Decimal) -> Decimal:
    return (final_total_value - initial_capital) / initial_capital * Decimal(100)


def cagr_pct(initial_capital: Decimal, final_total_value: Decimal, days: int) -> Decimal:
    """Annualized return. Short backtest windows extrapolate aggressively — a 25% gain
    over 9 days annualizes to a very large number; that's expected, not a bug."""
    ratio = final_total_value / initial_capital
    exponent = Decimal(365) / Decimal(days)
    return (ratio**exponent - 1) * Decimal(100)


def drawdown_curve(snapshots: list[PortfolioSnapshot]) -> list[tuple[int, Decimal]]:
    ordered = sorted(snapshots, key=lambda s: s.timestamp)
    curve: list[tuple[int, Decimal]] = []
    peak: Decimal | None = None
    for s in ordered:
        if peak is None or s.total_value > peak:
            peak = s.total_value
        dd = Decimal("0") if peak == 0 else (peak - s.total_value) / peak * Decimal(100)
        curve.append((s.timestamp, dd))
    return curve


def max_drawdown(snapshots: list[PortfolioSnapshot]) -> tuple[Decimal, int | None]:
    """Returns (max_drawdown_pct, recovery_days). recovery_days is None if the
    series never returns to the pre-drawdown peak."""
    curve = drawdown_curve(snapshots)
    if not curve:
        return Decimal("0"), None

    max_dd = max(dd for _, dd in curve)
    if max_dd == 0:
        return Decimal("0"), 0

    trough_index = next(i for i, (_, dd) in enumerate(curve) if dd == max_dd)
    trough_ts = curve[trough_index][0]

    peak_ts = trough_ts
    for i in range(trough_index, -1, -1):
        if curve[i][1] == 0:
            peak_ts = curve[i][0]
            break

    recovery_days = None
    for ts, dd in curve[trough_index:]:
        if dd == 0:
            recovery_days = (ts - peak_ts) // DAY_MS
            break

    return max_dd, recovery_days


def profit_factor(trades: list[Trade]) -> Decimal:
    sells = [t for t in trades if t.side == Side.SELL and t.realized_pnl is not None]
    gains = sum((t.realized_pnl for t in sells if t.realized_pnl > 0), Decimal("0"))
    losses = sum((-t.realized_pnl for t in sells if t.realized_pnl < 0), Decimal("0"))
    if losses == 0:
        return Decimal("Infinity") if gains > 0 else Decimal("0")
    return gains / losses


def expectancy(trades: list[Trade]) -> Decimal:
    sells = [t for t in trades if t.side == Side.SELL and t.realized_pnl is not None]
    if not sells:
        return Decimal("0")
    wins = [t.realized_pnl for t in sells if t.realized_pnl > 0]
    losses = [-t.realized_pnl for t in sells if t.realized_pnl < 0]
    total = Decimal(len(sells))
    win_rate = Decimal(len(wins)) / total
    loss_rate = Decimal(len(losses)) / total
    avg_win = sum(wins, Decimal("0")) / Decimal(len(wins)) if wins else Decimal("0")
    avg_loss = sum(losses, Decimal("0")) / Decimal(len(losses)) if losses else Decimal("0")
    return win_rate * avg_win - loss_rate * avg_loss


def exposure_time_pct(snapshots: list[PortfolioSnapshot]) -> Decimal:
    if not snapshots:
        return Decimal("0")
    exposed = sum(1 for s in snapshots if s.position_value > 0)
    return Decimal(exposed) / Decimal(len(snapshots)) * Decimal(100)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_analytics.py -v`
Expected: PASS (10 passed)

- [ ] **Step 6: Commit**

```bash
git add models.py analytics.py tests/test_analytics.py
git commit -m "feat: add PortfolioSnapshot model and analytics part 1 (return, drawdown, profit factor, expectancy, exposure)"
```

---

## Task 9: `analytics.py` part 2 (Sharpe, Sortino, Calmar, alpha, distribution, monthly returns)

**Files:**
- Modify: `analytics.py`
- Modify: `tests/test_analytics.py`

**Interfaces:**
- Consumes: everything from Task 8 (`PortfolioSnapshot`, `Trade`, `Side`, `drawdown_curve`, `max_drawdown`, `cagr_pct`)
- Produces: `sharpe_ratio(snapshots, periods_per_year) -> Decimal`, `sortino_ratio(snapshots, periods_per_year) -> Decimal`, `calmar_ratio(cagr_value, max_dd_pct) -> Decimal`, `alpha_vs_buy_hold(strategy_return_pct, buy_hold_return_pct) -> Decimal`, `trade_distribution(trades, bucket_count=10) -> list[dict]`, `monthly_returns(snapshots) -> dict[str, Decimal]`

`periods_per_year` is caller-supplied (not defaulted) because it depends on snapshot frequency: 365 for daily, ~8760 for hourly backtest bars, ~105120 for 5-minute live polling. Getting this wrong silently mis-annualizes risk metrics, so it's a required parameter, not a guess.

- [ ] **Step 1: Append the failing tests**

```python
# tests/test_analytics.py (append)

def test_sharpe_ratio_matches_hand_computed_value():
    result = analytics.sharpe_ratio(_snapshots(), periods_per_year=365)
    assert result.quantize(Decimal("0.0001")) == Decimal("7.1792")


def test_sharpe_ratio_is_zero_when_stdev_is_zero():
    flat = [
        PortfolioSnapshot(
            timestamp=T0 + i * DAY_MS, symbol="BTCUSDT",
            cash_balance=Decimal("1000"), position_value=Decimal("0"),
            total_value=Decimal("1000"), unrealized_pnl=Decimal("0"),
            realized_pnl_cumule=Decimal("0"),
        )
        for i in range(3)
    ]
    assert analytics.sharpe_ratio(flat, periods_per_year=365) == Decimal("0")


def test_sortino_ratio_matches_hand_computed_value():
    result = analytics.sortino_ratio(_snapshots(), periods_per_year=365)
    assert result.quantize(Decimal("0.0001")) == Decimal("8.8947")


def test_calmar_ratio():
    cagr_value = Decimal("851507.3801")
    max_dd = Decimal("18.1818")
    # cagr_value / max_dd using these already-rounded inputs (not the full-precision
    # intermediates) — 851507.3801 / 18.1818
    assert analytics.calmar_ratio(cagr_value, max_dd).quantize(Decimal("0.0001")) == Decimal("46832.9527")


def test_calmar_ratio_zero_drawdown_returns_zero():
    assert analytics.calmar_ratio(Decimal("10"), Decimal("0")) == Decimal("0")


def test_alpha_vs_buy_hold():
    assert analytics.alpha_vs_buy_hold(Decimal("25.00"), Decimal("18.50")) == Decimal("6.50")


def test_trade_distribution_buckets_pnls():
    trades = [_trade(Decimal("50")), _trade(Decimal("-20")), _trade(Decimal("80")),
              _trade(Decimal("-30")), _trade(Decimal("10"))]

    buckets = analytics.trade_distribution(trades, bucket_count=5)

    assert len(buckets) == 5
    assert [b["count"] for b in buckets] == [2, 1, 0, 1, 1]
    assert buckets[0]["range_low"] == Decimal("-30")
    assert buckets[4]["range_high"] == Decimal("80")
    assert sum(b["count"] for b in buckets) == 5


def test_trade_distribution_empty_when_no_sells():
    assert analytics.trade_distribution([_trade(None, side=Side.BUY)]) == []


def test_monthly_returns_buckets_by_calendar_month():
    def ts(y, m, d):
        return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp() * 1000)

    snaps = [
        PortfolioSnapshot(timestamp=ts(2026, 1, 5), symbol="BTCUSDT", cash_balance=Decimal("0"),
                           position_value=Decimal("1000"), total_value=Decimal("1000"),
                           unrealized_pnl=Decimal("0"), realized_pnl_cumule=Decimal("0")),
        PortfolioSnapshot(timestamp=ts(2026, 1, 25), symbol="BTCUSDT", cash_balance=Decimal("0"),
                           position_value=Decimal("1100"), total_value=Decimal("1100"),
                           unrealized_pnl=Decimal("0"), realized_pnl_cumule=Decimal("0")),
        PortfolioSnapshot(timestamp=ts(2026, 2, 5), symbol="BTCUSDT", cash_balance=Decimal("0"),
                           position_value=Decimal("1100"), total_value=Decimal("1100"),
                           unrealized_pnl=Decimal("0"), realized_pnl_cumule=Decimal("0")),
        PortfolioSnapshot(timestamp=ts(2026, 2, 25), symbol="BTCUSDT", cash_balance=Decimal("0"),
                           position_value=Decimal("1210"), total_value=Decimal("1210"),
                           unrealized_pnl=Decimal("0"), realized_pnl_cumule=Decimal("0")),
    ]

    result = analytics.monthly_returns(snaps)

    assert result == {"2026-01": Decimal("10"), "2026-02": Decimal("10")}


def test_monthly_returns_empty_for_no_snapshots():
    assert analytics.monthly_returns([]) == {}
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `pytest tests/test_analytics.py -v`
Expected: FAIL with `AttributeError: module 'analytics' has no attribute 'sharpe_ratio'` (and similarly for the other new functions); the 11 Task 8 tests still pass.

- [ ] **Step 3: Update `analytics.py` (part 2)**

First, add this import to the existing import block at the top of `analytics.py` (alongside the `decimal` import):

```python
from datetime import datetime, timezone
```

Then append the following to the end of the file:

```python
def _periodic_returns(snapshots: list[PortfolioSnapshot]) -> list[Decimal]:
    ordered = sorted(snapshots, key=lambda s: s.timestamp)
    return [
        (ordered[i].total_value - ordered[i - 1].total_value) / ordered[i - 1].total_value
        for i in range(1, len(ordered))
    ]


def _stdev(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    mean = sum(values, Decimal("0")) / Decimal(len(values))
    variance = sum(((v - mean) ** 2 for v in values), Decimal("0")) / Decimal(len(values))
    return variance.sqrt()


def sharpe_ratio(snapshots: list[PortfolioSnapshot], periods_per_year: int) -> Decimal:
    returns = _periodic_returns(snapshots)
    if len(returns) < 2:
        return Decimal("0")
    mean_r = sum(returns, Decimal("0")) / Decimal(len(returns))
    stdev = _stdev(returns)
    if stdev == 0:
        return Decimal("0")
    return (mean_r / stdev) * Decimal(periods_per_year).sqrt()


def sortino_ratio(snapshots: list[PortfolioSnapshot], periods_per_year: int) -> Decimal:
    returns = _periodic_returns(snapshots)
    if len(returns) < 2:
        return Decimal("0")
    mean_r = sum(returns, Decimal("0")) / Decimal(len(returns))
    downside = [min(r, Decimal("0")) for r in returns]
    downside_variance = sum((d * d for d in downside), Decimal("0")) / Decimal(len(downside))
    downside_dev = downside_variance.sqrt()
    if downside_dev == 0:
        return Decimal("0")
    return (mean_r / downside_dev) * Decimal(periods_per_year).sqrt()


def calmar_ratio(cagr_value: Decimal, max_dd_pct: Decimal) -> Decimal:
    if max_dd_pct == 0:
        return Decimal("0")
    return cagr_value / max_dd_pct


def alpha_vs_buy_hold(strategy_return_pct: Decimal, buy_hold_return_pct: Decimal) -> Decimal:
    return strategy_return_pct - buy_hold_return_pct


def trade_distribution(trades: list[Trade], bucket_count: int = 10) -> list[dict]:
    pnls = [t.realized_pnl for t in trades if t.side == Side.SELL and t.realized_pnl is not None]
    if not pnls:
        return []
    lo, hi = min(pnls), max(pnls)
    if lo == hi:
        return [{"range_low": lo, "range_high": hi, "count": len(pnls)}]
    width = (hi - lo) / Decimal(bucket_count)
    buckets = [
        {"range_low": lo + width * i, "range_high": lo + width * (i + 1), "count": 0}
        for i in range(bucket_count)
    ]
    for pnl in pnls:
        idx = int((pnl - lo) / width)
        if idx >= bucket_count:
            idx = bucket_count - 1
        buckets[idx]["count"] += 1
    return buckets


def monthly_returns(snapshots: list[PortfolioSnapshot]) -> dict[str, Decimal]:
    ordered = sorted(snapshots, key=lambda s: s.timestamp)
    if not ordered:
        return {}

    def month_key(ts_ms: int) -> str:
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        return f"{dt.year:04d}-{dt.month:02d}"

    months: dict[str, list[PortfolioSnapshot]] = {}
    for s in ordered:
        months.setdefault(month_key(s.timestamp), []).append(s)

    result: dict[str, Decimal] = {}
    prev_last_value: Decimal | None = None
    for key in sorted(months.keys()):
        month_snaps = months[key]
        start_value = prev_last_value if prev_last_value is not None else month_snaps[0].total_value
        end_value = month_snaps[-1].total_value
        result[key] = Decimal("0") if start_value == 0 else (end_value - start_value) / start_value * Decimal(100)
        prev_last_value = end_value
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_analytics.py -v`
Expected: PASS (20 passed)

- [ ] **Step 5: Commit**

```bash
git add analytics.py tests/test_analytics.py
git commit -m "feat: add analytics part 2 (Sharpe, Sortino, Calmar, alpha, distribution, monthly returns)"
```

---

## Task 10: Full suite verification + README stub

**Files:**
- Create: `README.md`
- Test: (runs the whole suite built so far)

**Interfaces:**
- Consumes: everything built in Tasks 1–9
- Produces: nothing new — this task is a verification + documentation checkpoint before Plan 2 (backtest) begins.

- [ ] **Step 1: Write `README.md`**

```markdown
# crypto-sim

Simulateur de trading crypto **en papier uniquement** — aucune clé API de trading, aucun ordre
réel, aucun argent réel, à aucun moment. Voir `docs/superpowers/specs/2026-09-15-crypto-paper-trading-sim-design.md`
pour la spec complète.

## État actuel

Plan 1/5 (fondation) : `MarketDataProvider` (Binance + repli Kraken), moteur FIFO, `analytics.py`.
Pas encore de base de données, de backtest, de moteur live, de dashboard ni de déploiement —
voir les plans suivants dans `docs/superpowers/plans/`.

## Développement

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
pytest -v
```
```

- [ ] **Step 2: Run the full test suite**

Run: `pytest -v`
Expected: all tests from Tasks 1–9 pass (config, market data types, Binance, Kraken, pagination, FIFO BUY+SELL, analytics parts 1+2) — 0 failures.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: add README stub for the foundation layer"
```
