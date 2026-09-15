from decimal import Decimal

import httpx
import pytest

from market_data.binance import BinanceProvider


def _client_with(handler) -> httpx.Client:
    transport = httpx.MockTransport(handler)
    return httpx.Client(transport=transport, base_url="https://data-api.binance.vision")


def test_get_klines_parses_binance_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v3/klines"
        assert request.url.params["symbol"] == "BTCUSDT"
        assert request.url.params["interval"] == "1h"
        assert request.url.params["startTime"] == "1000"
        assert request.url.params["endTime"] == "2000"
        assert request.url.params["limit"] == "1000"
        return httpx.Response(
            200,
            json=[
                [1000, "100.0", "101.5", "99.0", "100.8", "12.3", 3599999, "0", 0, "0", "0", "0"]
            ],
        )

    provider = BinanceProvider(client=_client_with(handler))
    klines = provider.get_klines("BTCUSDT", "1h", 1000, 2000)

    assert len(klines) == 1
    k = klines[0]
    assert k.open_time_ms == 1000
    assert k.open == Decimal("100.0")
    assert k.high == Decimal("101.5")
    assert k.low == Decimal("99.0")
    assert k.close == Decimal("100.8")
    assert k.volume == Decimal("12.3")
    assert k.close_time_ms == 3599999


def test_get_book_ticker_parses_binance_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v3/ticker/bookTicker"
        assert request.url.params["symbol"] == "BTCUSDT"
        return httpx.Response(
            200,
            json={"symbol": "BTCUSDT", "bidPrice": "100.00", "bidQty": "1", "askPrice": "100.10", "askQty": "1"},
        )

    provider = BinanceProvider(client=_client_with(handler))
    bt = provider.get_book_ticker("BTCUSDT")

    assert bt.symbol == "BTCUSDT"
    assert bt.bid_price == Decimal("100.00")
    assert bt.ask_price == Decimal("100.10")


def test_get_ticker_24h_parses_binance_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v3/ticker/24hr"
        assert request.url.params["symbol"] == "BTCUSDT"
        return httpx.Response(200, json={"symbol": "BTCUSDT", "quoteVolume": "60000000.50"})

    provider = BinanceProvider(client=_client_with(handler))
    t = provider.get_ticker_24h("BTCUSDT")

    assert t.symbol == "BTCUSDT"
    assert t.quote_volume == Decimal("60000000.50")


def test_get_klines_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(451, json={"code": -1, "msg": "restricted location"})

    provider = BinanceProvider(client=_client_with(handler))
    with pytest.raises(httpx.HTTPStatusError):
        provider.get_klines("BTCUSDT", "1h", 1000, 2000)
