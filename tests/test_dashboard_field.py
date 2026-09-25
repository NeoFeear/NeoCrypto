"""Principal page: the whole field of live pairs, ranked by return against
each pair's real starting cash (the 'tableau des partants')."""
from decimal import Decimal

from fastapi.testclient import TestClient

from dashboard.app import _field, app
from db.migrate import init_db
from db.repository import insert_snapshot, insert_trade
from engine.fifo_engine import Side, Trade
from models import PortfolioSnapshot


def _trade(tid, symbol, cost, cash_after):
    return Trade(id=tid, timestamp=1_790_000_000_000 + tid, symbol=symbol, side=Side.BUY, price=Decimal(10),
                 quantity=Decimal(1), fee_pct=Decimal("0.001"), fee_amount=Decimal("0.1"), total_cost=Decimal(cost),
                 realized_pnl=None, cash_balance_after=Decimal(cash_after), strategy_name="grid")


def _seed(tmp_path=None):
    conn = init_db(":memory:")
    insert_trade(conn, _trade(1, "BTCUSDT", "100", "25"))      # started with 125
    insert_trade(conn, _trade(2, "ZECUSDT", "100", "11.11"))   # started with 111.11
    for i, (btc, zec) in enumerate([("125", "111.11"), ("130", "105"), ("137.5", "100")]):
        t = 1_790_000_000_000 + i * 300_000
        insert_snapshot(conn, PortfolioSnapshot(t, "BTCUSDT", Decimal(btc), Decimal(0), Decimal(btc), Decimal(0), Decimal(0)))
        insert_snapshot(conn, PortfolioSnapshot(t, "ZECUSDT", Decimal(zec), Decimal(0), Decimal(zec), Decimal(0), Decimal(0)))
    conn.commit()
    return conn


def test_field_ranks_pairs_by_return_against_their_real_start(tmp_path, monkeypatch):
    monkeypatch.setattr("dashboard.app._pair_order", lambda: [("BTCUSDT", "dca"), ("ZECUSDT", "grid")])
    runners, totals = _field(_seed(tmp_path))
    assert [r["symbol"] for r in runners] == ["BTCUSDT", "ZECUSDT"]
    btc, zec = runners
    assert btc["position"] == 1 and btc["number"] == 1 and btc["strategy"] == "dca"
    assert btc["start"] == Decimal("125") and btc["return_pct"] == Decimal("10")
    assert zec["number"] == 2 and zec["return_pct"] < 0
    assert totals["start"] == Decimal("236.11") and totals["value"] == Decimal("237.5")
    assert len(btc["spark"]) == 3


def test_principal_page_shows_the_field(tmp_path, monkeypatch):
    monkeypatch.setattr("dashboard.app.get_conn", lambda: _seed())
    monkeypatch.setattr("dashboard.app._pair_order", lambda: [("BTCUSDT", "dca"), ("ZECUSDT", "grid")])
    monkeypatch.setattr("dashboard.app._active_symbol_default", lambda: "BTCUSDT")
    r = TestClient(app).get("/")
    assert r.status_code == 200
    assert "Tableau des partants" in r.text
    assert "ZECUSDT" in r.text and "137.50" in r.text
