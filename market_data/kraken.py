from decimal import Decimal

import httpx

from market_data.provider import MarketDataProvider
from market_data.types import BookTicker, Kline, Ticker24h

DEFAULT_BASE_URL = "https://api.kraken.com"

# Kraken pair codes for the watchlist that has a Kraken equivalent.
# Kraken has no BNBUSDT pair (BNB is a Binance-ecosystem token) — callers get ValueError.
SYMBOL_MAP: dict[str, str] = {
    "BTCUSDT": "XBTUSDT",
    "ETHUSDT": "ETHUSDT",
    "SOLUSDT": "SOLUSDT",
}

KRAKEN_INTERVAL_MINUTES: dict[str, int] = {"1m": 1, "5m": 5, "1h": 60, "1d": 1440}


class KrakenProvider(MarketDataProvider):
    """Documented fallback MarketDataProvider — api.kraken.com public endpoints, no API key.
    Kraken holds a valid CASP license in France; use this if data-api.binance.vision
    ever becomes unreachable (see spec section 4)."""

    def __init__(self, client: httpx.Client | None = None, base_url: str = DEFAULT_BASE_URL):
        self._client = client or httpx.Client(base_url=base_url, timeout=10.0)

    def _pair(self, symbol: str) -> str:
        if symbol not in SYMBOL_MAP:
            raise ValueError(f"Kraken repli: symbole non mappe: {symbol}")
        return SYMBOL_MAP[symbol]

    def _get_result(self, path: str, params: dict) -> dict:
        resp = self._client.get(path, params=params)
        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            raise RuntimeError(f"Kraken API error: {data['error']}")
        return data["result"]

    def get_klines(
        self, symbol: str, interval: str, start_ms: int, end_ms: int, limit: int = 1000
    ) -> list[Kline]:
        pair = self._pair(symbol)
        interval_minutes = KRAKEN_INTERVAL_MINUTES[interval]
        result = self._get_result(
            "/0/public/OHLC",
            {"pair": pair, "interval": interval_minutes, "since": start_ms // 1000},
        )
        rows = result[pair]
        klines: list[Kline] = []
        for row in rows:
            open_time_ms = int(row[0]) * 1000
            if open_time_ms > end_ms:
                break
            klines.append(
                Kline(
                    open_time_ms=open_time_ms,
                    open=Decimal(str(row[1])),
                    high=Decimal(str(row[2])),
                    low=Decimal(str(row[3])),
                    close=Decimal(str(row[4])),
                    volume=Decimal(str(row[6])),
                    close_time_ms=open_time_ms + interval_minutes * 60_000 - 1,
                )
            )
            if len(klines) >= limit:
                break
        return klines

    def get_book_ticker(self, symbol: str) -> BookTicker:
        pair = self._pair(symbol)
        result = self._get_result("/0/public/Ticker", {"pair": pair})
        t = result[pair]
        return BookTicker(symbol=symbol, bid_price=Decimal(t["b"][0]), ask_price=Decimal(t["a"][0]))

    def get_ticker_24h(self, symbol: str) -> Ticker24h:
        pair = self._pair(symbol)
        result = self._get_result("/0/public/Ticker", {"pair": pair})
        t = result[pair]
        # Kraken's Ticker has no quote-volume field, only base-currency 24h volume (v[1]).
        # Approximate quote volume as base_volume_24h * last trade price.
        last_price = Decimal(t["c"][0])
        base_volume_24h = Decimal(t["v"][1])
        return Ticker24h(symbol=symbol, quote_volume=base_volume_24h * last_price)
