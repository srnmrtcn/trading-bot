import pytest
from decimal import Decimal
from datetime import datetime

from src.db.models import FundingRateHistory, Symbol
from src.funding_collector import record_funding_event, funding_events_between, refresh_funding_history


def test_record_funding_event_inserts_a_new_row(db_session):
    yazildi = record_funding_event(db_session, 'BTCUSDT', datetime(2026, 1, 1, 8),
                                   Decimal('0.0001'), Decimal('50000'))
    db_session.commit()
    assert yazildi is True
    assert db_session.query(FundingRateHistory).count() == 1


def test_record_funding_event_returns_false_on_duplicate(db_session):
    record_funding_event(db_session, 'BTCUSDT', datetime(2026, 1, 1, 8),
                         Decimal('0.0001'), Decimal('50000'))
    db_session.commit()
    yazildi = record_funding_event(db_session, 'BTCUSDT', datetime(2026, 1, 1, 8),
                                    Decimal('0.0002'), Decimal('50100'))
    db_session.commit()
    assert yazildi is False
    assert db_session.query(FundingRateHistory).count() == 1


def test_record_funding_event_does_not_commit(db_session):
    record_funding_event(db_session, 'BTCUSDT', datetime(2026, 1, 1, 8),
                         Decimal('0.0001'), Decimal('50000'))
    db_session.rollback()
    assert db_session.query(FundingRateHistory).count() == 0


def test_funding_events_between_includes_end_excludes_start(db_session):
    db_session.add(FundingRateHistory(
        symbol='BTCUSDT',
        funding_time=datetime(2026, 1, 1, 7),
        funding_rate=Decimal('0.0001'),
        mark_price=Decimal('50000')
    ))
    db_session.add(FundingRateHistory(
        symbol='BTCUSDT',
        funding_time=datetime(2026, 1, 1, 8),
        funding_rate=Decimal('0.0002'),
        mark_price=Decimal('50100')
    ))
    db_session.commit()

    events = funding_events_between(db_session, 'BTCUSDT', datetime(2026, 1, 1, 7), datetime(2026, 1, 1, 8))
    assert len(events) == 1
    assert events[0][0] == datetime(2026, 1, 1, 8)
    assert events[0][1] == Decimal('0.0002')
    assert events[0][2] == Decimal('50100')


def test_funding_events_between_returns_sorted_by_funding_time(db_session):
    db_session.add(FundingRateHistory(
        symbol='BTCUSDT',
        funding_time=datetime(2026, 1, 1, 9),
        funding_rate=Decimal('0.0003'),
        mark_price=Decimal('50300')
    ))
    db_session.add(FundingRateHistory(
        symbol='BTCUSDT',
        funding_time=datetime(2026, 1, 1, 8),
        funding_rate=Decimal('0.0002'),
        mark_price=Decimal('50100')
    ))
    db_session.commit()

    events = funding_events_between(db_session, 'BTCUSDT', datetime(2026, 1, 1, 7), datetime(2026, 1, 1, 9))
    assert len(events) == 2
    assert events[0][0] == datetime(2026, 1, 1, 8)
    assert events[1][0] == datetime(2026, 1, 1, 9)


def test_funding_events_between_returns_empty_list_when_no_records(db_session):
    events = funding_events_between(db_session, 'BTCUSDT', datetime(2026, 1, 1, 7), datetime(2026, 1, 1, 8))
    assert events == []


def test_funding_events_between_excludes_other_symbols(db_session):
    db_session.add(FundingRateHistory(
        symbol='BTCUSDT',
        funding_time=datetime(2026, 1, 1, 8),
        funding_rate=Decimal('0.0001'),
        mark_price=Decimal('50000')
    ))
    db_session.add(FundingRateHistory(
        symbol='ETHUSDT',
        funding_time=datetime(2026, 1, 1, 8),
        funding_rate=Decimal('0.0002'),
        mark_price=Decimal('3000')
    ))
    db_session.commit()

    events = funding_events_between(db_session, 'BTCUSDT', datetime(2026, 1, 1, 7), datetime(2026, 1, 1, 8))
    assert len(events) == 1
    assert events[0][0] == datetime(2026, 1, 1, 8)
    assert events[0][1] == Decimal('0.0001')
    assert events[0][2] == Decimal('50000')


def test_refresh_funding_history_inserts_two_records_for_two_symbols(db_session):
    db_session.add(Symbol(symbol='BTCUSDT', base_asset='BTC', quote_asset='USDT', has_futures_contract=True))
    db_session.add(Symbol(symbol='ETHUSDT', base_asset='ETH', quote_asset='USDT', has_futures_contract=True))
    db_session.commit()

    class MockBinanceClient:
        def get_funding_events(self):
            return {
                'BTCUSDT': (datetime(2026, 1, 1, 8), Decimal('0.0001'), Decimal('50000')),
                'ETHUSDT': (datetime(2026, 1, 1, 8), Decimal('0.0002'), Decimal('3000'))
            }

    count = refresh_funding_history(db_session, MockBinanceClient(), datetime(2026, 1, 1, 8))
    assert count == 2
    assert db_session.query(FundingRateHistory).count() == 2


def test_refresh_funding_history_returns_zero_for_duplicate_events(db_session):
    db_session.add(Symbol(symbol='BTCUSDT', base_asset='BTC', quote_asset='USDT', has_futures_contract=True))
    db_session.commit()

    class MockBinanceClient:
        def get_funding_events(self):
            return {
                'BTCUSDT': (datetime(2026, 1, 1, 8), Decimal('0.0001'), Decimal('50000'))
            }

    refresh_funding_history(db_session, MockBinanceClient(), datetime(2026, 1, 1, 8))
    count = refresh_funding_history(db_session, MockBinanceClient(), datetime(2026, 1, 1, 8))
    assert count == 0
    assert db_session.query(FundingRateHistory).count() == 1


def test_refresh_funding_history_skips_non_futures_contracts(db_session):
    db_session.add(Symbol(symbol='BTCUSDT', base_asset='BTC', quote_asset='USDT', has_futures_contract=False))
    db_session.commit()

    class MockBinanceClient:
        def get_funding_events(self):
            return {
                'BTCUSDT': (datetime(2026, 1, 1, 8), Decimal('0.0001'), Decimal('50000'))
            }

    count = refresh_funding_history(db_session, MockBinanceClient(), datetime(2026, 1, 1, 8))
    assert count == 0
    assert db_session.query(FundingRateHistory).count() == 0


def test_refresh_funding_history_skips_none_values(db_session):
    db_session.add(Symbol(symbol='BTCUSDT', base_asset='BTC', quote_asset='USDT', has_futures_contract=True))
    db_session.commit()

    class MockBinanceClient:
        def get_funding_events(self):
            return {
                'BTCUSDT': (datetime(2026, 1, 1, 8), None, Decimal('50000'))
            }

    count = refresh_funding_history(db_session, MockBinanceClient(), datetime(2026, 1, 1, 8))
    assert count == 0
    assert db_session.query(FundingRateHistory).count() == 0
