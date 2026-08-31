from datetime import datetime, timedelta
from decimal import Decimal

from src.db.models import PaperPosition, Scenario
from src.paper_position_opener import open_qualifying_positions
from src.paper_trading_config import RISK_PCT, STARTING_EQUITY, MIN_EXPECTED_R, MAX_CONCURRENT_POSITIONS, MAX_TOTAL_NOTIONAL_MULTIPLE
from src.strategy_version import STRATEGY_VERSION


def _pending_scenario(symbol="BTCUSDT", direction="long", entry_price=Decimal("100"), stop_price=Decimal("90"),
                       target_price=Decimal("110"), calibrated_confidence=Decimal("0.8"),
                       created_at=None, expires_at=None, status="pending", strategy_version=STRATEGY_VERSION):
    created_at = created_at or datetime(2026, 1, 1, 12, 0, 0) - timedelta(minutes=30)
    expires_at = expires_at or created_at + timedelta(hours=24)
    return Scenario(
        symbol=symbol, direction=direction,
        entry_price=entry_price, target_price=target_price, stop_price=stop_price,
        expected_return_pct=Decimal("0.1"), confidence_score=calibrated_confidence,
        created_at=created_at, expires_at=expires_at, status=status,
        calibrated_confidence=calibrated_confidence,
        strategy_version=strategy_version,
    )


def _position(scenario, position_size=Decimal("10"), entry_price=Decimal("100"),
              status="open", strategy_version=STRATEGY_VERSION, equity_after=None, closed_at=None):
    return PaperPosition(
        scenario_id=scenario.id, symbol=scenario.symbol, direction=scenario.direction,
        entry_price=entry_price, stop_price=scenario.stop_price, target_price=scenario.target_price,
        risk_amount=Decimal("100"), position_size=position_size,
        opened_at=datetime(2026, 1, 1, 12, 0, 0), status=status,
        strategy_version=strategy_version,
        equity_after=equity_after,
        closed_at=closed_at,
    )


def test_1_basic_position_opening(db_session):
    scenario = _pending_scenario()
    db_session.add(scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026, 1, 1, 12, 0, 0))

    assert result.scanned == 1
    assert result.opened == 1
    assert result.skipped == 0
    assert result.failed == 0
    position = db_session.query(PaperPosition).first()
    assert position.scenario_id == scenario.id
    assert position.status == "open"
    assert position.opened_at == datetime(2026, 1, 1, 12, 0, 0)
    expected_risk = STARTING_EQUITY * RISK_PCT
    assert position.risk_amount == expected_risk
    assert position.position_size == expected_risk / Decimal("10")  # |100 - 90|
    assert position.strategy_version == STRATEGY_VERSION


def test_2_scenarios_created_more_than_an_hour_ago(db_session):
    scenario = _pending_scenario(created_at=datetime(2026, 1, 1, 11, 0, 0))
    db_session.add(scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026, 1, 1, 12, 0, 0))

    assert result.scanned == 0
    assert result.opened == 0
    assert result.skipped == 0
    assert result.failed == 0


def test_3_no_calibrated_confidence(db_session):
    scenario = _pending_scenario()
    scenario.calibrated_confidence = None
    db_session.add(scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026, 1, 1, 12, 0, 0))

    assert result.scanned == 0
    assert result.opened == 0
    assert result.skipped == 0
    assert result.failed == 0


def test_4_expired_scenario(db_session):
    scenario = _pending_scenario(expires_at=datetime(2026, 1, 1, 12, 0, 0))
    db_session.add(scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026, 1, 1, 12, 0, 0))

    assert result.scanned == 0
    assert result.opened == 0
    assert result.skipped == 0
    assert result.failed == 0


def test_5_wrong_strategy_version(db_session):
    scenario = _pending_scenario(strategy_version="2026.01.legacy-v0")
    db_session.add(scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026, 1, 1, 12, 0, 0))

    assert result.scanned == 0
    assert result.opened == 0
    assert result.skipped == 0
    assert result.failed == 0


def test_6_scenario_already_has_a_position(db_session):
    scenario = _pending_scenario()
    db_session.add(scenario)
    db_session.commit()
    db_session.add(_position(scenario))
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026, 1, 1, 12, 0, 0))

    assert result.scanned == 0
    assert result.opened == 0
    assert result.skipped == 0
    assert result.failed == 0


def test_7_target_price_too_close(db_session):
    scenario = _pending_scenario(target_price=Decimal("101"))
    db_session.add(scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026, 1, 1, 12, 0, 0))

    assert result.scanned == 1
    assert result.opened == 0
    assert result.skipped == 1
    assert result.failed == 0


def test_8_existing_position_on_same_symbol(db_session):
    scenario = _pending_scenario()
    db_session.add(scenario)
    db_session.commit()
    
    # Add a position for the same symbol but different strategy version
    other_scenario = _pending_scenario(symbol="BTCUSDT", direction="short")
    db_session.add(other_scenario)
    db_session.commit()
    db_session.add(_position(other_scenario, strategy_version="2026.01.legacy-v0"))
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026, 1, 1, 12, 0, 0))

    assert result.scanned == 1
    assert result.opened == 0
    assert result.skipped == 1
    assert result.failed == 0


def test_9_total_notional_exceeds_limit(db_session):
    scenario = _pending_scenario()
    db_session.add(scenario)
    db_session.commit()
    
    # Add a position for different symbol with large notional
    other_scenario = _pending_scenario(symbol="ETHUSDT", entry_price=Decimal("100"), position_size=Decimal("1001"))
    db_session.add(other_scenario)
    db_session.commit()
    db_session.add(_position(other_scenario, position_size=Decimal("1001")))
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026, 1, 1, 12, 0, 0))

    assert result.scanned == 1
    assert result.opened == 0
    assert result.skipped == 1
    assert result.failed == 0


def test_10_existing_position_with_different_strategy_version(db_session):
    scenario = _pending_scenario()
    db_session.add(scenario)
    db_session.commit()
    
    # Add a position for the same symbol but different strategy version
    other_scenario = _pending_scenario(symbol="BTCUSDT", direction="short")
    db_session.add(other_scenario)
    db_session.commit()
    db_session.add(_position(other_scenario, strategy_version="2026.01.legacy-v0"))
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026, 1, 1, 12, 0, 0))

    assert result.scanned == 1
    assert result.opened == 1
    assert result.skipped == 0
    assert result.failed == 0


def test_11_portfolio_wiped_out(db_session):
    scenario = _pending_scenario()
    db_session.add(scenario)
    db_session.commit()
    
    # Add a closed position with zero equity
    dead_scenario = _pending_scenario(symbol="OLDUSDT", direction="long")
    db_session.add(dead_scenario)
    db_session.commit()
    db_session.add(_position(dead_scenario, position_size=Decimal('10'), equity_after=Decimal('0'), closed_at=datetime(2026, 1, 1, 11, 0, 0)))
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026, 1, 1, 12, 0, 0))

    assert result.scanned == 0
    assert result.opened == 0
    assert result.skipped == 0
    assert result.failed == 0
