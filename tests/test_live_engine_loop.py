from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

import db.repository
from db.migrate import init_db
from db.repository import insert_snapshot
from discord_notifier import DiscordWebhooks
from engine.fifo_engine import FifoEngine
from housekeeping import DAY_MS
from live_engine import _check_daily_summary, reconstruct_engine_from_db, run_polling_loop
from market_data.provider import MarketDataProvider
from market_data.types import Kline
from models import PortfolioSnapshot


class SequenceProvider(MarketDataProvider):
    def __init__(self, klines: list[Kline]):
        self._klines = klines
        self._index = 0

    def get_klines(self, symbol, interval, start_ms, end_ms, limit=1000):
        if self._index >= len(self._klines):
            return [self._klines[-1]]  # keep serving the last one (simulates "no new candle yet")
        k = self._klines[self._index]
        self._index += 1
        return [k]

    def get_book_ticker(self, symbol):
        raise NotImplementedError

    def get_ticker_24h(self, symbol):
        raise NotImplementedError


class RaiseOnceProvider(MarketDataProvider):
    """Raises a plain RuntimeError (not httpx.HTTPError, so fetch_with_retry's
    except clause never catches it) on its first call, then serves a valid
    kline on every subsequent call."""

    def __init__(self, kline: Kline):
        self._kline = kline
        self.calls = 0

    def get_klines(self, symbol, interval, start_ms, end_ms, limit=1000):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("erreur API inattendue")
        return [self._kline]

    def get_book_ticker(self, symbol):
        raise NotImplementedError

    def get_ticker_24h(self, symbol):
        raise NotImplementedError


def _kline(open_time_ms: int, close: str) -> Kline:
    c = Decimal(close)
    return Kline(open_time_ms=open_time_ms, open=c, high=c, low=c, close=c,
                 volume=Decimal("1"), close_time_ms=open_time_ms + 299_999)


@pytest.fixture
def conn():
    c = init_db(":memory:")
    yield c
    c.close()


def test_run_polling_loop_stops_after_max_cycles_and_processes_new_candles(conn, monkeypatch):
    monkeypatch.setattr("live_engine.time.sleep", lambda s: None)
    provider = SequenceProvider([_kline(0, "100"), _kline(300_000, "110"), _kline(600_000, "120")])
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    params = {"invest_at": "start"}

    run_polling_loop(
        conn, provider, engine, "BTCUSDT", "buy_hold", params,
        poll_interval="5m", poll_interval_seconds=300,
        on_critical_failure=lambda msg: None, initial_cash=Decimal("1000"), max_cycles=3,
        discord_webhooks=DiscordWebhooks(daily_summary="", transactions="", alerts="", logs=""),
        drawdown_threshold_pct=Decimal("10"),
    )

    # buy_hold only ever buys once, on the first genuinely-new candle it sees
    assert len(engine.trades) == 1
    assert len(conn.execute("SELECT * FROM portfolio_snapshots").fetchall()) == 3


def test_run_polling_loop_sleeps_between_cycles(conn, monkeypatch):
    sleeps = []
    monkeypatch.setattr("live_engine.time.sleep", lambda s: sleeps.append(s))
    provider = SequenceProvider([_kline(0, "100"), _kline(300_000, "100")])
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))

    run_polling_loop(
        conn, provider, engine, "BTCUSDT", "buy_hold", {"invest_at": "start"},
        poll_interval="5m", poll_interval_seconds=300,
        on_critical_failure=lambda msg: None, initial_cash=Decimal("1000"), max_cycles=2,
        discord_webhooks=DiscordWebhooks(daily_summary="", transactions="", alerts="", logs=""),
        drawdown_threshold_pct=Decimal("10"),
    )

    # No sleep after the final cycle (mirrors Plan 1's pagination convention of no
    # trailing sleep once there's nothing left to do) -- with max_cycles=2 that's
    # exactly 1 sleep, between cycle 1 and cycle 2.
    assert sleeps == [300]


