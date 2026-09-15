import logging
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

logger = logging.getLogger(__name__)


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class Lot:
    id: int
    trade_id_achat: int
    symbol: str
    quantity_restante: Decimal
    prix_achat: Decimal
    timestamp_achat: int


@dataclass
class Trade:
    id: int
    timestamp: int
    symbol: str
    side: Side
    price: Decimal
    quantity: Decimal
    fee_pct: Decimal
    fee_amount: Decimal
    total_cost: Decimal
    realized_pnl: Decimal | None
    cash_balance_after: Decimal
    strategy_name: str


class FifoEngine:
    """Decimal-exact FIFO paper-trading engine. No cash may go negative, no
    implicit leverage: a BUY that costs more than cash_balance is rejected,
    never executed."""

    def __init__(self, initial_cash: Decimal, fee_pct: Decimal):
        self.cash_balance = initial_cash
        self.fee_pct = fee_pct
        self.lots: list[Lot] = []
        self.trades: list[Trade] = []
        self._next_trade_id = 1
        self._next_lot_id = 1

    def get_lots(self, symbol: str) -> list[Lot]:
        return [lot for lot in self.lots if lot.symbol == symbol]

    def position_value(self, symbol: str, current_price: Decimal) -> Decimal:
        total_qty = sum((lot.quantity_restante for lot in self.get_lots(symbol)), Decimal("0"))
        return total_qty * current_price

    def buy(
        self, timestamp: int, symbol: str, price: Decimal, quantity: Decimal, strategy_name: str
    ) -> Trade | None:
        gross = price * quantity
        fee_amount = gross * self.fee_pct
        total_cost = gross + fee_amount

        if total_cost > self.cash_balance:
            logger.warning(
                "BUY rejete: cash insuffisant symbol=%s cash_balance=%s total_cost=%s",
                symbol, self.cash_balance, total_cost,
            )
            return None

        self.cash_balance -= total_cost
        trade = Trade(
            id=self._next_trade_id,
            timestamp=timestamp,
            symbol=symbol,
            side=Side.BUY,
            price=price,
            quantity=quantity,
            fee_pct=self.fee_pct,
            fee_amount=fee_amount,
            total_cost=total_cost,
            realized_pnl=None,
            cash_balance_after=self.cash_balance,
            strategy_name=strategy_name,
        )
        self._next_trade_id += 1
        self.trades.append(trade)

        self.lots.append(
            Lot(
                id=self._next_lot_id,
                trade_id_achat=trade.id,
                symbol=symbol,
                quantity_restante=quantity,
                prix_achat=price,
                timestamp_achat=timestamp,
            )
        )
        self._next_lot_id += 1
        return trade

    def sell(
        self, timestamp: int, symbol: str, price: Decimal, quantity: Decimal, strategy_name: str
    ) -> Trade | None:
        symbol_lots = self.get_lots(symbol)
        available = sum((lot.quantity_restante for lot in symbol_lots), Decimal("0"))

        if quantity > available:
            logger.warning(
                "SELL rejete: quantite demandee superieure au disponible "
                "symbol=%s quantite_demandee=%s disponible=%s",
                symbol, quantity, available,
            )
            return None

        remaining = quantity
        realized_pnl = Decimal("0")
        for lot in symbol_lots:
            if remaining <= 0:
                break
            take = min(lot.quantity_restante, remaining)
            realized_pnl += (price - lot.prix_achat) * take
            lot.quantity_restante -= take
            remaining -= take

        self.lots = [lot for lot in self.lots if lot.quantity_restante > 0]

        gross = price * quantity
        fee_amount = gross * self.fee_pct
        realized_pnl -= fee_amount
        total_cost = gross - fee_amount  # net proceeds credited to cash

        self.cash_balance += total_cost
        trade = Trade(
            id=self._next_trade_id,
            timestamp=timestamp,
            symbol=symbol,
            side=Side.SELL,
            price=price,
            quantity=quantity,
            fee_pct=self.fee_pct,
            fee_amount=fee_amount,
            total_cost=total_cost,
            realized_pnl=realized_pnl,
            cash_balance_after=self.cash_balance,
            strategy_name=strategy_name,
        )
        self._next_trade_id += 1
        self.trades.append(trade)
        return trade
