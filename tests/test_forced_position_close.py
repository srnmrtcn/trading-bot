import datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from src.db.models import Kline, PaperPosition, Scenario
from src.paper_position_closer import close_resolved_positions
from src.timeutil import utc_now


def test_forced_close_with_kline(db_session: Session):
    """Test that positions with unresolvable status are closed when past grace period and kline exists."""
    now = utc_now()
    expires_at = now - datetime.timedelta(hours=2)  # Past grace period
    
    # Create a scenario with unresolvable status
    scenario = Scenario(
        symbol="BTCUSDT",
        direction="long",
        entry_price=Decimal("50000"),
        target_price=Decimal("60000"),
        stop_price=Decimal("40000"),
        expected_return_pct=Decimal("20"),
        confidence_score=Decimal("0.8"),
        created_at=now - datetime.timedelta(hours=3),
        expires_at=expires_at,
        status="unresolvable",
    )
    db_session.add(scenario)
    db_session.flush()
    
    # Create a kline for the scenario
    kline = Kline(
        symbol="BTCUSDT",
        timeframe="1h",
        open_time=expires_at - datetime.timedelta(minutes=30),
        open=Decimal("51000"),
        high=Decimal("52000"),
        low=Decimal("50000"),
        close=Decimal("51500"),
        volume=Decimal("1000"),
    )
    db_session.add(kline)
    db_session.flush()
    
    # Create a paper position
    position = PaperPosition(
        scenario_id=scenario.id,
        symbol="BTCUSDT",
        direction="long",
        entry_price=Decimal("50000"),
        stop_price=Decimal("40000"),
        target_price=Decimal("60000"),
        risk_amount=Decimal("1000"),
        position_size=Decimal("0.02"),
        opened_at=now - datetime.timedelta(hours=1),
    )
    db_session.add(position)
    db_session.flush()
    
    # Close positions
    result = close_resolved_positions(db_session, now)
    
    # Verify the position was closed
    assert result.closed == 1
    assert result.still_open == 0
    assert result.failed == 0
    
    # Verify position details
    updated_position = db_session.get(PaperPosition, position.id)
    assert updated_position.status == "closed"
    assert updated_position.exit_price == Decimal("51500")
    assert updated_position.exit_reason == "forced"


def test_forced_close_no_kline(db_session: Session):
    """Test that positions with unresolvable status are left open when no kline exists."""
    now = utc_now()
    expires_at = now - datetime.timedelta(hours=2)  # Past grace period
    
    # Create a scenario with unresolvable status
    scenario = Scenario(
        symbol="BTCUSDT",
        direction="long",
        entry_price=Decimal("50000"),
        target_price=Decimal("60000"),
        stop_price=Decimal("40000"),
        expected_return_pct=Decimal("20"),
        confidence_score=Decimal("0.8"),
        created_at=now - datetime.timedelta(hours=3),
        expires_at=expires_at,
        status="unresolvable",
    )
    db_session.add(scenario)
    db_session.flush()
    
    # Create a paper position
    position = PaperPosition(
        scenario_id=scenario.id,
        symbol="BTCUSDT",
        direction="long",
        entry_price=Decimal("50000"),
        stop_price=Decimal("40000"),
        target_price=Decimal("60000"),
        risk_amount=Decimal("1000"),
        position_size=Decimal("0.02"),
        opened_at=now - datetime.timedelta(hours=1),
    )
    db_session.add(position)
    db_session.flush()
    
    # Close positions
    result = close_resolved_positions(db_session, now)
    
    # Verify the position was left open
    assert result.closed == 0
    assert result.still_open == 1
    assert result.failed == 0
    
    # Verify position details
    updated_position = db_session.get(PaperPosition, position.id)
    assert updated_position.status == "open"


def test_forced_close_within_grace_period(db_session: Session):
    """Test that positions with unresolvable status are left open when within grace period."""
    now = utc_now()
    expires_at = now - datetime.timedelta(minutes=30)  # Within grace period
    
    # Create a scenario with unresolvable status
    scenario = Scenario(
        symbol="BTCUSDT",
        direction="long",
        entry_price=Decimal("50000"),
        target_price=Decimal("60000"),
        stop_price=Decimal("40000"),
        expected_return_pct=Decimal("20"),
        confidence_score=Decimal("0.8"),
        created_at=now - datetime.timedelta(hours=3),
        expires_at=expires_at,
        status="unresolvable",
    )
    db_session.add(scenario)
    db_session.flush()
    
    # Create a kline for the scenario
    kline = Kline(
        symbol="BTCUSDT",
        timeframe="1h",
        open_time=expires_at - datetime.timedelta(minutes=30),
        open=Decimal("51000"),
        high=Decimal("52000"),
        low=Decimal("50000"),
        close=Decimal("51500"),
        volume=Decimal("1000"),
    )
    db_session.add(kline)
    db_session.flush()
    
    # Create a paper position
    position = PaperPosition(
        scenario_id=scenario.id,
        symbol="BTCUSDT",
        direction="long",
        entry_price=Decimal("50000"),
        stop_price=Decimal("40000"),
        target_price=Decimal("60000"),
        risk_amount=Decimal("1000"),
        position_size=Decimal("0.02"),
        opened_at=now - datetime.timedelta(hours=1),
    )
    db_session.add(position)
    db_session.flush()
    
    # Close positions
    result = close_resolved_positions(db_session, now)
    
    # Verify the position was left open
    assert result.closed == 0
    assert result.still_open == 1
    assert result.failed == 0
    
    # Verify position details
    updated_position = db_session.get(PaperPosition, position.id)
    assert updated_position.status == "open"