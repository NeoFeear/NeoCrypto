from market_data.binance import BinanceProvider
from market_data.kraken import KrakenProvider
from market_data.provider import MarketDataProvider


def build_provider(data_source: str) -> MarketDataProvider:
    """The only place that maps config.yaml's data_source string to a concrete
    provider. Callers (backtest.py, the live engine) must go through this —
    never import BinanceProvider/KrakenProvider directly (spec section 4)."""
    if data_source == "binance":
        return BinanceProvider()
    if data_source == "kraken":
        return KrakenProvider()
    raise ValueError(f"data_source inconnu: {data_source}")
