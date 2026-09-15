from abc import ABC, abstractmethod

from market_data.types import BookTicker, Kline, Ticker24h

INTERVAL_MS: dict[str, int] = {
    "1m": 60_000,
    "5m": 300_000,
    "1h": 3_600_000,
    "1d": 86_400_000,
}


class MarketDataProvider(ABC):
    """Normalizes klines/ticker/spread across exchanges. Never call an exchange
    SDK/endpoint directly outside a MarketDataProvider implementation."""

    @abstractmethod
    def get_klines(
        self, symbol: str, interval: str, start_ms: int, end_ms: int, limit: int = 1000
    ) -> list[Kline]:
        raise NotImplementedError

    @abstractmethod
    def get_book_ticker(self, symbol: str) -> BookTicker:
        raise NotImplementedError

    @abstractmethod
    def get_ticker_24h(self, symbol: str) -> Ticker24h:
        raise NotImplementedError
