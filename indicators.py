# indicators.py -- standard technical-analysis levers, kept deliberately
# small and dependency-free (Decimal-exact, no numpy/pandas) since these run
# inside a paper-trading engine that must stay auditable line by line.
#
# RSI(14) and ATR(14) with 30/70 oversold/overbought and a 2-3x ATR stop/
# target are the textbook defaults used across retail bot platforms (3Commas,
# Coinrule, TradeSanta) -- see crypto-sim.md for the research this is based
# on. Fibonacci retracement ratios (23.6/38.2/50/61.8/78.6%) are the standard
# support/resistance levels used to place grid levels between a range's low
# and high, instead of blind even spacing.
from decimal import Decimal

FIBONACCI_RETRACEMENT_RATIOS = [
    Decimal("0"), Decimal("0.236"), Decimal("0.382"), Decimal("0.5"),
    Decimal("0.618"), Decimal("0.786"), Decimal("1"),
]


def rsi(closes: list[Decimal], period: int) -> Decimal | None:
    """Simple (non-Wilder-smoothed) RSI over the last `period` price changes --
    needs period+1 closes. Returns None when there isn't enough history yet
    (cold start), never raises and never divides by zero (a period with zero
    losses is treated as RSI=100, maximally overbought, not a ZeroDivisionError)."""
    if len(closes) < period + 1:
        return None
    window = closes[-(period + 1):]
    gains = Decimal("0")
    losses = Decimal("0")
    for i in range(1, len(window)):
        change = window[i] - window[i - 1]
        if change > 0:
            gains += change
        else:
            losses += -change
    avg_gain = gains / Decimal(period)
    avg_loss = losses / Decimal(period)
    if avg_loss == 0:
        return Decimal("100")
    rs = avg_gain / avg_loss
    return Decimal("100") - (Decimal("100") / (Decimal("1") + rs))


def true_range(high: Decimal, low: Decimal, prev_close: Decimal | None) -> Decimal:
    if prev_close is None:
        return high - low
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def atr(highs: list[Decimal], lows: list[Decimal], closes: list[Decimal], period: int) -> Decimal | None:
    """Average True Range over the last `period` bars (simple mean of True
    Range, not Wilder-smoothed -- recomputed fresh from the rolling window
    each call rather than carried incrementally, trading a little CPU for a
    much simpler, easier-to-audit implementation with no smoothing-state
    edge cases across restarts). Needs period+1 highs/lows/closes (one extra
    bar to know the first prev_close). Returns None when there isn't enough
    history yet."""
    n = period + 1
    if len(highs) < n or len(lows) < n or len(closes) < n:
        return None
    highs, lows, closes = highs[-n:], lows[-n:], closes[-n:]
    true_ranges = [
        true_range(highs[i], lows[i], closes[i - 1])
        for i in range(1, n)
    ]
    return sum(true_ranges, Decimal("0")) / Decimal(period)


def fibonacci_levels(low: Decimal, high: Decimal) -> list[Decimal]:
    """The 7 standard retracement levels (0%, 23.6%, 38.2%, 50%, 61.8%,
    78.6%, 100%) spanning [low, high], ascending."""
    span = high - low
    return [low + span * ratio for ratio in FIBONACCI_RETRACEMENT_RATIOS]
