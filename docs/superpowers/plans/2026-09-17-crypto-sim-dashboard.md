# Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only FastAPI dashboard (3 pages: main, Analyses, Transactions) that lets Florian watch the live paper-trading engine's performance on his LAN, with a permanent simulation warning banner and no application-level auth.

**Architecture:** `dashboard/app.py` is a FastAPI app serving server-rendered Jinja2 templates. It never writes to the database — it only reads, via new list-query functions added to `db/repository.py` (the project's existing single point of raw SQL for trades/lots/snapshots/engine_state) plus the existing `analytics.py` module and the CSV files `backtest.py` already writes (`backtest_report.csv`, `analytics_report.csv`). Charts are rendered client-side with Chart.js (loaded from a CDN `<script>` tag, no build step, no new heavy Python dependency) fed by data embedded directly in each page's initial render — no separate JSON API endpoints needed for this dashboard's scale.

**Tech Stack:** FastAPI + Jinja2Templates (new dependencies: `fastapi`, `uvicorn[standard]`, `jinja2`), Chart.js via CDN, the project's existing SQLite DB (read-only from this plan's perspective) and CSV report files.

**Spec:** `docs/superpowers/specs/2026-09-15-crypto-paper-trading-sim-design.md` (section 12 "Dashboard FastAPI", section 6 model, section 2 non-negotiables)

## Global Constraints

- All price/quantity/PnL values stay `Decimal` in Python; only convert to `str`/formatted text at the template-rendering boundary, never `float`.
- No application-level authentication — LAN access only, matches the spec's explicit "pas d'auth applicative" and "si exposé au-delà du LAN un jour, auth basique au niveau du reverse proxy, pas dans l'app."
- The dashboard is read-only: it must never call `insert_trade`/`insert_lot`/`insert_snapshot`/`set_engine_state`/`replace_lots_for_symbol` or otherwise mutate the database.
- A permanent banner reading exactly `⚠ SIMULATION — Aucun argent réel, aucun ordre réel envoyé` must appear on every page.
- Thresholds/ports live in `config.yaml`, never hardcoded in code.

---

### Task 1: DB read-query helpers

**Files:**
- Modify: `db/repository.py`
- Test: `tests/test_repository.py`

**Interfaces:**
- Consumes: `sqlite3.Connection` (Task 1 of Plan 3), `Trade`/`Side` (Plan 1), `PortfolioSnapshot` (Plan 1).
- Produces: `list_trades(conn: sqlite3.Connection, symbol: str | None = None) -> list[Trade]` (newest first), `list_snapshots(conn: sqlite3.Connection, symbol: str) -> list[PortfolioSnapshot]` (oldest first, for charting), `list_symbols_with_trades(conn: sqlite3.Connection) -> list[str]` (alphabetical, for the symbol selector).

- [ ] **Step 1: Write the failing tests**

```python
# Append to tests/test_repository.py
from engine.fifo_engine import Side
from db.repository import list_symbols_with_trades, list_snapshots, list_trades


def test_list_trades_returns_newest_first_with_correct_types(conn):
    t1 = Trade(
        id=1, timestamp=0, symbol="BTCUSDT", side=Side.BUY, price=Decimal("100"),
        quantity=Decimal("1"), fee_pct=Decimal("0.001"), fee_amount=Decimal("0.1"),
        total_cost=Decimal("100.1"), realized_pnl=None, cash_balance_after=Decimal("899.9"),
        strategy_name="dca",
    )
    t2 = Trade(
        id=2, timestamp=300_000, symbol="BTCUSDT", side=Side.SELL, price=Decimal("110"),
        quantity=Decimal("1"), fee_pct=Decimal("0.001"), fee_amount=Decimal("0.11"),
        total_cost=Decimal("109.89"), realized_pnl=Decimal("9.79"), cash_balance_after=Decimal("1009.79"),
        strategy_name="dca",
    )
    insert_trade(conn, t1)
    insert_trade(conn, t2)

    trades = list_trades(conn, symbol="BTCUSDT")

    assert [t.id for t in trades] == [2, 1]
    assert trades[0].side == Side.SELL
    assert trades[0].realized_pnl == Decimal("9.79")
    assert trades[1].realized_pnl is None
    assert isinstance(trades[0].price, Decimal)


def test_list_trades_without_symbol_returns_all(conn):
    insert_trade(conn, Trade(
        id=1, timestamp=0, symbol="BTCUSDT", side=Side.BUY, price=Decimal("100"),
        quantity=Decimal("1"), fee_pct=Decimal("0.001"), fee_amount=Decimal("0.1"),
        total_cost=Decimal("100.1"), realized_pnl=None, cash_balance_after=Decimal("899.9"),
        strategy_name="dca",
    ))
    insert_trade(conn, Trade(
        id=2, timestamp=0, symbol="ETHUSDT", side=Side.BUY, price=Decimal("50"),
        quantity=Decimal("2"), fee_pct=Decimal("0.001"), fee_amount=Decimal("0.1"),
        total_cost=Decimal("100.1"), realized_pnl=None, cash_balance_after=Decimal("799.8"),
        strategy_name="dca",
    ))

    trades = list_trades(conn)

    assert len(trades) == 2


def test_list_snapshots_returns_oldest_first(conn):
    insert_snapshot(conn, PortfolioSnapshot(
        timestamp=300_000, symbol="BTCUSDT", cash_balance=Decimal("500"), position_value=Decimal("510"),
        total_value=Decimal("1010"), unrealized_pnl=Decimal("10"), realized_pnl_cumule=Decimal("0"),
    ))
    insert_snapshot(conn, PortfolioSnapshot(
        timestamp=0, symbol="BTCUSDT", cash_balance=Decimal("500"), position_value=Decimal("500"),
        total_value=Decimal("1000"), unrealized_pnl=Decimal("0"), realized_pnl_cumule=Decimal("0"),
    ))

    snapshots = list_snapshots(conn, "BTCUSDT")

    assert [s.timestamp for s in snapshots] == [0, 300_000]
    assert isinstance(snapshots[0].total_value, Decimal)


def test_list_symbols_with_trades_is_alphabetical_and_distinct(conn):
    for symbol in ("ETHUSDT", "BTCUSDT", "BTCUSDT"):
        insert_trade(conn, Trade(
            id=0, timestamp=0, symbol=symbol, side=Side.BUY, price=Decimal("1"),
            quantity=Decimal("1"), fee_pct=Decimal("0.001"), fee_amount=Decimal("0"),
            total_cost=Decimal("1"), realized_pnl=None, cash_balance_after=Decimal("999"),
            strategy_name="dca",
        ))

    symbols = list_symbols_with_trades(conn)

    assert symbols == ["BTCUSDT", "ETHUSDT"]
```

