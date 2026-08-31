from src.db.models import Symbol
from src.symbol_registry import refresh_symbols


class _FakeBinanceClient:
    def __init__(self, symbols, futures_symbols=None):
        self._symbols = symbols
        self._futures_symbols = futures_symbols or set()

    def get_active_usdt_symbols(self):
        return self._symbols

    def get_futures_usdt_symbols(self):
        return self._futures_symbols


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


def test_refresh_symbols_flags_symbols_that_have_a_futures_contract(db_session):
    fake = _FakeBinanceClient(
        [
            {"symbol": "BTCUSDT", "base_asset": "BTC", "quote_asset": "USDT"},
            {"symbol": "TINYUSDT", "base_asset": "TINY", "quote_asset": "USDT"},
        ],
        futures_symbols={"BTCUSDT"},
    )

    result = refresh_symbols(db_session, fake)

    assert result.futures_count == 1
    assert db_session.get(Symbol, "BTCUSDT").has_futures_contract is True
    assert db_session.get(Symbol, "TINYUSDT").has_futures_contract is False


def test_refresh_symbols_clears_the_flag_when_a_futures_contract_disappears(db_session):
    db_session.add(Symbol(
        symbol="GONEUSDT", base_asset="GONE", quote_asset="USDT",
        is_active=True, has_futures_contract=True,
    ))
    db_session.commit()
    fake = _FakeBinanceClient(
        [{"symbol": "GONEUSDT", "base_asset": "GONE", "quote_asset": "USDT"}],
        futures_symbols=set(),
    )

    refresh_symbols(db_session, fake)

    assert db_session.get(Symbol, "GONEUSDT").has_futures_contract is False
