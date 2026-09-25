"""Discord delivery must never stall trading.

Regression (2026-09-25, CT303): a 429 carrying Retry-After: 1992 made
_post_embed time.sleep() 33 minutes inside a pair-worker thread. The fixes
under test: an inline cap on how long _post_embed may wait, network-error
retries for the background sender (DNS not ready at boot), and a single
background sender thread so send_transaction/send_alert/send_log return
immediately in production.
"""
import logging
import threading
import time

import httpx
import pytest

import discord_notifier as dn
from logutil import RepeatFilter


class _Resp:
    def __init__(self, status_code=200, headers=None, payload=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._payload = payload if payload is not None else {"id": "42"}

    def raise_for_status(self):
        if self.status_code >= 400:
            req = httpx.Request("POST", "https://discord.com/api/webhooks/1/aaa")
            raise httpx.HTTPStatusError("err", request=req, response=self)

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _no_background_sender():
    yield
    dn.stop_background_delivery(timeout=2)


def test_huge_retry_after_is_not_slept_inline(monkeypatch):
    sleeps = []
    monkeypatch.setattr("discord_notifier.time.sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr("discord_notifier.httpx.post", lambda *a, **k: _Resp(429, {"Retry-After": "1992"}))

    assert dn._post_embed("https://discord.com/api/webhooks/1/aaa", {"title": "t"}) is None
    assert sleeps == []


def test_small_retry_after_still_retried_once(monkeypatch):
    sleeps = []
    responses = iter([_Resp(429, {"Retry-After": "2"}), _Resp(200)])
    monkeypatch.setattr("discord_notifier.time.sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr("discord_notifier.httpx.post", lambda *a, **k: next(responses))

    assert dn._post_embed("https://discord.com/api/webhooks/1/aaa", {"title": "t"}) == "42"
    assert sleeps == [2.0]


def test_network_error_retried_when_requested(monkeypatch):
    sleeps = []
    calls = {"n": 0}

    def flaky_post(*a, **k):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("[Errno -3] Temporary failure in name resolution")
        return _Resp(200)

    monkeypatch.setattr("discord_notifier.time.sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr("discord_notifier.httpx.post", flaky_post)

    assert dn._post_embed("https://discord.com/api/webhooks/1/aaa", {"title": "t"}, network_retries=4) == "42"
    assert calls["n"] == 3
    assert len(sleeps) == 2 and all(s > 0 for s in sleeps)


def test_network_error_not_retried_by_default(monkeypatch):
    calls = {"n": 0}

    def failing_post(*a, **k):
        calls["n"] += 1
        raise httpx.ConnectError("down")

    monkeypatch.setattr("discord_notifier.time.sleep", lambda s: None)
    monkeypatch.setattr("discord_notifier.httpx.post", failing_post)

    assert dn._post_embed("https://discord.com/api/webhooks/1/aaa", {"title": "t"}) is None
    assert calls["n"] == 1


def test_background_delivery_returns_immediately_and_flushes_on_stop(monkeypatch):
    delivered = []
    gate = threading.Event()

    def slow_post(url, embed, **kwargs):
        gate.wait(2)
        delivered.append((url, embed["embeds"][0]["description"]))
        return "1"

    monkeypatch.setattr("discord_notifier._post_embed", slow_post)
    dn.start_background_delivery(min_interval_s=0)

    t0 = time.monotonic()
    dn.send_log("https://webhook/logs", "demarre", level="INFO")
    assert time.monotonic() - t0 < 0.5  # never waits for Discord

    gate.set()
    dn.stop_background_delivery(timeout=5)
    assert delivered == [("https://webhook/logs", "demarre")]


def test_background_sender_paces_messages_to_the_same_webhook(monkeypatch):
    stamps = []
    monkeypatch.setattr("discord_notifier._post_embed", lambda url, embed, **k: stamps.append(time.monotonic()) or "1")
    dn.start_background_delivery(min_interval_s=0.2)

    for i in range(3):
        dn.send_log("https://webhook/logs", f"m{i}", level="INFO")
    dn.stop_background_delivery(timeout=5)

    assert len(stamps) == 3
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert all(g >= 0.18 for g in gaps)


def test_without_background_sender_sends_synchronously(monkeypatch):
    sent = []
    monkeypatch.setattr("discord_notifier._post_embed", lambda url, embed: sent.append(url))

    dn.send_log("https://webhook/logs", "x", level="INFO")
    assert sent == ["https://webhook/logs"]


def test_repeat_filter_masks_identical_rejections_then_reports_count():
    records = []

    class _Keep(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    log = logging.getLogger("test.repeat")
    log.handlers[:] = [_Keep()]
    log.propagate = False
    clock = {"t": 0.0}
    log.filters[:] = [RepeatFilter(window_s=100, clock=lambda: clock["t"])]

    for _ in range(5):
        log.warning("BUY rejete: cash insuffisant symbol=%s cash_balance=%s", "ZECUSDT", "14.7")
    log.warning("BUY rejete: cash insuffisant symbol=%s cash_balance=%s", "ETHUSDT", "3.1")
    clock["t"] = 150
    log.warning("BUY rejete: cash insuffisant symbol=%s cash_balance=%s", "ZECUSDT", "14.7")

    assert records[0].startswith("BUY rejete: cash insuffisant symbol=ZECUSDT")
    assert records[1].startswith("BUY rejete: cash insuffisant symbol=ETHUSDT")
    assert "4 repetition(s) masquee(s)" in records[2]
    assert len(records) == 3