Note: `insert_trade`'s `Trade.id` field is ignored by the DB (it's `AUTOINCREMENT`); passing any placeholder int for `id` in a `Trade` you're about to insert is fine and matches how existing tests in this file already construct `Trade` objects for `insert_trade` — check the existing tests above your new ones in `tests/test_repository.py` for the established fixture style and reuse it rather than diverging.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_repository.py -v -k "list_trades or list_snapshots or list_symbols"`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Add the three functions to `db/repository.py`**

Add `Side` to the existing `from engine.fifo_engine import Lot, Trade` import line (making it `from engine.fifo_engine import Lot, Side, Trade`). Append:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_repository.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add db/repository.py tests/test_repository.py
git commit -m "feat: add list_trades/list_snapshots/list_symbols_with_trades read queries"
```

---

### Task 2: FastAPI app skeleton, base template, simulation banner, config

**Files:**
- Create: `dashboard/__init__.py`
- Create: `dashboard/app.py`
- Create: `dashboard/templates/base.html`
- Modify: `config.py`, `config.yaml`, `requirements.txt`
- Test: `tests/test_dashboard_app.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: `load_config` (Plan 1), `init_db` (Plan 3).
- Produces: FastAPI `app` object at `dashboard.app.app`, a `get_conn()` FastAPI dependency, `cfg.dashboard.port: int`.

- [ ] **Step 1: Add `fastapi`, `uvicorn[standard]`, `jinja2` to `requirements.txt`**

```
httpx>=0.27
PyYAML>=6.0
pytest>=8.0
python-dotenv>=1.0
tzdata>=2024.1; sys_platform == "win32"
fastapi>=0.115
uvicorn[standard]>=0.32
jinja2>=3.1
```

Run `pip install -r requirements.txt` to make these importable in this environment.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_dashboard_app.py
from fastapi.testclient import TestClient

from dashboard.app import app

client = TestClient(app)


def test_root_page_shows_simulation_banner():
    response = client.get("/")
    assert response.status_code == 200
    assert "⚠ SIMULATION — Aucun argent réel, aucun ordre réel envoyé" in response.text
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_dashboard_app.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dashboard'`

- [ ] **Step 4: Add `dashboard.port` to `config.py`/`config.yaml`**

In `config.py`, add:

```python
@dataclass(frozen=True)
class DashboardConfig:
    port: int
```

Add `dashboard: DashboardConfig` to the `Config` dataclass's fields, and in `load_config()`'s `Config(...)` construction add:

```python
        dashboard=DashboardConfig(port=int(raw["dashboard"]["port"])),
```

In `config.yaml`, add a new top-level section (spec section 8 names port 8303):

```yaml
dashboard:
  port: 8303
```

Update `tests/test_config.py`'s single consolidated sample YAML string to include this block, and add one assertion at the end of the existing assertion list: `assert cfg.dashboard.port == 8303`.

- [ ] **Step 5: Create `dashboard/__init__.py`**

```python
# dashboard/__init__.py
```

- [ ] **Step 6: Create `dashboard/templates/base.html`**

```html
<!doctype html>
<html lang="fr">
<head>
    <meta charset="utf-8">
    <title>{% block title %}crypto-sim{% endblock %}</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
    <style>
        body { font-family: sans-serif; margin: 0; padding: 0 1.5rem 2rem; background: #0f1115; color: #e6e6e6; }
        .banner { background: #b91c1c; color: white; text-align: center; padding: 0.5rem; font-weight: bold; margin: 0 -1.5rem 1rem; }
        nav a { color: #93c5fd; margin-right: 1.5rem; text-decoration: none; font-weight: bold; }
        nav { padding: 0.5rem 0 1rem; border-bottom: 1px solid #333; margin-bottom: 1.5rem; }
        table { border-collapse: collapse; width: 100%; margin-bottom: 1.5rem; }
        th, td { border: 1px solid #333; padding: 0.4rem 0.6rem; text-align: right; }
        th:first-child, td:first-child { text-align: left; }
        th { background: #1a1d24; }
        .stat-tiles { display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 1.5rem; }
        .stat-tile { background: #1a1d24; border: 1px solid #333; border-radius: 6px; padding: 1rem 1.5rem; min-width: 160px; }
        .stat-tile .label { font-size: 0.8rem; color: #999; }
        .stat-tile .value { font-size: 1.4rem; font-weight: bold; }
        .gain { color: #4ade80; }
        .loss { color: #f87171; }
    </style>
</head>
<body>
    <div class="banner">⚠ SIMULATION — Aucun argent réel, aucun ordre réel envoyé</div>
    <nav>
        <a href="/">Principal</a>
        <a href="/analyses">Analyses</a>
        <a href="/transactions">Transactions</a>
    </nav>
    {% block content %}{% endblock %}
</body>
</html>
```

- [ ] **Step 7: Create `dashboard/app.py`**

```python
# dashboard/app.py
import sqlite3
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from config import load_config
from db.migrate import init_db

app = FastAPI()
# Absolute path, not the relative string "dashboard/templates": a relative
# searchpath is resolved against the process's CURRENT working directory at
# template-load time (not at this line's execution time), which breaks the
# moment anything changes cwd -- a test using monkeypatch.chdir, or Plan 6's
# systemd unit running this from an unrelated WorkingDirectory=.
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def get_conn() -> sqlite3.Connection:
    """Read-only usage from every dashboard route -- never insert/update/delete.
    A fresh connection per request is simplest and cheap for a low-traffic
    LAN dashboard; WAL mode (already set by init_db) supports concurrent
    readers alongside the live engine's own writer connection."""
    cfg = load_config()
    return init_db(cfg.db_path)


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "base.html", {})
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest tests/test_dashboard_app.py tests/test_config.py -v` then the full suite `pytest -v`
Expected: PASS, 0 failures

- [ ] **Step 9: Commit**

```bash
git add dashboard/ config.py config.yaml requirements.txt tests/test_dashboard_app.py tests/test_config.py
git commit -m "feat: add FastAPI dashboard skeleton with permanent simulation banner"
```

---

### Task 3: Main page — portfolio summary tiles + symbol selector + last backtest table

**Files:**
- Modify: `dashboard/app.py`
- Create: `dashboard/templates/index.html`
- Test: `tests/test_dashboard_app.py`

**Interfaces:**
- Consumes: `list_symbols_with_trades`, `list_snapshots` (Task 1), `cfg.backtest.initial_capital`/`cfg.live.active_symbol` (Plan 1/3).
- Produces: `GET /?symbol=<SYMBOL>` renders portfolio summary tiles + a CSV-backed backtest comparison table for the selected symbol.

`backtest.py` writes `backtest_report.csv` at the repo root with columns `symbol,strategy,return_pct,nb_trades,win_rate_pct,avg_win,avg_loss,biggest_win,biggest_loss,max_drawdown_pct,total_fees,alpha_vs_buy_hold_pct` (one row per symbol/strategy combination). The file may not exist yet if `backtest.py` was never run — handle that gracefully (empty table, not a crash).

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_dashboard_app.py
import csv
from decimal import Decimal
from pathlib import Path

import pytest

