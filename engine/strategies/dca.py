import json
from dataclasses import dataclass, field
from decimal import Decimal

import indicators
from engine.fifo_engine import FifoEngine
from engine.strategies.base import build_snapshot
from market_data.types import Kline
from models import PortfolioSnapshot


@dataclass
class DcaState:
    last_buy_ms: int | None = None
    # Smart-DCA additions (all optional/additive -- a state or params dict
    # that never sets rsi_period/atr_period/trailing_stop_pct behaves
    # exactly like the original plain-DCA implementation).
    recent_closes: list[Decimal] = field(default_factory=list)
    recent_highs: list[Decimal] = field(default_factory=list)
    recent_lows: list[Decimal] = field(default_factory=list)
    peak_price_since_oldest_entry: Decimal | None = None
    # Identifies WHICH lot peak_price_since_oldest_entry is tracking, so the
    # peak resets when the oldest lot changes (a prior oldest lot exits and a
    # newer one becomes oldest) instead of comparing a new lot's price moves
    # against a stale peak left over from a lot that no longer exists.
    peak_tracks_lot_id: int | None = None


def _rolling_cap(params: dict) -> int:
    return max(int(params.get("rsi_period") or 0), int(params.get("atr_period") or 0)) + 1


def _update_history(state: DcaState, k: Kline, params: dict) -> None:
    cap = _rolling_cap(params)
    state.recent_closes.append(k.close)
    state.recent_highs.append(k.high)
    state.recent_lows.append(k.low)
    if len(state.recent_closes) > cap:
        excess = len(state.recent_closes) - cap
        del state.recent_closes[0:excess]
        del state.recent_highs[0:excess]
        del state.recent_lows[0:excess]


def _current_atr(state: DcaState, params: dict) -> Decimal | None:
    atr_period = params.get("atr_period")
    if not atr_period:
        return None
    return indicators.atr(state.recent_highs, state.recent_lows, state.recent_closes, int(atr_period))


def _take_profit_trigger_price(entry_price: Decimal, atr_value: Decimal | None, params: dict) -> Decimal | None:
    """ATR-based target takes priority when configured and warmed up (an ATR
    multiple scales automatically with each symbol's own recent volatility --
    a fixed % can't, which matters once the same DCA logic runs across 8
    differently-volatile cryptos). Falls back to the fixed take_profit_pct
    (original behavior) otherwise."""
    atr_mult = params.get("atr_take_profit_mult")
    if atr_value is not None and atr_mult is not None:
        return entry_price + atr_value * Decimal(str(atr_mult))
    take_profit_pct = params.get("take_profit_pct")
    if take_profit_pct is not None:
        return entry_price * (Decimal(1) + Decimal(str(take_profit_pct)) / Decimal(100))
    return None


def _stop_loss_trigger_price(entry_price: Decimal, atr_value: Decimal | None, params: dict) -> Decimal | None:
    atr_mult = params.get("atr_stop_loss_mult")
    if atr_value is not None and atr_mult is not None:
        return entry_price - atr_value * Decimal(str(atr_mult))
    stop_loss_pct = params.get("stop_loss_pct")
    if stop_loss_pct is not None:
        return entry_price * (Decimal(1) - Decimal(str(stop_loss_pct)) / Decimal(100))
    return None


def _trailing_stop_trigger_price(peak_price: Decimal, atr_value: Decimal | None, params: dict) -> Decimal | None:
    atr_mult = params.get("atr_trailing_stop_mult")
    if atr_value is not None and atr_mult is not None:
        return peak_price - atr_value * Decimal(str(atr_mult))
    trailing_stop_pct = params.get("trailing_stop_pct")
    if trailing_stop_pct is not None:
        return peak_price * (Decimal(1) - Decimal(str(trailing_stop_pct)) / Decimal(100))
    return None


def _check_exit(
    state: DcaState, engine: FifoEngine, symbol: str, price: Decimal, timestamp: int, params: dict,
    atr_value: Decimal | None,
) -> None:
    """Take-profit/stop-loss/trailing-stop overlay on top of pure DCA
    accumulation. All three are absent by default, in which case this is a
    no-op and DCA never sells (unchanged base behavior).

    Only ever evaluates the oldest lot: FifoEngine.sell() always consumes a
    symbol's lots FIFO (oldest first) regardless of which lot the caller
    "means" to sell. Selling any lot other than the current oldest one would
    silently realize PnL against the wrong lot instead of the one whose
    price move actually triggered the exit.
    """
    lots = engine.get_lots(symbol)
    if not lots:
        state.peak_price_since_oldest_entry = None
        state.peak_tracks_lot_id = None
        return
    oldest = lots[0]

    take_profit_price = _take_profit_trigger_price(oldest.prix_achat, atr_value, params)
    if take_profit_price is not None and price >= take_profit_price:
        engine.sell(timestamp, symbol, price, oldest.quantity_restante, "dca_take_profit")
        state.peak_price_since_oldest_entry = None
        state.peak_tracks_lot_id = None
        return

    stop_loss_price = _stop_loss_trigger_price(oldest.prix_achat, atr_value, params)
    if stop_loss_price is not None and price <= stop_loss_price:
        engine.sell(timestamp, symbol, price, oldest.quantity_restante, "dca_stop_loss")
        state.peak_price_since_oldest_entry = None
        state.peak_tracks_lot_id = None
        return

    if params.get("trailing_stop_pct") is not None or params.get("atr_trailing_stop_mult") is not None:
        if state.peak_tracks_lot_id != oldest.id:
            # A different (newer) lot became oldest since the last check --
            # start tracking its own peak from here, not a stale one.
            state.peak_tracks_lot_id = oldest.id
            state.peak_price_since_oldest_entry = price
        elif state.peak_price_since_oldest_entry is None or price > state.peak_price_since_oldest_entry:
            state.peak_price_since_oldest_entry = price

        trailing_trigger_price = _trailing_stop_trigger_price(state.peak_price_since_oldest_entry, atr_value, params)
        if trailing_trigger_price is not None and price <= trailing_trigger_price:
            engine.sell(timestamp, symbol, price, oldest.quantity_restante, "dca_trailing_stop")
            state.peak_price_since_oldest_entry = None
            state.peak_tracks_lot_id = None


