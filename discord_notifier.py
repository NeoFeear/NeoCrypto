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
