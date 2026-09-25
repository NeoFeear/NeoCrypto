# dashboard/app.py
import csv
import io
import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

import analytics
from config import load_config
from db.repository import list_snapshots, list_symbols_with_trades, list_trades, starting_capital
from engine.fifo_engine import Trade
from market_data.provider import INTERVAL_MS
from sats import format_quantity

app = FastAPI()


def _dashboard_credentials() -> tuple[str, str] | None:
    """DASHBOARD_USER / DASHBOARD_PASSWORD from .env (or the environment).
    Unset = no auth, the LAN-only default; set both before ever exposing
    the dashboard beyond the LAN (it binds 0.0.0.0)."""
    import os
    from dotenv import dotenv_values
    values = {**dotenv_values(".env"), **os.environ}
    user, password = values.get("DASHBOARD_USER"), values.get("DASHBOARD_PASSWORD")
    return (user, password) if user and password else None


@app.middleware("http")
async def _basic_auth(request, call_next):
    creds = _dashboard_credentials()
    if creds is not None:
        import base64
        import secrets
        from fastapi.responses import Response
        ok = False
        header = request.headers.get("authorization", "")
        if header.startswith("Basic "):
            try:
                user, _, password = base64.b64decode(header[6:]).decode("utf-8").partition(":")
                ok = secrets.compare_digest(user, creds[0]) and secrets.compare_digest(password, creds[1])
            except Exception:
                ok = False
        if not ok:
            return Response("Authentification requise.", status_code=401,
                            headers={"WWW-Authenticate": 'Basic realm="crypto-sim", charset="UTF-8"'})
    return await call_next(request)


@app.exception_handler(sqlite3.OperationalError)
def _database_not_available(request: Request, exc: sqlite3.OperationalError) -> PlainTextResponse:
    return PlainTextResponse(
        "Base de donnees introuvable ou inaccessible. Lancez live_engine.py "
        "au moins une fois pour l'initialiser avant d'utiliser le dashboard.",
        status_code=503,
    )


def _format_timestamp(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# Absolute path, not the relative string "dashboard/templates": a relative
# searchpath is resolved against the process's CURRENT working directory at
# template-load time (not at this line's execution time), which breaks the
# moment anything changes cwd -- a test using monkeypatch.chdir, or Plan 6's
# systemd unit running this from an unrelated WorkingDirectory=.
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def _num(value, digits: int = 2) -> str:
    """Fixed-point display for Decimal/float values (a raw Decimal quotient
    rendered 27 digits: "-0.999811862203201699120418800%")."""
    if value is None:
        return "N/A"
    try:
        return f"{Decimal(str(value)):.{digits}f}"
    except Exception:
        return str(value)


templates.env.filters["num"] = _num
templates.env.filters["format_ts"] = _format_timestamp
templates.env.filters["format_qty"] = format_quantity


def get_conn() -> sqlite3.Connection:
    """Read-only usage from every dashboard route -- never insert/update/delete.
    Opened in SQLite's own read-only URI mode so this is enforced by SQLite
    itself, not just by convention: a plain read-write connection (via
    init_db) would otherwise run schema migrations and commit on every
    single request, taking write locks against the live engine's own
    connection and silently fabricating an empty database if db_path is
    ever wrong -- a healthy-looking dashboard with silently no data, the
    worst kind of bug. mode=ro also fails loudly (OperationalError) if the
    database file doesn't exist yet, rather than creating one."""
    cfg = load_config()
    conn = sqlite3.connect(f"file:{cfg.db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _active_symbol_default() -> str:
    return load_config().live.pairs[0].symbol


def _initial_capital(conn: sqlite3.Connection, symbol: str) -> Decimal:
    # The live portfolio's actual starting cash for the selected symbol: what
    # its first trade shows it started with (see db.repository.starting_capital),
    # falling back to live.total_capital split equally across the configured
    # pairs -- never backtest.initial_capital (the full amount per symbol, used
    # only by backtest.py's own comparative analysis).
    return starting_capital(conn, symbol, load_config().live.capital_per_pair)


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
    initial_capital = _initial_capital(conn, active_symbol)

    if snapshots:
        latest = snapshots[-1]
        return_pct = ((latest.total_value - initial_capital) / initial_capital * Decimal(100)
                      if initial_capital > 0 else Decimal(0))
    else:
        latest = None
        return_pct = Decimal(0)

    backtest_rows = [row for row in _read_backtest_report() if row["symbol"] == active_symbol]

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
        initial_capital = _initial_capital(conn, active_symbol)
        cagr = (analytics.cagr_pct(initial_capital, snapshots[-1].total_value, days)
                if initial_capital > 0 else Decimal(0))
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

        dd_curve = analytics.drawdown_curve(snapshots)
        distribution = analytics.trade_distribution(trades)
        monthly = analytics.monthly_returns(snapshots)

        dd_labels_json = json.dumps([ts for ts, _ in dd_curve])
        dd_values_json = json.dumps([str(pct) for _, pct in dd_curve])
        dist_labels_json = json.dumps([str(bucket["range_low"]) for bucket in distribution])
        dist_counts_json = json.dumps([bucket["count"] for bucket in distribution])
    else:
        metrics = None

    return templates.TemplateResponse(request, "analyses.html", {
        "symbols": symbols,
        "active_symbol": active_symbol,
        "metrics": metrics,
        "monthly_returns": monthly if snapshots else {},
        "dd_labels_json": dd_labels_json if snapshots else "[]",
        "dd_values_json": dd_values_json if snapshots else "[]",
        "dist_labels_json": dist_labels_json if snapshots else "[]",
        "dist_counts_json": dist_counts_json if snapshots else "[]",
    })


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


@app.get("/transactions", response_class=HTMLResponse)
def transactions_page(
    request: Request, symbol: str | None = None, trade_type: str | None = None, outcome: str | None = None,
    date_from: str | None = None, date_to: str | None = None,
) -> HTMLResponse:
    conn = get_conn()
    symbols = list_symbols_with_trades(conn)
    trades = _filtered_trades(conn, symbol, trade_type, outcome, date_from, date_to)

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


@app.get("/transactions/export.csv")
def export_transactions_csv(
    symbol: str | None = None, trade_type: str | None = None, outcome: str | None = None,
    date_from: str | None = None, date_to: str | None = None,
) -> StreamingResponse:
    conn = get_conn()
    trades = _filtered_trades(conn, symbol, trade_type, outcome, date_from, date_to)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["timestamp", "symbol", "side", "price", "quantity", "total_cost", "fee_amount", "cash_balance_after", "realized_pnl"])
    for t in trades:
        writer.writerow([t.timestamp, t.symbol, t.side.value, t.price, t.quantity, t.total_cost, t.fee_amount, t.cash_balance_after, t.realized_pnl if t.realized_pnl is not None else ""])

    buffer.seek(0)
    return StreamingResponse(
        buffer, media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=transactions.csv"},
    )


def main() -> None:
    import uvicorn
    cfg = load_config()
    uvicorn.run("dashboard.app:app", host="0.0.0.0", port=cfg.dashboard.port)


if __name__ == "__main__":
    main()
