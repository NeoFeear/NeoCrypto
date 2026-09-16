import json
import logging
from dataclasses import dataclass
from decimal import Decimal

from engine.fifo_engine import FifoEngine
from engine.strategies.base import build_snapshot
from market_data.types import Kline
from models import PortfolioSnapshot

logger = logging.getLogger(__name__)


@dataclass
class BuyHoldState:
    invested: bool = False


def step(state: BuyHoldState, k: Kline, engine: FifoEngine, symbol: str, params: dict) -> None:
    if not state.invested:
        price = k.close
        quantity = (engine.cash_balance / (Decimal(1) + engine.fee_pct)) / price
        trade = engine.buy(k.open_time_ms, symbol, price, quantity, "buy_hold")
        if trade is None:
            logger.warning("Buy & Hold: achat initial rejete pour %s (cash insuffisant?)", symbol)
        else:
            state.invested = True


def run_buy_hold(klines: list[Kline], engine: FifoEngine, symbol: str, params: dict) -> list[PortfolioSnapshot]:
    """Spec section 3: {"invest_at": "start"} — invest everything on the first
    candle, hold forever. No selling."""
    state = BuyHoldState()
    snapshots: list[PortfolioSnapshot] = []
    for k in klines:
        step(state, k, engine, symbol, params)
        snapshots.append(build_snapshot(engine, symbol, k.close, k.open_time_ms))
    return snapshots


def buy_hold_state_to_json(state: BuyHoldState) -> str:
    return json.dumps({"invested": state.invested})


def buy_hold_state_from_json(s: str) -> BuyHoldState:
    data = json.loads(s)
    return BuyHoldState(invested=data["invested"])
