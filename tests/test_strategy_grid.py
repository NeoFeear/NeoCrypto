from decimal import Decimal

import pytest

from engine.strategies.grid import GridLevel, build_grid_levels


def test_build_grid_levels_arithmetic_spacing():
    levels = build_grid_levels(lower_bound=100, upper_bound=400, n_levels=3, spacing="arithmetic")

    assert len(levels) == 3
    # boundaries: 100, 200, 300, 400 (step 100)
    assert [lvl.buy_price for lvl in levels] == [Decimal("100"), Decimal("200"), Decimal("300")]
    assert [lvl.sell_price for lvl in levels] == [Decimal("200"), Decimal("300"), Decimal("400")]
    assert all(lvl.state == "EMPTY" and lvl.filled_quantity is None for lvl in levels)


def test_build_grid_levels_geometric_spacing():
    levels = build_grid_levels(lower_bound=100, upper_bound=400, n_levels=2, spacing="geometric")

    assert len(levels) == 2
    # ratio = sqrt(400/100) = 2 ; boundaries: 100, 200, 400
    assert levels[0].buy_price == Decimal("100")
    assert levels[0].sell_price == Decimal("200")
    assert levels[1].buy_price == Decimal("200")
    assert levels[1].sell_price == Decimal("400")


def test_build_grid_levels_unknown_spacing_raises():
    with pytest.raises(ValueError, match="triangular"):
        build_grid_levels(lower_bound=100, upper_bound=200, n_levels=1, spacing="triangular")


def test_build_grid_levels_accepts_int_and_float_params_without_float_leak():
    # JSON-loaded params can be plain int/float — must not leak float into Decimal math
    levels = build_grid_levels(lower_bound=25000, upper_bound=35000, n_levels=10, spacing="arithmetic")
    assert isinstance(levels[0].buy_price, Decimal)
    assert levels[0].buy_price == Decimal("25000")
    assert levels[-1].sell_price == Decimal("35000")


def test_build_grid_levels_geometric_endpoints_are_exact_even_with_irrational_ratio():
    levels = build_grid_levels(lower_bound=3, upper_bound=10, n_levels=5, spacing="geometric")
    assert levels[0].buy_price == Decimal("3")
    assert levels[-1].sell_price == Decimal("10")
