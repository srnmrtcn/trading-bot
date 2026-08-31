from datetime import datetime, timedelta
from decimal import Decimal

from src.db.models import PaperPosition, Scenario
from src.paper_position_opener import open_qualifying_positions
from src.paper_trading_config import RISK_PCT, STARTING_EQUITY, MIN_EXPECTED_R, MAX_CONCURRENT_POSITIONS, MAX_TOTAL_NOTIONAL_MULTIPLE
from src.strategy_version import STRATEGY_VERSION


def _pending_scenario(symbol="BTCUSDT", direction="long", entry_price=Decimal("100"), stop_price=Decimal("90"), target_price=Decimal("110"),
                       calibrated_confidence=Decimal("0.8"), created_at=None, expires_at=None, status="pending", strategy_version=STRATEGY_VERSION):
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


def _position(scenario, position_size=Decimal("10"), entry_price=Decimal("100"), status="open", strategy_version=STRATEGY_VERSION, equity_after=None, closed_at=None):
    return PaperPosition(
        scenario_id=scenario.id, symbol=scenario.symbol, direction=scenario.direction,
        entry_price=entry_price, stop_price=scenario.stop_price, target_price=scenario.target_price,
        risk_amount=Decimal("100"), position_size=position_size,
        opened_at=datetime(2026, 1, 1, 12, 0, 0), status=status,
        strategy_version=strategy_version,
        equity_after=equity_after,
        closed_at=closed_at,
    )


def test_opens_a_position_for_a_qualifying_scenario(db_session):
    scenario = _pending_scenario()
    db_session.add(scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026, 1, 1, 12, 0, 0))

    assert result.scanned == 1
    assert result.opened == 1
    assert result.skipped == 0
    position = db_session.query(PaperPosition).first()
    assert position.scenario_id == scenario.id
    assert position.status == "open"
    assert position.opened_at == datetime(2026, 1, 1, 12, 0, 0)
    expected_risk = STARTING_EQUITY * RISK_PCT
    assert position.risk_amount == expected_risk
    assert position.position_size == expected_risk / Decimal("10")  # |100 - 90|
    assert position.strategy_version == STRATEGY_VERSION


def test_scanned_count_is_zero_when_scenario_created_at_older_than_max_age(db_session):
    scenario = _pending_scenario(created_at=datetime(2026, 1, 1, 11, 0, 0))  # older than MAX_SCENARIO_AGE
    db_session.add(scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026, 1, 1, 12, 0, 0))

    assert result.scanned == 0
    assert result.opened == 0
    assert db_session.query(PaperPosition).count() == 0


def test_scanned_count_is_zero_when_scenario_has_no_calibrated_confidence(db_session):
    scenario = _pending_scenario()
    scenario.calibrated_confidence = None
    db_session.add(scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026, 1, 1, 12, 0, 0))

    assert result.scanned == 0
    assert db_session.query(PaperPosition).count() == 0


def test_scanned_count_is_zero_when_scenario_has_expired(db_session):
    scenario = _pending_scenario(expires_at=datetime(2026, 1, 1, 11, 0, 0))  # expired
    db_session.add(scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026, 1, 1, 12, 0, 0))

    assert result.scanned == 0
    assert db_session.query(PaperPosition).count() == 0


def test_scanned_count_is_zero_when_scenario_has_wrong_strategy_version(db_session):
    scenario = _pending_scenario(strategy_version="2026.01.legacy-v0")
    db_session.add(scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026, 1, 1, 12, 0, 0))

    assert result.scanned == 0
    assert db_session.query(PaperPosition).count() == 0


def test_scanned_count_is_zero_when_scenario_already_has_a_position(db_session):
    scenario = _pending_scenario()
    db_session.add(scenario)
    db_session.commit()
    db_session.add(_position(scenario))
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026, 1, 1, 12, 0, 0))

    assert result.scanned == 0
    assert result.opened == 0
    assert db_session.query(PaperPosition).count() == 1


def test_skips_scenario_with_target_price_too_close_to_entry(db_session):
    scenario = _pending_scenario(target_price=Decimal("101"))
    db_session.add(scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026, 1, 1, 12, 0, 0))

    assert result.scanned == 1
    assert result.opened == 0
    assert result.skipped == 1
    assert db_session.query(PaperPosition).count() == 0


def test_skips_scenario_when_symbol_already_has_an_open_position_from_different_scenario(db_session):
    # Add an existing open position from a different scenario for the same symbol
    existing_scenario = _pending_scenario(symbol="BTCUSDT", direction="long")
    db_session.add(existing_scenario)
    db_session.commit()
    db_session.add(_position(existing_scenario, position_size=Decimal("10")))
    db_session.commit()

    # Now try to open a new scenario for the same symbol
    new_scenario = _pending_scenario(symbol="BTCUSDT", direction="short")
    db_session.add(new_scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026, 1, 1, 12, 0, 0))

    assert result.scanned == 1
    assert result.opened == 0
    assert result.skipped == 1
    assert db_session.query(PaperPosition).count() == 1


def test_skips_scenario_when_total_notional_exceeds_limit(db_session):
    # Add an existing open position from a different scenario for ETHUSDT with large size
    existing_scenario = _pending_scenario(symbol="ETHUSDT", direction="long")
    db_session.add(existing_scenario)
    db_session.commit()
    db_session.add(_position(existing_scenario, position_size=Decimal("1001")))
    db_session.commit()

    # Now try to open a new scenario for BTCUSDT
    new_scenario = _pending_scenario(symbol="BTCUSDT", direction="long")
    db_session.add(new_scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026, 1, 1, 12, 0, 0))

    assert result.scanned == 1
    assert result.opened == 0
    assert result.skipped == 1
    assert db_session.query(PaperPosition).count() == 1


def test_opens_position_when_existing_position_is_from_different_strategy_version(db_session):
    # Add an existing open position from a different strategy version for the same symbol
    existing_scenario = _pending_scenario(symbol="BTCUSDT", direction="long", strategy_version="2026.01.legacy-v0")
    db_session.add(existing_scenario)
    db_session.commit()
    db_session.add(_position(existing_scenario, position_size=Decimal("10"), strategy_version="2026.01.legacy-v0"))
    db_session.commit()

    # Now try to open a new scenario for the same symbol
    new_scenario = _pending_scenario(symbol="BTCUSDT", direction="short")
    db_session.add(new_scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026, 1, 1, 12, 0, 0))

    assert result.scanned == 1
    assert result.opened == 1
    assert result.skipped == 0
    assert db_session.query(PaperPosition).count() == 2


def test_scanned_count_is_zero_when_portfolio_is_wiped_out(db_session):
    # Add a closed position that brings equity to zero
    dead_scenario = _pending_scenario(symbol="OLDUSDT", direction="long")
    db_session.add(dead_scenario)
    db_session.commit()
    db_session.add(_position(dead_scenario, position_size=Decimal("10"), equity_after=Decimal("0")))
    db_session.commit()

    # Add some pending scenarios
    for symbol in ("AUSDT", "BUSDT", "CUSDT"):
        db_session.add(_pending_scenario(symbol=symbol))
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026, 1, 1, 12, 0, 0))

    assert result.opened == 0
    assert result.failed == 0
    assert db_session.query(PaperPosition).filter(PaperPosition.status == "open").count() == 0
