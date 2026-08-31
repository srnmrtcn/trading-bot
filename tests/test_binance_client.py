from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

from src.binance_client import BinanceClient


class _FakeClient:
    def __init__(self, exchange_info=None, kline_pages=None, futures_exchange_info=None, mark_price=None):
        self._exchange_info = exchange_info or {"symbols": []}
        self._kline_pages = kline_pages or []
        self._futures_exchange_info = futures_exchange_info or {"symbols": []}
        self._mark_price = mark_price or []
        self._page_index = 0
        self.get_klines_calls = []

    def get_exchange_info(self):
        return self._exchange_info

    def futures_exchange_info(self):
        return self._futures_exchange_info

    def futures_mark_price(self):
        return self._mark_price

    def get_klines(self, symbol, interval, startTime, endTime, limit):
        self.get_klines_calls.append({"startTime": startTime, "endTime": endTime})
        if self._page_index >= len(self._kline_pages):
            return []
        page = self._kline_pages[self._page_index]
        self._page_index += 1
        return page


def test_init_configures_a_request_timeout_so_the_client_never_hangs_forever():
    with patch("src.binance_client.Client") as mock_client_cls:
        BinanceClient()
        _, kwargs = mock_client_cls.call_args
        timeout = kwargs.get("requests_params", {}).get("timeout")
        assert timeout is not None and timeout > 0


def test_init_never_pings_binance_so_an_outage_cannot_crash_the_boot():
    # python-binance's Client pings the API inside __init__ by default. That
    # call sits outside every try/except in main.startup, so a DNS failure or
    # a temporary IP ban at boot would take the whole service down with it.
    with patch("src.binance_client.Client") as mock_client_cls:
        BinanceClient()
        _, kwargs = mock_client_cls.call_args
        assert kwargs.get("ping") is False


def test_get_active_usdt_symbols_filters_trading_and_usdt():
    fake = _FakeClient(exchange_info={"symbols": [
        {"symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT", "status": "TRADING"},
        {"symbol": "ETHBTC", "baseAsset": "ETH", "quoteAsset": "BTC", "status": "TRADING"},
        {"symbol": "XRPUSDT", "baseAsset": "XRP", "quoteAsset": "USDT", "status": "BREAK"},
    ]})
    client = BinanceClient(client=fake)
    result = client.get_active_usdt_symbols()
    assert result == [{"symbol": "BTCUSDT", "base_asset": "BTC", "quote_asset": "USDT"}]


def test_get_klines_parses_rows_into_decimal_dicts():
    fake = _FakeClient(kline_pages=[
        [[1735689600000, "100.5", "110.2", "90.1", "105.3", "1000.0"]],
    ])
    client = BinanceClient(client=fake)
    rows = client.get_klines("BTCUSDT", "1h", start_ms=1735689600000, end_ms=1735693200000)
    assert len(rows) == 1
    assert rows[0]["open_time"] == datetime(2025, 1, 1, 0, 0, 0)
    assert str(rows[0]["close"]) == "105.3"


def test_get_klines_paginates_past_1000_row_limit():
    page1 = [[1735689600000 + i * 3600000, "1", "1", "1", "1", "1"] for i in range(1000)]
    page2 = [[page1[-1][0] + 3600000, "2", "2", "2", "2", "2"]]
    fake = _FakeClient(kline_pages=[page1, page2])
    client = BinanceClient(client=fake)
    rows = client.get_klines("BTCUSDT", "1h", start_ms=1735689600000, end_ms=1735689600000 + 2000 * 3600000)
    assert len(rows) == 1001
    assert len(fake.get_klines_calls) == 2


