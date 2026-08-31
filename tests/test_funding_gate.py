from datetime import datetime, timedelta
from decimal import Decimal

from src.db.models import FundingRate, Symbol
from src.funding_gate import FUNDING_DATA_MAX_AGE, FUNDING_RATE_THRESHOLD, funding_rejection

NOW = datetime(2026, 8, 25, 12, 5)


def _setup(db_session, has_futures=True, rate=None, fetched_at=NOW):
    db_session.add(Symbol(
        symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT",
        is_active=True, has_futures_contract=has_futures,
    ))
    if rate is not None:
        db_session.add(FundingRate(symbol="BTCUSDT", funding_rate=rate, fetched_at=fetched_at))
    db_session.commit()


def test_no_futures_contract_is_never_blocked(db_session):
    _setup(db_session, has_futures=False)
    assert funding_rejection(db_session, "BTCUSDT", "long", NOW) is None


def test_unknown_futures_contract_flag_is_never_blocked(db_session):
    # Rows that predate the column carry NULL until the daily refresh runs.
    _setup(db_session, has_futures=None)
    assert funding_rejection(db_session, "BTCUSDT", "long", NOW) is None


def test_symbol_absent_from_the_symbols_table_is_never_blocked(db_session):
    assert funding_rejection(db_session, "GHOSTUSDT", "long", NOW) is None


def test_missing_funding_data_blocks(db_session):
    _setup(db_session, has_futures=True, rate=None)
    assert funding_rejection(db_session, "BTCUSDT", "long", NOW) is not None


def test_stale_funding_data_blocks(db_session):
    _setup(db_session, rate=Decimal("0"), fetched_at=NOW - FUNDING_DATA_MAX_AGE - timedelta(minutes=1))
    assert funding_rejection(db_session, "BTCUSDT", "long", NOW) is not None


def test_funding_data_inside_the_tolerance_does_not_block(db_session):
    _setup(db_session, rate=Decimal("0"), fetched_at=NOW - FUNDING_DATA_MAX_AGE + timedelta(minutes=1))
    assert funding_rejection(db_session, "BTCUSDT", "long", NOW) is None


def test_crowded_longs_block_a_long_signal(db_session):
    _setup(db_session, rate=FUNDING_RATE_THRESHOLD + Decimal("0.0001"))
    assert funding_rejection(db_session, "BTCUSDT", "long", NOW) is not None


def test_crowded_longs_do_not_block_a_short_signal(db_session):
    _setup(db_session, rate=FUNDING_RATE_THRESHOLD + Decimal("0.0001"))
    assert funding_rejection(db_session, "BTCUSDT", "short", NOW) is None


def test_crowded_shorts_block_a_short_signal(db_session):
    _setup(db_session, rate=-FUNDING_RATE_THRESHOLD - Decimal("0.0001"))
    assert funding_rejection(db_session, "BTCUSDT", "short", NOW) is not None


def test_crowded_shorts_do_not_block_a_long_signal(db_session):
    _setup(db_session, rate=-FUNDING_RATE_THRESHOLD - Decimal("0.0001"))
    assert funding_rejection(db_session, "BTCUSDT", "long", NOW) is None


def test_exactly_at_the_threshold_does_not_block(db_session):
    # The comparison is strict, so the threshold value itself is allowed.
    _setup(db_session, rate=FUNDING_RATE_THRESHOLD)
    assert funding_rejection(db_session, "BTCUSDT", "long", NOW) is None


def test_normal_funding_does_not_block_either_direction(db_session):
    _setup(db_session, rate=Decimal("0.00005955"))
    assert funding_rejection(db_session, "BTCUSDT", "long", NOW) is None
    assert funding_rejection(db_session, "BTCUSDT", "short", NOW) is None
