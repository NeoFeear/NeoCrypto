import json
from dataclasses import dataclass, field
from decimal import Decimal

import indicators
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
    elif spacing == "fibonacci":
        # n_levels is ignored here: the 6 standard Fibonacci retracement
        # ratios (23.6/38.2/50/61.8/78.6%) are a fixed set, not a tunable
        # count -- levels sit at the same support/resistance zones traders
        # actually watch, instead of blind even spacing.
        boundaries = indicators.fibonacci_levels(lower, upper)
    else:
        raise ValueError(f"spacing inconnu: {spacing}")

    # Force exact endpoints to guard against rounding errors in irrational ratio exponents
    boundaries[0] = lower
    boundaries[-1] = upper

    return [GridLevel(buy_price=boundaries[i], sell_price=boundaries[i + 1]) for i in range(len(boundaries) - 1)]


def build_grid_levels_auto(current_price: Decimal, band_pct, n_levels: int, spacing: str) -> list[GridLevel]:
    """Symbol-agnostic bound derivation: +/- band_pct around current_price,
    instead of a hardcoded $ range. Works identically whether current_price
    is BTC at 60000 or an altcoin at 0.50 -- no per-symbol tuning needed."""
    band = Decimal(str(band_pct)) / Decimal(100)
    lower = current_price * (Decimal(1) - band)
    upper = current_price * (Decimal(1) + band)
    return build_grid_levels(lower, upper, n_levels, spacing)


def _effective_band_pct(params: dict, current_price: Decimal, atr_value: Decimal | None) -> Decimal:
    """ATR-derived band width takes priority once available: volatile
    markets warrant wider grid spacing, calm ones tighter -- a fixed
    band_pct can't adapt to that, which matters once the same grid config
    runs across several differently-volatile cryptos at once. band_pct
    stays as the documented static fallback (used for the very first grid
    build, before any ATR history exists, and whenever atr_band_multiplier
    isn't configured)."""
    atr_band_multiplier = params.get("atr_band_multiplier")
    if atr_value is not None and atr_band_multiplier is not None and current_price > 0:
        return (atr_value / current_price) * Decimal(100) * Decimal(str(atr_band_multiplier))
    return Decimal(str(params["band_pct"]))