from db.repository import insert_snapshot
from models import PortfolioSnapshot


@pytest.fixture
def sample_backtest_csv(tmp_path, monkeypatch):
    csv_path = tmp_path / "backtest_report.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "symbol", "strategy", "return_pct", "nb_trades", "win_rate_pct", "avg_win", "avg_loss",
            "biggest_win", "biggest_loss", "max_drawdown_pct", "total_fees", "alpha_vs_buy_hold_pct",
        ])
        writer.writeheader()
        writer.writerow({
            "symbol": "BTCUSDT", "strategy": "dca", "return_pct": "12.5", "nb_trades": "10",
            "win_rate_pct": "60", "avg_win": "5", "avg_loss": "2", "biggest_win": "20",
            "biggest_loss": "-8", "max_drawdown_pct": "15", "total_fees": "1.5", "alpha_vs_buy_hold_pct": "3.2",
        })
    monkeypatch.chdir(tmp_path)
    return csv_path


def test_index_shows_portfolio_summary_and_backtest_table(monkeypatch, sample_backtest_csv):
    monkeypatch.setattr("dashboard.app.get_conn", lambda: _seeded_conn())
    monkeypatch.setattr("dashboard.app._active_symbol_default", lambda: "BTCUSDT")
    monkeypatch.setattr("dashboard.app._initial_capital", lambda: Decimal("1000"))

    response = client.get("/?symbol=BTCUSDT")

    assert response.status_code == 200
    assert "1050" in response.text  # latest total_value
    assert "dca" in response.text  # from the backtest CSV table
    assert "12.5" in response.text


def _seeded_conn():
    from db.migrate import init_db
    conn = init_db(":memory:")
    insert_snapshot(conn, PortfolioSnapshot(
        timestamp=0, symbol="BTCUSDT", cash_balance=Decimal("500"), position_value=Decimal("500"),
        total_value=Decimal("1000"), unrealized_pnl=Decimal("0"), realized_pnl_cumule=Decimal("0"),
    ))
    insert_snapshot(conn, PortfolioSnapshot(
        timestamp=300_000, symbol="BTCUSDT", cash_balance=Decimal("500"), position_value=Decimal("550"),
        total_value=Decimal("1050"), unrealized_pnl=Decimal("50"), realized_pnl_cumule=Decimal("0"),
    ))
    return conn


