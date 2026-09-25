from decimal import Decimal

from sats import format_quantity, to_sats


def test_to_sats_converts_whole_btc():
    assert to_sats(Decimal("1")) == 100_000_000


def test_to_sats_converts_fraction():
    assert to_sats(Decimal("0.00050000")) == 50_000


def test_format_quantity_btc_symbol_shows_sats():
    text = format_quantity(Decimal("0.001"), "BTCUSDT")
    assert "BTC" in text
    assert "100,000 sats" in text


def test_format_quantity_non_btc_symbol_unchanged():
    assert format_quantity(Decimal("12.5"), "ETHUSDT") == "12.5"


def test_format_quantity_is_case_insensitive_on_symbol():
    assert "sats" in format_quantity(Decimal("1"), "btcusdt")
