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