def test_index_handles_missing_backtest_csv_gracefully(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no backtest_report.csv here
    monkeypatch.setattr("dashboard.app.get_conn", lambda: _seeded_conn())
    monkeypatch.setattr("dashboard.app._active_symbol_default", lambda: "BTCUSDT")
    monkeypatch.setattr("dashboard.app._initial_capital", lambda: Decimal("1000"))

    response = client.get("/?symbol=BTCUSDT")

    assert response.status_code == 200
    assert "1050" in response.text


def test_index_with_no_snapshots_yet_does_not_crash(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from db.migrate import init_db
    monkeypatch.setattr("dashboard.app.get_conn", lambda: init_db(":memory:"))
    monkeypatch.setattr("dashboard.app._active_symbol_default", lambda: "BTCUSDT")
    monkeypatch.setattr("dashboard.app._initial_capital", lambda: Decimal("1000"))

    response = client.get("/?symbol=BTCUSDT")

    assert response.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dashboard_app.py -v -k index`
Expected: FAIL (route doesn't read query params/CSV yet, `_active_symbol_default`/`_initial_capital` don't exist)

- [ ] **Step 3: Implement the main page route**

Replace `dashboard/app.py`'s `index` function and add supporting helpers:

```python
# dashboard/app.py
import csv
import sqlite3
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from config import load_config
from db.migrate import init_db
from db.repository import list_snapshots, list_symbols_with_trades

app = FastAPI()
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def get_conn() -> sqlite3.Connection:
    """Read-only usage from every dashboard route -- never insert/update/delete.
    A fresh connection per request is simplest and cheap for a low-traffic
    LAN dashboard; WAL mode (already set by init_db) supports concurrent
    readers alongside the live engine's own writer connection."""
    cfg = load_config()
    return init_db(cfg.db_path)


def _active_symbol_default() -> str:
    return load_config().live.active_symbol


def _initial_capital() -> Decimal:
    return load_config().backtest.initial_capital


def _read_backtest_report() -> list[dict]:
    path = Path("backtest_report.csv")
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@app.get("/", response_class=HTMLResponse)
def index(request: Request, symbol: str | None = None) -> HTMLResponse:
    conn = get_conn()
    active_symbol = symbol or _active_symbol_default()
    symbols = list_symbols_with_trades(conn) or [active_symbol]
    snapshots = list_snapshots(conn, active_symbol)
    initial_capital = _initial_capital()

    if snapshots:
        latest = snapshots[-1]
        return_pct = ((latest.total_value - initial_capital) / initial_capital * Decimal(100)
                      if initial_capital > 0 else Decimal(0))
    else:
        latest = None
        return_pct = Decimal(0)

    backtest_rows = [row for row in _read_backtest_report() if row["symbol"] == active_symbol]

    return templates.TemplateResponse(request, "index.html", {
        "symbols": symbols,
        "active_symbol": active_symbol,
        "latest": latest,
        "return_pct": return_pct,
        "backtest_rows": backtest_rows,
    })
```

- [ ] **Step 4: Create `dashboard/templates/index.html`**

```html
{% extends "base.html" %}
{% block title %}crypto-sim -- Principal{% endblock %}
{% block content %}
<form method="get" style="margin-bottom: 1rem;">
    <label>Symbole :
        <select name="symbol" onchange="this.form.submit()">
            {% for s in symbols %}
            <option value="{{ s }}" {% if s == active_symbol %}selected{% endif %}>{{ s }}</option>
            {% endfor %}
        </select>
    </label>
</form>

{% if latest %}
<div class="stat-tiles">
    <div class="stat-tile"><div class="label">Valeur totale</div><div class="value">{{ latest.total_value }}</div></div>
    <div class="stat-tile"><div class="label">PnL realise cumule</div>
        <div class="value {% if latest.realized_pnl_cumule >= 0 %}gain{% else %}loss{% endif %}">{{ latest.realized_pnl_cumule }}</div></div>
    <div class="stat-tile"><div class="label">PnL latent</div>
        <div class="value {% if latest.unrealized_pnl >= 0 %}gain{% else %}loss{% endif %}">{{ latest.unrealized_pnl }}</div></div>
    <div class="stat-tile"><div class="label">Rendement depuis le debut</div>
        <div class="value {% if return_pct >= 0 %}gain{% else %}loss{% endif %}">{{ return_pct }}%</div></div>
</div>
{% else %}
<p>Aucune donnee de portefeuille pour {{ active_symbol }} pour l'instant.</p>
{% endif %}

<h2>Dernier backtest comparatif -- {{ active_symbol }}</h2>
{% if backtest_rows %}
<table>
    <tr><th>Strategie</th><th>Rendement %</th><th>Trades</th><th>Taux de reussite %</th><th>Drawdown max %</th><th>Alpha vs Buy&amp;Hold %</th></tr>
    {% for row in backtest_rows %}
    <tr>
        <td>{{ row.strategy }}</td><td>{{ row.return_pct }}</td><td>{{ row.nb_trades }}</td>
        <td>{{ row.win_rate_pct }}</td><td>{{ row.max_drawdown_pct }}</td><td>{{ row.alpha_vs_buy_hold_pct }}</td>
    </tr>
    {% endfor %}
</table>
{% else %}
<p>Aucun resultat de backtest disponible (lancer <code>python backtest.py</code>).</p>
{% endif %}
{% endblock %}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_dashboard_app.py -v` then the full suite `pytest -v`
Expected: PASS, 0 failures

- [ ] **Step 6: Commit**

```bash
git add dashboard/ tests/test_dashboard_app.py
git commit -m "feat: main page portfolio summary tiles and last-backtest comparison table"
```

---

### Task 4: Main page — portfolio value + price chart

**Files:**
- Modify: `dashboard/app.py`, `dashboard/templates/index.html`
- Test: `tests/test_dashboard_app.py`

**Interfaces:**
- Consumes: `list_snapshots` (Task 1).
- Produces: a Chart.js line chart on `/` showing `total_value` over time on the primary axis. (`PortfolioSnapshot` has no market-price column, so a genuine secondary price axis isn't derivable from the data this project persists -- see the note below; this task charts what's actually available and documents the gap rather than fabricating a price series.)

**Design note carried into this task deliberately:** the spec's main-page chart calls for "valeur portefeuille + prix (axe secondaire)" (portfolio value plus price, secondary axis). `portfolio_snapshots` (schema from Plan 3) stores `cash_balance`/`position_value`/`total_value`/`unrealized_pnl`/`realized_pnl_cumule` -- it does not store the market price observed at that timestamp. Deriving price would require either adding a `price` column to that table (a Plan 3 schema change, out of scope for a dashboard-only plan) or joining against kline history the live engine never persists. Ship the portfolio-value line now; note the price-axis gap explicitly in this task rather than silently dropping it or inventing a fake price series.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_dashboard_app.py
def test_index_embeds_chart_data_as_json(monkeypatch, sample_backtest_csv):
    monkeypatch.setattr("dashboard.app.get_conn", lambda: _seeded_conn())
    monkeypatch.setattr("dashboard.app._active_symbol_default", lambda: "BTCUSDT")
    monkeypatch.setattr("dashboard.app._initial_capital", lambda: Decimal("1000"))

    response = client.get("/?symbol=BTCUSDT")

    assert response.status_code == 200
    assert "new Chart(" in response.text
    assert '"1000"' in response.text  # first snapshot's total_value, as a JSON string
    assert '"1050"' in response.text  # second snapshot's total_value
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dashboard_app.py -v -k chart`
Expected: FAIL (no chart data/script yet)

- [ ] **Step 3: Pass chart data to the template**

Modify `index()` in `dashboard/app.py` to also compute and pass chart series (add `import json` to the top of the file):

```python
    chart_labels = [s.timestamp for s in snapshots]
    chart_values = [str(s.total_value) for s in snapshots]

    return templates.TemplateResponse(request, "index.html", {
        "symbols": symbols,
        "active_symbol": active_symbol,
        "latest": latest,
        "return_pct": return_pct,
        "backtest_rows": backtest_rows,
        "chart_labels_json": json.dumps(chart_labels),
        "chart_values_json": json.dumps(chart_values),
    })
```

- [ ] **Step 4: Add the chart to `dashboard/templates/index.html`**

Append inside the `{% block content %}` block, right after the stat tiles/before the backtest table section:

```html
<canvas id="portfolio-chart" height="80"></canvas>
<script>
new Chart(document.getElementById("portfolio-chart"), {
    type: "line",
    data: {
        labels: {{ chart_labels_json | safe }},
        datasets: [{
            label: "Valeur totale (Note: pas d'axe prix -- portfolio_snapshots ne stocke pas le prix marche)",
            data: {{ chart_values_json | safe }}.map(Number),
            borderColor: "#93c5fd",
            tension: 0.1,
        }],
    },
    options: { scales: { x: { display: false } } },
});
</script>
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_dashboard_app.py -v` then the full suite `pytest -v`
Expected: PASS, 0 failures

- [ ] **Step 6: Commit**

```bash
git add dashboard/
git commit -m "feat: main page portfolio value chart (price secondary axis deferred, see note)"
```

---

### Task 5: Analyses page — live strategy metrics table

**Files:**
- Modify: `dashboard/app.py`
- Create: `dashboard/templates/analyses.html`
- Test: `tests/test_dashboard_app.py`

**Interfaces:**
- Consumes: `analytics.py` (Plan 1: `sharpe_ratio`, `sortino_ratio`, `calmar_ratio`, `cagr_pct`, `max_drawdown`, `profit_factor`, `expectancy`, `exposure_time_pct`), `list_trades`/`list_snapshots` (Task 1).
- Produces: `GET /analyses?symbol=<SYMBOL>` renders Sharpe/Sortino/Calmar/profit factor/expectancy/exposure-time for the live strategy on that symbol, computed directly from live DB data (not from `analytics_report.csv`, which only covers backtests).

`config.yaml`'s `live.poll_kline_interval` (e.g. `"5m"`) determines `periods_per_year` for the Sharpe/Sortino annualization, same formula already used in `backtest.py`: `(365 * 24 * 3_600_000) // INTERVAL_MS[interval]`.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_dashboard_app.py
def _seeded_conn_with_trades():
    from db.migrate import init_db
    from db.repository import insert_trade
    from engine.fifo_engine import Side, Trade
    conn = init_db(":memory:")
    insert_snapshot(conn, PortfolioSnapshot(
        timestamp=0, symbol="BTCUSDT", cash_balance=Decimal("500"), position_value=Decimal("500"),
        total_value=Decimal("1000"), unrealized_pnl=Decimal("0"), realized_pnl_cumule=Decimal("0"),
    ))
    insert_snapshot(conn, PortfolioSnapshot(
        timestamp=300_000, symbol="BTCUSDT", cash_balance=Decimal("500"), position_value=Decimal("550"),
        total_value=Decimal("1050"), unrealized_pnl=Decimal("50"), realized_pnl_cumule=Decimal("0"),
    ))
    insert_trade(conn, Trade(
        id=1, timestamp=0, symbol="BTCUSDT", side=Side.BUY, price=Decimal("100"), quantity=Decimal("5"),
        fee_pct=Decimal("0.001"), fee_amount=Decimal("0.5"), total_cost=Decimal("500.5"),
        realized_pnl=None, cash_balance_after=Decimal("500"), strategy_name="dca",
    ))
    return conn


def test_analyses_page_shows_metrics_table(monkeypatch):
    monkeypatch.setattr("dashboard.app.get_conn", lambda: _seeded_conn_with_trades())
    monkeypatch.setattr("dashboard.app._active_symbol_default", lambda: "BTCUSDT")
    monkeypatch.setattr("dashboard.app._poll_interval_default", lambda: "5m")

    response = client.get("/analyses?symbol=BTCUSDT")

    assert response.status_code == 200
    assert "Sharpe" in response.text
    assert "Sortino" in response.text
    assert "Calmar" in response.text
    assert "Profit factor" in response.text
    assert "Expectancy" in response.text


def test_analyses_page_with_no_data_does_not_crash(tmp_path, monkeypatch):
    from db.migrate import init_db
    monkeypatch.setattr("dashboard.app.get_conn", lambda: init_db(":memory:"))
    monkeypatch.setattr("dashboard.app._active_symbol_default", lambda: "BTCUSDT")
    monkeypatch.setattr("dashboard.app._poll_interval_default", lambda: "5m")

    response = client.get("/analyses?symbol=BTCUSDT")

    assert response.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dashboard_app.py -v -k analyses`
Expected: FAIL with 404 (route doesn't exist yet)

- [ ] **Step 3: Implement the Analyses route**

Add to `dashboard/app.py` (add `import analytics` and `from db.repository import list_trades` alongside the existing `db.repository` import, and `from market_data.provider import INTERVAL_MS`):

```python
def _poll_interval_default() -> str:
    return load_config().live.poll_kline_interval


@app.get("/analyses", response_class=HTMLResponse)
def analyses(request: Request, symbol: str | None = None) -> HTMLResponse:
    conn = get_conn()
    active_symbol = symbol or _active_symbol_default()
    symbols = list_symbols_with_trades(conn) or [active_symbol]
    snapshots = list_snapshots(conn, active_symbol)
    trades = list_trades(conn, active_symbol)

    if snapshots:
        periods_per_year = (365 * 24 * 3_600_000) // INTERVAL_MS[_poll_interval_default()]
        days = max(1, (snapshots[-1].timestamp - snapshots[0].timestamp) // 86_400_000)
        cagr = analytics.cagr_pct(_initial_capital(), snapshots[-1].total_value, days)
        max_dd, recovery_days = analytics.max_drawdown(snapshots)
        metrics = {
            "sharpe": analytics.sharpe_ratio(snapshots, periods_per_year),
            "sortino": analytics.sortino_ratio(snapshots, periods_per_year),
            "calmar": analytics.calmar_ratio(cagr, max_dd),
            "profit_factor": analytics.profit_factor(trades),
            "expectancy": analytics.expectancy(trades),
            "exposure_time_pct": analytics.exposure_time_pct(snapshots),
            "max_drawdown_pct": max_dd,
            "recovery_days": recovery_days,
        }
    else:
        metrics = None

    return templates.TemplateResponse(request, "analyses.html", {
        "symbols": symbols,
        "active_symbol": active_symbol,
        "metrics": metrics,
    })
```

- [ ] **Step 4: Create `dashboard/templates/analyses.html`**

```html
{% extends "base.html" %}
{% block title %}crypto-sim -- Analyses{% endblock %}
{% block content %}
<form method="get" style="margin-bottom: 1rem;">
    <label>Symbole :
        <select name="symbol" onchange="this.form.submit()">
            {% for s in symbols %}
            <option value="{{ s }}" {% if s == active_symbol %}selected{% endif %}>{{ s }}</option>
            {% endfor %}
        </select>
    </label>
</form>

{% if metrics %}
<table>
    <tr><th>Metrique</th><th>Valeur</th></tr>
    <tr><td>Sharpe</td><td>{{ metrics.sharpe }}</td></tr>
    <tr><td>Sortino</td><td>{{ metrics.sortino }}</td></tr>
    <tr><td>Calmar</td><td>{{ metrics.calmar }}</td></tr>
    <tr><td>Profit factor</td><td>{{ metrics.profit_factor }}</td></tr>
    <tr><td>Expectancy</td><td>{{ metrics.expectancy }}</td></tr>
    <tr><td>Exposure time %</td><td>{{ metrics.exposure_time_pct }}</td></tr>
    <tr><td>Drawdown max %</td><td>{{ metrics.max_drawdown_pct }}</td></tr>
    <tr><td>Recuperation (jours)</td><td>{{ metrics.recovery_days if metrics.recovery_days is not none else "N/A" }}</td></tr>
</table>
{% else %}
<p>Pas encore de donnees pour {{ active_symbol }}.</p>
{% endif %}
{% endblock %}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_dashboard_app.py -v` then the full suite `pytest -v`
Expected: PASS, 0 failures

- [ ] **Step 6: Commit**

```bash
git add dashboard/ tests/test_dashboard_app.py
git commit -m "feat: Analyses page live strategy metrics table"
```

---

### Task 6: Analyses page — drawdown curve, trade distribution histogram, monthly returns table

**Files:**
- Modify: `dashboard/app.py`, `dashboard/templates/analyses.html`
- Test: `tests/test_dashboard_app.py`

**Interfaces:**
- Consumes: `analytics.drawdown_curve`, `analytics.trade_distribution`, `analytics.monthly_returns` (Plan 1).

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_dashboard_app.py
def test_analyses_page_shows_drawdown_chart_distribution_and_monthly_table(monkeypatch):
    monkeypatch.setattr("dashboard.app.get_conn", lambda: _seeded_conn_with_trades())
    monkeypatch.setattr("dashboard.app._active_symbol_default", lambda: "BTCUSDT")
    monkeypatch.setattr("dashboard.app._poll_interval_default", lambda: "5m")

    response = client.get("/analyses?symbol=BTCUSDT")

    assert response.status_code == 200
    assert "drawdown-chart" in response.text
    assert "trade-distribution-chart" in response.text
    assert "Rendements mensuels" in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dashboard_app.py -v -k monthly`
Expected: FAIL (no drawdown/distribution/monthly sections yet)

- [ ] **Step 3: Extend the Analyses route**

Modify `analyses()` in `dashboard/app.py` to add three more computed series when `snapshots` is non-empty (inside the existing `if snapshots:` block, alongside `metrics`):

```python
        dd_curve = analytics.drawdown_curve(snapshots)
        distribution = analytics.trade_distribution(trades)
        monthly = analytics.monthly_returns(snapshots)

        dd_labels_json = json.dumps([ts for ts, _ in dd_curve])
        dd_values_json = json.dumps([str(pct) for _, pct in dd_curve])
        dist_labels_json = json.dumps([str(bucket["range_low"]) for bucket in distribution])
        dist_counts_json = json.dumps([bucket["count"] for bucket in distribution])
```

(Add `import json` to the top of `dashboard/app.py` if it isn't already there from Task 4.) Then add these four new keys to the `TemplateResponse`'s context dict, alongside `metrics`:

```python
        "monthly_returns": monthly if snapshots else {},
        "dd_labels_json": dd_labels_json if snapshots else "[]",
        "dd_values_json": dd_values_json if snapshots else "[]",
        "dist_labels_json": dist_labels_json if snapshots else "[]",
        "dist_counts_json": dist_counts_json if snapshots else "[]",
```

Read `analytics.trade_distribution`'s actual return shape (`analytics.py`) before writing this task's code for real -- confirm the exact dict keys it returns (used as `bucket["range_low"]`/`bucket["count"]` above) match what the function really produces, and adjust the two `dist_*` lines if the real key names differ.

- [ ] **Step 4: Add the new sections to `dashboard/templates/analyses.html`**

Append inside the `{% block content %}` block, after the metrics table's closing `{% endif %}`:

```html
{% if metrics %}
<h2>Courbe de drawdown</h2>
<canvas id="drawdown-chart" height="60"></canvas>
<script>
new Chart(document.getElementById("drawdown-chart"), {
    type: "line",
    data: { labels: {{ dd_labels_json | safe }}, datasets: [{ label: "Drawdown %", data: {{ dd_values_json | safe }}.map(Number), borderColor: "#f87171" }] },
    options: { scales: { x: { display: false } } },
});
</script>

<h2>Distribution des trades</h2>
<canvas id="trade-distribution-chart" height="60"></canvas>
<script>
new Chart(document.getElementById("trade-distribution-chart"), {
    type: "bar",
    data: { labels: {{ dist_labels_json | safe }}, datasets: [{ label: "Nombre de trades", data: {{ dist_counts_json | safe }}, backgroundColor: "#93c5fd" }] },
});
</script>

<h2>Rendements mensuels</h2>
<table>
    <tr><th>Mois</th><th>Rendement %</th></tr>
    {% for month, pct in monthly_returns.items() %}
    <tr><td>{{ month }}</td><td class="{% if pct >= 0 %}gain{% else %}loss{% endif %}">{{ pct }}</td></tr>
    {% endfor %}
</table>
{% endif %}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_dashboard_app.py -v` then the full suite `pytest -v`
Expected: PASS, 0 failures

- [ ] **Step 6: Commit**

```bash
git add dashboard/
git commit -m "feat: Analyses page drawdown curve, trade distribution, monthly returns"
```

---

### Task 7: Transactions page — filterable table with totals

**Files:**
- Modify: `dashboard/app.py`
- Create: `dashboard/templates/transactions.html`
- Test: `tests/test_dashboard_app.py`

**Interfaces:**
- Consumes: `list_trades` (Task 1).
- Produces: `GET /transactions?symbol=<SYMBOL>&trade_type=<BUY|SELL|>&outcome=<gagnant|perdant|>&date_from=<YYYY-MM-DD>&date_to=<YYYY-MM-DD>` renders a filtered, newest-first trade table with a total row. Spec section 12 requires filters for "période, type, gagnant/perdant" -- période (date range) is `date_from`/`date_to`; a symbol filter is an addition beyond the spec's explicit list, included since it's the natural complement to the main/Analyses pages' own symbol selector, at no extra cost.
- Produces (helper): `_parse_date_boundary(date_str: str | None, end_of_day: bool) -> int | None` -- converts a `YYYY-MM-DD` string to an inclusive epoch-ms boundary (start of that day, or end of that day when `end_of_day=True`), `None` for an empty/missing string. Trade timestamps are treated as UTC, consistent with the exchange kline timestamps they originate from.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_dashboard_app.py
def _seeded_conn_with_mixed_trades():
    from db.migrate import init_db
    from db.repository import insert_trade
    from engine.fifo_engine import Side, Trade
    conn = init_db(":memory:")
    insert_trade(conn, Trade(
        id=1, timestamp=0, symbol="BTCUSDT", side=Side.BUY, price=Decimal("100"), quantity=Decimal("1"),
        fee_pct=Decimal("0.001"), fee_amount=Decimal("0.1"), total_cost=Decimal("100.1"),
        realized_pnl=None, cash_balance_after=Decimal("899.9"), strategy_name="dca",
    ))
    insert_trade(conn, Trade(
        id=2, timestamp=300_000, symbol="BTCUSDT", side=Side.SELL, price=Decimal("110"), quantity=Decimal("1"),
        fee_pct=Decimal("0.001"), fee_amount=Decimal("0.11"), total_cost=Decimal("109.89"),
        realized_pnl=Decimal("9.79"), cash_balance_after=Decimal("1009.79"), strategy_name="dca",
    ))
    insert_trade(conn, Trade(
        id=3, timestamp=600_000, symbol="BTCUSDT", side=Side.SELL, price=Decimal("90"), quantity=Decimal("1"),
        fee_pct=Decimal("0.001"), fee_amount=Decimal("0.09"), total_cost=Decimal("89.91"),
        realized_pnl=Decimal("-10.09"), cash_balance_after=Decimal("999.7"), strategy_name="dca",
    ))
    return conn


def test_transactions_page_shows_all_trades_newest_first(monkeypatch):
    monkeypatch.setattr("dashboard.app.get_conn", lambda: _seeded_conn_with_mixed_trades())

    response = client.get("/transactions")

    assert response.status_code == 200
    text = response.text
    # list_trades orders by id DESC (newest first): id 3 (ts=600_000), then id 2 (ts=300_000), then id 1 (ts=0)
    assert text.index("-10.09") < text.index("9.79") < text.index("100.1")


def test_transactions_page_filters_by_type(monkeypatch):
    monkeypatch.setattr("dashboard.app.get_conn", lambda: _seeded_conn_with_mixed_trades())

    response = client.get("/transactions?trade_type=BUY")

    assert "100.1" in response.text
    assert "9.79" not in response.text


def test_transactions_page_filters_by_outcome(monkeypatch):
    monkeypatch.setattr("dashboard.app.get_conn", lambda: _seeded_conn_with_mixed_trades())

    response = client.get("/transactions?outcome=gagnant")

    assert "9.79" in response.text
    assert "-10.09" not in response.text


def test_transactions_page_shows_total_row(monkeypatch):
    monkeypatch.setattr("dashboard.app.get_conn", lambda: _seeded_conn_with_mixed_trades())

    response = client.get("/transactions")

    assert "Total" in response.text


def test_transactions_page_with_explicit_empty_symbol_shows_all_trades(monkeypatch):
    # Reproduces a real filter-form submission: the "Tous" <option value="">
    # is still a named field, so browsers submit symbol="" (an explicit empty
    # string), never an absent param. This must behave identically to no
    # filter at all, not silently match zero trades.
    monkeypatch.setattr("dashboard.app.get_conn", lambda: _seeded_conn_with_mixed_trades())

    response = client.get("/transactions?symbol=&trade_type=&outcome=")

    assert "100.1" in response.text
    assert "9.79" in response.text
    assert "-10.09" in response.text


def test_transactions_page_filters_by_date_range(monkeypatch):
    # All 3 fixture trades land on 1970-01-01 UTC (ts=0/300_000/600_000 ms are
    # all within the first day). A date_from of the NEXT day excludes all of
    # them, proving the boundary is a real epoch-ms comparison against
    # date_from's start-of-day, not a no-op.
    monkeypatch.setattr("dashboard.app.get_conn", lambda: _seeded_conn_with_mixed_trades())

    response = client.get("/transactions?date_from=1970-01-02")

    assert response.status_code == 200
    assert "100.1" not in response.text
    assert "9.79" not in response.text
    assert "-10.09" not in response.text


def test_transactions_page_date_to_is_inclusive_of_the_whole_day(monkeypatch):
    monkeypatch.setattr("dashboard.app.get_conn", lambda: _seeded_conn_with_mixed_trades())

    response = client.get("/transactions?date_to=1970-01-01")

    assert "100.1" in response.text
    assert "9.79" in response.text
    assert "-10.09" in response.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dashboard_app.py -v -k transactions`
Expected: FAIL with 404

- [ ] **Step 3: Implement the Transactions route**

Add to `dashboard/app.py`:

```python
from datetime import datetime, timezone


def _parse_date_boundary(date_str: str | None, end_of_day: bool) -> int | None:
    """Converts a YYYY-MM-DD string (interpreted as UTC, matching the
    exchange kline timestamps trades ultimately derive from) into an
    inclusive epoch-ms boundary. Returns None for an empty/missing string
    -- an unbounded filter, not "match nothing"."""
    if not date_str:
        return None
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59, microsecond=999_000)
    return int(dt.timestamp() * 1000)


@app.get("/transactions", response_class=HTMLResponse)
def transactions_page(
    request: Request, symbol: str | None = None, trade_type: str | None = None, outcome: str | None = None,
    date_from: str | None = None, date_to: str | None = None,
) -> HTMLResponse:
    conn = get_conn()
    symbols = list_symbols_with_trades(conn)
    # The filter form's "Tous" option submits symbol="" (an explicit empty
    # string), not an absent param -- browsers serialize every named <select>
    # on submit, even ones left at their empty default value. list_trades'
    # SQL does `WHERE symbol = ?`, and no trade has symbol=="", so passing ""
    # straight through would silently return zero rows instead of "no filter".
    trades = list_trades(conn, symbol or None)

    if trade_type in ("BUY", "SELL"):
        trades = [t for t in trades if t.side.value == trade_type]
    if outcome == "gagnant":
        trades = [t for t in trades if t.realized_pnl is not None and t.realized_pnl > 0]
    elif outcome == "perdant":
        trades = [t for t in trades if t.realized_pnl is not None and t.realized_pnl < 0]

    from_ms = _parse_date_boundary(date_from, end_of_day=False)
    to_ms = _parse_date_boundary(date_to, end_of_day=True)
    if from_ms is not None:
        trades = [t for t in trades if t.timestamp >= from_ms]
    if to_ms is not None:
        trades = [t for t in trades if t.timestamp <= to_ms]

    total_fees = sum((t.fee_amount for t in trades), Decimal("0"))
    total_realized_pnl = sum((t.realized_pnl for t in trades if t.realized_pnl is not None), Decimal("0"))

    return templates.TemplateResponse(request, "transactions.html", {
        "symbols": symbols,
        "selected_symbol": symbol or "",
        "selected_type": trade_type or "",
        "selected_outcome": outcome or "",
        "selected_date_from": date_from or "",
        "selected_date_to": date_to or "",
        "trades": trades,
        "total_fees": total_fees,
        "total_realized_pnl": total_realized_pnl,
    })
```

- [ ] **Step 4: Create `dashboard/templates/transactions.html`**

```html
{% extends "base.html" %}
{% block title %}crypto-sim -- Transactions{% endblock %}
{% block content %}
<form method="get" style="margin-bottom: 1rem; display: flex; gap: 1rem;">
    <label>Symbole :
        <select name="symbol">
            <option value="">Tous</option>
            {% for s in symbols %}
            <option value="{{ s }}" {% if s == selected_symbol %}selected{% endif %}>{{ s }}</option>
            {% endfor %}
        </select>
    </label>
    <label>Type :
        <select name="trade_type">
            <option value="">Tous</option>
            <option value="BUY" {% if selected_type == "BUY" %}selected{% endif %}>Achat</option>
            <option value="SELL" {% if selected_type == "SELL" %}selected{% endif %}>Vente</option>
        </select>
    </label>
    <label>Resultat :
        <select name="outcome">
            <option value="">Tous</option>
            <option value="gagnant" {% if selected_outcome == "gagnant" %}selected{% endif %}>Gagnant</option>
            <option value="perdant" {% if selected_outcome == "perdant" %}selected{% endif %}>Perdant</option>
        </select>
    </label>
    <label>Du : <input type="date" name="date_from" value="{{ selected_date_from }}"></label>
    <label>Au : <input type="date" name="date_to" value="{{ selected_date_to }}"></label>
    <button type="submit">Filtrer</button>
    <a href="/transactions/export.csv?symbol={{ selected_symbol }}&trade_type={{ selected_type }}&outcome={{ selected_outcome }}&date_from={{ selected_date_from }}&date_to={{ selected_date_to }}">Exporter CSV</a>
</form>

<table>
    <tr>
        <th>Date/heure</th><th>Symbole</th><th>Type</th><th>Prix</th><th>Quantite</th>
        <th>Montant</th><th>Frais</th><th>Solde apres</th><th>Gain/Perte</th>
    </tr>
    {% for t in trades %}
    <tr>
        <td>{{ t.timestamp }}</td><td>{{ t.symbol }}</td><td>{{ t.side.value }}</td>
        <td>{{ t.price }}</td><td>{{ t.quantity }}</td><td>{{ t.total_cost }}</td><td>{{ t.fee_amount }}</td>
        <td>{{ t.cash_balance_after }}</td>
        <td class="{% if t.realized_pnl is not none and t.realized_pnl >= 0 %}gain{% elif t.realized_pnl is not none %}loss{% endif %}">
            {{ t.realized_pnl if t.realized_pnl is not none else "" }}
        </td>
    </tr>
    {% endfor %}
    <tr><td colspan="6"><strong>Total</strong></td><td><strong>{{ total_fees }}</strong></td><td></td><td><strong>{{ total_realized_pnl }}</strong></td></tr>
</table>
{% endblock %}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_dashboard_app.py -v` then the full suite `pytest -v`
Expected: PASS, 0 failures

- [ ] **Step 6: Commit**

```bash
git add dashboard/ tests/test_dashboard_app.py
git commit -m "feat: Transactions page with symbol/type/outcome/date-range filters and total row"
```

---

### Task 8: Transactions page — filtered CSV export

**Files:**
- Modify: `dashboard/app.py`
- Test: `tests/test_dashboard_app.py`

**Interfaces:**
- Consumes: `list_trades` (Task 1), the same filtering logic as Task 7 (including `_parse_date_boundary`).
- Produces: `GET /transactions/export.csv?symbol=&trade_type=&outcome=&date_from=&date_to=` returns a CSV attachment of the currently filtered set.

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_dashboard_app.py
def test_transactions_export_csv_respects_filters(monkeypatch):
    monkeypatch.setattr("dashboard.app.get_conn", lambda: _seeded_conn_with_mixed_trades())

    response = client.get("/transactions/export.csv?trade_type=BUY")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    body = response.text
    assert "100.1" in body
    assert "9.79" not in body


def test_transactions_export_csv_respects_date_range(monkeypatch):
    monkeypatch.setattr("dashboard.app.get_conn", lambda: _seeded_conn_with_mixed_trades())

    response = client.get("/transactions/export.csv?date_from=1970-01-02")

    assert response.status_code == 200
    body = response.text
    assert "100.1" not in body
    assert "9.79" not in body
    assert "-10.09" not in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dashboard_app.py -v -k export`
Expected: FAIL with 404

- [ ] **Step 3: Extract the shared filter logic and add the export route**

Refactor `dashboard/app.py`: extract the filtering block from `transactions_page` into a shared helper, then use it from both routes:

```python
def _filtered_trades(
    conn: sqlite3.Connection, symbol: str | None, trade_type: str | None, outcome: str | None,
    date_from: str | None = None, date_to: str | None = None,
) -> list[Trade]:
    # symbol or None: both callers may receive an explicit empty string
    # (a submitted-but-unset <select>, or an unset query param copied
    # verbatim into the export link) rather than an absent param -- normalize
    # here once so neither call site has to remember to do it separately.
    trades = list_trades(conn, symbol or None)
    if trade_type in ("BUY", "SELL"):
        trades = [t for t in trades if t.side.value == trade_type]
    if outcome == "gagnant":
        trades = [t for t in trades if t.realized_pnl is not None and t.realized_pnl > 0]
    elif outcome == "perdant":
        trades = [t for t in trades if t.realized_pnl is not None and t.realized_pnl < 0]
    from_ms = _parse_date_boundary(date_from, end_of_day=False)
    to_ms = _parse_date_boundary(date_to, end_of_day=True)
    if from_ms is not None:
        trades = [t for t in trades if t.timestamp >= from_ms]
    if to_ms is not None:
        trades = [t for t in trades if t.timestamp <= to_ms]
    return trades
```

(Add `from engine.fifo_engine import Trade` to the imports if not already present, for the type hint.) Update `transactions_page` to call `_filtered_trades(conn, symbol, trade_type, outcome, date_from, date_to)` in place of its own inline filtering block (delete the lines that duplicated this logic, including Task 7's own `symbol or None` guard and its own `_parse_date_boundary` calls -- `_filtered_trades` now does all of that internally; the rest of the function, including building `total_fees`/`total_realized_pnl` from the resulting `trades` list and the `TemplateResponse` context dict, is unchanged).

Add the export route:

```python
import csv as csv_module
import io

from fastapi.responses import StreamingResponse


@app.get("/transactions/export.csv")
def export_transactions_csv(
    symbol: str | None = None, trade_type: str | None = None, outcome: str | None = None,
    date_from: str | None = None, date_to: str | None = None,
) -> StreamingResponse:
    conn = get_conn()
    trades = _filtered_trades(conn, symbol, trade_type, outcome, date_from, date_to)

    buffer = io.StringIO()
    writer = csv_module.writer(buffer)
    writer.writerow(["timestamp", "symbol", "side", "price", "quantity", "total_cost", "fee_amount", "cash_balance_after", "realized_pnl"])
    for t in trades:
        writer.writerow([t.timestamp, t.symbol, t.side.value, t.price, t.quantity, t.total_cost, t.fee_amount, t.cash_balance_after, t.realized_pnl or ""])

    buffer.seek(0)
    return StreamingResponse(
        buffer, media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=transactions.csv"},
    )
```

Query params arrive as empty strings (`""`) from the template's export link (and from the filter form's "Tous" `<select>`) when a filter is unset, not `None` -- `_filtered_trades` already normalizes this internally (`symbol or None`), so neither call site needs to guard it separately.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_dashboard_app.py -v` then the full suite `pytest -v`
Expected: PASS, 0 failures

- [ ] **Step 5: Commit**

```bash
git add dashboard/ tests/test_dashboard_app.py
git commit -m "feat: filtered CSV export for the Transactions page"
```

---

### Task 9: `main()` entrypoint, README, full suite verification

**Files:**
- Modify: `dashboard/app.py`, `README.md`
- Test: (verification only)

**Interfaces:**
- Consumes: everything from Tasks 1-8.
- Produces: `python -m dashboard.app` starts the dashboard with `uvicorn` on `cfg.dashboard.port`.

- [ ] **Step 1: Add a `main()` entrypoint to `dashboard/app.py`**

Append:

```python
def main() -> None:
    import uvicorn
    cfg = load_config()
    uvicorn.run("dashboard.app:app", host="0.0.0.0", port=cfg.dashboard.port)


if __name__ == "__main__":
    main()
```

`main()` is not covered by the automated suite (same precedent as `backtest.py`/`live_engine.py`'s own `main()` -- it starts a real server and blocks forever). Verify by reading the diff, not by running it live in this task.

- [ ] **Step 2: Update `README.md`'s "État actuel" section**

Replace between the `## État actuel` heading and the next `## Développement` heading with:

```markdown
## État actuel

Plan 1/6 (fondation) : `MarketDataProvider` (Binance + repli Kraken), moteur FIFO, `analytics.py` — termine.
Plan 2/6 (backtest) : filtre de liquidite, 3 strategies, `backtest.py` (telechargement, rejeu, exports CSV) — termine.
Plan 3/6 (moteur live) : persistance SQLite (WAL), strategies pilotables au poll (`step()`), `live_engine.py`
(polling, reprise sur incident, retry/backoff, housekeeping snapshots) — termine.
Plan 4/6 (Discord) : `discord_notifier.py` (transactions, resume quotidien, alertes seuil, logs demarrage/arret) — termine.
Plan 5/6 (dashboard) : FastAPI + Jinja2, pages Principal/Analyses/Transactions, bandeau simulation permanent — termine.

Lancer le backtest : `python backtest.py`
Lancer le moteur live : `python live_engine.py` (tourne indefiniment, Ctrl+C pour arreter)
Valider les notifications Discord avant le premier lancement du moteur live : `python test_notifier.py`
(necessite `.env` rempli avec les 4 webhooks -- voir `.env.example`).
Lancer le dashboard : `python -m dashboard.app` (port configurable dans `config.yaml`, defaut 8303).

Pas encore de deploiement — voir `docs/superpowers/plans/` pour le plan suivant (Plan 6/6).
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/app.py README.md
git commit -m "feat: dashboard main() entrypoint, update README for Plan 5 completion"
```

- [ ] **Step 4: Run the full test suite**

Run: `pytest -v`
Expected: all tests pass — 0 failures.

---
