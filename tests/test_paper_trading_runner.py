from datetime import datetime, timedelta
from decimal import Decimal

from src.db.models import PaperPosition, Scenario
from src.paper_trading_config import STARTING_EQUITY
from src.paper_trading_runner import run_paper_trading_cycle
from src.strategy_version import STRATEGY_VERSION


def test_run_paper_trading_cycle_opens_and_later_closes_a_position_end_to_end(db_session):
    """Real scenario, real DB round-trip through both halves — a stub or
    monkeypatched version of this test would not catch a broken wire between
    the opener and the closer."""
    created_at = datetime(2026, 1, 1, 10, 5, 0)
    scenario = Scenario(
        symbol="BTCUSDT", direction="long",
        entry_price=Decimal("100"), target_price=Decimal("110"), stop_price=Decimal("90"),
        expected_return_pct=Decimal("0.1"), confidence_score=Decimal("0.7"),
        created_at=created_at, expires_at=created_at + timedelta(hours=24), status="pending",
        calibrated_confidence=Decimal("0.7"),
        strategy_version=STRATEGY_VERSION,
    )
    db_session.add(scenario)
    db_session.commit()

    result_1 = run_paper_trading_cycle(db_session, now=created_at)
    assert result_1.opened == 1
    assert result_1.closed == 0
    position = db_session.query(PaperPosition).first()
    assert position.status == "open"

    # Subsystem C's learning cycle would have set this by the time this
    # subsystem's next cycle runs.
    scenario.status = "hit_target"
    db_session.commit()

    result_2 = run_paper_trading_cycle(db_session, now=created_at + timedelta(hours=1))

    assert result_2.opened == 0
    assert result_2.closed == 1
    reloaded = db_session.query(PaperPosition).first()
    assert reloaded.status == "closed"
    assert reloaded.exit_price == Decimal("110")
    assert reloaded.equity_after == STARTING_EQUITY + reloaded.realized_pnl


def test_run_paper_trading_cycle_closes_then_opens_using_freed_equity_in_one_cycle(db_session):
    from src.paper_trading_config import RISK_PCT, STARTING_EQUITY

    now = datetime(2026, 1, 1, 10)
    resolved_scenario = Scenario(
        symbol="BTCUSDT", direction="long",
        entry_price=Decimal("100"), target_price=Decimal("110"), stop_price=Decimal("90"),
        expected_return_pct=Decimal("0.1"), confidence_score=Decimal("0.7"),
        created_at=now - timedelta(hours=1), expires_at=now + timedelta(hours=23), status="hit_target",
        calibrated_confidence=Decimal("0.7"),
        strategy_version=STRATEGY_VERSION,
    )
    db_session.add(resolved_scenario)
    db_session.commit()
    db_session.add(PaperPosition(
        scenario_id=resolved_scenario.id, symbol="BTCUSDT", direction="long",
        entry_price=Decimal("100"), stop_price=Decimal("90"), target_price=Decimal("110"),
        risk_amount=Decimal("100"), position_size=Decimal("10"),
        opened_at=now - timedelta(hours=1), status="open",
        strategy_version=STRATEGY_VERSION,
    ))
    new_scenario = Scenario(
        symbol="ETHUSDT", direction="long",
        entry_price=Decimal("100"), target_price=Decimal("110"), stop_price=Decimal("90"),
        expected_return_pct=Decimal("0.1"), confidence_score=Decimal("0.7"),
        created_at=now, expires_at=now + timedelta(hours=24), status="pending",
        calibrated_confidence=Decimal("0.7"),
        strategy_version=STRATEGY_VERSION,
    )
    db_session.add(new_scenario)
    db_session.commit()

    result = run_paper_trading_cycle(db_session, now=now)

    assert result.closed == 1
    assert result.opened == 1
    new_position = db_session.query(PaperPosition).filter(PaperPosition.symbol == "ETHUSDT").first()
    # 10 * (110 - 100) gross, less round_trip_cost: taker fee plus slippage on
    # each leg's own notional (1.0 in, 1.1 out).
    expected_equity_after_close = STARTING_EQUITY + Decimal("97.9")
    assert new_position.risk_amount == expected_equity_after_close * RISK_PCT


def test_run_paper_trading_cycle_survives_a_closer_failure_and_still_opens(db_session, monkeypatch):
    scenario = Scenario(
        symbol="BTCUSDT", direction="long",
        entry_price=Decimal("100"), target_price=Decimal("110"), stop_price=Decimal("90"),
        expected_return_pct=Decimal("0.1"), confidence_score=Decimal("0.7"),
        created_at=datetime(2026, 1, 1), expires_at=datetime(2026, 1, 2), status="pending",
        calibrated_confidence=Decimal("0.7"),
        strategy_version=STRATEGY_VERSION,
    )
    db_session.add(scenario)
    db_session.commit()

    import src.paper_trading_runner as runner_module

    def boom(session, now=None):
        raise RuntimeError("closer exploded")

    monkeypatch.setattr(runner_module, "close_resolved_positions", boom)

    result = run_paper_trading_cycle(db_session, now=datetime(2026, 1, 1))

    assert result.closed == 0
    assert result.opened == 1
    assert db_session.query(PaperPosition).count() == 1


def test_run_paper_trading_cycle_survives_an_opener_failure_and_still_closes(db_session, monkeypatch):
    created_at = datetime(2026, 1, 1)
    scenario = Scenario(
        symbol="BTCUSDT", direction="long",
        entry_price=Decimal("100"), target_price=Decimal("110"), stop_price=Decimal("90"),
        expected_return_pct=Decimal("0.1"), confidence_score=Decimal("0.7"),
        created_at=created_at, expires_at=created_at + timedelta(hours=24), status="hit_target",
        calibrated_confidence=Decimal("0.7"),
        strategy_version=STRATEGY_VERSION,
    )
    db_session.add(scenario)
    db_session.commit()
    db_session.add(PaperPosition(
        scenario_id=scenario.id, symbol="BTCUSDT", direction="long",
        entry_price=Decimal("100"), stop_price=Decimal("90"), target_price=Decimal("110"),
        risk_amount=Decimal("100"), position_size=Decimal("10"),
        opened_at=created_at, status="open",
        strategy_version=STRATEGY_VERSION,
    ))
    db_session.commit()

    import src.paper_trading_runner as runner_module

    def boom(session, now=None):
        raise RuntimeError("opener exploded")

    monkeypatch.setattr(runner_module, "open_qualifying_positions", boom)

    result = run_paper_trading_cycle(db_session, now=created_at + timedelta(hours=1))

    assert result.closed == 1
    assert result.opened == 0
