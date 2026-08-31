from datetime import datetime, timedelta
from decimal import Decimal

from src.db.models import PaperPosition, Scenario
from src.paper_position_opener import open_qualifying_positions
from src.paper_trading_config import RISK_PCT, STARTING_EQUITY
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

    result = open_qualifying_positions(db_session, now=datetime(2026,1,1,12,0,0))

    assert result.scanned == 1
    assert result.opened == 1
    assert result.skipped == 0
    position = db_session.query(PaperPosition).first()
    assert position.scenario_id == scenario.id
    assert position.status == "open"
    assert position.opened_at == datetime(2026,1,1,12,0,0)
    expected_risk = STARTING_EQUITY * RISK_PCT
    assert position.risk_amount == expected_risk
    assert position.position_size == Decimal('10')  # 1000 / 100
    assert position.strategy_version == STRATEGY_VERSION


def test_skips_a_scenario_below_the_confidence_threshold(db_session):
    scenario = _pending_scenario(calibrated_confidence=Decimal("0.7"))
    db_session.add(scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026,1,1,12,0,0))

    assert result.scanned == 0
    assert db_session.query(PaperPosition).count() == 0


def test_skips_a_scenario_with_no_calibrated_confidence_yet(db_session):
    scenario = _pending_scenario()
    scenario.calibrated_confidence = None
    db_session.add(scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026,1,1,12,0,0))

    assert result.scanned == 0
    assert db_session.query(PaperPosition).count() == 0


def test_skips_a_pending_scenario_past_its_expiry(db_session):
    scenario = _pending_scenario(created_at=datetime(2025, 1, 1))  # expires_at = created_at + 24h, long past
    db_session.add(scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026,1,1,12,0,0))

    assert result.scanned == 0
    assert db_session.query(PaperPosition).count() == 0


def test_skips_a_scenario_that_already_has_a_position(db_session):
    scenario = _pending_scenario()
    db_session.add(scenario)
    db_session.commit()
    db_session.add(PaperPosition(
        scenario_id=scenario.id, symbol=scenario.symbol, direction=scenario.direction,
        entry_price=scenario.entry_price, stop_price=scenario.stop_price, target_price=scenario.target_price,
        risk_amount=Decimal("100"), position_size=Decimal("10"),
        opened_at=datetime(2026, 1, 1), status="closed",
    ))
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026,1,1,12,0,0))

    assert result.scanned == 0
    assert db_session.query(PaperPosition).count() == 1


def test_skips_a_second_qualifying_scenario_on_the_same_symbol_while_one_is_open(db_session):
    first = _pending_scenario(symbol="BTCUSDT", created_at=datetime(2026, 1, 1, 0))
    second = _pending_scenario(symbol="BTCUSDT", direction="short", created_at=datetime(2026, 1, 1, 1))
    db_session.add_all([first, second])
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026,1,1,12,0,0))

    assert result.scanned == 2
    assert result.opened == 1
    assert result.skipped == 1
    open_positions = db_session.query(PaperPosition).filter(PaperPosition.status == "open").all()
    assert len(open_positions) == 1
    assert open_positions[0].scenario_id == first.id


def test_stops_opening_once_max_concurrent_positions_is_reached(db_session, monkeypatch):
    monkeypatch.setattr("src.paper_position_opener.MAX_CONCURRENT_POSITIONS", 1)
    first = _pending_scenario(symbol="BTCUSDT", created_at=datetime(2026, 1, 1, 0))
    second = _pending_scenario(symbol="ETHUSDT", created_at=datetime(2026, 1, 1, 1))
    db_session.add_all([first, second])
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026,1,1,12,0,0))

    assert result.opened == 1
    assert result.skipped == 1
    assert db_session.query(PaperPosition).filter(PaperPosition.status == "open").count() == 1


def test_isolates_a_failing_position_open(db_session, monkeypatch):
    good = _pending_scenario(symbol="BTCUSDT", created_at=datetime(2026, 1, 1, 0))
    bad = _pending_scenario(symbol="ETHUSDT", entry_price=Decimal("999"), created_at=datetime(2026, 1, 1, 1))
    db_session.add_all([good, bad])
    db_session.commit()

    import src.paper_position_opener as opener_module
    real_size_position = opener_module.size_position

    def flaky_size_position(equity, entry_price, stop_price, risk_pct):
        if entry_price == Decimal("999"):
            raise RuntimeError("boom")
        return real_size_position(equity, entry_price, stop_price, risk_pct)

    monkeypatch.setattr(opener_module, "size_position", flaky_size_position)

    result = open_qualifying_positions(db_session, now=datetime(2026,1,1,12,0,0))

    assert result.scanned == 2
    assert result.opened == 1
    assert result.failed == 1
    assert db_session.query(PaperPosition).filter(PaperPosition.status == "open").count() == 1


