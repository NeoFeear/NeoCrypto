# analytics.py
from datetime import datetime, timezone
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


def _sell_realized_pnls(trades: list[Trade]) -> list[Decimal]:
    """Extract realized PnLs from SELL trades. Private helper to eliminate duplication."""
    return [t.realized_pnl for t in trades if t.side == Side.SELL and t.realized_pnl is not None]


def profit_factor(trades: list[Trade]) -> Decimal:
    pnls = _sell_realized_pnls(trades)
    gains = sum((pnl for pnl in pnls if pnl > 0), Decimal("0"))
    losses = sum((-pnl for pnl in pnls if pnl < 0), Decimal("0"))
    if losses == 0:
        return Decimal("Infinity") if gains > 0 else Decimal("0")
    return gains / losses


def trade_stats(trades: list[Trade]) -> dict[str, Decimal]:
    pnls = _sell_realized_pnls(trades)
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [-pnl for pnl in pnls if pnl < 0]
    total = Decimal(len(pnls))
    if total == 0:
        return {
            "win_rate": Decimal("0"), "loss_rate": Decimal("0"),
            "avg_win": Decimal("0"), "avg_loss": Decimal("0"),
            "win_count": Decimal("0"), "loss_count": Decimal("0"),
            "total_sells": Decimal("0"),
        }
    win_rate = Decimal(len(wins)) / total
    loss_rate = Decimal(len(losses)) / total
    avg_win = sum(wins, Decimal("0")) / Decimal(len(wins)) if wins else Decimal("0")
    avg_loss = sum(losses, Decimal("0")) / Decimal(len(losses)) if losses else Decimal("0")
    return {
        "win_rate": win_rate, "loss_rate": loss_rate,
        "avg_win": avg_win, "avg_loss": avg_loss,
        "win_count": Decimal(len(wins)), "loss_count": Decimal(len(losses)),
        "total_sells": total,
    }


def expectancy(trades: list[Trade]) -> Decimal:
    stats = trade_stats(trades)
    return stats["win_rate"] * stats["avg_win"] - stats["loss_rate"] * stats["avg_loss"]


def exposure_time_pct(snapshots: list[PortfolioSnapshot]) -> Decimal:
    if not snapshots:
        return Decimal("0")
    exposed = sum(1 for s in snapshots if s.position_value > 0)
    return Decimal(exposed) / Decimal(len(snapshots)) * Decimal(100)


def _periodic_returns(snapshots: list[PortfolioSnapshot]) -> list[Decimal]:
    ordered = sorted(snapshots, key=lambda s: s.timestamp)
    return [
        (ordered[i].total_value - ordered[i - 1].total_value) / ordered[i - 1].total_value
        for i in range(1, len(ordered))
    ]


def _stdev(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    mean = sum(values, Decimal("0")) / Decimal(len(values))
    variance = sum(((v - mean) ** 2 for v in values), Decimal("0")) / Decimal(len(values))
    return variance.sqrt()


def sharpe_ratio(snapshots: list[PortfolioSnapshot], periods_per_year: int) -> Decimal:
    returns = _periodic_returns(snapshots)
    if len(returns) < 2:
        return Decimal("0")
    mean_r = sum(returns, Decimal("0")) / Decimal(len(returns))
    stdev = _stdev(returns)
    if stdev == 0:
        return Decimal("0")
    return (mean_r / stdev) * Decimal(periods_per_year).sqrt()


def sortino_ratio(snapshots: list[PortfolioSnapshot], periods_per_year: int) -> Decimal:
    returns = _periodic_returns(snapshots)
    if len(returns) < 2:
        return Decimal("0")
    mean_r = sum(returns, Decimal("0")) / Decimal(len(returns))
    downside = [min(r, Decimal("0")) for r in returns]
    downside_variance = sum((d * d for d in downside), Decimal("0")) / Decimal(len(downside))
    downside_dev = downside_variance.sqrt()
    if downside_dev == 0:
        return Decimal("0")
    return (mean_r / downside_dev) * Decimal(periods_per_year).sqrt()


def calmar_ratio(cagr_value: Decimal, max_dd_pct: Decimal) -> Decimal:
    if max_dd_pct == 0:
        return Decimal("0")
    return cagr_value / max_dd_pct


def alpha_vs_buy_hold(strategy_return_pct: Decimal, buy_hold_return_pct: Decimal) -> Decimal:
    return strategy_return_pct - buy_hold_return_pct


def trade_distribution(trades: list[Trade], bucket_count: int = 10) -> list[dict]:
    pnls = _sell_realized_pnls(trades)
    if not pnls:
        return []
    lo, hi = min(pnls), max(pnls)
    if lo == hi:
        return [{"range_low": lo, "range_high": hi, "count": len(pnls)}]
    width = (hi - lo) / Decimal(bucket_count)
    buckets = [
        {"range_low": lo + width * i, "range_high": lo + width * (i + 1), "count": 0}
        for i in range(bucket_count)
    ]
    for pnl in pnls:
        idx = int((pnl - lo) / width)
        if idx >= bucket_count:
            idx = bucket_count - 1
        buckets[idx]["count"] += 1
    return buckets


def monthly_returns(snapshots: list[PortfolioSnapshot]) -> dict[str, Decimal]:
    ordered = sorted(snapshots, key=lambda s: s.timestamp)
    if not ordered:
        return {}

    def month_key(ts_ms: int) -> str:
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        return f"{dt.year:04d}-{dt.month:02d}"

    months: dict[str, list[PortfolioSnapshot]] = {}
    for s in ordered:
        months.setdefault(month_key(s.timestamp), []).append(s)

    result: dict[str, Decimal] = {}
    prev_last_value: Decimal | None = None
    for key in sorted(months.keys()):
        month_snaps = months[key]
        start_value = prev_last_value if prev_last_value is not None else month_snaps[0].total_value
        end_value = month_snaps[-1].total_value
        result[key] = Decimal("0") if start_value == 0 else (end_value - start_value) / start_value * Decimal(100)
        prev_last_value = end_value
    return result