def test_run_polling_loop_requires_initial_cash_no_silent_gap(conn):
    # initial_cash is required (no default), not opt-in: a caller that omits it
    # now gets an immediate, loud TypeError instead of silently losing NEW-4's
    # engine/DB reconciliation on a rolled-back cycle.
    provider = SequenceProvider([_kline(0, "100")])
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))

    with pytest.raises(TypeError):
        run_polling_loop(
            conn, provider, engine, "BTCUSDT", "buy_hold", {"invest_at": "start"},
            poll_interval="5m", poll_interval_seconds=300,
            on_critical_failure=lambda msg: None, max_cycles=1,
            discord_webhooks=DiscordWebhooks(daily_summary="", transactions="", alerts="", logs=""),
            drawdown_threshold_pct=Decimal("10"),
        )


def test_run_polling_loop_survives_unhandled_exception_and_continues_next_cycle(conn, monkeypatch):
    monkeypatch.setattr("live_engine.time.sleep", lambda s: None)
    provider = RaiseOnceProvider(_kline(0, "100"))
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    failures = []

    run_polling_loop(
        conn, provider, engine, "BTCUSDT", "buy_hold", {"invest_at": "start"},
        poll_interval="5m", poll_interval_seconds=300,
        on_critical_failure=failures.append, initial_cash=Decimal("1000"), max_cycles=2,
        discord_webhooks=DiscordWebhooks(daily_summary="", transactions="", alerts="", logs=""),
        drawdown_threshold_pct=Decimal("10"),
    )

    # First cycle's unhandled RuntimeError must not propagate out of the loop.
    assert len(failures) == 1
    # Second cycle's kline is still processed normally. Checked against the DB,
    # not the `engine` object this test passed in: NEW-4's fix reassigns
    # run_polling_loop's own local `engine` name on a rolled-back cycle (here,
    # cycle 1's failure, even though nothing had mutated yet), which does NOT
    # update this test's own reference -- so asserting against `engine` here
    # would be checking a stale object, not what actually happened.
    trades_in_db = conn.execute("SELECT * FROM trades WHERE symbol = 'BTCUSDT'").fetchall()
    assert len(trades_in_db) == 1
    assert len(conn.execute("SELECT * FROM portfolio_snapshots").fetchall()) == 1