def test_get_futures_usdt_symbols_keeps_only_trading_usdt_perpetuals():
    fake = _FakeClient(futures_exchange_info={"symbols": [
        {"symbol": "BTCUSDT", "status": "TRADING", "quoteAsset": "USDT", "contractType": "PERPETUAL"},
        {"symbol": "ETHUSDT", "status": "TRADING", "quoteAsset": "USDT", "contractType": "PERPETUAL"},
        {"symbol": "BTCUSDT_260626", "status": "TRADING", "quoteAsset": "USDT", "contractType": "CURRENT_QUARTER"},
        {"symbol": "BTCUSDC", "status": "TRADING", "quoteAsset": "USDC", "contractType": "PERPETUAL"},
        {"symbol": "DEADUSDT", "status": "BREAK", "quoteAsset": "USDT", "contractType": "PERPETUAL"},
    ]})
    client = BinanceClient(client=fake)

    assert client.get_futures_usdt_symbols() == {"BTCUSDT", "ETHUSDT"}


def test_get_funding_rates_returns_decimals_keyed_by_symbol():
    fake = _FakeClient(mark_price=[
        {"symbol": "BTCUSDT", "markPrice": "79636.12", "lastFundingRate": "0.00005955"},
        {"symbol": "ETHUSDT", "markPrice": "3000.00", "lastFundingRate": "-0.00012000"},
    ])
    client = BinanceClient(client=fake)

    rates = client.get_funding_rates()

    assert rates == {"BTCUSDT": Decimal("0.00005955"), "ETHUSDT": Decimal("-0.00012000")}
    # Parsed via str(), never float, so the stored rate is exact.
    assert isinstance(rates["BTCUSDT"], Decimal)


def test_get_funding_rates_skips_entries_without_a_funding_rate():
    # The bulk endpoint covers every contract type; not all of them carry a
    # lastFundingRate field.
    fake = _FakeClient(mark_price=[
        {"symbol": "BTCUSDT", "lastFundingRate": "0.00005955"},
        {"symbol": "WEIRDUSDT", "markPrice": "1.0"},
        {"symbol": "EMPTYUSDT", "lastFundingRate": ""},
    ])
    client = BinanceClient(client=fake)

    assert client.get_funding_rates() == {"BTCUSDT": Decimal("0.00005955")}


def test_get_funding_events_returns_datetime_and_decimals_keyed_by_symbol():
    fake = _FakeClient(mark_price=[
        {"symbol": "BTCUSDT", "nextFundingTime": "1735689600000", "lastFundingRate": "0.00005955", "markPrice": "79636.12"},
        {"symbol": "ETHUSDT", "nextFundingTime": "1735689600000", "lastFundingRate": "-0.00012000", "markPrice": "3000.00"},
    ])
    client = BinanceClient(client=fake)

    events = client.get_funding_events()

    assert events == {
        "BTCUSDT": (
            datetime(2025, 1, 1, 0, 0, 0),
            Decimal("0.00005955"),
            Decimal("79636.12")
        ),
        "ETHUSDT": (
            datetime(2025, 1, 1, 0, 0, 0),
            Decimal("-0.00012000"),
            Decimal("3000.00")
        )
    }
    # Parsed via str(), never float, so the stored rate is exact.
    assert isinstance(events["BTCUSDT"][1], Decimal)
    assert isinstance(events["BTCUSDT"][2], Decimal)


def test_get_funding_events_skips_entries_without_required_fields():
    fake = _FakeClient(mark_price=[
        {"symbol": "BTCUSDT", "nextFundingTime": "1735689600000", "lastFundingRate": "0.00005955", "markPrice": "79636.12"},
        {"symbol": "WEIRDUSDT", "markPrice": "1.0"},  # missing nextFundingTime and lastFundingRate
        {"symbol": "EMPTYUSDT", "nextFundingTime": "", "lastFundingRate": "0.00005955", "markPrice": "79636.12"},  # empty nextFundingTime
        {"symbol": "", "nextFundingTime": "1735689600000", "lastFundingRate": "0.00005955", "markPrice": "79636.12"},  # empty symbol
    ])
    client = BinanceClient(client=fake)

    assert client.get_funding_events() == {
        "BTCUSDT": (
            datetime(2025, 1, 1, 0, 0, 0),
            Decimal("0.00005955"),
            Decimal("79636.12")
        )
    }