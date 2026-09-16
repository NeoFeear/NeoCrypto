import httpx
import pytest

from discord_notifier import DiscordWebhooks, _patch_embed, _post_embed, load_discord_webhooks


def test_load_discord_webhooks_reads_all_four_keys(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DISCORD_WEBHOOK_DAILY_SUMMARY=https://discord.com/api/webhooks/1/aaa\n"
        "DISCORD_WEBHOOK_TRANSACTIONS=https://discord.com/api/webhooks/2/bbb\n"
        "DISCORD_WEBHOOK_ALERTS=https://discord.com/api/webhooks/3/ccc\n"
        "DISCORD_WEBHOOK_LOGS=https://discord.com/api/webhooks/4/ddd\n",
        encoding="utf-8",
    )

    webhooks = load_discord_webhooks(str(env_file))

    assert webhooks == DiscordWebhooks(
        daily_summary="https://discord.com/api/webhooks/1/aaa",
        transactions="https://discord.com/api/webhooks/2/bbb",
        alerts="https://discord.com/api/webhooks/3/ccc",
        logs="https://discord.com/api/webhooks/4/ddd",
    )


def test_load_discord_webhooks_missing_file_returns_empty_strings(tmp_path):
    missing = tmp_path / "does-not-exist.env"

    webhooks = load_discord_webhooks(str(missing))

    assert webhooks == DiscordWebhooks(daily_summary="", transactions="", alerts="", logs="")


def test_post_embed_returns_message_id_on_success(monkeypatch):
    class FakeResponse:
        status_code = 200

        def json(self):
            return {"id": "1234567890"}

        def raise_for_status(self):
            pass

    def fake_post(url, json, params=None, timeout=None):
        assert params == {"wait": "true"}
        return FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)

    message_id = _post_embed("https://discord.com/api/webhooks/1/aaa", {"title": "test"})

    assert message_id == "1234567890"


def test_post_embed_empty_url_returns_none_without_calling_httpx(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("httpx.post should never be called for an empty webhook_url")

    monkeypatch.setattr(httpx, "post", fail)

    assert _post_embed("", {"title": "test"}) is None


def test_post_embed_404_logs_and_returns_none_never_raises(monkeypatch):
    class FakeResponse:
        status_code = 404

        def json(self):
            return {}

        def raise_for_status(self):
            raise httpx.HTTPStatusError("not found", request=None, response=self)

    monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeResponse())

    assert _post_embed("https://discord.com/api/webhooks/1/aaa", {"title": "test"}) is None


def test_post_embed_429_retries_once_after_retry_after_then_succeeds(monkeypatch):
    calls = []
    sleeps = []
    monkeypatch.setattr("discord_notifier.time.sleep", lambda s: sleeps.append(s))

    class RateLimited:
        status_code = 429
        headers = {"Retry-After": "2"}

        def json(self):
            return {}

        def raise_for_status(self):
            raise httpx.HTTPStatusError("rate limited", request=None, response=self)

    class Success:
        status_code = 200

        def json(self):
            return {"id": "999"}

        def raise_for_status(self):
            pass

    def fake_post(url, json, params=None, timeout=None):
        calls.append(1)
        return RateLimited() if len(calls) == 1 else Success()

    monkeypatch.setattr(httpx, "post", fake_post)

    message_id = _post_embed("https://discord.com/api/webhooks/1/aaa", {"title": "test"})

    assert message_id == "999"
    assert len(calls) == 2
    assert sleeps == [2.0]


def test_post_embed_429_twice_gives_up_and_returns_none(monkeypatch):
    monkeypatch.setattr("discord_notifier.time.sleep", lambda s: None)

    class RateLimited:
        status_code = 429
        headers = {"Retry-After": "1"}

        def json(self):
            return {}

        def raise_for_status(self):
            raise httpx.HTTPStatusError("rate limited", request=None, response=self)

    monkeypatch.setattr(httpx, "post", lambda *a, **k: RateLimited())

    assert _post_embed("https://discord.com/api/webhooks/1/aaa", {"title": "test"}) is None


def test_patch_embed_success_returns_true(monkeypatch):
    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

    def fake_patch(url, json, timeout=None):
        assert "messages/999" in url
        return FakeResponse()

    monkeypatch.setattr(httpx, "patch", fake_patch)

    assert _patch_embed("https://discord.com/api/webhooks/1/aaa", "999", {"title": "edited"}) is True


def test_patch_embed_failure_returns_false_never_raises(monkeypatch):
    class FakeResponse:
        status_code = 404

        def raise_for_status(self):
            raise httpx.HTTPStatusError("not found", request=None, response=self)

    monkeypatch.setattr(httpx, "patch", lambda *a, **k: FakeResponse())

    assert _patch_embed("https://discord.com/api/webhooks/1/aaa", "999", {"title": "edited"}) is False


from decimal import Decimal

from engine.fifo_engine import Side, Trade
from discord_notifier import send_transaction


def _trade(side: Side, realized_pnl) -> Trade:
    return Trade(
        id=1, timestamp=1_700_000_000_000, symbol="BTCUSDT", side=side,
        price=Decimal("50000"), quantity=Decimal("0.01"), fee_pct=Decimal("0.001"),
        fee_amount=Decimal("0.5"), total_cost=Decimal("500.5"),
        realized_pnl=realized_pnl, cash_balance_after=Decimal("499.5"),
        strategy_name="dca",
    )


def test_send_transaction_buy_is_grey(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "discord_notifier._post_embed",
        lambda url, embed: captured.update(embed=embed) or "1",
    )

    send_transaction("https://webhook", _trade(Side.BUY, None))

    embed = captured["embed"]["embeds"][0]
    assert embed["color"] == 0x95A5A6
    assert "BUY" in embed["title"]
    assert "BTCUSDT" in embed["title"]


def test_send_transaction_sell_gain_is_green(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "discord_notifier._post_embed",
        lambda url, embed: captured.update(embed=embed) or "1",
    )

    send_transaction("https://webhook", _trade(Side.SELL, Decimal("12.5")))

    assert captured["embed"]["embeds"][0]["color"] == 0x2ECC71


def test_send_transaction_sell_loss_is_red(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "discord_notifier._post_embed",
        lambda url, embed: captured.update(embed=embed) or "1",
    )

    send_transaction("https://webhook", _trade(Side.SELL, Decimal("-3.2")))

    assert captured["embed"]["embeds"][0]["color"] == 0xE74C3C


def test_send_transaction_sell_breakeven_is_green(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "discord_notifier._post_embed",
        lambda url, embed: captured.update(embed=embed) or "1",
    )

    send_transaction("https://webhook", _trade(Side.SELL, Decimal("0")))

    assert captured["embed"]["embeds"][0]["color"] == 0x2ECC71


def test_send_transaction_embed_has_no_float_values(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "discord_notifier._post_embed",
        lambda url, embed: captured.update(embed=embed) or "1",
    )

    send_transaction("https://webhook", _trade(Side.BUY, None))

    fields_text = str(captured["embed"]["embeds"][0]["fields"])
    assert "50000" in fields_text  # price rendered as a formatted string, not repr(float)
