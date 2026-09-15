# analytics.py
from decimal import Decimal

from engine.fifo_engine import Side, Trade
from models import PortfolioSnapshot

DAY_MS = 86_400_000


def total_return_pct(initial_capital: Decimal, final_total_value: Decimal) -> Decimal:
    return (final_total_value - initial_capital) / initial_capital * Decimal(100)


def cagr_pct(initial_capital: Decimal, final_total_value: Decimal, days: int) -> Decimal:
    """Annualized return. Short backtest windows extrapolate aggressively — a 25% gain
    over 9 days annualizes to a very large number; that's expected, not a bug."""
    ratio = final_total_value / initial_capital
    exponent = Decimal(365) / Decimal(days)
    return (ratio**exponent - 1) * Decimal(100)


def drawdown_curve(snapshots: list[PortfolioSnapshot]) -> list[tuple[int, Decimal]]:
    ordered = sorted(snapshots, key=lambda s: s.timestamp)
    curve: list[tuple[int, Decimal]] = []
    peak: Decimal | None = None
    for s in ordered:
        if peak is None or s.total_value > peak:
            peak = s.total_value
        dd = Decimal("0") if peak == 0 else (peak - s.total_value) / peak * Decimal(100)
        curve.append((s.timestamp, dd))
    return curve


def max_drawdown(snapshots: list[PortfolioSnapshot]) -> tuple[Decimal, int | None]:
    """Returns (max_drawdown_pct, recovery_days). recovery_days is None if the
    series never returns to the pre-drawdown peak."""
    curve = drawdown_curve(snapshots)
    if not curve:
        return Decimal("0"), None

    max_dd = max(dd for _, dd in curve)
    if max_dd == 0:
        return Decimal("0"), 0

    trough_index = next(i for i, (_, dd) in enumerate(curve) if dd == max_dd)
    trough_ts = curve[trough_index][0]

    peak_ts = trough_ts
    for i in range(trough_index, -1, -1):
        if curve[i][1] == 0:
            peak_ts = curve[i][0]
            break

    recovery_days = None
    for ts, dd in curve[trough_index:]:
        if dd == 0:
            recovery_days = (ts - peak_ts) // DAY_MS
            break

    return max_dd, recovery_days


def profit_factor(trades: list[Trade]) -> Decimal:
    sells = [t for t in trades if t.side == Side.SELL and t.realized_pnl is not None]
    gains = sum((t.realized_pnl for t in sells if t.realized_pnl > 0), Decimal("0"))
    losses = sum((-t.realized_pnl for t in sells if t.realized_pnl < 0), Decimal("0"))
    if losses == 0:
        return Decimal("Infinity") if gains > 0 else Decimal("0")
    return gains / losses


def expectancy(trades: list[Trade]) -> Decimal:
    sells = [t for t in trades if t.side == Side.SELL and t.realized_pnl is not None]
    if not sells:
        return Decimal("0")
    wins = [t.realized_pnl for t in sells if t.realized_pnl > 0]
    losses = [-t.realized_pnl for t in sells if t.realized_pnl < 0]
    total = Decimal(len(sells))
    win_rate = Decimal(len(wins)) / total
    loss_rate = Decimal(len(losses)) / total
    avg_win = sum(wins, Decimal("0")) / Decimal(len(wins)) if wins else Decimal("0")
    avg_loss = sum(losses, Decimal("0")) / Decimal(len(losses)) if losses else Decimal("0")
    return win_rate * avg_win - loss_rate * avg_loss


def exposure_time_pct(snapshots: list[PortfolioSnapshot]) -> Decimal:
    if not snapshots:
        return Decimal("0")
    exposed = sum(1 for s in snapshots if s.position_value > 0)
    return Decimal(exposed) / Decimal(len(snapshots)) * Decimal(100)
