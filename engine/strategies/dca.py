import json
from dataclasses import dataclass
from decimal import Decimal

from engine.fifo_engine import FifoEngine
from engine.strategies.base import build_snapshot
from market_data.types import Kline
from models import PortfolioSnapshot


@dataclass
class DcaState:
    last_buy_ms: int | None = None


def step(state: DcaState, k: Kline, engine: FifoEngine, symbol: str, params: dict) -> None:
    """reference_price is always "close" (the only value the spec's schema defines)."""
    frequency_ms = int(params["frequency_hours"]) * 3_600_000
    if state.last_buy_ms is None or k.open_time_ms - state.last_buy_ms >= frequency_ms:
        amount_per_buy = Decimal(str(params["amount_per_buy"]))
        price = k.close
        quantity = amount_per_buy / price
        engine.buy(k.open_time_ms, symbol, price, quantity, "dca")
        state.last_buy_ms = k.open_time_ms


def run_dca(klines: list[Kline], engine: FifoEngine, symbol: str, params: dict) -> list[PortfolioSnapshot]:
    """Spec section 3: buy amount_per_buy (quote) every frequency_hours, starting
    immediately at the first candle. Scheduling is timestamp-based (compares each
    candle's own open_time_ms against the last buy), not candle-index-based —
    this needs no interval parameter and has no divide-by-zero failure mode for
    sub-hourly candles."""
    state = DcaState()
    snapshots: list[PortfolioSnapshot] = []
    for k in klines:
        step(state, k, engine, symbol, params)
        snapshots.append(build_snapshot(engine, symbol, k.close, k.open_time_ms))
    return snapshots


def dca_state_to_json(state: DcaState) -> str:
    return json.dumps({"last_buy_ms": state.last_buy_ms})


def dca_state_from_json(s: str) -> DcaState:
    data = json.loads(s)
    return DcaState(last_buy_ms=data["last_buy_ms"])
