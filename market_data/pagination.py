import time

from market_data.provider import INTERVAL_MS, MarketDataProvider
from market_data.types import Kline


def fetch_klines_paginated(
    provider: MarketDataProvider,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    limit: int = 1000,
    sleep_seconds: float = 0.2,
) -> list[Kline]:
    """Loops startTime/endTime to download more klines than a single call's limit allows.
    Sleeps between calls to respect the provider's rate limit (spec section 1)."""
    out: list[Kline] = []
    cursor = start_ms
    interval_ms = INTERVAL_MS[interval]
    while cursor < end_ms:
        batch = provider.get_klines(symbol, interval, cursor, end_ms, limit=limit)
        if not batch:
            break
        out.extend(batch)
        cursor = batch[-1].open_time_ms + interval_ms
        time.sleep(sleep_seconds)
    return out
