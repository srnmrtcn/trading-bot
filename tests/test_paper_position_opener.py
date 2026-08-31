import pytest
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timedelta
from decimal import Decimal
from src.paper_position_opener import open_qualifying_positions
from src.paper_equity import current_equity
from src.paper_sizer import size_position
from src.trading_costs import fee_and_slippage_cost_in_r
from src.strategy_version import STRATEGY_VERSION
from src.paper_trading_config import (
    STARTING_EQUITY, RISK_PCT, MIN_EXPECTED_R, MAX_CONCURRENT_POSITIONS,
    MAX_LEVERAGE, MAX_TOTAL_NOTIONAL_MULTIPLE
)
from src.timeutil import utc_now
from src.db.models import PaperPosition, Scenario

@pytest.fixture(scope='function')
def db_session():
    # Mock DB session setup
    Session = sessionmaker()
    yield Session()
    # Cleanup

@pytest.fixture(scope='function')
def paper_position_opener():
    return open_qualifying_positions

def create_scenario(session, **kwargs):
    defaults = {
        'symbol': 'BTCUSDT',
        'entry_price': Decimal('100'),
        'stop_price': Decimal('90'),
        'target_price': Decimal('110'),
        'expected_return_pct': Decimal('0.1'),
        'confidence_score': Decimal('0.8'),
        'calibrated_confidence': Decimal('0.8'),
        'created_at': utc_now() - timedelta(minutes=30),
        'expires_at': utc_now() + timedelta(hours=24),
        'status': 'pending',
        'strategy_version': STRATEGY_VERSION,
        'risk_pct': RISK_PCT,
    }
    params = defaults | kwargs
    scenario = Scenario(**params)
    session.add(scenario)
    session.commit()
    return scenario

def create_position(session, **kwargs):
    defaults = {
        'symbol': 'BTCUSDT',
        'entry_price': Decimal('100'),
        'stop_price': Decimal('90'),
        'position_size': Decimal('10'),
        'status': 'open',
        'strategy_version': STRATEGY_VERSION,
    }
    params = defaults | kwargs
    position = PaperPosition(**params)
    session.add(position)
    session.commit()
    return position

# Test 1: Basic scenario with all conditions met
def test_basic_scenario(db_session, paper_position_opener):
    session = db_session
    now = datetime(2026, 1, 1, 12, 0, 0)
    scenario = create_scenario(session, created_at=now - timedelta(minutes=30), expires_at=now + timedelta(hours=24))
    # Create existing position to test skipped
    create_position(session, status='open', strategy_version=STRATEGY_VERSION)
    result = paper_position_opener(session, now=now)
    assert result.opened_count == 1
    assert result.total_notional == Decimal('1000')
    assert result.scanned_count == 1
    # Check logs for skipped scenario
    # (Implement logging verification as needed)

# Test 2: Scenario with created_at older than MAX_SCENARIO_AGE
def test_old_created_at(db_session, paper_position_opener):
    session = db_session
    now = datetime(2026, 1, 1, 12, 0, 0)
    scenario = create_scenario(session, created_at=now - timedelta(hours=1), expires_at=now + timedelta(hours=24))
    result = paper_position_opener(session, now=now)
    assert result.scanned_count == 0

# Test 3: Scenario with calibrated_confidence None
def test_missing_calibrated_confidence(db_session, paper_position_opener):
    session = db_session
    now = datetime(2026, 1, 1, 12, 0, 0)
    scenario = create_scenario(session, calibrated_confidence=None, created_at=now - timedelta(minutes=30), expires_at=now + timedelta(hours=24))
    result = paper_position_opener(session, now=now)
    assert result.scanned_count == 0

# Test 4: Scenario with expired expires_at
def test_expired_scenario(db_session, paper_position_opener):
    session = db_session
    now = datetime(2026, 1, 1, 12, 0, 0)
    scenario = create_scenario(session, expires_at=now, created_at=now - timedelta(minutes=30))
    result = paper_position_opener(session, now=now)
    assert result.scanned_count == 0

