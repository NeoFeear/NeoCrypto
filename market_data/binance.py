from decimal import Decimal

import httpx

from market_data.provider import MarketDataProvider
from market_data.types import BookTicker, Kline, Ticker24h

DEFAULT_BASE_URL = "https://data-api.binance.vision"


class BinanceProvider(MarketDataProvider):
    """Default MarketDataProvider — data-api.binance.vision, public endpoints, no API key."""

    def __init__(self, client: httpx.Client | None = None, base_url: str = DEFAULT_BASE_URL):
        self._client = client or httpx.Client(base_url=base_url, timeout=10.0)

    def get_klines(
        self, symbol: str, interval: str, start_ms: int, end_ms: int, limit: int = 1000
    ) -> list[Kline]:
        resp = self._client.get(
            "/api/v3/klines",
            params={
                "symbol": symbol,
                "interval": interval,
                "startTime": start_ms,
                "endTime": end_ms,
                "limit": limit,
            },
        )
        resp.raise_for_status()
        rows = resp.json()
        return [
            Kline(
                open_time_ms=row[0],
                open=Decimal(str(row[1])),
                high=Decimal(str(row[2])),
                low=Decimal(str(row[3])),
                close=Decimal(str(row[4])),
                volume=Decimal(str(row[5])),
                close_time_ms=row[6],
            )
            for row in rows
        ]

    def get_book_ticker(self, symbol: str) -> BookTicker:
        resp = self._client.get("/api/v3/ticker/bookTicker", params={"symbol": symbol})
        resp.raise_for_status()
        data = resp.json()
        return BookTicker(
            symbol=data["symbol"],
            bid_price=Decimal(str(data["bidPrice"])),
            ask_price=Decimal(str(data["askPrice"])),
        )

    def get_ticker_24h(self, symbol: str) -> Ticker24h:
        resp = self._client.get("/api/v3/ticker/24hr", params={"symbol": symbol})
        resp.raise_for_status()
        data = resp.json()
        return Ticker24h(symbol=data["symbol"], quote_volume=Decimal(str(data["quoteVolume"])))
