from datetime import datetime
from decimal import Decimal

from src.db.models import PaperPosition, Scenario
from src.paper_equity import current_equity
from src.paper_trading_config import STARTING_EQUITY
from src.strategy_version import STRATEGY_VERSION


def _scenario(symbol):
    now = datetime(2026, 1, 1)
    return Scenario(
        symbol=symbol, direction="long",
        entry_price=Decimal("100"), target_price=Decimal("110"), stop_price=Decimal("90"),
        expected_return_pct=Decimal("0.1"), confidence_score=Decimal("0.7"),
        created_at=now, expires_at=now, status="pending",
    )


def _closed_position(scenario, equity_after, closed_at, strategy_version=None):
    return PaperPosition(
        scenario_id=scenario.id, symbol=scenario.symbol, direction="long",
        entry_price=Decimal("100"), stop_price=Decimal("90"), target_price=Decimal("110"),
        risk_amount=Decimal("100"), position_size=Decimal("10"),
        opened_at=datetime(2026, 1, 1), status="closed",
        closed_at=closed_at, exit_price=Decimal("110"), realized_pnl=equity_after - STARTING_EQUITY,
        equity_before=STARTING_EQUITY, equity_after=equity_after,
        strategy_version=strategy_version,
    )


def _open_position(scenario):
    return PaperPosition(
        scenario_id=scenario.id, symbol=scenario.symbol, direction="long",
        entry_price=Decimal("100"), stop_price=Decimal("90"), target_price=Decimal("110"),
        risk_amount=Decimal("100"), position_size=Decimal("10"),
        opened_at=datetime(2026, 1, 1), status="open",
    )


def test_current_equity_returns_starting_equity_when_nothing_closed(db_session):
    assert current_equity(db_session) == STARTING_EQUITY


def test_current_equity_returns_the_most_recently_closed_positions_equity_after(db_session):
    s1 = _scenario("BTCUSDT")
    s2 = _scenario("ETHUSDT")
    db_session.add_all([s1, s2])
    db_session.commit()

    db_session.add(_closed_position(s1, Decimal("10100"), closed_at=datetime(2026, 1, 1, 10), strategy_version=STRATEGY_VERSION))
    db_session.add(_closed_position(s2, Decimal("10250"), closed_at=datetime(2026, 1, 1, 11), strategy_version=STRATEGY_VERSION))
    db_session.commit()

    assert current_equity(db_session) == Decimal("10250")


def test_current_equity_ignores_open_positions(db_session):
    s1 = _scenario("BTCUSDT")
    db_session.add(s1)
    db_session.commit()
    db_session.add(_open_position(s1))
    db_session.commit()

    assert current_equity(db_session) == STARTING_EQUITY


def test_current_equity_ignores_a_closed_position_that_recorded_no_equity(db_session):
    """`equity_after` is nullable and only written when the closer runs. A row
    marked closed without it carries no equity information, so equity must fall
    back to the last row that does — returning None instead poisons
    `size_position` and every caller downstream of it.
    """
    good, blank = _scenario("AUSDT"), _scenario("BUSDT")
    db_session.add_all([good, blank])
    db_session.commit()
    db_session.add(_closed_position(good, Decimal("10500"), datetime(2026, 1, 1), strategy_version=STRATEGY_VERSION))
    db_session.add(PaperPosition(
        scenario_id=blank.id, symbol="BUSDT", direction="long",
        entry_price=Decimal("100"), stop_price=Decimal("90"), target_price=Decimal("110"),
        risk_amount=Decimal("100"), position_size=Decimal("10"),
        opened_at=datetime(2026, 1, 1), status="closed",
        closed_at=datetime(2026, 1, 2), equity_after=None,
        strategy_version=STRATEGY_VERSION,
    ))
    db_session.commit()

    assert current_equity(db_session) == Decimal("10500")


def test_current_equity_ignores_legacy_positions(db_session):
    """Legacy positions (without strategy_version or different version) should be ignored."""
    s1 = _scenario("BTCUSDT")
    s2 = _scenario("ETHUSDT")
    db_session.add_all([s1, s2])
    db_session.commit()

    # Add a position with current version
    db_session.add(_closed_position(s1, Decimal("10100"), closed_at=datetime(2026, 1, 1, 10), strategy_version=STRATEGY_VERSION))
    
    # Add a position with legacy version (should be ignored)
    db_session.add(_closed_position(s2, Decimal("10250"), closed_at=datetime(2026, 1, 1, 11), strategy_version="legacy"))
    
    db_session.commit()

    # Should return equity from current version position
    assert current_equity(db_session) == Decimal("10100")


def test_current_equity_ignores_positions_with_none_strategy_version(db_session):
    """Positions with None strategy_version should be ignored."""
    s1 = _scenario("BTCUSDT")
    s2 = _scenario("ETHUSDT")
    db_session.add_all([s1, s2])
    db_session.commit()

    # Add a position with current version
    db_session.add(_closed_position(s1, Decimal("10100"), closed_at=datetime(2026, 1, 1, 10), strategy_version=STRATEGY_VERSION))
    
    # Add a position with None strategy_version (should be ignored)
    db_session.add(_closed_position(s2, Decimal("10250"), closed_at=datetime(2026, 1, 1, 11), strategy_version=None))
    
    db_session.commit()

    # Should return equity from current version position
    assert current_equity(db_session) == Decimal("10100")