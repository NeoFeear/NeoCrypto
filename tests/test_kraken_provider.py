from decimal import Decimal

import httpx
import pytest

from market_data.kraken import KrakenProvider


def _client_with(handler) -> httpx.Client:
    transport = httpx.MockTransport(handler)
    return httpx.Client(transport=transport, base_url="https://api.kraken.com")


def test_get_klines_parses_kraken_ohlc_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/0/public/OHLC"
        assert request.url.params["pair"] == "XBTUSDT"
        assert request.url.params["interval"] == "60"
        return httpx.Response(
            200,
            json={
                "error": [],
                "result": {
                    "XBTUSDT": [
                        [1000, "100.0", "101.5", "99.0", "100.8", "100.4", "12.3", 5]
                    ],
                    "last": 1000,
                },
            },
        )

    provider = KrakenProvider(client=_client_with(handler))
    klines = provider.get_klines("BTCUSDT", "1h", 0, 2_000_000)

    assert len(klines) == 1
    k = klines[0]
    assert k.open_time_ms == 1_000_000
    assert k.open == Decimal("100.0")
    assert k.high == Decimal("101.5")
    assert k.low == Decimal("99.0")
    assert k.close == Decimal("100.8")
    assert k.volume == Decimal("12.3")


def test_get_klines_stops_at_end_ms():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "error": [],
                "result": {
                    "XBTUSDT": [
                        [1000, "100.0", "101.0", "99.0", "100.5", "100.2", "1", 1],
                        [4000, "100.5", "102.0", "100.0", "101.0", "100.7", "1", 1],
                    ],
                    "last": 4000,
                },
            },
        )

    provider = KrakenProvider(client=_client_with(handler))
    klines = provider.get_klines("BTCUSDT", "1h", 0, 2_000_000)

    assert len(klines) == 1
    assert klines[0].open_time_ms == 1_000_000


def test_get_book_ticker_parses_kraken_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/0/public/Ticker"
        assert request.url.params["pair"] == "XBTUSDT"
        return httpx.Response(
            200,
            json={
                "error": [],
                "result": {"XBTUSDT": {"a": ["100.10", "1", "1"], "b": ["100.00", "1", "1"], "c": ["100.05", "1"], "v": ["10", "200"]}},
            },
        )

    provider = KrakenProvider(client=_client_with(handler))
    bt = provider.get_book_ticker("BTCUSDT")

    assert bt.symbol == "BTCUSDT"
    assert bt.bid_price == Decimal("100.00")
    assert bt.ask_price == Decimal("100.10")


def test_get_ticker_24h_approximates_quote_volume():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "error": [],
                "result": {"XBTUSDT": {"a": ["100.10", "1", "1"], "b": ["100.00", "1", "1"], "c": ["100.00", "1"], "v": ["10", "200"]}},
            },
        )

    provider = KrakenProvider(client=_client_with(handler))
    t = provider.get_ticker_24h("BTCUSDT")

    # base 24h volume (200) * last price (100.00)
    assert t.quote_volume == Decimal("20000.00")


def test_unmapped_symbol_raises_value_error():
    provider = KrakenProvider(client=_client_with(lambda r: httpx.Response(200, json={})))
    with pytest.raises(ValueError, match="BNBUSDT"):
        provider.get_klines("BNBUSDT", "1h", 0, 1000)


def test_kraken_error_payload_raises_runtime_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": ["EQuery:Unknown asset pair"], "result": {}})

    provider = KrakenProvider(client=_client_with(handler))
    with pytest.raises(RuntimeError, match="EQuery"):
        provider.get_klines("BTCUSDT", "1h", 0, 1000)