def test_a_wiped_out_portfolio_stops_opening_instead_of_failing_per_candidate(db_session, caplog):
    """Equity at or below zero is the end of the portfolio, not a per-scenario
    error. Left to the loop, `size_position` would raise for every candidate in
    turn — one stack trace each, `failed` climbing with the number of pending
    scenarios, and the actual cause buried."""
    import logging

    dead = Scenario(
        symbol="OLDUSDT", direction="long", entry_price=Decimal("100"),
        target_price=Decimal("110"), stop_price=Decimal("90"),
        expected_return_pct=Decimal("0.1"), confidence_score=Decimal("0.7"),
        created_at=datetime(2025, 1, 1), expires_at=datetime(2025, 1, 2),
        status="hit_stop", calibrated_confidence=Decimal("0.7"),
        strategy_version=STRATEGY_VERSION,
    )
    db_session.add(dead)
    db_session.commit()
    db_session.add(PaperPosition(
        scenario_id=dead.id, symbol="OLDUSDT", direction="long",
        entry_price=Decimal("100"), stop_price=Decimal("90"), target_price=Decimal("110"),
        risk_amount=Decimal("100"), position_size=Decimal("10"),
        opened_at=datetime(2025, 1, 1), status="closed",
        closed_at=datetime(2025, 1, 2), exit_price=Decimal("90"),
        realized_pnl=Decimal("-10000"), equity_before=STARTING_EQUITY, equity_after=Decimal("0"),
    ))
    for symbol in ("AUSDT", "BUSDT", "CUSDT"):
        db_session.add(_pending_scenario(symbol=symbol))
    db_session.commit()

    with caplog.at_level(logging.ERROR):
        result = open_qualifying_positions(db_session, now=datetime(2026,1,1,12,0,0))

    assert result.opened == 0
    assert result.failed == 0, "a dead portfolio is not three separate failures"
    assert db_session.query(PaperPosition).filter(PaperPosition.status == "open").count() == 0
    assert len([r for r in caplog.records if r.levelno >= logging.ERROR]) == 1


def test_skips_scenarios_with_different_strategy_version(db_session):
    scenario = _pending_scenario(strategy_version="2025.01.old-version")
    db_session.add(scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026,1,1,12,0,0))

    assert result.scanned == 0
    assert db_session.query(PaperPosition).count() == 0


def test_opens_position_with_explicit_strategy_version(db_session):
    scenario = _pending_scenario(strategy_version=STRATEGY_VERSION)
    db_session.add(scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026,1,1,12,0,0))

    assert result.scanned == 1
    assert result.opened == 1
    position = db_session.query(PaperPosition).first()
    assert position.strategy_version == STRATEGY_VERSION


def test_scanned_positions_are_filtered_by_strategy_version(db_session):
    # Create an existing open position with the current strategy version
    scenario = _pending_scenario(strategy_version=STRATEGY_VERSION)
    db_session.add(scenario)
    db_session.commit()
    
    # Add a paper position for this scenario (to make it "already has a position")
    db_session.add(PaperPosition(
        scenario_id=scenario.id, symbol=scenario.symbol, direction=scenario.direction,
        entry_price=scenario.entry_price, stop_price=scenario.stop_price, target_price=scenario.target_price,
        risk_amount=Decimal("100"), position_size=Decimal("10"),
        opened_at=datetime(2026, 1, 1), status="open",
        strategy_version=STRATEGY_VERSION,
    ))
    db_session.commit()
    
    # Create a new scenario with the same symbol but different strategy version
    old_scenario = _pending_scenario(strategy_version="2025.01.old-version")
    db_session.add(old_scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026,1,1,12,0,0))

    # Should scan both scenarios but skip the one with old strategy version
    assert result.scanned == 1
    assert result.opened == 0
    assert result.skipped == 1


