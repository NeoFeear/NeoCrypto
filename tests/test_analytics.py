from datetime import datetime, timezone
from decimal import Decimal

import analytics
from engine.fifo_engine import Side, Trade
from models import PortfolioSnapshot

DAY_MS = 86_400_000
T0 = 1_700_000_000_000

# 10 daily snapshots: a rise to a peak, a drawdown, then a recovery past the old peak.
_TOTAL_VALUES = [1000, 1050, 1100, 900, 950, 1000, 1080, 1150, 1200, 1250]
_POSITION_VALUES = [800, 850, 900, 0, 0, 800, 880, 950, 1000, 1050]


def _snapshots() -> list[PortfolioSnapshot]:
    return [
        PortfolioSnapshot(
            timestamp=T0 + i * DAY_MS,
            symbol="BTCUSDT",
            cash_balance=Decimal(total) - Decimal(pos),
            position_value=Decimal(pos),
            total_value=Decimal(total),
            unrealized_pnl=Decimal("0"),
            realized_pnl_cumule=Decimal("0"),
        )
        for i, (total, pos) in enumerate(zip(_TOTAL_VALUES, _POSITION_VALUES))
    ]


def _trade(realized_pnl: Decimal, side: Side = Side.SELL, trade_id: int = 1) -> Trade:
    return Trade(
        id=trade_id, timestamp=1000, symbol="BTCUSDT", side=side,
        price=Decimal("100"), quantity=Decimal("1"), fee_pct=Decimal("0.001"),
        fee_amount=Decimal("0.1"), total_cost=Decimal("100"),
        realized_pnl=realized_pnl if side == Side.SELL else None,
        cash_balance_after=Decimal("1000"), strategy_name="t",
    )


def test_total_return_pct():
    assert analytics.total_return_pct(Decimal("1000"), Decimal("1250")) == Decimal("25.00")


def test_cagr_pct_matches_hand_computed_value():
    result = analytics.cagr_pct(Decimal("1000"), Decimal("1250"), days=9)
    assert result.quantize(Decimal("0.0001")) == Decimal("851507.3801")


def test_drawdown_curve_tracks_running_peak():
    curve = analytics.drawdown_curve(_snapshots())
    dd_values = [round(dd, 4) for _, dd in curve]
    assert dd_values[0] == Decimal("0")
    assert dd_values[2] == Decimal("0")  # new peak at index 2 (1100)
    assert dd_values[3].quantize(Decimal("0.0001")) == Decimal("18.1818")  # trough at index 3
    assert dd_values[7] == Decimal("0")  # recovered past old peak at index 7 (1150)


def test_max_drawdown_reports_magnitude_and_recovery_days():
    max_dd, recovery_days = analytics.max_drawdown(_snapshots())
    assert max_dd.quantize(Decimal("0.0001")) == Decimal("18.1818")
    assert recovery_days == 5


def test_max_drawdown_with_no_decline_is_zero_with_zero_recovery():
    flat = [
        PortfolioSnapshot(
            timestamp=T0 + i * DAY_MS, symbol="BTCUSDT",
            cash_balance=Decimal("1000"), position_value=Decimal("0"),
            total_value=Decimal("1000"), unrealized_pnl=Decimal("0"),
            realized_pnl_cumule=Decimal("0"),
        )
        for i in range(3)
    ]
    max_dd, recovery_days = analytics.max_drawdown(flat)
    assert max_dd == Decimal("0")
    assert recovery_days == 0


def test_profit_factor_normal_case():
    trades = [_trade(Decimal("50")), _trade(Decimal("-20")), _trade(Decimal("80")),
              _trade(Decimal("-30")), _trade(Decimal("10"))]
    # gains = 50+80+10 = 140 ; losses = 20+30 = 50 ; 140/50 = 2.8
    assert analytics.profit_factor(trades) == Decimal("2.8")


def test_profit_factor_no_losses_is_infinite():
    trades = [_trade(Decimal("50")), _trade(Decimal("10"))]
    assert analytics.profit_factor(trades) == Decimal("Infinity")


def test_profit_factor_no_sell_trades_is_zero():
    trades = [_trade(None, side=Side.BUY)]
    assert analytics.profit_factor(trades) == Decimal("0")


def test_expectancy_matches_hand_computed_value():
    trades = [_trade(Decimal("50")), _trade(Decimal("-20")), _trade(Decimal("80")),
              _trade(Decimal("-30")), _trade(Decimal("10"))]
    # win_rate=3/5=0.6 avg_win=140/3 ; loss_rate=0.4 avg_loss=50/2=25
    # expectancy = 0.6*(140/3) - 0.4*25 = 28 - 10 = 18
    assert analytics.expectancy(trades).quantize(Decimal("0.0001")) == Decimal("18.0000")


def test_exposure_time_pct():
    # 8 of 10 snapshots have position_value > 0 (indices 3,4 are flat cash)
    assert analytics.exposure_time_pct(_snapshots()) == Decimal("80.00")
