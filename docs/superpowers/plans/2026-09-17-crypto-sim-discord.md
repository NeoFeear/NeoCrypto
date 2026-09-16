# Discord Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the live engine (built in Plan 3) a standalone Discord notifier — real trade confirmations, a daily portfolio summary, threshold-based alerts, and service start/stop logs — posted via raw webhook HTTP calls, with strict anti-spam rules and zero crash risk to the trading process.

**Architecture:** `discord_notifier.py` is a self-contained module with no dependency on the trading engine — it only knows how to POST/PATCH Discord embeds given a webhook URL and plain data. `live_engine.py` is the only caller, and only ever notifies about state that is already durably committed (never before `conn.commit()` succeeds) — a notification must never announce something that later gets rolled back. Webhook URLs come from `.env` via `python-dotenv`, loaded once at `main()` startup into a small config object threaded through the same way `MarketDataProvider` already is.

**Tech Stack:** `httpx` (already a dependency, used directly — no `discord.py`, no bot gateway), `python-dotenv` (new dependency, added in Task 1), stdlib `zoneinfo` for the Europe/Paris daily-summary timing.

**Spec:** `docs/superpowers/specs/2026-09-15-crypto-paper-trading-sim-design.md` (section 13 "Discord", section 2 non-negotiables, section 11 live engine integration points)

## Global Constraints