def test_run_polling_loop_rolls_back_partial_cycle_so_signal_is_not_replayed(conn, monkeypatch):
    # Reproduces the exact bug the re-reviewer found: insert_snapshot raises
    # partway through cycle 1, AFTER insert_trade already ran with commit=False.
    # Without a conn.rollback() in run_polling_loop's except handler, that
    # uncommitted BUY trade sits pending in the open transaction (not committed,
    # but not discarded either) and last_ts never advances (its own write never
    # ran). Cycle 2 then re-serves the SAME candle, buys again, and its own
    # successful conn.commit() silently makes cycle 1's leftover trade durable
    # too -- two BUY trades for one candle, exactly the replay bug I3 exists to
    # prevent.
    monkeypatch.setattr("live_engine.time.sleep", lambda s: None)
    calls = {"n": 0}
    real_insert_snapshot = db.repository.insert_snapshot

    def _fail_once(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return real_insert_snapshot(*args, **kwargs)

    monkeypatch.setattr("live_engine.insert_snapshot", _fail_once)

    # SAME candle timestamp served on both cycles (last_ts was never advanced by
    # the failed first cycle).
    provider = SequenceProvider([_kline(0, "100"), _kline(0, "100")])
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    failures = []

    run_polling_loop(
        conn, provider, engine, "BTCUSDT", "buy_hold", {"invest_at": "start"},
        poll_interval="5m", poll_interval_seconds=300,
        on_critical_failure=failures.append, initial_cash=Decimal("1000"), max_cycles=2,
        discord_webhooks=DiscordWebhooks(daily_summary="", transactions="", alerts="", logs=""),
        drawdown_threshold_pct=Decimal("10"),
    )

    assert len(failures) == 1
    trades_in_db = conn.execute("SELECT * FROM trades WHERE symbol = 'BTCUSDT'").fetchall()
    assert len(trades_in_db) == 1


def test_run_polling_loop_runs_daily_housekeeping_and_aggregates_old_snapshots(conn, monkeypatch):
    now_ms = 40 * DAY_MS  # "today" is day 40
    monkeypatch.setattr("live_engine.time.time", lambda: now_ms / 1000)
    monkeypatch.setattr("live_engine.time.sleep", lambda s: None)

    # Two synthetic old snapshots for a DIFFERENT symbol than the one the cycle
    # itself trades, in the same hour bucket, well past a 30-day retention window
    # relative to the mocked "now" -- this way the cycle's own BTCUSDT snapshot
    # (whatever bucket it lands in) can never make the assertion ambiguous.
    old_bucket_start = 0
    insert_snapshot(conn, PortfolioSnapshot(
        timestamp=old_bucket_start, symbol="ETHUSDT", cash_balance=Decimal("1000"),
        position_value=Decimal("0"), total_value=Decimal("1000"),
        unrealized_pnl=Decimal("0"), realized_pnl_cumule=Decimal("0"),
    ))
    insert_snapshot(conn, PortfolioSnapshot(
        timestamp=old_bucket_start + 5 * 60_000, symbol="ETHUSDT", cash_balance=Decimal("1010"),
        position_value=Decimal("0"), total_value=Decimal("1010"),
        unrealized_pnl=Decimal("0"), realized_pnl_cumule=Decimal("0"),
    ))

    # A closed candle safely in the past relative to the mocked "now" so the
    # cycle actually runs and inserts its own (unrelated, BTCUSDT) snapshot too.
    provider = SequenceProvider([_kline(now_ms - 400_000, "100")])
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))

    run_polling_loop(
        conn, provider, engine, "BTCUSDT", "buy_hold", {"invest_at": "start"},
        poll_interval="5m", poll_interval_seconds=300,
        on_critical_failure=lambda msg: None, initial_cash=Decimal("1000"),
        max_cycles=1, retention_days=30,
        discord_webhooks=DiscordWebhooks(daily_summary="", transactions="", alerts="", logs=""),
        drawdown_threshold_pct=Decimal("10"),
    )

    eth_rows = conn.execute(
        "SELECT * FROM portfolio_snapshots WHERE symbol = 'ETHUSDT'"
    ).fetchall()
    # Total row count for the symbol is what actually distinguishes "aggregated"
    # from "untouched": both seeded rows bucket to timestamp=0 regardless of
    # whether aggregation ran, so asserting COUNT(*) WHERE timestamp=0 alone
    # (the previous version of this test) can't tell the two cases apart --
    # without the wiring, both rows survive untouched (2), one at ts=0 and one
    # at ts=300_000.
    assert len(eth_rows) == 1
    assert eth_rows[0]["timestamp"] == old_bucket_start
    # Confirms it's the aggregated average, not just coincidentally one surviving
    # row: average of the two seeded cash_balance values (1000, 1010) is 1005.
    assert Decimal(eth_rows[0]["cash_balance"]) == Decimal("1005")


