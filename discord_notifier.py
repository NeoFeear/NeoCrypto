import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

import httpx
from dotenv import dotenv_values

from engine.fifo_engine import Side, Trade
from sats import format_quantity

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
    (needed by send_portfolio_daily_summary to PATCH it later), or None if webhook_url
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
            return response.json().get("id")
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
        except Exception as e:
            # Final safety net, additive to the two clauses above: never remove
            # or narrow those. Catches anything they don't already -- e.g.
            # response.json() raising json.JSONDecodeError on a non-JSON body
            # (an intercepting proxy's HTML error page), or httpx.InvalidURL
            # (a malformed webhook URL from a hand-edited .env), which is NOT
            # a subclass of httpx.HTTPError. send_log is called outside
            # main()'s try block and send_alert runs inside the polling
            # loop's own exception handler, so this function raising at all
            # would defeat its entire "never crash the caller" contract.
            logger.warning("Envoi Discord echoue (erreur inattendue): %s", e)
            return None
    return None


def _patch_embed(webhook_url: str, message_id: str, embed: dict) -> bool:
    """Edits an existing webhook message (used by send_portfolio_daily_summary so the
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
    except Exception as e:
        # See the matching clause in _post_embed: additive safety net for
        # anything httpx.HTTPError doesn't already cover (e.g. httpx.InvalidURL).
        logger.warning("Edition Discord echouee (erreur inattendue): %s", e)
        return False


_COLOR_BUY = 0x95A5A6
_COLOR_GAIN = 0x2ECC71
_COLOR_LOSS = 0xE74C3C

# Every amount in this app is denominated in the pair's quote asset -- USDT
# for every Binance symbol traded here (BTCUSDT, ETHUSDT, ...) -- never
# silently relabeled to EUR: that would need a live FX rate this app has no
# source for, and would just be wrong. Every monetary field is suffixed
# "USDT" instead so it is never ambiguous what unit a number is in.
_QUOTE_CURRENCY = "USDT"


def _fmt_money(value: Decimal) -> str:
    return f"{value:.2f} {_QUOTE_CURRENCY}"


def _fmt_signed_pct(value: Decimal) -> str:
    """For a genuine gain/loss (realized PnL, portfolio return): always
    carries an explicit sign so +/- is legible at a glance without reading
    the color."""
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.2f}%"


def _fmt_ratio_pct(part: Decimal, whole: Decimal | None) -> str | None:
    """For a plain proportion (e.g. "this buy spent X% of the pair's
    starting capital") -- not a signed delta, so no forced +/-. None when
    `whole` isn't available (e.g. initial_cash wasn't passed in) or is 0."""
    if not whole:
        return None
    return f"{(part / whole * Decimal(100)):.2f}%"


def send_transaction(webhook_url: str, trade: Trade, initial_cash: Decimal | None = None) -> None:
    """Spec section 7: 1 embed per trade that actually executed -- never for
    a no-op cycle, never for a rejected order. Grey for a BUY, green for a
    SELL at gain or breakeven, red for a SELL at a loss.

    initial_cash (this pair's own starting capital -- live.capital_per_pair,
    NOT the global total_capital) is optional so existing callers/tests that
    don't have it keep working unchanged; when given, it grounds every
    amount against a concrete reference point (the "Capital de depart"
    field) and turns a SELL's realized PnL into a %, both in the field and
    the title, so a gain/loss is legible without doing mental math against
    a number from a different message."""
    is_sell = trade.side == Side.SELL
    is_gain = trade.realized_pnl is not None and trade.realized_pnl >= 0
    if not is_sell:
        color, emoji = _COLOR_BUY, "\U0001F535"  # blue circle
    else:
        color, emoji = (_COLOR_GAIN, "\U0001F7E2") if is_gain else (_COLOR_LOSS, "\U0001F534")  # green/red circle

    title = f"{emoji} {trade.side.value} {trade.symbol}"
    pnl_pct_str = None
    if is_sell and trade.realized_pnl is not None and initial_cash:
        pnl_pct_str = _fmt_signed_pct(trade.realized_pnl / initial_cash * Decimal(100))
        title += f" ({pnl_pct_str})"

    montant_str = _fmt_money(trade.total_cost)
    montant_ratio = _fmt_ratio_pct(trade.total_cost, initial_cash)
    if montant_ratio is not None:
        montant_str += f" ({montant_ratio} du capital de la paire)"

    fields = [
        {"name": "Symbole", "value": str(trade.symbol), "inline": True},
        {"name": "Prix", "value": _fmt_money(trade.price), "inline": True},
        {"name": "Quantite", "value": format_quantity(trade.quantity, trade.symbol), "inline": True},
        {"name": "Montant", "value": montant_str, "inline": True},
        {"name": "Frais", "value": _fmt_money(trade.fee_amount), "inline": True},
        {"name": "Strategie", "value": trade.strategy_name, "inline": True},
    ]
    if initial_cash is not None:
        fields.append({"name": "Capital de depart (paire)", "value": _fmt_money(initial_cash), "inline": True})
    fields.append({"name": "Solde apres", "value": _fmt_money(trade.cash_balance_after), "inline": True})
    if trade.realized_pnl is not None:
        pnl_value = _fmt_money(trade.realized_pnl)
        if pnl_pct_str is not None:
            pnl_value += f" ({pnl_pct_str} du capital de la paire)"
        fields.append({"name": "PnL realise", "value": pnl_value, "inline": True})

    embed = {
        "title": title,
        "color": color,
        "fields": fields,
        "timestamp": datetime.fromtimestamp(trade.timestamp / 1000, tz=timezone.utc).isoformat(),
    }
    _post_embed(webhook_url, {"embeds": [embed]})


_COLOR_CRITICAL = 0xE74C3C
_COLOR_WARNING = 0xE67E22
_COLOR_LOG = 0x3498DB

_ALERT_COLORS = {"critical": _COLOR_CRITICAL, "warning": _COLOR_WARNING}
_ALERT_EMOJI = {"critical": "\U0001F6A8", "warning": "⚠️"}  # 🚨 / ⚠️


def send_alert(webhook_url: str, alert_type: str, message: str, severity: str) -> None:
    """Spec section 7: only for a real threshold crossing (drawdown > seuil
    configure, service arrete de facon inattendue, 3 echecs API consecutifs)
    -- never a periodic heartbeat."""
    emoji = _ALERT_EMOJI.get(severity, _ALERT_EMOJI["warning"])
    embed = {
        "title": f"{emoji} Alerte: {alert_type}",
        "description": message,
        "color": _ALERT_COLORS.get(severity, _COLOR_WARNING),
    }
    _post_embed(webhook_url, {"embeds": [embed]})


def send_log(webhook_url: str, message: str, level: str) -> None:
    """Spec section 7: only for service start/stop -- no other runtime
    logging goes to Discord."""
    embed = {
        "title": f"ℹ️ Log {level}",  # ℹ️
        "description": message,
        "color": _COLOR_LOG,
    }
    _post_embed(webhook_url, {"embeds": [embed]})


@dataclass(frozen=True)
class PairDailySummary:
    """One live pair's row in the consolidated daily summary embed (see
    send_portfolio_daily_summary). return_pct_24h is None when this pair
    has under 24h of snapshot history yet (e.g. just added to live.pairs) --
    rendered as "N/A" rather than a misleading 0%."""

    symbol: str
    total_value: Decimal
    return_pct_since_start: Decimal
    return_pct_24h: Decimal | None


def send_portfolio_daily_summary(
    webhook_url: str, pairs: list[PairDailySummary], total_value: Decimal, total_capital: Decimal,
    return_pct: Decimal, return_pct_24h: Decimal | None, existing_message_id: str | None,
) -> str | None:
    """Spec section 7 (revised): exactly 1 message per day for the WHOLE
    portfolio (not 1 per pair -- the previous per-symbol send_daily_summary
    spammed one message per live.pairs entry), edited (PATCH) if it already
    exists for today rather than duplicated.

    total_value/return_pct are the sum/aggregate across every pair in
    `pairs`; return_pct_24h is None when no pair has 24h of history yet
    (e.g. right after a DB reset), rendered as "N/A" rather than a
    misleading 0%. Color and a trend emoji mirror return_pct's sign (global,
    since-start) so a glance at the message list already shows which days
    were up. Each pair gets its own field: current value, 24h evolution,
    evolution since start."""
    trend_emoji = "\U0001F4C8" if return_pct >= 0 else "\U0001F4C9"  # 📈 / 📉

    fields = [
        {"name": "Valeur totale du portefeuille", "value": _fmt_money(total_value), "inline": True},
        {"name": "Capital de depart (total)", "value": _fmt_money(total_capital), "inline": True},
        {
            "name": "Evolution 24h",
            "value": _fmt_signed_pct(return_pct_24h) if return_pct_24h is not None else "N/A (< 24h d'historique)",
            "inline": True,
        },
    ]
    for pair in pairs:
        pct_24h_str = _fmt_signed_pct(pair.return_pct_24h) if pair.return_pct_24h is not None else "N/A"
        fields.append({
            "name": pair.symbol,
            "value": (
                f"Valeur: {_fmt_money(pair.total_value)}\n"
                f"24h: {pct_24h_str}\n"
                f"Depuis le debut: {_fmt_signed_pct(pair.return_pct_since_start)}"
            ),
            "inline": True,
        })

    embed = {
        "title": f"{trend_emoji} Resume quotidien -- Portefeuille ({_fmt_signed_pct(return_pct)})",
        "color": _COLOR_GAIN if return_pct >= 0 else _COLOR_LOSS,
        "fields": fields,
    }
    if existing_message_id is None:
        return _post_embed(webhook_url, {"embeds": [embed]})
    return existing_message_id if _patch_embed(webhook_url, existing_message_id, {"embeds": [embed]}) else None
