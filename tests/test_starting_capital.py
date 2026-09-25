"""A pair's return is measured against the cash it actually started with.

Regression (CT303, 2026-09-25): reconstruct_engine_from_db continues each
pair's cash from its last trade, so a pair keeps the capital it started with
(1000/8 = 125 for the first 8 pairs) even after live.pairs grows to 9 and
live.capital_per_pair drops to 111.11. Every report divided by 111.11, which
showed +16.5% for a portfolio really at +4.8% on 1111.11 of starting cash.
"""
from decimal import Decimal

from db.migrate import init_db
from db.repository import insert_trade, starting_capital
from engine.fifo_engine import Side, Trade


def _trade(tid, symbol, side, total_cost, cash_after):
    return Trade(
        id=tid, timestamp=1_700_000_000_000 + tid, symbol=symbol, side=side,
        price=Decimal("10"), quantity=Decimal("1"), fee_pct=Decimal("0.001"),
        fee_amount=Decimal("0.1"), total_cost=Decimal(total_cost), realized_pnl=None,
        cash_balance_after=Decimal(cash_after), strategy_name="grid",
    )


def test_no_trades_falls_back_to_configured_capital(tmp_path):
    conn = init_db(str(tmp_path / "t.db"))
    assert starting_capital(conn, "BTCUSDT", Decimal("111.11")) == Decimal("111.11")


def test_derived_from_the_first_buy(tmp_path):
    conn = init_db(str(tmp_path / "t.db"))
    insert_trade(conn, _trade(1, "BTCUSDT", Side.BUY, "100.1", "24.9"))
    insert_trade(conn, _trade(2, "BTCUSDT", Side.SELL, "105", "129.9"))
    conn.commit()
    assert starting_capital(conn, "BTCUSDT", Decimal("111.11")) == Decimal("125.0")


def test_scoped_by_symbol(tmp_path):
    conn = init_db(str(tmp_path / "t.db"))
    insert_trade(conn, _trade(1, "BTCUSDT", Side.BUY, "100.1", "24.9"))
    insert_trade(conn, _trade(2, "ZECUSDT", Side.BUY, "100.1", "11.01"))
    conn.commit()
    assert starting_capital(conn, "BTCUSDT", Decimal("111.11")) == Decimal("125.0")
    assert starting_capital(conn, "ZECUSDT", Decimal("111.11")) == Decimal("111.11")
    assert starting_capital(conn, "ETHUSDT", Decimal("111.11")) == Decimal("111.11")


def test_daily_summary_measures_each_pair_against_its_real_starting_cash(tmp_path, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    import live_engine
    from config import LivePair
    from db.repository import insert_snapshot
    from models import PortfolioSnapshot

    conn = init_db(str(tmp_path / "t.db"))
    # BTC started under 8 pairs (125), ZEC joined under 9 (111.11)
    insert_trade(conn, _trade(1, "BTCUSDT", Side.BUY, "100", "25"))
    insert_trade(conn, _trade(2, "ZECUSDT", Side.BUY, "100", "11.11"))
    now_ms = int(datetime(2026, 9, 25, 12, 0, tzinfo=ZoneInfo("Europe/Paris")).timestamp() * 1000)
    for sym, value in (("BTCUSDT", "137.5"), ("ZECUSDT", "111.11")):
        insert_snapshot(conn, PortfolioSnapshot(now_ms, sym, Decimal(value), Decimal(0), Decimal(value), Decimal(0), Decimal(0)))
    conn.commit()

    captured = {}
    monkeypatch.setattr("live_engine.send_portfolio_daily_summary", lambda webhook_url, **kw: captured.update(kw) or "1")
    live_engine._check_global_daily_summary(
        conn, [LivePair("BTCUSDT", "grid"), LivePair("ZECUSDT", "grid")], "https://webhook", now_ms, Decimal("111.11"),
    )

    assert captured["total_capital"] == Decimal("236.11")
    btc = next(p for p in captured["pairs"] if p.symbol == "BTCUSDT")
    assert btc.return_pct_since_start == Decimal("10")          # 137.5 vs 125, not vs 111.11
    assert round(captured["return_pct"], 2) == Decimal("5.29")  # 248.61 vs 236.11
