from decimal import Decimal

from engine.fifo_engine import FifoEngine
from models import PortfolioSnapshot


def build_snapshot(engine: FifoEngine, symbol: str, current_price: Decimal, timestamp: int) -> PortfolioSnapshot:
    position_value = engine.position_value(symbol, current_price)
    return PortfolioSnapshot(
        timestamp=timestamp,
        symbol=symbol,
        cash_balance=engine.cash_balance,
        position_value=position_value,
        total_value=engine.cash_balance + position_value,
        unrealized_pnl=engine.unrealized_pnl(symbol, current_price),
        realized_pnl_cumule=engine.realized_pnl_cumule(symbol),
    )