def test_positions_notional_is_calculated_correctly(db_session):
    # Create an existing open position with the current strategy version
    existing_scenario = _pending_scenario(symbol="BTCUSDT", strategy_version=STRATEGY_VERSION)
    db_session.add(existing_scenario)
    db_session.commit()
    
    db_session.add(PaperPosition(
        scenario_id=existing_scenario.id, symbol=existing_scenario.symbol, direction=existing_scenario.direction,
        entry_price=existing_scenario.entry_price, stop_price=existing_scenario.stop_price, target_price=existing_scenario.target_price,
        risk_amount=Decimal("100"), position_size=Decimal("10"),
        opened_at=datetime(2026, 1, 1), status="open",
        strategy_version=STRATEGY_VERSION,
    ))
    db_session.commit()
    
    # Create a new scenario that would exceed the notional limit
    new_scenario = _pending_scenario(symbol="ETHUSDT", entry_price=Decimal("1000"), target_price=Decimal("1100"))
    db_session.add(new_scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026,1,1,12,0,0))

    # Should scan both scenarios but skip the one that would exceed notional limit
    assert result.scanned == 1
    assert result.opened == 0
    assert result.skipped == 1


def test_positions_are_filtered_by_strategy_version_for_notional_calculation(db_session):
    # Create an existing open position with a different strategy version (should be ignored)
    old_scenario = _pending_scenario(symbol="BTCUSDT", strategy_version="2025.01.old-version")
    db_session.add(old_scenario)
    db_session.commit()
    
    db_session.add(PaperPosition(
        scenario_id=old_scenario.id, symbol=old_scenario.symbol, direction=old_scenario.direction,
        entry_price=old_scenario.entry_price, stop_price=old_scenario.stop_price, target_price=old_scenario.target_price,
        risk_amount=Decimal("100"), position_size=Decimal("10"),
        opened_at=datetime(2026, 1, 1), status="open",
        strategy_version="2025.01.old-version",
    ))
    db_session.commit()
    
    # Create a new scenario with the current strategy version
    new_scenario = _pending_scenario(symbol="ETHUSDT")
    db_session.add(new_scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026,1,1,12,0,0))

    # Should scan and open the new scenario since old one is ignored in notional calculation
    assert result.scanned == 1
    assert result.opened == 1
    assert result.skipped == 0


def test_scenario_created_more_than_max_age_is_skipped(db_session):
    scenario = _pending_scenario(created_at=datetime(2025, 1, 1))  # More than MAX_SCENARIO_AGE (1 hour)
    db_session.add(scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026,1,1,12,0,0))

    assert result.scanned == 0
    assert result.opened == 0
    assert db_session.query(PaperPosition).count() == 0


def test_scenario_with_zero_risk_is_skipped(db_session):
    scenario = _pending_scenario(stop_price=Decimal("100"))  # Zero risk
    db_session.add(scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026,1,1,12,0,0))

    assert result.scanned == 1
    assert result.opened == 0
    assert result.skipped == 1


def test_scenario_with_negative_expected_return_is_skipped(db_session):
    scenario = _pending_scenario(calibrated_confidence=Decimal("0.3"))  # Low confidence leads to negative expected return
    db_session.add(scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026,1,1,12,0,0))

    assert result.scanned == 1
    assert result.opened == 0
    assert result.skipped == 1


def test_scenario_with_target_price_equal_to_entry_price_is_skipped(db_session):
    scenario = _pending_scenario(target_price=Decimal("100"))  # No target return
    db_session.add(scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026,1,1,12,0,0))

    assert result.scanned == 1
    assert result.opened == 0
    assert result.skipped == 1


def test_scenario_with_no_position_and_existing_open_position_of_different_symbol_is_allowed(db_session):
    # Create an existing open position with a different symbol
    existing_scenario = _pending_scenario(symbol="BTCUSDT", strategy_version=STRATEGY_VERSION)
    db_session.add(existing_scenario)
    db_session.commit()
    
    db_session.add(PaperPosition(
        scenario_id=existing_scenario.id, symbol=existing_scenario.symbol, direction=existing_scenario.direction,
        entry_price=existing_scenario.entry_price, stop_price=existing_scenario.stop_price, target_price=existing_scenario.target_price,
        risk_amount=Decimal("100"), position_size=Decimal("10"),
        opened_at=datetime(2026, 1, 1), status="open",
        strategy_version=STRATEGY_VERSION,
    ))
    db_session.commit()
    
    # Create a new scenario with a different symbol
    new_scenario = _pending_scenario(symbol="ETHUSDT")
    db_session.add(new_scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026,1,1,12,0,0))

    # Should scan and open the new scenario since it's a different symbol
    assert result.scanned == 1
    assert result.opened == 1
    assert result.skipped == 0


