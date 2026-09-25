# sats.py -- BTC <-> satoshi display helpers.
from decimal import Decimal

SATS_PER_BTC = Decimal("100000000")


def to_sats(btc_quantity: Decimal) -> int:
    return int((btc_quantity * SATS_PER_BTC).to_integral_value())


def format_quantity(quantity: Decimal, symbol: str) -> str:
    """BTC-denominated symbols (BTCUSDT, BTCEUR, ...) display in satoshis
    alongside the raw BTC amount -- satoshis are the more legible unit for a
    fraction of a bitcoin. Every other symbol is unaffected."""
    if symbol.upper().startswith("BTC"):
        return f"{quantity} BTC (~{to_sats(quantity):,} sats)"
    return str(quantity)
