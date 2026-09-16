from decimal import Decimal

import httpx

from live_engine import fetch_with_retry
from market_data.provider import MarketDataProvider
from market_data.types import Kline


class FlakyProvider(MarketDataProvider):
    def __init__(self, fail_count: int):
        self.fail_count = fail_count
        self.calls = 0

    def get_klines(self, symbol, interval, start_ms, end_ms, limit=1000):
        self.calls += 1
        if self.calls <= self.fail_count:
            raise httpx.HTTPError("boom")
        return [Kline(open_time_ms=0, open=Decimal("1"), high=Decimal("1"),
                       low=Decimal("1"), close=Decimal("1"), volume=Decimal("1"),
                       close_time_ms=299_999)]

    def get_book_ticker(self, symbol):
        raise NotImplementedError

    def get_ticker_24h(self, symbol):
        raise NotImplementedError


def test_fetch_with_retry_succeeds_after_one_failure(monkeypatch):
    sleeps = []
    monkeypatch.setattr("live_engine.time.sleep", lambda s: sleeps.append(s))
    provider = FlakyProvider(fail_count=1)
    alerts = []

    result = fetch_with_retry(provider, "BTCUSDT", "5m", on_critical_failure=alerts.append)

    assert len(result) == 1
    assert sleeps == [1]  # one retry, 1s backoff
    assert alerts == []


def test_fetch_with_retry_gives_up_after_three_failures_and_alerts(monkeypatch):
    sleeps = []
    monkeypatch.setattr("live_engine.time.sleep", lambda s: sleeps.append(s))
    provider = FlakyProvider(fail_count=10)  # always fails
    alerts = []

    result = fetch_with_retry(provider, "BTCUSDT", "5m", on_critical_failure=alerts.append)

    assert result == []
    assert len(alerts) == 1
    assert "BTCUSDT" in alerts[0]
    assert provider.calls == 3  # exactly 3 attempts, no more


def test_fetch_with_retry_no_alert_when_never_fails(monkeypatch):
    monkeypatch.setattr("live_engine.time.sleep", lambda s: None)
    provider = FlakyProvider(fail_count=0)
    alerts = []

    result = fetch_with_retry(provider, "BTCUSDT", "5m", on_critical_failure=alerts.append)

    assert len(result) == 1
    assert alerts == []
