import pytest

from market_data.binance import BinanceProvider
from market_data.factory import build_provider
from market_data.kraken import KrakenProvider


def test_build_provider_binance():
    provider = build_provider("binance")
    assert isinstance(provider, BinanceProvider)


def test_build_provider_kraken():
    provider = build_provider("kraken")
    assert isinstance(provider, KrakenProvider)


def test_build_provider_unknown_raises_value_error():
    with pytest.raises(ValueError, match="stellar"):
        build_provider("stellar")
