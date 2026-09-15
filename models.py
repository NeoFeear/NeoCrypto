# models.py
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class PortfolioSnapshot:
    timestamp: int
    symbol: str
    cash_balance: Decimal
    position_value: Decimal
    total_value: Decimal
    unrealized_pnl: Decimal
    realized_pnl_cumule: Decimal
