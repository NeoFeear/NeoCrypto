from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Kline:
    open_time_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    close_time_ms: int


@dataclass(frozen=True)
class BookTicker:
    symbol: str
    bid_price: Decimal
    ask_price: Decimal

    @property
    def spread_relative(self) -> Decimal:
        mid = (self.bid_price + self.ask_price) / Decimal(2)
        return (self.ask_price - self.bid_price) / mid

    @property
    def spread_bps(self) -> Decimal:
        return self.spread_relative * Decimal(10000)


@dataclass(frozen=True)
class Ticker24h:
    symbol: str
    quote_volume: Decimal