# Test 5: Scenario with wrong strategy_version
def test_wrong_strategy_version(db_session, paper_position_opener):
    session = db_session
    now = datetime(2026, 1, 1, 12, 0, 0)
    scenario = create_scenario(session, strategy_version='2026.01.legacy-v0', created_at=now - timedelta(minutes=30), expires_at=now + timedelta(hours=24))
    result = paper_position_opener(session, now=now)
    assert result.scanned_count == 0

# Test 6: Scenario with existing PaperPosition
def test_existing_position(db_session, paper_position_opener):
    session = db_session
    now = datetime(2026, 1, 1, 12, 0, 0)
    scenario = create_scenario(session, created_at=now - timedelta(minutes=30), expires_at=now + timedelta(hours=24))
    create_position(session, symbol='BTCUSDT', status='open', strategy_version=STRATEGY_VERSION)
    result = paper_position_opener(session, now=now)
    assert result.scanned_count == 1
    assert result.opened_count == 0

# Test 7: Scenario with target_price not matching entry/stop
def test_target_price_mismatch(db_session, paper_position_opener):
    session = db_session
    now = datetime(2026, 1, 1, 12, 0, 0)
    scenario = create_scenario(session, target_price=Decimal('101'), created_at=now - timedelta(minutes=30), expires_at=now + timedelta(hours=24))
    result = paper_position_opener(session, now=now)
    assert result.scanned_count == 1
    assert result.opened_count == 0

# Test 8: Scenario with existing open position in same symbol
def test_existing_open_position(db_session, paper_position_opener):
    session = db_session
    now = datetime(2026, 1, 1, 12, 0, 0)
    scenario = create_scenario(session, created_at=now - timedelta(minutes=30), expires_at=now + timedelta(hours=24))
    create_position(session, symbol='BTCUSDT', status='open', strategy_version=STRATEGY_VERSION)
    result = paper_position_opener(session, now=now)
    assert result.scanned_count == 1
    assert result.opened_count == 0

# Test 9: Scenario with existing open position in different symbol
def test_existing_open_position_different_symbol(db_session, paper_position_opener):
    session = db_session
    now = datetime(2026, 1, 1, 12, 0, 0)
    scenario = create_scenario(session, created_at=now - timedelta(minutes=30), expires_at=now + timedelta(hours=24))
    create_position(session, symbol='ETHUSDT', status='open', strategy_version=STRATEGY_VERSION)
    result = paper_position_opener(session, now=now)
    assert result.scanned_count == 1
    assert result.opened_count == 1

# Test 10: Scenario with existing open position in same symbol but old strategy version
def test_existing_open_position_old_version(db_session, paper_position_opener):
    session = db_session
    now = datetime(2026, 1, 1, 12, 0, 0)
    scenario = create_scenario(session, created_at=now - timedelta(minutes=30), expires_at=now + timedelta(hours=24))
    create_position(session, symbol='BTCUSDT', status='open', strategy_version='2026.01.legacy-v0')
    result = paper_position_opener(session, now=now)
    assert result.scanned_count == 1
    assert result.opened_count == 1

# Test 11: Scenario with equity_after <= 0
def test_equity_zero(db_session, paper_position_opener):
    session = db_session
    now = datetime(2026, 1, 1, 12, 0, 0)
    scenario = create_scenario(session, created_at=now - timedelta(minutes=30), expires_at=now + timedelta(hours=24))
    # Set equity to zero
    session.query(PaperPosition).filter(PaperPosition.symbol == 'BTCUSDT').update({PaperPosition.equity_after: Decimal('0')})
    result = paper_position_opener(session, now=now)
    assert result.opened_count == 0
    assert result.total_notional == Decimal('0')
    # Check error log
    # (Implement logging verification as needed)
