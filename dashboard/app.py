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
