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


def test_index_embeds_chart_data_as_json(monkeypatch, sample_backtest_csv):
    monkeypatch.setattr("dashboard.app.get_conn", lambda: _seeded_conn())
    monkeypatch.setattr("dashboard.app._active_symbol_default", lambda: "BTCUSDT")
    monkeypatch.setattr("dashboard.app._initial_capital", lambda: Decimal("1000"))

    response = client.get("/?symbol=BTCUSDT")

    assert response.status_code == 200
    assert "new Chart(" in response.text
    assert '"1000"' in response.text  # first snapshot's total_value, as a JSON string
    assert '"1050"' in response.text  # second snapshot's total_value


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


def test_analyses_page_shows_drawdown_chart_distribution_and_monthly_table(monkeypatch):
    monkeypatch.setattr("dashboard.app.get_conn", lambda: _seeded_conn_with_trades())
    monkeypatch.setattr("dashboard.app._active_symbol_default", lambda: "BTCUSDT")
    monkeypatch.setattr("dashboard.app._poll_interval_default", lambda: "5m")

    response = client.get("/analyses?symbol=BTCUSDT")

    assert response.status_code == 200
    assert "drawdown-chart" in response.text
    assert "trade-distribution-chart" in response.text
    assert "Rendements mensuels" in response.text
