from decimal import Decimal

from indicators import atr, fibonacci_levels, rsi, true_range


def test_rsi_none_when_not_enough_history():
    assert rsi([Decimal("100")], period=14) is None


def test_rsi_all_gains_is_100():
    closes = [Decimal(100 + i) for i in range(15)]  # 14 consecutive +1 moves
    assert rsi(closes, period=14) == Decimal("100")


def test_rsi_all_losses_is_0():
    closes = [Decimal(100 - i) for i in range(15)]  # 14 consecutive -1 moves
    assert rsi(closes, period=14) == Decimal("0")


def test_rsi_known_textbook_example():
    # 4 up days of +1, 4 down days of -1, alternating -> avg_gain=avg_loss -> RSI=50
    closes = [Decimal("100")]
    for _ in range(4):
        closes.append(closes[-1] + 1)
        closes.append(closes[-1] - 1)
    assert rsi(closes, period=8) == Decimal("50")


def test_true_range_no_prev_close_is_high_minus_low():
    assert true_range(Decimal("110"), Decimal("100"), None) == Decimal("10")


def test_true_range_gap_up_uses_high_minus_prev_close():
    # Gapped up: prev_close=100, today's range 105-108 -- true range must
    # capture the gap (108-100=8), not just today's own 3-point range.
    assert true_range(Decimal("108"), Decimal("105"), Decimal("100")) == Decimal("8")


def test_true_range_gap_down_uses_prev_close_minus_low():
    assert true_range(Decimal("95"), Decimal("92"), Decimal("100")) == Decimal("8")


def test_atr_none_when_not_enough_history():
    assert atr([Decimal("110")], [Decimal("100")], [Decimal("105")], period=14) is None


def test_atr_constant_range_equals_that_range():
    # Every bar has the exact same 10-point high-low range and closes never
    # gap beyond it -> ATR must equal that constant range.
    highs = [Decimal("110")] * 15
    lows = [Decimal("100")] * 15
    closes = [Decimal("105")] * 15
    assert atr(highs, lows, closes, period=14) == Decimal("10")


def test_fibonacci_levels_standard_ratios():
    levels = fibonacci_levels(Decimal("100"), Decimal("200"))
    assert levels == [
        Decimal("100"), Decimal("123.6"), Decimal("138.2"), Decimal("150.0"),
        Decimal("161.8"), Decimal("178.6"), Decimal("200"),
    ]
