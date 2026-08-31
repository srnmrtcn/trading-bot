from datetime import datetime, timedelta
from decimal import Decimal

from src.db.models import PaperPosition, Scenario
from src.paper_position_opener import open_qualifying_positions
from src.paper_trading_config import RISK_PCT, STARTING_EQUITY, MIN_EXPECTED_R, MAX_CONCURRENT_POSITIONS, MAX_TOTAL_NOTIONAL_MULTIPLE
from src.strategy_version import STRATEGY_VERSION


def _pending_scenario(symbol="BTCUSDT", direction="long", calibrated_confidence=Decimal("0.8"),
                       entry_price=Decimal("100"), stop_price=Decimal("90"), target_price=Decimal("110"),
                       created_at=None, strategy_version=STRATEGY_VERSION):
    created_at = created_at or datetime(2026, 1, 1, 12, 0, 0)
    return Scenario(
        symbol=symbol, direction=direction,
        entry_price=entry_price, target_price=target_price, stop_price=stop_price,
        expected_return_pct=Decimal("0.1"), confidence_score=calibrated_confidence,
        created_at=created_at, expires_at=created_at + timedelta(hours=24), status="pending",
        calibrated_confidence=calibrated_confidence,
        strategy_version=strategy_version,
    )


def _position(scenario, position_size=Decimal("10"), entry_price=Decimal("100"), status="open", strategy_version=STRATEGY_VERSION, equity_after=None, closed_at=None):
    return PaperPosition(
        scenario_id=scenario.id, symbol=scenario.symbol, direction=scenario.direction,
        entry_price=entry_price, stop_price=scenario.stop_price, target_price=scenario.target_price,
        risk_amount=position_size * (entry_price - scenario.stop_price), position_size=position_size,
        opened_at=datetime(2026, 1, 1, 12, 0, 0), status=status,
        strategy_version=strategy_version,
        equity_after=equity_after,
        closed_at=closed_at,
    )


NOW = datetime(2026, 1, 1, 12, 0, 0)


def test_opens_a_position_for_a_qualifying_scenario(db_session):
    scenario = _pending_scenario()
    db_session.add(scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=NOW)

    assert result.scanned == 1
    assert result.opened == 1
    assert result.skipped == 0
    position = db_session.query(PaperPosition).first()
    assert position.scenario_id == scenario.id
    assert position.status == "open"
    assert position.opened_at == NOW
    expected_risk = STARTING_EQUITY * RISK_PCT
    assert position.risk_amount == expected_risk
    assert position.position_size == expected_risk / Decimal("10")  # |100 - 90|
    assert position.strategy_version == STRATEGY_VERSION


def test_scanned_is_zero_when_created_at_older_than_max_age(db_session):
    scenario = _pending_scenario(created_at=NOW - timedelta(hours=2))
    db_session.add(scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=NOW)

    assert result.scanned == 0
    assert result.opened == 0


def test_scanned_is_zero_when_calibrated_confidence_is_none(db_session):
    scenario = _pending_scenario()
    scenario.calibrated_confidence = None
    db_session.add(scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=NOW)

    assert result.scanned == 0
    assert db_session.query(PaperPosition).count() == 0


def test_scanned_is_zero_when_expires_at_equals_now(db_session):
    scenario = _pending_scenario(expires_at=NOW)
    db_session.add(scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=NOW)

    assert result.scanned == 0
    assert db_session.query(PaperPosition).count() == 0


def test_scanned_is_zero_when_strategy_version_differs(db_session):
    scenario = _pending_scenario(strategy_version="2026.01.legacy-v0")
    db_session.add(scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=NOW)

    assert result.scanned == 0
    assert db_session.query(PaperPosition).count() == 0


def test_scanned_is_zero_when_a_position_already_exists_for_the_scenario(db_session):
    scenario = _pending_scenario()
    db_session.add(scenario)
    db_session.commit()
    db_session.add(_position(scenario))
    db_session.commit()

    result = open_qualifying_positions(db_session, now=NOW)

    assert result.scanned == 0
    assert result.opened == 0


def test_skipped_when_target_price_is_too_close_to_entry(db_session):
    scenario = _pending_scenario(target_price=Decimal("101"))
    db_session.add(scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=NOW)

    assert result.scanned == 1
    assert result.opened == 0
    assert result.skipped == 1


def test_skipped_when_symbol_already_has_an_open_position(db_session):
    scenario = _pending_scenario()
    db_session.add(scenario)
    db_session.commit()
    
    # Add an existing open position for the same symbol with current strategy version
    existing_position = _position(scenario, position_size=Decimal("10"), status="open")
    db_session.add(existing_position)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=NOW)

    assert result.scanned == 1
    assert result.opened == 0
    assert result.skipped == 1


def test_skipped_when_total_notional_exceeds_limit(db_session):
    scenario = _pending_scenario()
    db_session.add(scenario)
    db_session.commit()
    
    # Add an existing open position that pushes us over the limit
    existing_position = _position(scenario, position_size=Decimal("1001"), entry_price=Decimal("100"), status="open")
    db_session.add(existing_position)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=NOW)

    assert result.scanned == 1
    assert result.opened == 0
    assert result.skipped == 1


def test_opened_when_strategy_version_is_different_but_no_existing_position_exists(db_session):
    # Add an existing position with different strategy version for same symbol
    old_scenario = _pending_scenario(strategy_version="2026.01.legacy-v0")
    db_session.add(old_scenario)
    db_session.commit()
    
    old_position = _position(old_scenario, position_size=Decimal("10"), status="open", strategy_version="2026.01.legacy-v0")
    db_session.add(old_position)
    db_session.commit()

    # Now add a new scenario with current strategy version
    new_scenario = _pending_scenario()
    db_session.add(new_scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=NOW)

    assert result.scanned == 1
    assert result.opened == 1
    assert result.skipped == 0


def test_scanned_is_zero_when_portfolio_is_wiped_out(db_session):
    # Create a scenario that would normally be opened
    scenario = _pending_scenario()
    db_session.add(scenario)
    db_session.commit()
    
    # Add a closed position with zero equity after
    dead_position = _position(scenario, position_size=Decimal("10"), status="closed", equity_after=Decimal("0"))
    db_session.add(dead_position)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=NOW)

    assert result.scanned == 0
    assert result.opened == 0
    assert result.skipped == 0
    assert result.failed == 0
