from decimal import Decimal

from engine.fifo_engine import FifoEngine
from engine.strategies.base import build_snapshot
from market_data.types import Kline
from models import PortfolioSnapshot


def run_buy_hold(klines: list[Kline], engine: FifoEngine, symbol: str, params: dict) -> list[PortfolioSnapshot]:
    """Spec section 3: {"invest_at": "start"} — invest everything on the first
    candle, hold forever. No selling."""
    snapshots: list[PortfolioSnapshot] = []
    invested = False
    for k in klines:
        if not invested:
            price = k.close
            quantity = (engine.cash_balance / (Decimal(1) + engine.fee_pct)) / price
            engine.buy(k.open_time_ms, symbol, price, quantity, "buy_hold")
            invested = True
        snapshots.append(build_snapshot(engine, symbol, k.close, k.open_time_ms))
    return snapshots
