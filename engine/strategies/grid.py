from dataclasses import dataclass
from decimal import Decimal


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
