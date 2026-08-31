import pytest
from decimal import Decimal
from datetime import datetime
from src.kline_fetcher import fetch_and_store, process_symbol_timeframe


def _mum(open_time, close=Decimal('100')):
    return {
        'open_time': open_time,
        'open': Decimal('99'),
        'high': Decimal('101'),
        'low': Decimal('98'),
        'close': close,
        'volume': Decimal('1000'),
    }


class SahteBinanceClient:
    def __init__(self):
        self.get_klines_call_count = 0
        self.get_futures_klines_call_count = 0

    def get_klines(self, symbol, timeframe, start_ms, end_ms):
        self.get_klines_call_count += 1
        return [_mum(datetime(2026, 1, 1, 0))]

    def get_futures_klines(self, symbol, timeframe, start_ms, end_ms):
        self.get_futures_klines_call_count += 1
        return [_mum(datetime(2026, 1, 1, 0))]


def test_1_varsayilan_spot_fetch_and_store(db_session):
    client = SahteBinanceClient()
    result = fetch_and_store(db_session, client, "BTCUSDT", "1h", 0, 1000)
    assert client.get_klines_call_count == 1
    assert client.get_futures_klines_call_count == 0


def test_2_futures_yolu_fetch_and_store(db_session):
    client = SahteBinanceClient()
    result = fetch_and_store(db_session, client, "BTCUSDT", "1h", 0, 1000, market="futures")
    assert client.get_klines_call_count == 0
    assert client.get_futures_klines_call_count == 1


def test_3_futures_yolu_yazar(db_session):
    client = SahteBinanceClient()
    result = fetch_and_store(db_session, client, "BTCUSDT", "1h", 0, 1000, market="futures")
    assert result.fetched == 2
    assert result.inserted == 2
    assert client.get_futures_klines_call_count == 1
    assert client.get_klines_call_count == 0


def test_4_gecersiz_market_fetch_and_store(db_session):
    client = SahteBinanceClient()
    result = fetch_and_store(db_session, client, "BTCUSDT", "1h", 0, 1000, market="forex")
    assert result.error is not None
    assert 'market' in result.error
    assert result.fetched == 0


def test_5_process_symbol_timeframe_gecirir(db_session):
    client = SahteBinanceClient()
    result = process_symbol_timeframe(db_session, client, "BTCUSDT", "1h", 0, 1000, market="futures")
    assert client.get_klines_call_count == 0
    assert client.get_futures_klines_call_count == 1


def test_6_process_symbol_timeframe_varsayilani(db_session):
    client = SahteBinanceClient()
    result = process_symbol_timeframe(db_session, client, "BTCUSDT", "1h", 0, 1000)
    assert client.get_klines_call_count == 1
    assert client.get_futures_klines_call_count == 0


def test_7_hata_izolasyonu_korunur(db_session):
    class HataliBinanceClient:
        def get_klines(self, symbol, timeframe, start_ms, end_ms):
            raise ValueError("DB connection failed")

        def get_futures_klines(self, symbol, timeframe, start_ms, end_ms):
            raise ValueError("DB connection failed")

    client = HataliBinanceClient()
    result = process_symbol_timeframe(db_session, client, "BTCUSDT", "1h", 0, 1000, market="futures")
    assert result.error is not None
    assert result.fetched == 0