def run_grid(klines: list[Kline], engine: FifoEngine, symbol: str, params: dict) -> list[PortfolioSnapshot]:
    """Spec section 8 (decisions D1-D3). Levels re-arm indefinitely (D1); a
    level triggers if the candle's [low, high] touches its price (D2,
    backtest side); simultaneous BUY triggers are attempted cheapest-price-
    first so a cash shortfall rejects the priciest ones (D3). A level bought
    in a candle cannot also be sold in that same candle — since backtest
    crossing detection only sees a candle's [low, high] range (not the actual
    intrabar price path), allowing a same-candle round-trip would assume a
    favorable price path the OHLC data doesn't actually prove."""
    if "lower_bound" in params and "upper_bound" in params:
        levels = build_grid_levels(
            params["lower_bound"], params["upper_bound"], int(params["n_levels"]), params["spacing"]
        )
    else:
        # No fixed bounds configured: derive a band around the very first
        # candle's close, same mechanism the live side uses at startup.
        levels = build_grid_levels_auto(
            klines[0].close, params["band_pct"], int(params["n_levels"]), params["spacing"]
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
                trade = engine.sell(k.open_time_ms, symbol, lvl.sell_price, lvl.filled_quantity, "grid",
                                    lot_price=lvl.buy_price)
                if trade is not None:
                    lvl.state = "EMPTY"
                    lvl.filled_quantity = None

        snapshots.append(build_snapshot(engine, symbol, k.close, k.open_time_ms))
    return snapshots


@dataclass
class GridState:
    levels: list[GridLevel]
    prev_price: Decimal | None = None
    # Tracked so step_grid_live can detect a breakout and re-range (additive --
    # only populated/consulted when params carries band_pct/rebuild_breakout_pct).
    lower_bound: Decimal | None = None
    upper_bound: Decimal | None = None
    # ATR history (additive -- only grows when params carries
    # atr_band_multiplier; see _effective_band_pct).
    recent_highs: list[Decimal] = field(default_factory=list)
    recent_lows: list[Decimal] = field(default_factory=list)
    recent_closes: list[Decimal] = field(default_factory=list)


def build_grid_state(params: dict, current_price: Decimal | None = None) -> GridState:
    if "lower_bound" in params and "upper_bound" in params:
        lower_bound = Decimal(str(params["lower_bound"]))
        upper_bound = Decimal(str(params["upper_bound"]))
        levels = build_grid_levels(lower_bound, upper_bound, int(params["n_levels"]), params["spacing"])
    else:
        if current_price is None:
            raise ValueError("current_price est requis quand params n'a pas de lower_bound/upper_bound explicite")
        # The very first grid has no ATR history yet -- seeded from the
        # static band_pct fallback; step_grid_live switches to ATR-derived
        # width (when configured) from the first re-range onward, once
        # enough live-polled bars have accumulated.
        band_pct = _effective_band_pct(params, current_price, atr_value=None)
        lower_bound = current_price * (Decimal(1) - band_pct / Decimal(100))
        upper_bound = current_price * (Decimal(1) + band_pct / Decimal(100))
        levels = build_grid_levels(lower_bound, upper_bound, int(params["n_levels"]), params["spacing"])
    return GridState(levels=levels, lower_bound=lower_bound, upper_bound=upper_bound)


def _rerange_grid(
    state: GridState, current_price: Decimal, params: dict, engine: FifoEngine, symbol: str, timestamp: int,
    atr_value: Decimal | None,
) -> None:
    """Re-centers the grid around current_price once price has broken out of
    the current [lower_bound, upper_bound] band. Any still-FILLED level is
    left untouched -- a level's own sell_price never exceeds the band's
    current upper_bound, so an upward breakout has, by construction, already
    sold every filled level via the ordinary crossing check above before this
    ever runs; a downward breakout leaves filled levels exactly as they were,
    keeping their original sell targets standing (no forced exit at a loss --
    they can still profit if price recovers). Only the -- now entirely EMPTY
    -- set of levels is rebuilt, shifted to surround the new price, at a
    width that reflects the volatility that just caused the breakout (ATR-
    derived when configured, see _effective_band_pct)."""
    still_filled = [lvl for lvl in state.levels if lvl.state == "FILLED"]
    band_pct = _effective_band_pct(params, current_price, atr_value)
    new_lower = current_price * (Decimal(1) - band_pct / Decimal(100))
    new_upper = current_price * (Decimal(1) + band_pct / Decimal(100))
    fresh_levels = build_grid_levels(new_lower, new_upper, int(params["n_levels"]), params["spacing"])

    state.levels = still_filled + fresh_levels
    state.lower_bound = new_lower
    state.upper_bound = new_upper


def step_grid_live(
    state: GridState, current_price: Decimal, timestamp: int, engine: FifoEngine, symbol: str, params: dict,
    high: Decimal | None = None, low: Decimal | None = None,
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
    guard is needed here.

    Additive: when params carries rebuild_breakout_pct (and band_pct), a price
    that breaks out past the current [lower_bound, upper_bound] by more than
    that margin triggers an automatic re-range (see _rerange_grid) instead of
    leaving a stale, now-irrelevant grid in place indefinitely. When params
    also carries atr_band_multiplier, high/low (the polled candle's own high/
    low -- degrades to a single point sample, current_price, when omitted, as
    every existing caller that predates this feature does) feed a rolling ATR
    that sizes each re-range's new band to the symbol's actual recent
    volatility instead of a fixed percentage."""
    order_size_quote = Decimal(str(params["order_size_quote"]))
    # "range" (review 2026-09-25): a resting level fills when the closed
    # candle's [low, high] touches it -- the backtest's own rule, so live and
    # backtest measure the same execution model and wicks are no longer
    # missed. "cross" keeps the original close-to-close crossing. Either way a
    # level only ever sees candles AFTER it existed (prev_price is None on a
    # brand-new grid) and never round-trips inside one candle.
    use_range = params.get("fill_model", "cross") == "range" and high is not None and low is not None

    if state.prev_price is not None:
        if use_range:
            buy_hit = lambda lvl: low <= lvl.buy_price <= high  # noqa: E731
            sell_hit = lambda lvl: low <= lvl.sell_price <= high  # noqa: E731
        else:
            buy_hit = lambda lvl: state.prev_price > lvl.buy_price >= current_price  # noqa: E731
            sell_hit = lambda lvl: state.prev_price < lvl.sell_price <= current_price  # noqa: E731

        just_filled = []
        buy_candidates = sorted(
            (lvl for lvl in state.levels if lvl.state == "EMPTY" and buy_hit(lvl)),
            key=lambda lvl: lvl.buy_price,
        )
        for lvl in buy_candidates:
            quantity = order_size_quote / lvl.buy_price
            trade = engine.buy(timestamp, symbol, lvl.buy_price, quantity, "grid")
            if trade is not None:
                lvl.state = "FILLED"
                lvl.filled_quantity = quantity
                just_filled.append(lvl)

        for lvl in state.levels:
            if lvl.state == "FILLED" and lvl not in just_filled and sell_hit(lvl):
                trade = engine.sell(timestamp, symbol, lvl.sell_price, lvl.filled_quantity, "grid",
                                    lot_price=lvl.buy_price)
                if trade is not None:
                    lvl.state = "EMPTY"
                    lvl.filled_quantity = None

    state.prev_price = current_price

    atr_value = None
    atr_band_multiplier = params.get("atr_band_multiplier")
    if atr_band_multiplier is not None:
        atr_period = int(params.get("atr_period", 14))
        state.recent_highs.append(high if high is not None else current_price)
        state.recent_lows.append(low if low is not None else current_price)
        state.recent_closes.append(current_price)
        cap = atr_period + 1
        if len(state.recent_closes) > cap:
            excess = len(state.recent_closes) - cap
            del state.recent_highs[0:excess]
            del state.recent_lows[0:excess]
            del state.recent_closes[0:excess]
        atr_value = indicators.atr(state.recent_highs, state.recent_lows, state.recent_closes, atr_period)

    rebuild_breakout_pct = params.get("rebuild_breakout_pct")
    if rebuild_breakout_pct is not None and state.lower_bound is not None and state.upper_bound is not None:
        breakout = Decimal(str(rebuild_breakout_pct)) / Decimal(100)
        if current_price > state.upper_bound * (Decimal(1) + breakout) or current_price < state.lower_bound * (Decimal(1) - breakout):
            _rerange_grid(state, current_price, params, engine, symbol, timestamp, atr_value)


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
        "lower_bound": str(state.lower_bound) if state.lower_bound is not None else None,
        "upper_bound": str(state.upper_bound) if state.upper_bound is not None else None,
        "recent_highs": [str(v) for v in state.recent_highs],
        "recent_lows": [str(v) for v in state.recent_lows],
        "recent_closes": [str(v) for v in state.recent_closes],
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
    lower_bound = Decimal(data["lower_bound"]) if data.get("lower_bound") is not None else None
    upper_bound = Decimal(data["upper_bound"]) if data.get("upper_bound") is not None else None
    recent_highs = [Decimal(v) for v in data.get("recent_highs", [])]
    recent_lows = [Decimal(v) for v in data.get("recent_lows", [])]
    recent_closes = [Decimal(v) for v in data.get("recent_closes", [])]
    return GridState(
        levels=levels, prev_price=prev_price, lower_bound=lower_bound, upper_bound=upper_bound,
        recent_highs=recent_highs, recent_lows=recent_lows, recent_closes=recent_closes,
    )
