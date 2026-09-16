import json
from dataclasses import dataclass
from decimal import Decimal

from engine.fifo_engine import FifoEngine
from engine.strategies.base import build_snapshot
from market_data.types import Kline
from models import PortfolioSnapshot


@dataclass
class GridLevel:
    buy_price: Decimal
    sell_price: Decimal
    state: str = "EMPTY"
    filled_quantity: Decimal | None = None


def build_grid_levels(lower_bound, upper_bound, n_levels: int, spacing: str) -> list[GridLevel]:
    lower = Decimal(str(lower_bound))
    upper = Decimal(str(upper_bound))
    n = int(n_levels)

    if spacing == "arithmetic":
        step = (upper - lower) / Decimal(n)
        boundaries = [lower + step * i for i in range(n + 1)]
    elif spacing == "geometric":
        ratio = upper / lower
        boundaries = [lower * (ratio ** (Decimal(i) / Decimal(n))) for i in range(n + 1)]
    else:
        raise ValueError(f"spacing inconnu: {spacing}")

    # Force exact endpoints to guard against rounding errors in irrational ratio exponents
    boundaries[0] = lower
    boundaries[-1] = upper

    return [GridLevel(buy_price=boundaries[i], sell_price=boundaries[i + 1]) for i in range(n)]


def run_grid(klines: list[Kline], engine: FifoEngine, symbol: str, params: dict) -> list[PortfolioSnapshot]:
    """Spec section 8 (decisions D1-D3). Levels re-arm indefinitely (D1); a
    level triggers if the candle's [low, high] touches its price (D2,
    backtest side); simultaneous BUY triggers are attempted cheapest-price-
    first so a cash shortfall rejects the priciest ones (D3). A level bought
    in a candle cannot also be sold in that same candle — since backtest
    crossing detection only sees a candle's [low, high] range (not the actual
    intrabar price path), allowing a same-candle round-trip would assume a
    favorable price path the OHLC data doesn't actually prove."""
    levels = build_grid_levels(
        params["lower_bound"], params["upper_bound"], int(params["n_levels"]), params["spacing"]
    )
    order_size_quote = Decimal(str(params["order_size_quote"]))

    snapshots: list[PortfolioSnapshot] = []
    for k in klines:
        just_filled = []  # Track levels filled in this iteration to avoid selling them in the same candle

        buy_candidates = sorted(
            (lvl for lvl in levels if lvl.state == "EMPTY" and lvl.buy_price >= k.low and lvl.buy_price <= k.high),
            key=lambda lvl: lvl.buy_price,
        )
        for lvl in buy_candidates:
            quantity = order_size_quote / lvl.buy_price
            trade = engine.buy(k.open_time_ms, symbol, lvl.buy_price, quantity, "grid")
            if trade is not None:
                lvl.state = "FILLED"
                lvl.filled_quantity = quantity
                just_filled.append(lvl)

        for lvl in levels:
            if lvl not in just_filled and lvl.state == "FILLED" and lvl.sell_price >= k.low and lvl.sell_price <= k.high:
                trade = engine.sell(k.open_time_ms, symbol, lvl.sell_price, lvl.filled_quantity, "grid")
                if trade is not None:
                    lvl.state = "EMPTY"
                    lvl.filled_quantity = None

        snapshots.append(build_snapshot(engine, symbol, k.close, k.open_time_ms))
    return snapshots


@dataclass
class GridState:
    levels: list[GridLevel]
    prev_price: Decimal | None = None


def build_grid_state(params: dict) -> GridState:
    levels = build_grid_levels(
        params["lower_bound"], params["upper_bound"], int(params["n_levels"]), params["spacing"]
    )
    return GridState(levels=levels)


def step_grid_live(
    state: GridState, current_price: Decimal, timestamp: int, engine: FifoEngine, symbol: str, params: dict
) -> None:
    """Spec section 8, D2 live side: a level triggers when price crosses through
    it between two consecutive polls, not via an intrabar OHLC touch (there is
    no OHLC in live polling — just point samples). D1 (re-arm) and D3
    (cheapest-first fill) apply identically to the backtest side. The first-ever
    call for a fresh state has no prior price to compare against, so it can only
    record the current price, never trigger — this is unavoidable with a
    point-to-point crossing detector. Unlike backtest's OHLC range, a single
    poll-to-poll price comparison can only ever satisfy a downward (buy) OR
    upward (sell) crossing in one call, never both, so no same-cycle round-trip
    guard is needed here."""
    order_size_quote = Decimal(str(params["order_size_quote"]))

    if state.prev_price is not None:
        buy_candidates = sorted(
            (lvl for lvl in state.levels if lvl.state == "EMPTY" and state.prev_price > lvl.buy_price >= current_price),
            key=lambda lvl: lvl.buy_price,
        )
        for lvl in buy_candidates:
            quantity = order_size_quote / lvl.buy_price
            trade = engine.buy(timestamp, symbol, lvl.buy_price, quantity, "grid")
            if trade is not None:
                lvl.state = "FILLED"
                lvl.filled_quantity = quantity

        for lvl in state.levels:
            if lvl.state == "FILLED" and state.prev_price < lvl.sell_price <= current_price:
                trade = engine.sell(timestamp, symbol, lvl.sell_price, lvl.filled_quantity, "grid")
                if trade is not None:
                    lvl.state = "EMPTY"
                    lvl.filled_quantity = None

    state.prev_price = current_price


def grid_state_to_json(state: GridState) -> str:
    return json.dumps({
        "levels": [
            {
                "buy_price": str(lvl.buy_price),
                "sell_price": str(lvl.sell_price),
                "state": lvl.state,
                "filled_quantity": str(lvl.filled_quantity) if lvl.filled_quantity is not None else None,
            }
            for lvl in state.levels
        ],
        "prev_price": str(state.prev_price) if state.prev_price is not None else None,
    })


def grid_state_from_json(s: str) -> GridState:
    data = json.loads(s)
    levels = [
        GridLevel(
            buy_price=Decimal(lvl["buy_price"]),
            sell_price=Decimal(lvl["sell_price"]),
            state=lvl["state"],
            filled_quantity=Decimal(lvl["filled_quantity"]) if lvl["filled_quantity"] is not None else None,
        )
        for lvl in data["levels"]
    ]
    prev_price = Decimal(data["prev_price"]) if data["prev_price"] is not None else None
    return GridState(levels=levels, prev_price=prev_price)
