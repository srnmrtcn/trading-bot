from datetime import datetime, timedelta
from decimal import Decimal

from src.db.models import PaperPosition, Scenario
from src.paper_position_opener import open_qualifying_positions
from src.paper_trading_config import RISK_PCT, STARTING_EQUITY, MIN_EXPECTED_R, MAX_CONCURRENT_POSITIONS, MAX_TOTAL_NOTIONAL_MULTIPLE
from src.strategy_version import STRATEGY_VERSION


def _pending_scenario(symbol="BTCUSDT", direction="long", calibrated_confidence=Decimal("0.8"),
                       entry_price=Decimal("100"), stop_price=Decimal("90"), target_price=Decimal("110"),
                       created_at=None, strategy_version=STRATEGY_VERSION):
    created_at = created_at or datetime(2026, 1, 1, 12)
    return Scenario(
        symbol=symbol, direction=direction,
        entry_price=entry_price, target_price=target_price, stop_price=stop_price,
        expected_return_pct=Decimal("0.1"), confidence_score=calibrated_confidence,
        created_at=created_at, expires_at=created_at + timedelta(hours=24), status="pending",
        calibrated_confidence=calibrated_confidence,
        strategy_version=strategy_version,
    )


def test_opens_a_position_for_a_qualifying_scenario(db_session):
    scenario = _pending_scenario()
    db_session.add(scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026, 1, 1, 12))

    assert result.scanned == 1
    assert result.opened == 1
    assert result.skipped == 0
    position = db_session.query(PaperPosition).first()
    assert position.scenario_id == scenario.id
    assert position.status == "open"
    assert position.opened_at == datetime(2026, 1, 1, 12)
    risk_amount, position_size = (STARTING_EQUITY * RISK_PCT, STARTING_EQUITY * RISK_PCT / Decimal("10"))
    assert position.risk_amount == risk_amount
    assert position.position_size == position_size
    assert position.strategy_version == STRATEGY_VERSION


def test_scanned_is_zero_for_scenarios_older_than_max_age(db_session):
    scenario = _pending_scenario(created_at=datetime(2026, 1, 1, 10))
    db_session.add(scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026, 1, 1, 12))

    assert result.scanned == 0
    assert result.opened == 0
    assert db_session.query(PaperPosition).count() == 0


def test_scanned_is_zero_for_scenarios_with_no_calibrated_confidence_yet(db_session):
    scenario = _pending_scenario()
    scenario.calibrated_confidence = None
    db_session.add(scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026, 1, 1, 12))

    assert result.scanned == 0
    assert db_session.query(PaperPosition).count() == 0


def test_scanned_is_zero_for_expired_scenarios(db_session):
    scenario = _pending_scenario(created_at=datetime(2026, 1, 1, 12), expires_at=datetime(2026, 1, 1, 12))
    db_session.add(scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026, 1, 1, 12))

    assert result.scanned == 0
    assert db_session.query(PaperPosition).count() == 0


def test_scanned_is_zero_for_wrong_strategy_version(db_session):
    scenario = _pending_scenario(strategy_version="2026.01.legacy-v0")
    db_session.add(scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026, 1, 1, 12))

    assert result.scanned == 0
    assert db_session.query(PaperPosition).count() == 0


def test_scanned_is_zero_for_scenarios_that_already_have_a_position(db_session):
    scenario = _pending_scenario()
    db_session.add(scenario)
    db_session.commit()
    db_session.add(PaperPosition(
        scenario_id=scenario.id, symbol=scenario.symbol, direction=scenario.direction,
        entry_price=scenario.entry_price, stop_price=scenario.stop_price, target_price=scenario.target_price,
        risk_amount=Decimal("100"), position_size=Decimal("10"),
        opened_at=datetime(2026, 1, 1), status="open",
    ))
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026, 1, 1, 12))

    assert result.scanned == 0
    assert db_session.query(PaperPosition).count() == 1


def test_skips_scenario_with_target_price_too_close_to_entry(db_session):
    scenario = _pending_scenario(target_price=Decimal("101"))
    db_session.add(scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026, 1, 1, 12))

    assert result.scanned == 1
    assert result.opened == 0
    assert result.skipped == 1
    assert db_session.query(PaperPosition).count() == 0