def test_run_polling_loop_reconciles_in_memory_engine_with_db_after_mid_sell_rollback(conn, monkeypatch):
    # Reproduces NEW-4: conn.rollback() alone only undoes the DB side of a failed
    # cycle. run_cycle mutates `engine` in memory BEFORE persisting anything, so
    # without also re-deriving `engine` from the DB, a rolled-back SELL's phantom
    # realized_pnl stays "real" in the in-process engine and leaks into a LATER,
    # successful cycle's durable snapshot (build_snapshot reads off that same
    # in-memory engine) -- even though the trades table never durably recorded
    # that SELL. That's the DB vs. reconstruction disagreement C2 originally fixed,
    # reintroduced through this narrower door.
    #
    # Sequence (grid, one level: buy_price=100, sell_price=200, order_size_quote=100):
    #   cycle 1 (price=150): just records prev_price, no trade
    #   cycle 2 (price=90):  crosses down through 100 -> BUY, commits successfully
    #   cycle 3 (price=250): crosses up through 200 -> SELL attempted, but
    #                        insert_snapshot fails right after the in-memory
    #                        engine.sell() already ran -> rolled back
    #   cycle 4 (price=250 again, same candle re-served since last_ts never
    #            advanced): SELL retried
    #
    # NOTE: intentionally does NOT assert against the `engine` object passed into
    # run_polling_loop -- reassigning `engine` inside the function only rebinds
    # that local name, it never updates the caller's own reference, so an
    # assertion against this test's own `engine` variable would not observe the
    # fix at all (and would be exactly the kind of vacuous test earlier rounds
    # were built to avoid). Instead this checks black-box DB consistency: a fresh
    # reconstruct_engine_from_db() call must agree with the last durable snapshot.
    monkeypatch.setattr("live_engine.time.sleep", lambda s: None)
    calls = {"n": 0}
    real_insert_snapshot = db.repository.insert_snapshot

    def _fail_on_third_call(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("boom during SELL cycle")
        return real_insert_snapshot(*args, **kwargs)

    monkeypatch.setattr("live_engine.insert_snapshot", _fail_on_third_call)

    provider = SequenceProvider([_kline(0, "150"), _kline(300_000, "90"), _kline(600_000, "250")])
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    params = {"lower_bound": 100, "upper_bound": 200, "n_levels": 1,
              "spacing": "arithmetic", "order_size_quote": 100}
    failures = []

    run_polling_loop(
        conn, provider, engine, "BTCUSDT", "grid", params,
        poll_interval="5m", poll_interval_seconds=300,
        on_critical_failure=failures.append, max_cycles=4,
        initial_cash=Decimal("1000"),
        discord_webhooks=DiscordWebhooks(daily_summary="", transactions="", alerts="", logs=""),
        drawdown_threshold_pct=Decimal("10"),
    )

    assert len(failures) == 1

    # The SELL genuinely happened durably (on the retry, cycle 4) -- confirms
    # this isn't a trivial "nothing happened" pass.
    sell_rows = conn.execute(
        "SELECT realized_pnl FROM trades WHERE symbol = 'BTCUSDT' AND side = 'SELL'"
    ).fetchall()
    assert len(sell_rows) == 1
    expected_pnl = Decimal(sell_rows[0]["realized_pnl"])
    assert expected_pnl == Decimal("99.8")

    # The last durable snapshot's realized_pnl_cumule column must match a FRESH
    # reconstruction from the DB -- not a phantom value carried over from the
    # rolled-back cycle 3 attempt via the poisoned in-memory engine.
    last_snapshot = conn.execute(
        "SELECT * FROM portfolio_snapshots WHERE symbol = 'BTCUSDT' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    restored = reconstruct_engine_from_db(conn, "BTCUSDT", initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))

    assert restored.realized_pnl_cumule("BTCUSDT") == expected_pnl
    assert Decimal(last_snapshot["realized_pnl_cumule"]) == restored.realized_pnl_cumule("BTCUSDT")


def test_run_polling_loop_sends_discord_notification_for_each_committed_trade(conn, monkeypatch):
    monkeypatch.setattr("live_engine.time.sleep", lambda s: None)
    sent = []
    monkeypatch.setattr("live_engine.send_transaction", lambda webhook_url, trade: sent.append((webhook_url, trade)))
    provider = SequenceProvider([_kline(0, "100")])
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    webhooks = DiscordWebhooks(daily_summary="", transactions="https://webhook/tx", alerts="", logs="")

    run_polling_loop(
        conn, provider, engine, "BTCUSDT", "buy_hold", {"invest_at": "start"},
        poll_interval="5m", poll_interval_seconds=300,
        on_critical_failure=lambda msg: None, initial_cash=Decimal("1000"),
        max_cycles=1, discord_webhooks=webhooks, drawdown_threshold_pct=Decimal("10"),
    )

    assert len(sent) == 1
    assert sent[0][0] == "https://webhook/tx"
    assert sent[0][1].side.value == "BUY"


def test_run_polling_loop_sends_no_discord_notification_when_cycle_produces_no_trade(conn, monkeypatch):
    monkeypatch.setattr("live_engine.time.sleep", lambda s: None)
    sent = []
    monkeypatch.setattr("live_engine.send_transaction", lambda webhook_url, trade: sent.append((webhook_url, trade)))
    provider = SequenceProvider([_kline(0, "100")])
    # dca with cash=0 and amount_per_buy=50 -- total_cost (50.05) > cash_balance
    # (0), so engine.buy() genuinely rejects and no trade is committed. (A
    # buy_hold/cash=0 scenario would NOT work here: buy_hold computes
    # quantity = cash_balance / price = 0, so total_cost is also exactly 0, and
    # FifoEngine.buy()'s rejection check is strict (`total_cost > cash_balance`),
    # so `0 > 0` is False and a trivial zero-quantity trade is NOT rejected.)
    engine = FifoEngine(initial_cash=Decimal("0"), fee_pct=Decimal("0.001"))
    webhooks = DiscordWebhooks(daily_summary="", transactions="https://webhook/tx", alerts="", logs="")
    params = {"amount_per_buy": 50, "frequency_hours": 24, "reference_price": "close"}

    run_polling_loop(
        conn, provider, engine, "BTCUSDT", "dca", params,
        poll_interval="5m", poll_interval_seconds=300,
        on_critical_failure=lambda msg: None, initial_cash=Decimal("0"),
        max_cycles=1, discord_webhooks=webhooks, drawdown_threshold_pct=Decimal("10"),
    )

    assert sent == []


def test_run_polling_loop_sends_drawdown_alert_once_when_crossing_threshold(conn, monkeypatch):
    monkeypatch.setattr("live_engine.time.sleep", lambda s: None)
    alerts = []
    monkeypatch.setattr(
        "live_engine.send_alert",
        lambda webhook_url, alert_type, message, severity: alerts.append((alert_type, severity)),
    )
    # Price crashes from 100 -> 85 (15% drop), well past a 10% threshold.
    provider = SequenceProvider([_kline(0, "100"), _kline(300_000, "85"), _kline(600_000, "85")])
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    webhooks = DiscordWebhooks(daily_summary="", transactions="", alerts="https://webhook/alerts", logs="")

    run_polling_loop(
        conn, provider, engine, "BTCUSDT", "buy_hold", {"invest_at": "start"},
        poll_interval="5m", poll_interval_seconds=300,
        on_critical_failure=lambda msg: None, initial_cash=Decimal("1000"),
        max_cycles=3, discord_webhooks=webhooks, drawdown_threshold_pct=Decimal("10"),
    )

    # Crosses the threshold once (cycle 2) and must not re-alert every
    # subsequent cycle while still under it (cycle 3) -- anti-spam.
    assert len(alerts) == 1
    assert alerts[0] == ("drawdown", "warning")


def test_run_polling_loop_does_not_repeat_drawdown_alert_when_daily_summary_fails_same_cycle(conn, monkeypatch):
    # Reproduces the exact bug the final review found: _check_drawdown_alert
    # used to send the alert BEFORE its "already alerted" flag was durably
    # committed (the actual commit happened later, in run_polling_loop, AFTER
    # _check_daily_summary also ran). If _check_daily_summary raises in that
    # same cycle -- e.g. because send_daily_summary itself blows up -- the
    # except handler's conn.rollback() discarded the "already alerted" flag
    # even though the alert had already been delivered to Discord, so the
    # NEXT cycle re-read was_active=False and fired a duplicate alert.
    #
    # This test FAILS against the pre-fix code (2 alerts: cycle 2 and cycle 3
    # both fire) and PASSES post-fix (1 alert: cycle 2 only), verified by
    # temporarily reverting the live_engine.py fix and re-running this test.
    monkeypatch.setattr("live_engine.time.sleep", lambda s: None)
    now_paris = datetime(2026, 1, 15, 9, 0, tzinfo=ZoneInfo("Europe/Paris"))  # past 8h -> daily summary runs too
    monkeypatch.setattr("live_engine.time.time", lambda: now_paris.timestamp())

    alerts = []
    monkeypatch.setattr(
        "live_engine.send_alert",
        lambda webhook_url, alert_type, message, severity: alerts.append((alert_type, severity)),
    )
    # First daily-summary call (cycle 1, before any drawdown) succeeds and
    # durably records a message id, exactly like a real healthy day would --
    # every call from cycle 2 onward (i.e. once the drawdown alert itself
    # starts firing) blows up, simulating something else in that cycle's
    # remaining processing failing.
    daily_summary_calls = {"n": 0}

    def flaky_send_daily_summary(webhook_url, **kwargs):
        daily_summary_calls["n"] += 1
        if daily_summary_calls["n"] == 1:
            return "999"
        raise RuntimeError("boom in daily summary")

    monkeypatch.setattr("live_engine.send_daily_summary", flaky_send_daily_summary)

    # Price crashes from 100 -> 85 (15% drop) and stays there, well past a
    # 10% threshold, then stays flat for a 3rd cycle -- same shape as the
    # existing "fires once" test, just with the daily-summary failure added.
    provider = SequenceProvider([_kline(0, "100"), _kline(300_000, "85"), _kline(600_000, "85")])
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    webhooks = DiscordWebhooks(daily_summary="https://webhook/summary", transactions="", alerts="https://webhook/alerts", logs="")
    failures = []

    run_polling_loop(
        conn, provider, engine, "BTCUSDT", "buy_hold", {"invest_at": "start"},
        poll_interval="5m", poll_interval_seconds=300,
        on_critical_failure=failures.append, initial_cash=Decimal("1000"),
        max_cycles=3, discord_webhooks=webhooks, drawdown_threshold_pct=Decimal("10"),
    )

    # Daily summary blew up on cycles 2 and 3 (both after the drawdown check
    # ran), confirming the failure really was injected where the bug needs it.
    assert len(failures) == 2
    # The drawdown alert must still fire exactly once across all cycles, not
    # once per cycle -- proving the "already alerted" flag survived cycle 2's
    # rollback because it was committed before send_alert was ever called.
    assert len(alerts) == 1
    assert alerts[0] == ("drawdown", "warning")


def test_run_polling_loop_sends_daily_summary_once_past_8h_paris_then_edits_on_next_cycle(conn, monkeypatch):
    monkeypatch.setattr("live_engine.time.sleep", lambda s: None)
    # 2026-01-15 09:00 Europe/Paris (past the 8h threshold), in ms since epoch.
    now_paris = datetime(2026, 1, 15, 9, 0, tzinfo=ZoneInfo("Europe/Paris"))
    now_ms = int(now_paris.timestamp() * 1000)
    monkeypatch.setattr("live_engine.time.time", lambda: now_ms / 1000)

    calls = []

    def fake_send_daily_summary(webhook_url, **kwargs):
        calls.append(kwargs["existing_message_id"])
        return "999"

    monkeypatch.setattr("live_engine.send_daily_summary", fake_send_daily_summary)
    provider = SequenceProvider([_kline(0, "100"), _kline(300_000, "100")])
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    webhooks = DiscordWebhooks(daily_summary="https://webhook/summary", transactions="", alerts="", logs="")

    run_polling_loop(
        conn, provider, engine, "BTCUSDT", "buy_hold", {"invest_at": "start"},
        poll_interval="5m", poll_interval_seconds=300,
        on_critical_failure=lambda msg: None, initial_cash=Decimal("1000"),
        max_cycles=2, discord_webhooks=webhooks, drawdown_threshold_pct=Decimal("10"),
    )

    # First cycle: no message yet for today -> POST (existing_message_id=None).
    # Second cycle: a message id is now stored for today -> PATCH (existing_message_id="999").
    assert calls == [None, "999"]


def test_run_polling_loop_sends_no_daily_summary_before_8h_paris(conn, monkeypatch):
    monkeypatch.setattr("live_engine.time.sleep", lambda s: None)
    now_paris = datetime(2026, 1, 15, 7, 0, tzinfo=ZoneInfo("Europe/Paris"))
    now_ms = int(now_paris.timestamp() * 1000)
    monkeypatch.setattr("live_engine.time.time", lambda: now_ms / 1000)

    calls = []
    monkeypatch.setattr("live_engine.send_daily_summary", lambda webhook_url, **kwargs: calls.append(1) or "999")
    provider = SequenceProvider([_kline(0, "100")])
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    webhooks = DiscordWebhooks(daily_summary="https://webhook/summary", transactions="", alerts="", logs="")

    run_polling_loop(
        conn, provider, engine, "BTCUSDT", "buy_hold", {"invest_at": "start"},
        poll_interval="5m", poll_interval_seconds=300,
        on_critical_failure=lambda msg: None, initial_cash=Decimal("1000"),
        max_cycles=1, discord_webhooks=webhooks, drawdown_threshold_pct=Decimal("10"),
    )

    assert calls == []
