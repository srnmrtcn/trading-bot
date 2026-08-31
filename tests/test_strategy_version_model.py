import pytest
from sqlalchemy.orm import sessionmaker

from src.db.models import Scenario, PaperPosition
from src.strategy_version import STRATEGY_VERSION


def test_scenario_strategy_version_column():
    # Check that the column exists and is nullable
    assert hasattr(Scenario, 'strategy_version')
    strategy_version_column = Scenario.__table__.c.strategy_version
    assert strategy_version_column.nullable is True
    assert strategy_version_column.default is None


def test_paper_position_strategy_version_column():
    # Check that the column exists and is nullable
    assert hasattr(PaperPosition, 'strategy_version')
    strategy_version_column = PaperPosition.__table__.c.strategy_version
    assert strategy_version_column.nullable is True
    assert strategy_version_column.default is None


def test_scenario_can_be_created_with_none_strategy_version():
    # Test that we can create a Scenario with explicit strategy_version=None
    scenario = Scenario(
        symbol="BTCUSDT",
        direction="long",
        entry_price=50000,
        target_price=60000,
        stop_price=45000,
        expected_return_pct=0.2,
        confidence_score=0.9,
        created_at=None,
        expires_at=None,
        strategy_version=None
    )
    assert scenario.strategy_version is None


def test_paper_position_can_be_created_with_none_strategy_version():
    # Test that we can create a PaperPosition with explicit strategy_version=None
    paper_position = PaperPosition(
        scenario_id=1,
        symbol="BTCUSDT",
        direction="long",
        entry_price=50000,
        stop_price=45000,
        target_price=60000,
        risk_amount=1000,
        position_size=0.02,
        opened_at=None,
        strategy_version=None
    )
    assert paper_position.strategy_version is None


def test_scenario_can_be_created_without_strategy_version():
    # Test that we can create a Scenario without providing strategy_version (should default to None)
    scenario = Scenario(
        symbol="BTCUSDT",
        direction="long",
        entry_price=50000,
        target_price=60000,
        stop_price=45000,
        expected_return_pct=0.2,
        confidence_score=0.9,
        created_at=None,
        expires_at=None
    )
    assert scenario.strategy_version is None


def test_paper_position_can_be_created_without_strategy_version():
    # Test that we can create a PaperPosition without providing strategy_version (should default to None)
    paper_position = PaperPosition(
        scenario_id=1,
        symbol="BTCUSDT",
        direction="long",
        entry_price=50000,
        stop_price=45000,
        target_price=60000,
        risk_amount=1000,
        position_size=0.02,
        opened_at=None
    )
    assert paper_position.strategy_version is None


def test_scenario_strategy_version_assignment():
    # Test that we can assign a value to strategy_version
    scenario = Scenario(
        symbol="BTCUSDT",
        direction="long",
        entry_price=50000,
        target_price=60000,
        stop_price=45000,
        expected_return_pct=0.2,
        confidence_score=0.9,
        created_at=None,
        expires_at=None
    )
    scenario.strategy_version = STRATEGY_VERSION
    assert scenario.strategy_version == STRATEGY_VERSION


def test_paper_position_strategy_version_assignment():
    # Test that we can assign a value to strategy_version
    paper_position = PaperPosition(
        scenario_id=1,
        symbol="BTCUSDT",
        direction="long",
        entry_price=50000,
        stop_price=45000,
        target_price=60000,
        risk_amount=1000,
        position_size=0.02,
        opened_at=None
    )
    paper_position.strategy_version = STRATEGY_VERSION
    assert paper_position.strategy_version == STRATEGY_VERSION