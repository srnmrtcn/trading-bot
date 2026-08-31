from datetime import datetime, timedelta
from decimal import Decimal

from src.db.models import Scenario
from src.scenario_builder import ScenarioDraft
from src.scenario_storage import has_pending_scenario, insert_scenario
from src.strategy_version import STRATEGY_VERSION


def _add_pending(db_session, symbol, direction, created_at, expires_at):
    db_session.add(Scenario(
        symbol=symbol, direction=direction,
        entry_price=Decimal("100"), target_price=Decimal("110"), stop_price=Decimal("95"),
        expected_return_pct=Decimal("0.1"), confidence_score=Decimal("0.6"),
        created_at=created_at, expires_at=expires_at, status="pending",
    ))
    db_session.commit()


def test_has_pending_scenario_false_when_none_exists(db_session):
    assert has_pending_scenario(db_session, "BTCUSDT", "long", datetime(2026, 1, 1)) is False


def test_has_pending_scenario_true_when_one_exists(db_session):
    now = datetime(2026, 1, 1)
    _add_pending(db_session, "BTCUSDT", "long", now, now + timedelta(hours=24))
    assert has_pending_scenario(db_session, "BTCUSDT", "long", now) is True
    assert has_pending_scenario(db_session, "BTCUSDT", "short", now) is False


def test_has_pending_scenario_ignores_an_expired_pending_row(db_session):
    """The dedup hold lasts until the scenario expires *or* is updated. Nothing
    in this subsystem mutates `status`, so without the expiry half of that rule
    one row would block its (symbol, direction) pair permanently."""
    created = datetime(2026, 1, 1)
    _add_pending(db_session, "BTCUSDT", "long", created, created + timedelta(hours=6))

    now = created + timedelta(hours=7)  # past expires_at, status still "pending"

    assert has_pending_scenario(db_session, "BTCUSDT", "long", now) is False


def test_has_pending_scenario_still_blocks_right_up_to_expiry(db_session):
    created = datetime(2026, 1, 1)
    expires = created + timedelta(hours=6)
    _add_pending(db_session, "BTCUSDT", "long", created, expires)

    assert has_pending_scenario(db_session, "BTCUSDT", "long", expires - timedelta(seconds=1)) is True
    assert has_pending_scenario(db_session, "BTCUSDT", "long", expires) is False


def test_insert_scenario_persists_a_pending_row(db_session):
    now = datetime(2026, 1, 1)
    draft = ScenarioDraft(
        symbol="ETHUSDT", direction="short",
        entry_price=Decimal("3000"), target_price=Decimal("2900"), stop_price=Decimal("3050"),
        expected_return_pct=Decimal("0.033"), confidence_score=Decimal("0.4"),
        created_at=now, expires_at=now + timedelta(hours=12),
    )
    insert_scenario(db_session, draft)
    row = db_session.query(Scenario).filter(Scenario.symbol == "ETHUSDT").first()
    assert row is not None
    assert row.status == "pending"
    assert row.direction == "short"
    assert row.strategy_version == STRATEGY_VERSION