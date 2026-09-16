# tests/test_dashboard_app.py
from fastapi.testclient import TestClient

from dashboard.app import app

client = TestClient(app)


def test_root_page_shows_simulation_banner():
    response = client.get("/")
    assert response.status_code == 200
    assert "⚠ SIMULATION — Aucun argent réel, aucun ordre réel envoyé" in response.text


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