def test_skips_scenario_when_symbol_already_has_an_open_position(db_session):
    # Add an existing open position for BTCUSDT
    existing_scenario = _pending_scenario(symbol="BTCUSDT", created_at=datetime(2026, 1, 1, 10))
    db_session.add(existing_scenario)
    db_session.commit()
    db_session.add(PaperPosition(
        scenario_id=existing_scenario.id, symbol=existing_scenario.symbol, direction=existing_scenario.direction,
        entry_price=existing_scenario.entry_price, stop_price=existing_scenario.stop_price, target_price=existing_scenario.target_price,
        risk_amount=Decimal("100"), position_size=Decimal("10"),
        opened_at=datetime(2026, 1, 1), status="open",
    ))
    db_session.commit()

    # Add a new scenario for the same symbol
    new_scenario = _pending_scenario(symbol="BTCUSDT")
    db_session.add(new_scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026, 1, 1, 12))

    assert result.scanned == 1
    assert result.opened == 0
    assert result.skipped == 1
    assert db_session.query(PaperPosition).count() == 1


def test_skips_scenario_when_total_notional_exceeds_limit(db_session):
    # Add an existing open position for ETHUSDT with high notional
    existing_scenario = _pending_scenario(symbol="ETHUSDT", entry_price=Decimal("100"), created_at=datetime(2026, 1, 1, 10))
    db_session.add(existing_scenario)
    db_session.commit()
    db_session.add(PaperPosition(
        scenario_id=existing_scenario.id, symbol=existing_scenario.symbol, direction=existing_scenario.direction,
        entry_price=existing_scenario.entry_price, stop_price=existing_scenario.stop_price, target_price=existing_scenario.target_price,
        risk_amount=Decimal("1000"), position_size=Decimal("1001"),
        opened_at=datetime(2026, 1, 1), status="open",
    ))
    db_session.commit()

    # Add a new scenario for BTCUSDT that would exceed the notional limit
    new_scenario = _pending_scenario()
    db_session.add(new_scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026, 1, 1, 12))

    assert result.scanned == 1
    assert result.opened == 0
    assert result.skipped == 1
    assert db_session.query(PaperPosition).count() == 1


def test_scenarios_with_different_strategy_version_are_not_counted_towards_open_positions(db_session):
    # Add an existing open position with different strategy version
    existing_scenario = _pending_scenario(symbol="BTCUSDT", strategy_version="2026.01.legacy-v0", created_at=datetime(2026, 1, 1, 10))
    db_session.add(existing_scenario)
    db_session.commit()
    db_session.add(PaperPosition(
        scenario_id=existing_scenario.id, symbol=existing_scenario.symbol, direction=existing_scenario.direction,
        entry_price=existing_scenario.entry_price, stop_price=existing_scenario.stop_price, target_price=existing_scenario.target_price,
        risk_amount=Decimal("100"), position_size=Decimal("10"),
        opened_at=datetime(2026, 1, 1), status="open",
        strategy_version="2026.01.legacy-v0"
    ))
    db_session.commit()

    # Add a new scenario for BTCUSDT that should be opened
    new_scenario = _pending_scenario()
    db_session.add(new_scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026, 1, 1, 12))

    assert result.scanned == 1
    assert result.opened == 1
    assert result.skipped == 0
    assert db_session.query(PaperPosition).count() == 2


def test_scanned_is_zero_for_closed_positions_with_zero_equity(db_session):
    # Add a closed position with zero equity
    dead_scenario = _pending_scenario(symbol="OLDUSDT", created_at=datetime(2025, 1, 1))
    db_session.add(dead_scenario)
    db_session.commit()
    db_session.add(PaperPosition(
        scenario_id=dead_scenario.id, symbol="OLDUSDT", direction="long",
        entry_price=Decimal("100"), stop_price=Decimal("90"), target_price=Decimal("110"),
        risk_amount=Decimal("100"), position_size=Decimal("10"),
        opened_at=datetime(2025, 1, 1), status="closed",
        closed_at=datetime(2025, 1, 2), exit_price=Decimal("90"),
        realized_pnl=Decimal("-10000"), equity_before=STARTING_EQUITY, equity_after=Decimal("0"),
    ))
    db_session.commit()

    # Add a new scenario that would be opened
    new_scenario = _pending_scenario()
    db_session.add(new_scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026, 1, 1, 12))

    assert result.scanned == 0
    assert result.opened == 0
    assert result.skipped == 0
    assert result.failed == 0
    assert db_session.query(PaperPosition).count() == 1
