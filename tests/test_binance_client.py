from datetime import datetime

from src.binance_client import BinanceClient


class _FakeClient:
    def __init__(self, exchange_info=None, kline_pages=None):
        self._exchange_info = exchange_info or {"symbols": []}
        self._kline_pages = kline_pages or []
        self._page_index = 0
        self.get_klines_calls = []

    def get_exchange_info(self):
        return self._exchange_info

    def get_klines(self, symbol, interval, startTime, endTime, limit):
        self.get_klines_calls.append({"startTime": startTime, "endTime": endTime})
        if self._page_index >= len(self._kline_pages):
            return []
        page = self._kline_pages[self._page_index]
        self._page_index += 1
        return page


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