def test_scenario_with_existing_position_of_same_symbol_is_skipped(db_session):
    # Create an existing open position with the same symbol
    existing_scenario = _pending_scenario(symbol="BTCUSDT", strategy_version=STRATEGY_VERSION)
    db_session.add(existing_scenario)
    db_session.commit()
    
    db_session.add(PaperPosition(
        scenario_id=existing_scenario.id, symbol=existing_scenario.symbol, direction=existing_scenario.direction,
        entry_price=existing_scenario.entry_price, stop_price=existing_scenario.stop_price, target_price=existing_scenario.target_price,
        risk_amount=Decimal("100"), position_size=Decimal("10"),
        opened_at=datetime(2026, 1, 1), status="open",
        strategy_version=STRATEGY_VERSION,
    ))
    db_session.commit()
    
    # Create a new scenario with the same symbol
    new_scenario = _pending_scenario(symbol="BTCUSDT")
    db_session.add(new_scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026,1,1,12,0,0))

    # Should scan both but skip the second one due to existing position
    assert result.scanned == 2
    assert result.opened == 1
    assert result.skipped == 1


def test_scenario_with_existing_position_of_same_symbol_and_different_strategy_version_is_skipped(db_session):
    # Create an existing open position with a different strategy version
    existing_scenario = _pending_scenario(symbol="BTCUSDT", strategy_version="2025.01.old-version")
    db_session.add(existing_scenario)
    db_session.commit()
    
    db_session.add(PaperPosition(
        scenario_id=existing_scenario.id, symbol=existing_scenario.symbol, direction=existing_scenario.direction,
        entry_price=existing_scenario.entry_price, stop_price=existing_scenario.stop_price, target_price=existing_scenario.target_price,
        risk_amount=Decimal("100"), position_size=Decimal("10"),
        opened_at=datetime(2026, 1, 1), status="open",
        strategy_version="2025.01.old-version",
    ))
    db_session.commit()
    
    # Create a new scenario with the same symbol and current strategy version
    new_scenario = _pending_scenario(symbol="BTCUSDT")
    db_session.add(new_scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026,1,1,12,0,0))

    # Should scan both but skip the second one due to existing position
    assert result.scanned == 2
    assert result.opened == 1
    assert result.skipped == 1


def test_scenario_with_existing_position_of_same_symbol_and_status_closed_is_allowed(db_session):
    # Create an existing closed position with the same symbol
    existing_scenario = _pending_scenario(symbol="BTCUSDT", strategy_version=STRATEGY_VERSION)
    db_session.add(existing_scenario)
    db_session.commit()
    
    db_session.add(PaperPosition(
        scenario_id=existing_scenario.id, symbol=existing_scenario.symbol, direction=existing_scenario.direction,
        entry_price=existing_scenario.entry_price, stop_price=existing_scenario.stop_price, target_price=existing_scenario.target_price,
        risk_amount=Decimal("100"), position_size=Decimal("10"),
        opened_at=datetime(2026, 1, 1), status="closed",
        strategy_version=STRATEGY_VERSION,
    ))
    db_session.commit()
    
    # Create a new scenario with the same symbol
    new_scenario = _pending_scenario(symbol="BTCUSDT")
    db_session.add(new_scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026,1,1,12,0,0))

    # Should scan and open the new scenario since old one is closed
    assert result.scanned == 2
    assert result.opened == 2
    assert result.skipped == 0


def test_scenario_with_existing_position_of_same_symbol_and_status_open_is_skipped(db_session):
    # Create an existing open position with the same symbol
    existing_scenario = _pending_scenario(symbol="BTCUSDT", strategy_version=STRATEGY_VERSION)
    db_session.add(existing_scenario)
    db_session.commit()
    
    db_session.add(PaperPosition(
        scenario_id=existing_scenario.id, symbol=existing_scenario.symbol, direction=existing_scenario.direction,
        entry_price=existing_scenario.entry_price, stop_price=existing_scenario.stop_price, target_price=existing_scenario.target_price,
        risk_amount=Decimal("100"), position_size=Decimal("10"),
        opened_at=datetime(2026, 1, 1), status="open",
        strategy_version=STRATEGY_VERSION,
    ))
    db_session.commit()
    
    # Create a new scenario with the same symbol
    new_scenario = _pending_scenario(symbol="BTCUSDT")
    db_session.add(new_scenario)
    db_session.commit()

    result = open_qualifying_positions(db_session, now=datetime(2026,1,1,12,0,0))

    # Should scan both but skip the second one due to existing position
    assert result.scanned == 2
    assert result.opened == 1
    assert result.skipped == 1
