from decimal import Decimal

from engine.fifo_engine import FifoEngine
from engine.strategies.base import build_snapshot
from market_data.types import Kline
from models import PortfolioSnapshot


def run_dca(
    klines: list[Kline], engine: FifoEngine, symbol: str, params: dict, interval_hours: int
) -> list[PortfolioSnapshot]:
    """Spec section 3: buy amount_per_buy (quote) every frequency_hours, starting
    immediately at the first candle. reference_price is always "close" (the
    only value the spec's schema defines)."""
    amount_per_buy = Decimal(str(params["amount_per_buy"]))
    frequency_hours = int(params["frequency_hours"])
    candles_per_buy = frequency_hours // interval_hours

    snapshots: list[PortfolioSnapshot] = []
    for i, k in enumerate(klines):
        if i % candles_per_buy == 0:
            price = k.close
            quantity = amount_per_buy / price
            engine.buy(k.open_time_ms, symbol, price, quantity, "dca")
        snapshots.append(build_snapshot(engine, symbol, k.close, k.open_time_ms))
    return snapshots