def _scheduled_buy_amount(state: DcaState, price: Decimal, params: dict) -> Decimal | None:
    """Returns the quote amount to spend on this scheduled buy, or None to
    skip it entirely. Plain DCA (no rsi_period in params) always returns
    amount_per_buy, unconditionally -- identical to the original behavior.

    With rsi_period set: RSI(14)/30/70 oversold-overbought is the textbook
    lever retail DCA bots use to time safety orders (3Commas/Coinrule-style
    "RSI oversold entry") -- buys bigger when RSI signals oversold, skips
    the scheduled buy entirely when RSI signals overbought (don't chase a
    pump), buys the normal amount otherwise."""
    amount_per_buy = Decimal(str(params["amount_per_buy"]))
    rsi_period = params.get("rsi_period")
    if not rsi_period:
        return amount_per_buy
    rsi_value = indicators.rsi(state.recent_closes, int(rsi_period))
    if rsi_value is None:
        return amount_per_buy

    rsi_overbought = params.get("rsi_overbought")
    if rsi_overbought is not None and rsi_value >= Decimal(str(rsi_overbought)):
        return None

    rsi_oversold = params.get("rsi_oversold")
    dip_buy_multiplier = params.get("dip_buy_multiplier")
    if rsi_oversold is not None and dip_buy_multiplier is not None and rsi_value <= Decimal(str(rsi_oversold)):
        return amount_per_buy * Decimal(str(dip_buy_multiplier))

    return amount_per_buy


def step(state: DcaState, k: Kline, engine: FifoEngine, symbol: str, params: dict) -> None:
    """reference_price is always "close" (the only value the spec's schema defines)."""
    _update_history(state, k, params)
    atr_value = _current_atr(state, params)
    _check_exit(state, engine, symbol, k.close, k.open_time_ms, params, atr_value)

    frequency_ms = int(params["frequency_hours"]) * 3_600_000
    if state.last_buy_ms is None or k.open_time_ms - state.last_buy_ms >= frequency_ms:
        amount = _scheduled_buy_amount(state, k.close, params)
        if amount is not None:
            price = k.close
            quantity = amount / price
            engine.buy(k.open_time_ms, symbol, price, quantity, "dca")
        # A skipped (overbought) buy still consumes this schedule slot --
        # otherwise the very next candle would immediately retry, and an
        # extended overbought streak would just spam skip after skip
        # instead of waiting a full frequency_hours before checking again.
        state.last_buy_ms = k.open_time_ms


def run_dca(klines: list[Kline], engine: FifoEngine, symbol: str, params: dict) -> list[PortfolioSnapshot]:
    """Spec section 3: buy amount_per_buy (quote) every frequency_hours, starting
    immediately at the first candle. Scheduling is timestamp-based (compares each
    candle's own open_time_ms against the last buy), not candle-index-based --
    this needs no interval parameter and has no divide-by-zero failure mode for
    sub-hourly candles. Also applies the optional take-profit/stop-loss/trailing-
    stop exit (percent-based, or ATR-multiple-based when atr_period is set) and
    the optional RSI-based dip-buy/overbought-skip sizing from step(), on every
    candle."""
    state = DcaState()
    snapshots: list[PortfolioSnapshot] = []
    for k in klines:
        step(state, k, engine, symbol, params)
        snapshots.append(build_snapshot(engine, symbol, k.close, k.open_time_ms))
    return snapshots


def dca_state_to_json(state: DcaState) -> str:
    return json.dumps({
        "last_buy_ms": state.last_buy_ms,
        "recent_closes": [str(c) for c in state.recent_closes],
        "recent_highs": [str(c) for c in state.recent_highs],
        "recent_lows": [str(c) for c in state.recent_lows],
        "peak_price_since_oldest_entry": (
            str(state.peak_price_since_oldest_entry) if state.peak_price_since_oldest_entry is not None else None
        ),
        "peak_tracks_lot_id": state.peak_tracks_lot_id,
    })


def dca_state_from_json(s: str) -> DcaState:
    data = json.loads(s)
    return DcaState(
        last_buy_ms=data["last_buy_ms"],
        recent_closes=[Decimal(c) for c in data.get("recent_closes", [])],
        recent_highs=[Decimal(c) for c in data.get("recent_highs", [])],
        recent_lows=[Decimal(c) for c in data.get("recent_lows", [])],
        peak_price_since_oldest_entry=(
            Decimal(data["peak_price_since_oldest_entry"])
            if data.get("peak_price_since_oldest_entry") is not None
            else None
        ),
        peak_tracks_lot_id=data.get("peak_tracks_lot_id"),
    )
