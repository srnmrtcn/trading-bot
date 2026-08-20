from src.db.models import Symbol
from src.symbol_registry import refresh_symbols


class _FakeBinanceClient:
    def __init__(self, symbols):
        self._symbols = symbols

    def get_active_usdt_symbols(self):
        return self._symbols


def test_refresh_symbols_inserts_new_active_symbols(db_session):
    fake = _FakeBinanceClient([
        {"symbol": "BTCUSDT", "base_asset": "BTC", "quote_asset": "USDT"},
        {"symbol": "ETHUSDT", "base_asset": "ETH", "quote_asset": "USDT"},
    ])
    result = refresh_symbols(db_session, fake)
    assert result.active_count == 2
    assert result.deactivated_count == 0
    assert db_session.get(Symbol, "BTCUSDT").is_active is True


def test_refresh_symbols_deactivates_delisted_symbol(db_session):
    db_session.add(Symbol(symbol="OLDUSDT", base_asset="OLD", quote_asset="USDT", is_active=True))
    db_session.commit()
    fake = _FakeBinanceClient([{"symbol": "BTCUSDT", "base_asset": "BTC", "quote_asset": "USDT"}])
    result = refresh_symbols(db_session, fake)
    assert result.deactivated_count == 1
    assert db_session.get(Symbol, "OLDUSDT").is_active is False
