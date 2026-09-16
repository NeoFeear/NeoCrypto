import logging
import time
from dataclasses import dataclass
from decimal import Decimal

import httpx
from dotenv import dotenv_values

from engine.fifo_engine import Side, Trade

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
        {"name": "Symbole", "value": str(trade.symbol), "inline": True},
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