- All price/quantity/PnL fields stay `Decimal` end-to-end; embeds render `Decimal` values as formatted strings, never coerce through `float`.
- Webhook URLs come from `.env`, never committed, never hardcoded, never asked for in conversation (already filled in by Florian — `.env`'s 4 keys are `DISCORD_WEBHOOK_DAILY_SUMMARY`, `DISCORD_WEBHOOK_TRANSACTIONS`, `DISCORD_WEBHOOK_ALERTS`, `DISCORD_WEBHOOK_LOGS`, see `.env.example`).
- Thresholds (drawdown %) live in `config.yaml`, never hardcoded in code.
- Webhook errors (404, 429) are logged locally and **never** crash the calling process. On 429: exactly 1 retry, delay from the `Retry-After` header.
- `send_transaction` fires only for a trade that **actually executed** — never for a no-op cycle, never for a rejected order (that's technical-log territory, not this plan's job to add).
- `send_daily_summary` sends exactly one message per calendar day (8h Europe/Paris), and edits (PATCH) it if a message already exists for that day — never duplicates.
- `send_alert` fires only on real thresholds (drawdown > configured %, unexpected service stop, 3 consecutive API failures) — no periodic heartbeat, no notification for a healthy system.
- `send_log` fires only on service start/stop — nothing else.
- A cycle's Discord notification for a trade must only be sent **after** that trade is durably committed to the DB (after `run_cycle`'s `conn.commit()` succeeds), never before — otherwise a rolled-back cycle (see Plan 3's atomicity fix) would announce a trade that never actually happened.

---

### Task 1: Webhook config loading + low-level HTTP helper

**Files:**
- Create: `discord_notifier.py`
- Test: `tests/test_discord_notifier.py`
- Modify: `requirements.txt`

**Interfaces:**
- Consumes: nothing from earlier plans.
- Produces: `DiscordWebhooks` (frozen dataclass: `daily_summary: str`, `transactions: str`, `alerts: str`, `logs: str`), `load_discord_webhooks(env_path: str = ".env") -> DiscordWebhooks`, `_post_embed(webhook_url: str, embed: dict) -> str | None` (returns the created message's id, or `None` if the send failed or `webhook_url` is empty), `_patch_embed(webhook_url: str, message_id: str, embed: dict) -> bool` (returns whether the edit succeeded).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_discord_notifier.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_discord_notifier.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'discord_notifier'`

- [ ] **Step 3: Write `discord_notifier.py`**

```python
# discord_notifier.py
import logging
import time
from dataclasses import dataclass

import httpx
from dotenv import dotenv_values

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class DiscordWebhooks:
    daily_summary: str
    transactions: str
    alerts: str
    logs: str


def load_discord_webhooks(env_path: str = ".env") -> DiscordWebhooks:
    """Reads the 4 webhook URLs from .env (never committed, see .gitignore).
    A missing file or missing key resolves to "" -- callers must treat an
    empty webhook_url as "skip, do not send" (see _post_embed)."""
    values = dotenv_values(env_path)
    return DiscordWebhooks(
        daily_summary=values.get("DISCORD_WEBHOOK_DAILY_SUMMARY") or "",
        transactions=values.get("DISCORD_WEBHOOK_TRANSACTIONS") or "",
        alerts=values.get("DISCORD_WEBHOOK_ALERTS") or "",
        logs=values.get("DISCORD_WEBHOOK_LOGS") or "",
    )


def _post_embed(webhook_url: str, embed: dict) -> str | None:
    """POSTs one embed to a Discord webhook. Returns the created message's id
    (needed by send_daily_summary to PATCH it later), or None if webhook_url
    is empty or the send failed for any reason. Spec section 7: webhook
    errors (404, 429) are logged locally and must never crash the caller.
    On 429: exactly 1 retry, delay from the Retry-After header."""
    if not webhook_url:
        logger.debug("Webhook Discord non configure, envoi ignore.")
        return None

    for attempt in range(2):
        try:
            response = httpx.post(webhook_url, json=embed, params={"wait": "true"}, timeout=_TIMEOUT_SECONDS)
            response.raise_for_status()
            return response.json()["id"]
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if status == 429 and attempt == 0:
                retry_after = float(e.response.headers.get("Retry-After", "1"))
                logger.warning("Discord 429, retry dans %ss.", retry_after)
                time.sleep(retry_after)
                continue
            logger.warning("Envoi Discord echoue (HTTP %s): %s", status, e)
            return None
        except httpx.HTTPError as e:
            logger.warning("Envoi Discord echoue: %s", e)
            return None
    return None


def _patch_embed(webhook_url: str, message_id: str, embed: dict) -> bool:
    """Edits an existing webhook message (used by send_daily_summary so the
    daily summary is never duplicated). Returns whether the edit succeeded;
    never raises."""
    if not webhook_url:
        return False
    try:
        response = httpx.patch(f"{webhook_url}/messages/{message_id}", json=embed, timeout=_TIMEOUT_SECONDS)
        response.raise_for_status()
        return True
    except httpx.HTTPError as e:
        logger.warning("Edition Discord echouee: %s", e)
        return False
```

- [ ] **Step 4: Add `python-dotenv` to `requirements.txt`**

```
httpx>=0.27
PyYAML>=6.0
pytest>=8.0
python-dotenv>=1.0
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_discord_notifier.py -v`
Expected: PASS (9 passed). Then run `pip install -r requirements.txt` if `python-dotenv` isn't already installed in the environment.

- [ ] **Step 6: Commit**

```bash
git add discord_notifier.py tests/test_discord_notifier.py requirements.txt
git commit -m "feat: add Discord webhook config loading and low-level embed HTTP helper"
```

---

### Task 2: `send_transaction` — one embed per real trade

**Files:**
- Modify: `discord_notifier.py`
- Test: `tests/test_discord_notifier.py`

**Interfaces:**
- Consumes: `_post_embed` (Task 1), `engine.fifo_engine.Trade`/`Side` (Plan 1).
- Produces: `send_transaction(webhook_url: str, trade: Trade) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# Append to tests/test_discord_notifier.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_discord_notifier.py -k test_send_transaction -v`
Expected: FAIL with `ImportError: cannot import name 'send_transaction'`

- [ ] **Step 3: Implement `send_transaction`**

Append to `discord_notifier.py` (add `from engine.fifo_engine import Side, Trade` to the imports at the top):

```python
_COLOR_BUY = 0x95A5A6
_COLOR_GAIN = 0x2ECC71
_COLOR_LOSS = 0xE74C3C


def send_transaction(webhook_url: str, trade: Trade) -> None:
    """Spec section 7: 1 embed per trade that actually executed -- never for
    a no-op cycle, never for a rejected order. Grey for a BUY, green for a
    SELL at gain or breakeven, red for a SELL at a loss."""
    if trade.side == Side.BUY:
        color = _COLOR_BUY
    else:
        color = _COLOR_GAIN if trade.realized_pnl is not None and trade.realized_pnl >= 0 else _COLOR_LOSS

    fields = [
        {"name": "Symbole", "value": trade.symbol, "inline": True},
        {"name": "Prix", "value": str(trade.price), "inline": True},
        {"name": "Quantite", "value": str(trade.quantity), "inline": True},
        {"name": "Frais", "value": str(trade.fee_amount), "inline": True},
        {"name": "Solde apres", "value": str(trade.cash_balance_after), "inline": True},
        {"name": "Strategie", "value": trade.strategy_name, "inline": True},
    ]
    if trade.realized_pnl is not None:
        fields.append({"name": "PnL realise", "value": str(trade.realized_pnl), "inline": True})

    embed = {
        "title": f"{trade.side.value} {trade.symbol}",
        "color": color,
        "fields": fields,
        "timestamp": None,
    }
    _post_embed(webhook_url, {"embeds": [embed]})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_discord_notifier.py -v`
Expected: PASS (14 passed)

- [ ] **Step 5: Commit**

```bash
git add discord_notifier.py tests/test_discord_notifier.py
git commit -m "feat: add send_transaction with grey/green/red color coding"
```

---

### Task 3: `send_alert` and `send_log`

**Files:**
- Modify: `discord_notifier.py`
- Test: `tests/test_discord_notifier.py`

**Interfaces:**
- Consumes: `_post_embed` (Task 1).
- Produces: `send_alert(webhook_url: str, alert_type: str, message: str, severity: str) -> None`, `send_log(webhook_url: str, message: str, level: str) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# Append to tests/test_discord_notifier.py
from discord_notifier import send_alert, send_log


def test_send_alert_critical_is_red(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "discord_notifier._post_embed",
        lambda url, embed: captured.update(embed=embed) or "1",
    )

    send_alert("https://webhook", "api_error", "3 echecs consecutifs pour BTCUSDT", severity="critical")

    embed = captured["embed"]["embeds"][0]
    assert embed["color"] == 0xE74C3C
    assert "api_error" in embed["title"]
    assert "3 echecs consecutifs" in embed["description"]


def test_send_alert_warning_is_orange(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "discord_notifier._post_embed",
        lambda url, embed: captured.update(embed=embed) or "1",
    )

    send_alert("https://webhook", "drawdown", "Drawdown de 12.3% pour BTCUSDT", severity="warning")

    assert captured["embed"]["embeds"][0]["color"] == 0xE67E22


def test_send_log_sends_plain_message(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "discord_notifier._post_embed",
        lambda url, embed: captured.update(embed=embed) or "1",
    )

    send_log("https://webhook", "Moteur live demarre pour BTCUSDT/dca", level="INFO")

    embed = captured["embed"]["embeds"][0]
    assert "Moteur live demarre" in embed["description"]
    assert "INFO" in embed["title"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_discord_notifier.py -k "test_send_alert or test_send_log" -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement `send_alert` and `send_log`**

Append to `discord_notifier.py`:

```python
_COLOR_CRITICAL = 0xE74C3C
_COLOR_WARNING = 0xE67E22
_COLOR_LOG = 0x3498DB

_ALERT_COLORS = {"critical": _COLOR_CRITICAL, "warning": _COLOR_WARNING}


def send_alert(webhook_url: str, alert_type: str, message: str, severity: str) -> None:
    """Spec section 7: only for a real threshold crossing (drawdown > seuil
    configure, service arrete de facon inattendue, 3 echecs API consecutifs)
    -- never a periodic heartbeat."""
    embed = {
        "title": f"Alerte: {alert_type}",
        "description": message,
        "color": _ALERT_COLORS.get(severity, _COLOR_WARNING),
    }
    _post_embed(webhook_url, {"embeds": [embed]})


def send_log(webhook_url: str, message: str, level: str) -> None:
    """Spec section 7: only for service start/stop -- no other runtime
    logging goes to Discord."""
    embed = {
        "title": f"Log {level}",
        "description": message,
        "color": _COLOR_LOG,
    }
    _post_embed(webhook_url, {"embeds": [embed]})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_discord_notifier.py -v`
Expected: PASS (17 passed)

- [ ] **Step 5: Commit**

```bash
git add discord_notifier.py tests/test_discord_notifier.py
git commit -m "feat: add send_alert and send_log"
```

---

### Task 4: `send_daily_summary` — post-or-edit, never duplicated

**Files:**
- Modify: `discord_notifier.py`
- Test: `tests/test_discord_notifier.py`

**Interfaces:**
- Consumes: `_post_embed`, `_patch_embed` (Task 1).
- Produces: `send_daily_summary(webhook_url: str, symbol: str, total_value: Decimal, realized_pnl_cumule: Decimal, unrealized_pnl: Decimal, return_pct: Decimal, existing_message_id: str | None) -> str | None` — returns the message id to persist (same id if edited, a new id if posted, `None` if the send/edit failed).

- [ ] **Step 1: Write the failing tests**

```python
# Append to tests/test_discord_notifier.py
from discord_notifier import send_daily_summary


def test_send_daily_summary_posts_when_no_existing_message(monkeypatch):
    monkeypatch.setattr("discord_notifier._post_embed", lambda url, embed: "555")

    message_id = send_daily_summary(
        "https://webhook", symbol="BTCUSDT", total_value=Decimal("1050.25"),
        realized_pnl_cumule=Decimal("30.10"), unrealized_pnl=Decimal("20.15"),
        return_pct=Decimal("5.025"), existing_message_id=None,
    )

    assert message_id == "555"


def test_send_daily_summary_patches_when_existing_message(monkeypatch):
    patch_calls = []
    monkeypatch.setattr(
        "discord_notifier._post_embed",
        lambda url, embed: (_ for _ in ()).throw(AssertionError("should PATCH, not POST")),
    )
    monkeypatch.setattr(
        "discord_notifier._patch_embed",
        lambda url, message_id, embed: patch_calls.append(message_id) or True,
    )

    message_id = send_daily_summary(
        "https://webhook", symbol="BTCUSDT", total_value=Decimal("1050.25"),
        realized_pnl_cumule=Decimal("30.10"), unrealized_pnl=Decimal("20.15"),
        return_pct=Decimal("5.025"), existing_message_id="123",
    )

    assert message_id == "123"
    assert patch_calls == ["123"]


def test_send_daily_summary_patch_failure_returns_none(monkeypatch):
    monkeypatch.setattr("discord_notifier._patch_embed", lambda url, message_id, embed: False)

    message_id = send_daily_summary(
        "https://webhook", symbol="BTCUSDT", total_value=Decimal("1050.25"),
        realized_pnl_cumule=Decimal("30.10"), unrealized_pnl=Decimal("20.15"),
        return_pct=Decimal("5.025"), existing_message_id="123",
    )

    assert message_id is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_discord_notifier.py -k test_send_daily_summary -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement `send_daily_summary`**

Append to `discord_notifier.py` (add `from decimal import Decimal` to the imports at the top):

```python
def send_daily_summary(
    webhook_url: str, symbol: str, total_value: Decimal, realized_pnl_cumule: Decimal,
    unrealized_pnl: Decimal, return_pct: Decimal, existing_message_id: str | None,
) -> str | None:
    """Spec section 7: exactly 1 message per day, edited (PATCH) if it
    already exists for today rather than duplicated."""
    embed = {
        "title": f"Resume quotidien -- {symbol}",
        "color": _COLOR_LOG,
        "fields": [
            {"name": "Valeur totale", "value": str(total_value), "inline": True},
            {"name": "PnL realise cumule", "value": str(realized_pnl_cumule), "inline": True},
            {"name": "PnL latent", "value": str(unrealized_pnl), "inline": True},
            {"name": "Rendement", "value": f"{return_pct}%", "inline": True},
        ],
    }
    if existing_message_id is None:
        return _post_embed(webhook_url, {"embeds": [embed]})
    return existing_message_id if _patch_embed(webhook_url, existing_message_id, {"embeds": [embed]}) else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_discord_notifier.py -v`
Expected: PASS (20 passed)

- [ ] **Step 5: Commit**

```bash
git add discord_notifier.py tests/test_discord_notifier.py
git commit -m "feat: add send_daily_summary with post-or-edit idempotency"
```

---

### Task 5: `test_notifier.py` — manual visual-validation script

**Files:**
- Create: `test_notifier.py`

**Interfaces:**
- Consumes: everything from Tasks 1-4.
- Produces: a `main()` entry point, not covered by pytest (it deliberately sends real messages to real webhooks so Florian can visually confirm each embed looks right before the live engine ever runs) — same "not automated, makes real calls" precedent as `backtest.py`/`live_engine.py`'s own `main()`.

- [ ] **Step 1: Write `test_notifier.py`**

```python
# test_notifier.py
"""Sends one example of each Discord notification type with fake data, for
visual validation independent of the trading engine. Run manually after
filling in .env, before starting live_engine.py for the first time:

    python test_notifier.py
"""
from decimal import Decimal

from discord_notifier import load_discord_webhooks, send_alert, send_daily_summary, send_log, send_transaction
from engine.fifo_engine import Side, Trade


def main() -> None:
    webhooks = load_discord_webhooks()

    print("Envoi d'un exemple de transaction (BUY)...")
    send_transaction(webhooks.transactions, Trade(
        id=1, timestamp=1_700_000_000_000, symbol="BTCUSDT", side=Side.BUY,
        price=Decimal("50000"), quantity=Decimal("0.01"), fee_pct=Decimal("0.001"),
        fee_amount=Decimal("0.5"), total_cost=Decimal("500.5"), realized_pnl=None,
        cash_balance_after=Decimal("499.5"), strategy_name="dca",
    ))

    print("Envoi d'un exemple de transaction (SELL, gain)...")
    send_transaction(webhooks.transactions, Trade(
        id=2, timestamp=1_700_000_300_000, symbol="BTCUSDT", side=Side.SELL,
        price=Decimal("52000"), quantity=Decimal("0.01"), fee_pct=Decimal("0.001"),
        fee_amount=Decimal("0.52"), total_cost=Decimal("519.48"), realized_pnl=Decimal("18.98"),
        cash_balance_after=Decimal("1019"), strategy_name="dca",
    ))

    print("Envoi d'un exemple de resume quotidien...")
    send_daily_summary(
        webhooks.daily_summary, symbol="BTCUSDT", total_value=Decimal("1050.25"),
        realized_pnl_cumule=Decimal("30.10"), unrealized_pnl=Decimal("20.15"),
        return_pct=Decimal("5.025"), existing_message_id=None,
    )

    print("Envoi d'un exemple d'alerte critique...")
    send_alert(webhooks.alerts, "api_error", "Exemple: 3 echecs API consecutifs pour BTCUSDT", severity="critical")

    print("Envoi d'un exemple de log technique...")
    send_log(webhooks.logs, "Exemple: moteur live demarre pour BTCUSDT/dca", level="INFO")

    print("Termine. Verifie les 4 salons Discord.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify it's syntactically valid (no network call needed for this check)**

Run: `python -c "import ast; ast.parse(open('test_notifier.py').read())"`
Expected: no output (no `SyntaxError`)

- [ ] **Step 3: Commit**

```bash
git add test_notifier.py
git commit -m "feat: add test_notifier.py manual visual-validation script"
```

---

### Task 6: `run_cycle` returns its committed trades; `send_transaction` wired in after commit

**Files:**
- Modify: `live_engine.py`
- Test: `tests/test_live_engine_cycle.py`, `tests/test_live_engine_loop.py`

**Interfaces:**
- Consumes: `send_transaction` (Task 2), `DiscordWebhooks` (Task 1).
- Produces: `run_cycle(...) -> list[Trade]` (signature changes from `-> None`), `run_polling_loop(..., discord_webhooks: DiscordWebhooks, ...)`.

`run_cycle`'s current final lines (`live_engine.py`, right before `conn.commit()`) already compute the newly-created trades in the loop `for trade in engine.trades[trades_before:]:`. Capture that list explicitly and return it — this must happen **after** `conn.commit()` succeeds, since a cycle that raises before the commit must never report any trades to the caller (the caller only sends Discord notifications for what `run_cycle` returns, and a raised exception means nothing is returned at all).

- [ ] **Step 1: Write the failing tests**

Add `from discord_notifier import DiscordWebhooks` to the imports at the top of `tests/test_live_engine_loop.py` (it doesn't import anything from `discord_notifier` yet).

```python
# Append to tests/test_live_engine_cycle.py
def test_run_cycle_returns_the_trades_it_committed(conn):
    provider = FakeProvider([_kline(0, "100")])
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    params = {"amount_per_buy": 50, "frequency_hours": 24, "reference_price": "close"}

    committed = run_cycle(conn, provider, engine, "BTCUSDT", "dca", params, poll_interval="5m", trade_id_map={})

    assert len(committed) == 1
    assert committed[0].side.value == "BUY"


def test_run_cycle_returns_empty_list_when_no_trade_happens(conn):
    provider = FakeProvider([_kline(0, "100")])
    engine = FifoEngine(initial_cash=Decimal("1000"), fee_pct=Decimal("0.001"))
    # buy_hold with cash already at 0 -- engine.buy() rejects, no trade committed
    engine.cash_balance = Decimal("0")
    params = {"invest_at": "start"}

    committed = run_cycle(conn, provider, engine, "BTCUSDT", "buy_hold", params, poll_interval="5m", trade_id_map={})

    assert committed == []
```

```python
# Append to tests/test_live_engine_loop.py
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
        max_cycles=1, discord_webhooks=webhooks,
    )

    assert len(sent) == 1
    assert sent[0][0] == "https://webhook/tx"
    assert sent[0][1].side.value == "BUY"


def test_run_polling_loop_sends_no_discord_notification_when_cycle_produces_no_trade(conn, monkeypatch):
    monkeypatch.setattr("live_engine.time.sleep", lambda s: None)
    sent = []
    monkeypatch.setattr("live_engine.send_transaction", lambda webhook_url, trade: sent.append((webhook_url, trade)))
    provider = SequenceProvider([_kline(0, "100")])
    engine = FifoEngine(initial_cash=Decimal("0"), fee_pct=Decimal("0.001"))  # cash insufficient, buy rejected
    webhooks = DiscordWebhooks(daily_summary="", transactions="https://webhook/tx", alerts="", logs="")

    run_polling_loop(
        conn, provider, engine, "BTCUSDT", "buy_hold", {"invest_at": "start"},
        poll_interval="5m", poll_interval_seconds=300,
        on_critical_failure=lambda msg: None, initial_cash=Decimal("0"),
        max_cycles=1, discord_webhooks=webhooks,
    )

    assert sent == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_live_engine_cycle.py tests/test_live_engine_loop.py -v`
Expected: FAIL (`run_cycle` still returns `None`; `run_polling_loop` doesn't accept `discord_webhooks`)

- [ ] **Step 3: Modify `run_cycle` and `run_polling_loop` in `live_engine.py`**

Add to the imports at the top: `from discord_notifier import DiscordWebhooks, send_transaction`.

Change `run_cycle`'s return type and its final two lines:

```python
def run_cycle(
    conn: sqlite3.Connection,
    provider: MarketDataProvider,
    engine: FifoEngine,
    symbol: str,
    strategy_type: str,
    params: dict,
    poll_interval: str,
    trade_id_map: dict[int, int],
    fetch_fn=None,
) -> list[Trade]:
    klines = fetch_fn() if fetch_fn is not None else _fetch_latest_closed_kline(provider, symbol, poll_interval)
    if not klines:
        logger.debug("Aucune bougie recue pour %s, cycle ignore.", symbol)
        return []
    k = klines[-1]

    last_ts_raw = get_engine_state(conn, _last_processed_key(symbol, strategy_type))
    if last_ts_raw is not None and int(last_ts_raw) >= k.open_time_ms:
        logger.debug("Bougie deja traitee pour %s/%s (ts=%s), cycle ignore.", symbol, strategy_type, k.open_time_ms)
        return []

    trades_before = len(engine.trades)

    state = _load_state(conn, symbol, strategy_type, params)
    if strategy_type == "buy_hold":
        buy_hold_step(state, k, engine, symbol, params)
    elif strategy_type == "dca":
        dca_step(state, k, engine, symbol, params)
    elif strategy_type == "grid":
        step_grid_live(state, k.close, k.open_time_ms, engine, symbol, params)
    else:
        raise ValueError(f"strategie inconnue: {strategy_type}")

    new_trades = engine.trades[trades_before:]
    for trade in new_trades:
        db_trade_id = insert_trade(conn, trade, commit=False)
        trade_id_map[trade.id] = db_trade_id

    translated_lots = [
        Lot(
            id=lot.id, symbol=lot.symbol, quantity_restante=lot.quantity_restante,
            prix_achat=lot.prix_achat, timestamp_achat=lot.timestamp_achat,
            trade_id_achat=trade_id_map.get(lot.trade_id_achat, lot.trade_id_achat),
        )
        for lot in engine.get_lots(symbol)
    ]
    replace_lots_for_symbol(conn, symbol, translated_lots, commit=False)

    snapshot = strategy_base.build_snapshot(engine, symbol, k.close, k.open_time_ms)
    insert_snapshot(conn, snapshot, commit=False)

    _save_state(conn, symbol, strategy_type, state)
    set_engine_state(conn, _last_processed_key(symbol, strategy_type), str(k.open_time_ms), commit=False)

    conn.commit()
    # Only report trades as "happened" after the commit above succeeds --
    # returning new_trades before this point (or reporting them if commit()
    # were to raise) would let the caller announce a trade to Discord that
    # never actually became durable.
    return new_trades
```

Add `discord_webhooks: DiscordWebhooks` as a required parameter to `run_polling_loop` (position it right after `initial_cash: Decimal`, before the two defaulted params `max_cycles`/`retention_days`), and send notifications for whatever `run_cycle` returns, right after the call:

```python
def run_polling_loop(
    conn: sqlite3.Connection,
    provider: MarketDataProvider,
    engine: FifoEngine,
    symbol: str,
    strategy_type: str,
    params: dict,
    poll_interval: str,
    poll_interval_seconds: int,
    on_critical_failure: Callable[[str], None],
    initial_cash: Decimal,
    discord_webhooks: DiscordWebhooks,
    max_cycles: int | None = None,
    retention_days: int | None = None,
) -> None:
    trade_id_map: dict[int, int] = {}
    cycles = 0
    last_housekeeping_day: int | None = None
    while max_cycles is None or cycles < max_cycles:
        try:
            committed_trades = run_cycle(
                conn, provider, engine, symbol, strategy_type, params, poll_interval, trade_id_map,
                fetch_fn=lambda: fetch_with_retry(provider, symbol, poll_interval, on_critical_failure),
            )
            for trade in committed_trades:
                send_transaction(discord_webhooks.transactions, trade)

            if retention_days is not None:
                now_ms = int(time.time() * 1000)
                today = now_ms // DAY_MS
                if today != last_housekeeping_day:
                    aggregate_old_snapshots(conn, now_ms=now_ms, retention_days=retention_days)
                    last_housekeeping_day = today
        except Exception as e:
            logger.exception("Cycle en echec pour %s/%s, cycle ignore.", symbol, strategy_type)
            conn.rollback()
            engine = reconstruct_engine_from_db(conn, symbol, initial_cash, engine.fee_pct)
            trade_id_map.clear()
            on_critical_failure(f"Exception non geree pendant le cycle pour {symbol}: {e}")

        cycles += 1
        if max_cycles is None or cycles < max_cycles:
            time.sleep(poll_interval_seconds)
```

(The rest of `run_polling_loop`'s body — the `except` block's rollback/reconciliation logic — is unchanged from Plan 3; only the new `discord_webhooks` parameter and the `send_transaction` loop after a successful `run_cycle` call are new.)

Update `main()`'s call to `run_polling_loop(...)` — this will be finished in Task 7 alongside the rest of the Discord wiring in `main()`, so for this task just make the call pass a placeholder `discord_webhooks=DiscordWebhooks(daily_summary="", transactions="", alerts="", logs="")` (Task 7 replaces this with a real `load_discord_webhooks()` call).

**`discord_webhooks` is a required parameter (no default), same reasoning as `initial_cash` in Plan 3: an opt-in/defaulted Discord config would silently disable notifications for any future caller that forgets to pass it.** This breaks every EXISTING `run_polling_loop(...)` call site that predates this task — Plan 3 already left 7 of them in `tests/test_live_engine_loop.py` (grep the file for `run_polling_loop(` to find all 7: `test_run_polling_loop_stops_after_max_cycles_and_processes_new_candles`, `test_run_polling_loop_sleeps_between_cycles`, `test_run_polling_loop_requires_initial_cash_no_silent_gap`, `test_run_polling_loop_survives_unhandled_exception_and_continues_next_cycle`, `test_run_polling_loop_rolls_back_partial_cycle_so_signal_is_not_replayed`, `test_run_polling_loop_runs_daily_housekeeping_and_aggregates_old_snapshots`, `test_run_polling_loop_reconciles_in_memory_engine_with_db_after_mid_sell_rollback`). Add `discord_webhooks=DiscordWebhooks(daily_summary="", transactions="", alerts="", logs="")` as a keyword argument to every one of them (all-empty URLs are a safe no-op per Task 1's "empty webhook_url -> skip, do not send" design, so this cannot change any of those tests' existing behavior or assertions — it only satisfies the new required parameter). `test_run_polling_loop_requires_initial_cash_no_silent_gap` specifically tests that OMITTING `initial_cash` raises `TypeError` — leave that one's omission of `initial_cash` alone, just add `discord_webhooks=` to it like the rest, keeping the test's actual point (missing `initial_cash`) intact.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest -v` (full suite — confirm every one of the 7 pre-existing `run_polling_loop` calls above was updated and none raise `TypeError`; also confirms `run_cycle`'s return-type change didn't break Plan 3's existing tests, which call it but never asserted against the return value)
Expected: PASS, 0 failures

- [ ] **Step 5: Commit**

```bash
git add live_engine.py tests/test_live_engine_cycle.py tests/test_live_engine_loop.py
git commit -m "feat: run_cycle returns its committed trades, wire send_transaction into the polling loop"
```

---

### Task 7: Drawdown threshold alert + API-failure alert wiring

**Files:**
- Modify: `live_engine.py`, `config.py`, `config.yaml`
- Test: `tests/test_live_engine_loop.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: `send_alert` (Task 3), `engine_state` get/set (Plan 3).
- Produces: `DiscordConfig` (new dataclass on `Config`: `alert_drawdown_threshold_pct: Decimal`), a drawdown check inside `run_polling_loop`.

Add a new `discord:` section to `config.yaml` and a matching `DiscordConfig` to `config.py`, holding the one genuinely configurable Discord-related threshold (the spec's other Discord behaviors — daily summary hour, retry count, backoff — are fixed spec requirements, not tunable thresholds, and stay as code constants per the pattern already established in Plan 3 for `_RETRY_BACKOFF_SECONDS`).

- [ ] **Step 1: Write the failing test**

`tests/test_config.py` has exactly one test function, `test_load_config_parses_defaults`, which builds one big sample YAML string and asserts every field against it. Follow that exact pattern — do not add a second test function. Modify the existing test:

1. Add a `discord:` block to the sample YAML string (anywhere among the other top-level blocks, e.g. right after `snapshots:`):

```yaml
discord:
  alert_drawdown_threshold_pct: 10
```

2. Add one new assertion at the end of the existing assertion list:

```python
    assert cfg.discord.alert_drawdown_threshold_pct == Decimal("10")
```

```python
# Append to tests/test_live_engine_loop.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config.py tests/test_live_engine_loop.py -v -k "discord or drawdown"`
Expected: FAIL

- [ ] **Step 3: Add `DiscordConfig` to `config.py`, `discord:` to `config.yaml`**

In `config.py`, add:

```python
@dataclass(frozen=True)
class DiscordConfig:
    alert_drawdown_threshold_pct: Decimal
```

Add `discord: DiscordConfig` to the `Config` dataclass's fields, and in `load_config()`'s `Config(...)` construction add:

```python
        discord=DiscordConfig(
            alert_drawdown_threshold_pct=Decimal(str(raw["discord"]["alert_drawdown_threshold_pct"]))
        ),
```

In `config.yaml`, add a new top-level section:

```yaml
discord:
  alert_drawdown_threshold_pct: 10
```

(The sample YAML and assertion for this were already written in Step 1.)

- [ ] **Step 4: Add the drawdown check to `run_polling_loop`**

Add `drawdown_threshold_pct: Decimal` as a new parameter to `run_polling_loop`, with no default — every caller must supply it, same reasoning as `initial_cash`/`discord_webhooks`: an opt-in/defaulted drawdown check would silently disable itself for any future caller that forgets to pass it. **Position it right after `discord_webhooks: DiscordWebhooks`, BEFORE the two defaulted parameters `max_cycles: int | None = None` and `retention_days: int | None = None`** — Python requires every non-default parameter to precede any parameter with a default, so a parameter with no default cannot be appended after `retention_days` (that would be a `SyntaxError`). The signature's parameter order becomes: `..., initial_cash: Decimal, discord_webhooks: DiscordWebhooks, drawdown_threshold_pct: Decimal, max_cycles: int | None = None, retention_days: int | None = None`. Every caller in this plan already passes it by keyword, so this reordering doesn't affect any call site. Add `from discord_notifier import send_alert` to the imports (alongside the existing `send_transaction` import from Task 6).

Track the running peak via `engine_state` (key `f"portfolio_peak_value:{symbol}:{strategy_type}"`) and an edge-triggered "currently in drawdown" flag (key `f"drawdown_alert_active:{symbol}:{strategy_type}"`, storing `"1"` or `"0"`) so the alert fires once per crossing, not every cycle while still over threshold:

```python
def _check_drawdown_alert(
    conn: sqlite3.Connection, symbol: str, strategy_type: str, total_value: Decimal,
    threshold_pct: Decimal, webhook_url: str,
) -> None:
    peak_key = f"portfolio_peak_value:{symbol}:{strategy_type}"
    active_key = f"drawdown_alert_active:{symbol}:{strategy_type}"

    peak_raw = get_engine_state(conn, peak_key)
    peak = Decimal(peak_raw) if peak_raw is not None else total_value
    if total_value > peak:
        peak = total_value
    set_engine_state(conn, peak_key, str(peak), commit=False)

    if peak <= 0:
        return
    drawdown_pct = (peak - total_value) / peak * Decimal(100)
    was_active = get_engine_state(conn, active_key) == "1"

    if drawdown_pct > threshold_pct and not was_active:
        send_alert(webhook_url, "drawdown", f"Drawdown de {drawdown_pct:.2f}% pour {symbol}", severity="warning")
        set_engine_state(conn, active_key, "1", commit=False)
    elif drawdown_pct <= threshold_pct and was_active:
        set_engine_state(conn, active_key, "0", commit=False)
```

Call it inside `run_polling_loop`'s loop, right after the `for trade in committed_trades:` block, using the snapshot `run_cycle` just wrote -- read the latest snapshot's `total_value` back from the DB (simplest correct source, since `run_cycle` doesn't currently return the snapshot it built). This runs every cycle that reaches this point, whether or not a trade happened this cycle (a snapshot is written on every processed cycle, not only ones with a trade) -- it's naturally skipped when `run_cycle` itself skipped the whole cycle (no new candle, or already processed), since there is then no new snapshot and `latest_snapshot` just re-reads the previous one, a harmless no-op re-check:

```python
            latest_snapshot = conn.execute(
                "SELECT total_value FROM portfolio_snapshots WHERE symbol = ? ORDER BY id DESC LIMIT 1",
                (symbol,),
            ).fetchone()
            if latest_snapshot is not None:
                _check_drawdown_alert(
                    conn, symbol, strategy_type, Decimal(latest_snapshot["total_value"]),
                    drawdown_threshold_pct, discord_webhooks.alerts,
                )
                conn.commit()
```

Update `fetch_with_retry`'s existing `on_critical_failure` wiring (unchanged from Plan 3) — Task 9 handles connecting `main()`'s `on_critical_failure` callback to `send_alert` for the "3 consecutive API failures" case; this task only adds the drawdown check.

**`drawdown_threshold_pct` is required (no default) — same reasoning as `initial_cash`/`discord_webhooks`.** This breaks every `run_polling_loop(...)` call site that predates this task: the 7 already listed in Task 6 (`tests/test_live_engine_loop.py`'s pre-existing Plan 3 tests) plus the 2 Task 6 itself just added (`test_run_polling_loop_sends_discord_notification_for_each_committed_trade`, `test_run_polling_loop_sends_no_discord_notification_when_cycle_produces_no_trade`) — 9 total. Grep the file for `run_polling_loop(` to find all of them and add `drawdown_threshold_pct=Decimal("10")` as a keyword argument to every one (this task's own new test already includes it). None of those 9 tests' assertions depend on the drawdown check firing, so this cannot change any of their existing behavior — it only satisfies the new required parameter.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest -v`
Expected: PASS, 0 failures

- [ ] **Step 6: Commit**

```bash
git add live_engine.py config.py config.yaml tests/test_config.py tests/test_live_engine_loop.py
git commit -m "feat: add configurable drawdown alert threshold, edge-triggered to avoid repeat spam"
```

---

### Task 8: Daily summary scheduling (8h Europe/Paris, post-or-edit)

**Files:**
- Modify: `live_engine.py`
- Test: `tests/test_live_engine_loop.py`

**Interfaces:**
- Consumes: `send_daily_summary` (Task 4), `get_engine_state`/`set_engine_state` (Plan 3), `initial_cash`/`discord_webhooks` (already parameters of `run_polling_loop` from Plan 3 and Task 6 — no signature change needed).
- Produces: `_check_daily_summary(conn, symbol, strategy_type, webhook_url, now_ms, initial_cash) -> None`, called once per cycle inside `run_polling_loop`.

Design decision: once the clock passes 8h Europe/Paris, this check runs on every subsequent cycle for the rest of the day (not just the first time), calling `send_daily_summary` with whatever `existing_message_id` is stored for today's date — `None` on the first call of the day (causing a POST), the real id on every later call that day (causing a PATCH). This keeps the summary current throughout the day rather than freezing it at the 8h snapshot, satisfies "1 seul message/jour... jamais dupliqué" (the stored per-date message id guarantees exactly one message no matter how many times this fires), and exercises `send_daily_summary`'s PATCH path in real operation, not just in Task 4's isolated unit tests. Before 8h, the check is a no-op.

- [ ] **Step 1: Write the failing tests**

```python
# Append to tests/test_live_engine_loop.py
from datetime import datetime
from zoneinfo import ZoneInfo


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_live_engine_loop.py -k daily_summary -v`
Expected: FAIL (`_check_daily_summary` doesn't exist yet, so nothing calls the monkeypatched `send_daily_summary`)

- [ ] **Step 3: Implement `_check_daily_summary` and call it from `run_polling_loop`**

Add `from datetime import datetime` and `from zoneinfo import ZoneInfo` to the imports at the top of `live_engine.py`. Add `send_daily_summary` to the existing `from discord_notifier import ...` line.

```python
_PARIS_TZ = ZoneInfo("Europe/Paris")
_DAILY_SUMMARY_HOUR = 8


def _check_daily_summary(
    conn: sqlite3.Connection, symbol: str, strategy_type: str, webhook_url: str,
    now_ms: int, initial_cash: Decimal,
) -> None:
    now_paris = datetime.fromtimestamp(now_ms / 1000, tz=_PARIS_TZ)
    if now_paris.hour < _DAILY_SUMMARY_HOUR:
        return
    today_str = now_paris.date().isoformat()

    date_key = f"discord_daily_summary_date:{symbol}:{strategy_type}"
    message_id_key = f"discord_daily_summary_message_id:{symbol}:{strategy_type}"

    sent_date = get_engine_state(conn, date_key)
    existing_message_id = get_engine_state(conn, message_id_key) if sent_date == today_str else None

    latest_snapshot = conn.execute(
        "SELECT * FROM portfolio_snapshots WHERE symbol = ? ORDER BY id DESC LIMIT 1", (symbol,)
    ).fetchone()
    if latest_snapshot is None:
        return

    total_value = Decimal(latest_snapshot["total_value"])
    realized_pnl_cumule = Decimal(latest_snapshot["realized_pnl_cumule"])
    unrealized_pnl = Decimal(latest_snapshot["unrealized_pnl"])
    return_pct = ((total_value - initial_cash) / initial_cash * Decimal(100)) if initial_cash > 0 else Decimal(0)

    new_message_id = send_daily_summary(
        webhook_url, symbol=symbol, total_value=total_value, realized_pnl_cumule=realized_pnl_cumule,
        unrealized_pnl=unrealized_pnl, return_pct=return_pct, existing_message_id=existing_message_id,
    )
    if new_message_id is not None:
        set_engine_state(conn, date_key, today_str, commit=False)
        set_engine_state(conn, message_id_key, new_message_id, commit=False)
```

Call it inside `run_polling_loop`'s loop, in the same `if latest_snapshot is not None:` block Task 7 added the drawdown check to (right after `_check_drawdown_alert(...)`, before that block's `conn.commit()`) -- Task 7 left that block looking like this:

```python
            latest_snapshot = conn.execute(
                "SELECT total_value FROM portfolio_snapshots WHERE symbol = ? ORDER BY id DESC LIMIT 1",
                (symbol,),
            ).fetchone()
            if latest_snapshot is not None:
                _check_drawdown_alert(
                    conn, symbol, strategy_type, Decimal(latest_snapshot["total_value"]),
                    drawdown_threshold_pct, discord_webhooks.alerts,
                )
                conn.commit()
```

Insert the new call between `_check_drawdown_alert(...)` and `conn.commit()`, at the same indentation level:

```python
                _check_daily_summary(
                    conn, symbol, strategy_type, discord_webhooks.daily_summary,
                    int(time.time() * 1000), initial_cash,
                )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest -v`
Expected: PASS, 0 failures

- [ ] **Step 5: Commit**

```bash
git add live_engine.py tests/test_live_engine_loop.py
git commit -m "feat: schedule daily portfolio summary at 8h Europe/Paris, post-or-edit"
```

---

### Task 9: Wire `send_log`/`send_alert` into `main()` for start/stop/API-failure

**Files:**
- Modify: `live_engine.py`

**Interfaces:**
- Consumes: `send_log`, `send_alert`, `load_discord_webhooks` (Tasks 1-3).
- Produces: nothing new (wiring only) — `main()`'s Discord-related behavior.

`main()` is not covered by the automated suite (same precedent as `backtest.py`'s `main()` — real network calls, runs forever). This task changes it directly with no new tests; verify by reading the diff carefully rather than running it live (running it requires real webhook URLs and would post real Discord messages, which is not appropriate for an automated task).

- [ ] **Step 1: Rewrite `main()`**

```python
def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    provider = build_provider(cfg.data_source)
    conn = init_db(cfg.db_path)
    discord_webhooks = load_discord_webhooks()

    symbol = cfg.live.active_symbol
    strategy_type = cfg.live.active_strategy
    params = cfg.strategy_defaults[strategy_type]

    engine = reconstruct_engine_from_db(
        conn, symbol, initial_cash=cfg.backtest.initial_capital, fee_pct=cfg.fees.default_fee_pct
    )

    def on_critical_failure(message: str) -> None:
        logger.critical(message)
        send_alert(discord_webhooks.alerts, "api_error", message, severity="critical")

    logger.info("Demarrage du moteur live: %s / %s", symbol, strategy_type)
    send_log(discord_webhooks.logs, f"Moteur live demarre pour {symbol}/{strategy_type}.", level="INFO")

    try:
        run_polling_loop(
            conn, provider, engine, symbol, strategy_type, params,
            poll_interval=cfg.live.poll_kline_interval,
            poll_interval_seconds=cfg.live.poll_interval_seconds,
            on_critical_failure=on_critical_failure,
            retention_days=cfg.snapshots.retention_detail_days,
            initial_cash=cfg.backtest.initial_capital,
            discord_webhooks=discord_webhooks,
            drawdown_threshold_pct=cfg.discord.alert_drawdown_threshold_pct,
        )
    except KeyboardInterrupt:
        logger.info("Arret demande (Ctrl+C).")
        send_log(discord_webhooks.logs, f"Moteur live arrete (Ctrl+C) pour {symbol}/{strategy_type}.", level="INFO")
    except Exception as e:
        logger.exception("Arret inattendu du moteur live pour %s/%s.", symbol, strategy_type)
        send_alert(
            discord_webhooks.alerts, "service_down",
            f"Le moteur live pour {symbol}/{strategy_type} s'est arrete de facon inattendue: {e}",
            severity="critical",
        )
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
```

Add `send_alert, send_log, load_discord_webhooks` to the `from discord_notifier import ...` line at the top of the file (already importing `DiscordWebhooks, send_transaction` from Task 6).

Note the new top-level `except Exception` (between `KeyboardInterrupt` and `finally`): this is deliberately scoped to `main()` only, not inside `run_polling_loop` (which already has its own per-cycle exception handling from Plan 3 and must never let a single bad cycle propagate this far). This outer handler only ever fires for something `run_polling_loop` itself cannot recover from (e.g. the DB connection dying entirely) — spec section 7's "service arrete" alert trigger is for exactly this abnormal-termination case, distinct from `send_log`'s graceful-shutdown message on `KeyboardInterrupt`. It re-raises after alerting so systemd's `Restart=` (Plan 6) still applies.

- [ ] **Step 2: Run the full suite to confirm nothing else broke**

Run: `pytest -v`
Expected: PASS, 0 failures (this task touches only `main()`, which has no direct test coverage, but must not break any import or existing test)

- [ ] **Step 3: Commit**

```bash
git add live_engine.py
git commit -m "feat: wire send_log/send_alert into main() for start/stop/abnormal-termination"
```

---

### Task 10: `.env.example` cross-check, `README.md` update

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: nothing new.
- Produces: nothing new (documentation only).

`.env.example` already has the correct 4 keys (`DISCORD_WEBHOOK_DAILY_SUMMARY`, `DISCORD_WEBHOOK_TRANSACTIONS`, `DISCORD_WEBHOOK_ALERTS`, `DISCORD_WEBHOOK_LOGS`) from before this plan started — no change needed there. This task only updates the "État actuel" section of `README.md`.

- [ ] **Step 1: Replace the "État actuel" section**

Read the current `README.md` first (it was rewritten in Plan 3's Task 10 and looks different from earlier versions — replace between the `## État actuel` heading and the next `## Développement` heading with this exact block, leaving everything else in the file untouched):

```markdown
## État actuel

Plan 1/6 (fondation) : `MarketDataProvider` (Binance + repli Kraken), moteur FIFO, `analytics.py` — termine.
Plan 2/6 (backtest) : filtre de liquidite, 3 strategies, `backtest.py` (telechargement, rejeu, exports CSV) — termine.
Plan 3/6 (moteur live) : persistance SQLite (WAL), strategies pilotables au poll (`step()`), `live_engine.py`
(polling, reprise sur incident, retry/backoff, housekeeping snapshots) — termine.
Plan 4/6 (Discord) : `discord_notifier.py` (transactions, resume quotidien, alertes seuil, logs demarrage/arret) — termine.

Lancer le backtest : `python backtest.py`
Lancer le moteur live : `python live_engine.py` (tourne indefiniment, Ctrl+C pour arreter)
Valider les notifications Discord avant le premier lancement du moteur live : `python test_notifier.py`
(necessite `.env` rempli avec les 4 webhooks -- voir `.env.example`).

Pas encore de dashboard ni de deploiement — voir les plans suivants dans
`docs/superpowers/plans/`.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: update README for Plan 4 (Discord) completion"
```

---

### Task 11: Full suite verification

**Files:**
- (no new files — verification checkpoint)

**Interfaces:**
- Consumes: everything built in Tasks 1-9.
- Produces: nothing new.

- [ ] **Step 1: Run the full test suite**

Run: `pytest -v`
Expected: all tests pass — 0 failures.

---
